"""验证模式动作的真实读回、最新用户模式，以及未被接收队列的单次保守重试。"""
from dataclasses import replace
from pathlib import Path

import pytest

from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.music_control import TrackInfo
from onepic_desktop_pet.music_playback import MusicPlaybackOutcome, SongCandidate
from onepic_desktop_pet.qqmusic_online import QQMusicOnlinePlayer
from onepic_desktop_pet.qqmusic_queue_mode import QQQueueMode
from test_qq_queue_mode import Backend


@pytest.mark.parametrize('action', ['false', 'exception'])
@pytest.mark.parametrize('original', ['随机播放', '列表循环', '单曲循环'])
def test_fresh_target_readback_wins_over_a_rejected_or_stale_action_reply(action, original):
    backend = Backend(original)
    def set_mode(target):
        backend.value = target
        if action == 'exception':
            raise OSError('QQ rebuilt its old UIA node after applying mode')
        return False
    backend.set = set_mode
    mode = QQQueueMode(backend, target='顺序播放')
    assert mode.acquire() and mode.isolated
    assert mode.release() and backend.value == original


def test_new_user_mode_becomes_the_restoration_target_on_next_selection():
    backend = Backend('随机播放')
    mode = QQQueueMode(backend, target='顺序播放')
    assert mode.acquire()
    backend.value = '列表循环'  # User changes QQ while the pet is listening.
    assert mode.acquire() and backend.value == '顺序播放'
    assert mode.release() and backend.value == '列表循环'


def test_failed_acquire_does_not_force_overwrite_another_mode_chosen_during_the_action():
    backend = Backend('随机播放')
    calls = []
    def set_mode(_target):
        calls.append(_target)
        backend.value = '单曲循环'
        return False
    backend.set = set_mode
    mode = QQQueueMode(backend, target='顺序播放')
    assert not mode.acquire() and not mode.isolated
    assert backend.value == '单曲循环'
    assert calls == ['顺序播放']


TARGET = SongCandidate('qq', '空中怪车事件', '陈楚生', identifier='634001649', native={'album': '空中怪车事件'})
OLD = TrackInfo('我在何地', '陈楚生', '我在何地', 'paused', 5000, 200000, raw_position_ms=5000)
PLAYING = TrackInfo(TARGET.title, TARGET.artist, '空中怪车事件', 'playing')


def recovery_player(reader=None, *, accept_second=False, cancelled=None):
    now, sent = [0.0], []
    def track():
        return PLAYING if accept_second and len(sent) == 2 else reader(now[0]) if reader else OLD
    player = QQMusicOnlinePlayer(PetSettings(), track,
        client_finder=lambda *_: Path('QQMusic.exe'),
        candidate_dispatcher=lambda *_: sent.append(now[0]),
        clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s),
        cancelled=(lambda: cancelled(now[0])) if cancelled else lambda: False,
        verify_timeout=4, poll_interval=.25)
    return player, sent, now


def test_only_unacknowledged_native_queue_gets_one_retry_with_the_same_song():
    player, sent, now = recovery_player(accept_second=True)
    result = player.play_candidate(TARGET, retry_unacknowledged=True, retry_guard=lambda *_: True)
    assert result.outcome is MusicPlaybackOutcome.PLAYBACK_CONFIRMED
    assert result.play_attempts == 2 and len(sent) == 2
    assert 2 <= now[0] < 3


def test_retry_is_bounded_by_original_deadline_and_reports_the_actual_old_paused_song():
    player, sent, now = recovery_player()
    result = player.play_candidate(TARGET, retry_unacknowledged=True, retry_guard=lambda *_: True)
    assert result.outcome is MusicPlaybackOutcome.PLAYBACK_STARTED_UNVERIFIED
    assert result.play_attempts == len(sent) == 2 and now[0] == 4
    assert '我在何地' in result.message and '未开始播放' in result.message


@pytest.mark.parametrize('transition', [
    lambda t: None,
    lambda t: replace(OLD, playback_status='playing' if t < 1 else 'paused'),
    lambda t: replace(PLAYING, playback_status='paused'),
    lambda t: OLD if t < .5 else replace(OLD, raw_position_ms=6000),
    lambda t: OLD if t < .5 else replace(PLAYING, playback_status='changing'),
    lambda t: OLD if t < .5 else replace(OLD, title='用户切到的另一首'),
])
def test_any_acknowledgement_missing_evidence_or_user_change_disables_retry(transition):
    player, sent, _now = recovery_player(transition)
    player.play_candidate(TARGET, retry_unacknowledged=True, retry_guard=lambda *_: True)
    assert len(sent) == 1


def test_cancel_beats_a_retry_and_the_non_native_default_never_retries():
    player, sent, _now = recovery_player(cancelled=lambda t: t >= 1.75)
    assert not player.play_candidate(TARGET, retry_unacknowledged=True, retry_guard=lambda *_: True).success
    assert len(sent) == 1
    player, sent, _now = recovery_player()
    player.play_candidate(TARGET)
    assert len(sent) == 1


@pytest.mark.parametrize('guard', [None, lambda *_: False, lambda *_: 1 / 0])
def test_a_stale_paused_session_cannot_restart_a_song_when_client_evidence_is_missing_or_different(guard):
    player, sent, _now = recovery_player()
    player.play_candidate(TARGET, retry_unacknowledged=True, retry_guard=guard)
    assert len(sent) == 1


@pytest.mark.parametrize('captions,expected', [
    (['我在何地 - 陈楚生   '], True), (['空中怪车事件 - 陈楚生'], False),
    (['QQ音乐'], False), ([], False), (['我在何地 - 陈楚生', '我在何地 - 陈楚生'], False),
    (['我在何地 - 其他歌手'], False),
])
def test_only_one_exact_old_song_caption_can_support_a_retry(captions, expected):
    from onepic_desktop_pet.qqmusic_native_queue import unchanged_song_caption
    assert unchanged_song_caption(captions, OLD) is expected


@pytest.mark.parametrize('windows,expected', [
    ([('我在何地 - 陈楚生', True), ('QQ音乐', False)], True),
    ([('我在何地 - 陈楚生', True), ('QQ音乐', True)], False),
    ([('空中怪车事件 - 陈楚生', True), ('我在何地 - 陈楚生', False)], False),
    ([('我在何地 - 陈楚生', False)], True),
    ([], False),
])
def test_client_title_guard_uses_visible_player_or_hidden_tray_fallback_and_vetoes_other_visible_windows(monkeypatch, windows, expected):
    import ctypes
    from types import SimpleNamespace
    from onepic_desktop_pet import music_background
    from onepic_desktop_pet.qqmusic_native_queue import QQNativeQueue
    def class_name(_handle, output, _size):
        output.value = 'TXGuiFoundation'
        return len(output.value)
    def caption(handle, output, _size):
        output.value = windows[handle - 1][0]
        return len(output.value)
    user = SimpleNamespace(GetWindow=lambda *_: 0, GetClassNameW=class_name,
                           GetWindowTextW=caption, IsWindowVisible=lambda h: windows[h - 1][1])
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *_a, **_k: user, raising=False)
    monkeypatch.setattr(music_background, 'music_client_mode_windows', lambda _: tuple(range(1, len(windows) + 1)))
    assert QQNativeQueue().retry_is_safe(Path('QQMusic.exe'), OLD) is expected
