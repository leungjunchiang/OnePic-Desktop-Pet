"""验证已有 QQ 窗口完全不处理，未运行时仅传启动参数，不使用事后最小化回调。"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from onepic_desktop_pet import music, music_background
from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.music_control import TrackInfo
from onepic_desktop_pet.music_playback import MusicPlaybackOutcome
from onepic_desktop_pet.qqmusic_online import QQMusicOnlinePlayer
from onepic_desktop_pet.qqmusic_online import candidate_matches
from test_qqmusic_playlist import song


@pytest.mark.parametrize('handles,pids', [((100,), set()), ((), {123})])
def test_existing_window_or_tray_process_has_no_background_launch_or_hide(tmp_path, monkeypatch, handles, pids):
    executable = tmp_path / 'QQMusic.exe'
    executable.touch()
    monkeypatch.setattr(music_background, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(music_background, 'music_client_windows', lambda _: handles)
    monkeypatch.setattr(music, '_windows_process_ids', lambda _: pids)
    monkeypatch.setattr(music_background.subprocess, 'STARTUPINFO', lambda: pytest.fail('Existing QQ must remain unchanged'), raising=False)
    assert music_background.qq_background_startup_options(executable) == {}


def test_only_a_cold_start_receives_initial_minimized_flag(tmp_path, monkeypatch):
    executable = tmp_path / 'QQMusic.exe'
    executable.touch()
    monkeypatch.setattr(music_background, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(music_background, 'music_client_windows', lambda _: ())
    monkeypatch.setattr(music, '_windows_process_ids', lambda _: set())
    monkeypatch.setattr(music_background.subprocess, 'STARTUPINFO', lambda: SimpleNamespace(dwFlags=0), raising=False)
    monkeypatch.setattr(music_background.subprocess, 'STARTF_USESHOWWINDOW', 1, raising=False)
    options = music_background.qq_background_startup_options(executable)
    assert options['startupinfo'].wShowWindow == 7 and options['startupinfo'].dwFlags == 1
    # After the client starts, subsequent tracks must not submit another hide request.
    monkeypatch.setattr(music, '_windows_process_ids', lambda _: {123})
    assert music_background.qq_background_startup_options(executable) == {}


def test_verification_retries_mode_setup_and_timeout_never_call_legacy_hider():
    current, events = [None], []
    candidate = song(1)
    player = QQMusicOnlinePlayer(PetSettings(), lambda: current[0], verify_timeout=0,
        client_finder=lambda *_: Path('QQMusic.exe'), dispatcher=lambda *_: events.append('dispatch'),
        backgrounder=lambda *_: pytest.fail('Never minimize or hide an existing client'))
    result = player.play_candidate(candidate)
    assert result.success and result.outcome is MusicPlaybackOutcome.PLAYBACK_STARTED_UNVERIFIED and events == ['dispatch']
    current[0] = TrackInfo(candidate.title, candidate.artist, '专辑', 'playing')
    assert player.play_candidate(candidate, prepare_queue=lambda: True).outcome is MusicPlaybackOutcome.PLAYBACK_CONFIRMED
    assert not player.play_candidate(candidate, prepare_queue=lambda: False).success


def test_mode_window_selection_excludes_large_timer_and_shadow_but_keeps_player_and_menu(monkeypatch):
    import ctypes
    windows = {1: ('TXGuiFoundation', True, 0, 1000), 2: ('QMShadowWndClass', True, 1, 2000),
        3: ('AnimationTimerClass', False, 0, 3000), 4: ('TXGuiFoundation', False, 0, 900),
        5: ('#32768', True, 1, 100), 6: ('TXGuiFoundation', False, 0, 200), 7: ('ServiceWnd', False, 0, 0)}
    def class_name(hwnd, buffer, _size):
        buffer.value = windows[hwnd][0]
        return len(buffer.value)
    def placement(hwnd, pointer):
        pointer._obj.rect.right = windows[hwnd][3]
        pointer._obj.rect.bottom = 1
        return True
    api = SimpleNamespace(GetClassNameW=class_name, GetWindowPlacement=placement,
        IsWindowVisible=lambda hwnd: windows[hwnd][1], GetWindow=lambda hwnd, _: windows[hwnd][2])
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *_a, **_k: api, raising=False)
    monkeypatch.setattr(music_background, 'music_client_windows', lambda _: tuple(windows))
    assert music_background.music_client_mode_windows(Path('QQMusic.exe')) == (1, 5, 4)


def test_trusted_windows_missing_album_still_requires_full_version_and_artists():
    candidate = song(1)
    track = TrackInfo(candidate.title, candidate.artist, '', 'playing', metadata_scope='windows-gsmtc')
    assert candidate_matches(candidate, track)
    assert not candidate_matches(candidate, TrackInfo(candidate.title + ' (Live)', candidate.artist, '', 'playing', metadata_scope='windows-gsmtc'))
    assert not candidate_matches(candidate, TrackInfo(candidate.title, '翻唱者', '', 'playing', metadata_scope='windows-gsmtc'))
    assert not candidate_matches(candidate, TrackInfo(candidate.title, candidate.artist, '另一专辑', 'playing', metadata_scope='windows-gsmtc'))
    assert not candidate_matches(candidate, TrackInfo(candidate.title, candidate.artist, '', 'playing'))


def test_unverified_playback_keeps_real_reason_instead_of_wrong_mode_error():
    from onepic_desktop_pet.qqmusic_playlist import QQPlaylist
    from onepic_desktop_pet.music_playback import SongPlaybackResult, MusicPlaybackError
    from test_qqmusic_playlist import Player
    player = Player()
    player.play_candidate = lambda selected, **_: SongPlaybackResult(True, 'qq', '', '陈楚生', '[Mac QQ / 播放栏] 未识别',
        selected=selected, outcome=MusicPlaybackOutcome.PLAYBACK_STARTED_UNVERIFIED)
    mode = SimpleNamespace(acquire=lambda: pytest.fail('Playback must be confirmed before mode acquisition'), release=lambda: True)
    stopped = []
    queue = QQPlaylist(player, queue_mode=mode, stopper=lambda: stopped.append(True))
    result = queue.create()
    assert not result.success and result.error_code is MusicPlaybackError.MEDIA_SESSION_TIMEOUT
    assert '[Mac QQ / 播放栏]' in result.message and '单曲循环' not in result.message and stopped


def test_only_own_cold_start_can_hide_once_and_new_user_input_cancels_ownership(tmp_path, monkeypatch):
    executable = tmp_path / 'QQMusic.exe'
    executable.touch()
    monkeypatch.setattr(music_background, 'sys', SimpleNamespace(platform='win32'))
    current_input, hidden = [10], []
    policy = music_background.QQColdStartPolicy(running=lambda _: False,
        input_stamp=lambda: current_input[0], minimize=hidden.append)
    ticket = policy.begin(executable)
    policy.finish(ticket)
    policy.finish(ticket)
    assert hidden == [executable]
    ticket = policy.begin(executable)
    current_input[0] += 1  # The user may open QQ or interact with another app while it loads.
    policy.finish(ticket)
    assert hidden == [executable]
    policy.running = lambda _: True
    policy.finish(policy.begin(executable))
    assert hidden == [executable]
