"""验证已有 QQ 单次完整就绪、详情页离屏复核、认证阻断及冷启动稳定等待。"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from onepic_desktop_pet import qqmusic_startup
from onepic_desktop_pet.qqmusic_startup import WindowsQQStartup, QQClientNotReady, classify_startup
from onepic_desktop_pet.qqmusic_online import QQMusicOnlinePlayer, dispatch_song
from onepic_desktop_pet.qqmusic_playlist import QQPlaylist
from onepic_desktop_pet.qqmusic_queue_mode import QQQueueMode
from onepic_desktop_pet.music_playback import MusicPlaybackError, SongPlaybackResult
from onepic_desktop_pet.config import PetSettings
from test_qqmusic_playlist import Player, song


READY = [('EditControl', ''), ('ButtonControl', '上一曲'), ('ButtonControl', '下一曲')]


@pytest.mark.parametrize('label,state', [('点击登录','login-required'), ('正在登录','signing-in'), ('安全验证','login-required')])
def test_login_marker_blocks_even_with_cached_player_and_search(label, state):
    assert classify_startup(READY + [('TextControl', label)]) == state
    assert classify_startup(READY) == 'ready'
    assert classify_startup(READY[1:]) == 'loading'


@pytest.mark.parametrize('label', ['登录', 'Login', 'Log in', 'Sign in'])
def test_generic_text_cannot_override_a_complete_player_but_real_login_controls_still_block(label):
    assert classify_startup(READY + [('TextControl', label)]) == 'ready'
    assert classify_startup([('TextControl', label)]) == 'login-required'
    for role in ('ButtonControl', 'WindowControl', 'Other'):
        assert classify_startup(READY + [(role, label)]) == 'login-required'


@pytest.mark.parametrize('third', [('ComboBoxControl', ''), ('ButtonControl', '暂停'),
                                  ('ButtonControl', '播放'), ('ButtonControl', '顺序播放')])
def test_versions_without_edit_search_can_confirm_their_complete_player(third):
    assert classify_startup(READY[1:] + [third]) == 'ready'
    assert classify_startup([third]) == 'loading'


def harness(tmp_path, monkeypatch, states, paths=(), timeout=4):
    executable = tmp_path / 'QQMusic.exe'
    executable.touch()
    now, events, polls = [0.], [], []
    sequence = iter(states)
    current = [states[-1]]
    def probe(path):
        polls.append(path)
        current[0] = next(sequence, current[0])
        return current[0]
    monkeypatch.setattr(qqmusic_startup, 'qq_background_startup_options', lambda _: {})
    value = WindowsQQStartup(running_paths=lambda: paths, probe=probe,
        popen=lambda *a, **k: events.append((a, k)), clock=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds), timeout=timeout)
    return executable, value, events, polls, now


def test_cold_start_is_one_plain_command_before_login_and_never_restarts(tmp_path, monkeypatch):
    exe, startup, events, polls, now = harness(tmp_path, monkeypatch,
        ['loading', 'login-required', 'signing-in', 'ready'])
    assert startup.ensure(exe) == exe
    assert len(events) == 1 and events[0][0] == ([str(exe)],)
    assert events[0][1]['cwd'] == str(exe.parent) and events[0][1]['shell'] is False
    assert len(polls) >= 4 and now[0] >= 2
    assert not any('background' in str(event) or 'play' in str(event) for event in events)


def test_existing_actual_version_is_reused_instead_of_saved_old_executable(tmp_path, monkeypatch):
    actual = tmp_path / 'current' / 'QQMusic.exe'
    exe, startup, events, polls, now = harness(tmp_path, monkeypatch, ['ready'], paths=(actual,))
    assert startup.ensure(exe) == actual and not events and polls == [actual]


def test_transient_login_label_during_existing_client_rebuild_can_recover(tmp_path, monkeypatch):
    exe, startup, events, polls, _ = harness(tmp_path, monkeypatch,
        ['login-required', 'ready', 'ready'], paths=(tmp_path / 'QQMusic.exe',))
    assert startup.ensure(exe) == exe and not events and len(polls) == 2


def test_detail_page_false_offscreen_needs_a_complete_bar_and_verified_mode():
    from onepic_desktop_pet.qqmusic_queue_mode import startup_from_controls
    player = [('ButtonControl', name) for name in ('上一首', '下一首', '播放', '随机播放')]
    seen = []
    assert startup_from_controls([], player, lambda: seen.append(True) or '随机播放') == 'ready'
    assert seen == [True]
    assert startup_from_controls([], player, lambda: '未知模式') == 'loading'
    assert startup_from_controls([], player[:2], lambda: pytest.fail('Incomplete bar')) == 'loading'


@pytest.mark.parametrize('label', ['点击登录', '安全验证', '正在登录'])
def test_offscreen_recovery_never_overrides_visible_or_hidden_authentication_blocker(label):
    from onepic_desktop_pet.qqmusic_queue_mode import startup_from_controls
    player = [('ButtonControl', name) for name in ('上一首', '下一首', '播放')]
    forbidden = lambda: pytest.fail('Authentication evidence cannot be replaced by cached mode')
    expected = 'signing-in' if label == '正在登录' else 'login-required'
    assert startup_from_controls([('TextControl', label)], player, forbidden) == expected
    assert startup_from_controls([], player + [('TextControl', label)], forbidden) == 'loading'


@pytest.mark.parametrize('state,detail', [('loading', '这不表示账号未登录'), ('unavailable', '界面读取失败或超时'),
                                        ('login-required', '持续显示登录或验证'), ('signing-in', '仍显示正在登录')])
def test_not_ready_messages_explain_the_observed_reason_instead_of_asserting_logout(tmp_path, monkeypatch, state, detail):
    exe, startup, events, _, _ = harness(tmp_path, monkeypatch, [state], paths=(tmp_path / 'QQMusic.exe',))
    if state == 'unavailable':
        def unreadable(_): raise OSError('Private implementation detail must not appear in the message')
        startup.probe = unreadable
    with pytest.raises(QQClientNotReady, match=detail) as error:
        startup.ensure(exe)
    assert 'Private' not in str(error.value) and '未发送歌单' in str(error.value) and not events


@pytest.mark.parametrize('state', ['login-required', 'signing-in', 'loading'])
def test_login_not_ready_never_launches_a_second_process_or_dispatches(tmp_path, monkeypatch, state):
    existing = tmp_path / 'QQMusic.exe'
    exe, startup, events, polls, now = harness(tmp_path, monkeypatch, [state], paths=(existing,))
    with pytest.raises(QQClientNotReady, match='未发送歌单'):
        startup.ensure(exe)
    assert not events and now[0] <= 4


def test_cold_login_timeout_leaves_client_alone_and_cancel_cannot_resume(tmp_path, monkeypatch):
    exe, startup, events, polls, now = harness(tmp_path, monkeypatch, ['login-required'])
    with pytest.raises(QQClientNotReady): startup.ensure(exe)
    assert len(events) == 1
    startup.cancelled = lambda: True
    with pytest.raises(QQClientNotReady, match='取消'): startup.ensure(exe)
    assert len(events) == 1


def test_multiple_versions_refuse_without_launch_or_probe(tmp_path, monkeypatch):
    exe, startup, events, polls, _ = harness(tmp_path, monkeypatch, ['ready'], paths=(Path('one'), Path('two')))
    with pytest.raises(QQClientNotReady, match='多个'): startup.ensure(exe)
    assert not events and not polls


def test_online_player_checks_readiness_before_any_dispatch_or_mode(tmp_path):
    exe = tmp_path / 'QQMusic.exe'; exe.touch()
    def not_ready(_): raise QQClientNotReady('正在登录，未投递')
    player = QQMusicOnlinePlayer(PetSettings(), lambda: pytest.fail('No playback check before dispatch'),
        client_finder=lambda *_: exe, client_preparer=not_ready,
        dispatcher=lambda *_: pytest.fail('No song command during login'))
    result = player.play_candidate(song(1), prepare_queue=lambda: pytest.fail('No mode command during login'))
    assert not result.success and result.error_code is MusicPlaybackError.CLIENT_NOT_READY and result.play_attempts == 0


def test_cancel_arriving_during_readiness_cannot_dispatch(tmp_path):
    cancelled = [False]
    def prepare(path): cancelled[0] = True; return path
    player = QQMusicOnlinePlayer(PetSettings(), lambda: None, client_finder=lambda *_: tmp_path / 'QQMusic.exe',
        client_preparer=prepare, cancelled=lambda: cancelled[0], dispatcher=lambda *_: pytest.fail('Late play after cancel'))
    assert player.play_candidate(song(1)).error_code is MusicPlaybackError.CLIENT_NOT_READY


def test_blocked_playlist_keeps_thirty_without_pause_or_restore_commands(tmp_path):
    player = Player()
    player.play_candidate = lambda *_a, **_k: SongPlaybackResult(False, 'qq', '', '陈楚生', '请先登录', MusicPlaybackError.CLIENT_NOT_READY)
    backend = SimpleNamespace(read=lambda: pytest.fail('No mode read'), set=lambda *_: pytest.fail('No mode command'))
    mode = QQQueueMode(backend)
    mode.original, mode.isolated = '随机播放', True
    queue = QQPlaylist(player, store_path=tmp_path / 'list.json', queue_mode=mode,
        stopper=lambda: pytest.fail('No pause for an unissued play'))
    result = queue.create()
    assert not result.success and len(queue.entries) == 30 and not queue.auto_advance
    assert mode.original is None and not mode.isolated
    assert len(QQPlaylist(Player(), store_path=tmp_path / 'list.json').entries) == 30
    assert not queue.select(1).success


def test_dispatch_rechecks_process_and_never_becomes_a_cold_start(tmp_path):
    exe = tmp_path / 'QQMusic.exe'; exe.touch()
    def gone(_): raise QQClientNotReady('QQ closed')
    with pytest.raises(QQClientNotReady):
        dispatch_song(exe, '1', running_check=gone, popen=lambda *_a, **_k: pytest.fail('No cold song command'))
    from test_qq_native_queue import native
    queue, _requests, commands = native((song(1),))
    queue.running_check = gone
    with pytest.raises(QQClientNotReady): queue.dispatch(exe, (song(1),), 0)
    assert not commands
