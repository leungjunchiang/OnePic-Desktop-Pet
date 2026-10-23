"""验证原生命令及模式读回、最小化前准备、失败暂停，以及完整单曲回绕才续播。"""
from contextlib import nullcontext
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
from onepic_desktop_pet.qqmusic_queue_mode import QQQueueMode, SINGLE, _WindowsQQModeNativeBackend
from test_qqmusic_playlist import queue, track
from onepic_desktop_pet.music_control import MusicProviderManager, WindowsSessionSnapshot, TrackInfo
from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.qqmusic_online import QQMusicOnlinePlayer
from onepic_desktop_pet.qqmusic_playlist import QQPlaylist
from onepic_desktop_pet.music_playback import SongCandidate, MusicPlaybackError


class Backend:
    def __init__(self, value='随机播放', *, accept=True):
        self.value, self.accept, self.changes = value, accept, []

    def read(self):
        return self.value

    def set(self, value):
        self.changes.append(value)
        if self.accept:
            self.value = value
        return self.accept


def test_acquire_verifies_single_and_pause_restores_original_mode():
    backend = Backend()
    mode = QQQueueMode(backend)
    assert mode.acquire() and mode.isolated and backend.value == SINGLE
    assert mode.acquire() and backend.changes == [SINGLE]
    assert mode.release() and not mode.isolated and backend.value == '随机播放'
    assert mode.release() and backend.changes == [SINGLE, '随机播放']


def test_user_single_mode_is_kept_and_user_later_mode_change_is_respected():
    backend = Backend(SINGLE)
    mode = QQQueueMode(backend)
    assert mode.acquire() and mode.release() and not backend.changes
    backend.value = '随机播放'
    assert mode.acquire()
    backend.value = '顺序播放'  # A new explicit user action owns the mode now.
    assert mode.release() and backend.value == '顺序播放'


def test_claimed_invoke_success_without_readback_never_arms():
    backend = Backend(accept=False)
    mode = QQQueueMode(backend)
    assert not mode.acquire() and not mode.isolated
    value, player = queue()
    value.queue_mode = mode
    pauses = []
    value.stopper = lambda: pauses.append('pause')
    result = value.create()
    assert not result.success and not value.auto_advance
    assert pauses == ['pause'] and len(value.entries) == 30
    assert result.playlist_size == 30 and result.playlist_created


def test_verified_single_loop_wrap_advances_without_a_foreign_track():
    value, player = queue()
    value.queue_mode = SimpleNamespace(isolated=True)
    assert not value.ended(track(value, position=199000))
    assert not value.ended(track(value, position=0))  # Deadline not reached yet.
    value.test_time[0] += 1.1
    assert value.ended(track(value, position=500))
    assert len(player.sent) == 1  # Completion detection itself sends no command.


def test_seek_back_in_middle_does_not_imitate_a_single_loop():
    value, _ = queue()
    value.queue_mode = SimpleNamespace(isolated=True)
    assert not value.ended(track(value, position=100000))
    value.test_time[0] += 200
    assert not value.ended(track(value, position=0))


def test_thirty_single_loop_wraps_keep_playlist_order_and_create_next_round():
    value, player = queue()
    mode = QQQueueMode(Backend(SINGLE))
    assert mode.acquire()
    value.queue_mode = mode
    manager = MusicProviderManager(PetSettings(), platform_name='win32', client_finder=lambda *_: None)
    manager.qq_playlist = value
    for index in range(1, 31):
        before = value.entries[(value.index + 1) % 30].identifier
        manager._qq_online_track = lambda: track(value, position=199000)
        assert manager._playlist_tick() is None
        value.test_time[0] += 1.1
        manager._qq_online_track = lambda: track(value, position=500)
        result = manager._playlist_tick()
        assert result.success and value.index == index % 30
        if index < 30:
            assert player.sent[-1] == before
        else:
            assert value.round_number == 2
    assert len(player.sent) == 31 and mode.isolated


def test_resume_mode_failure_disarms_and_pauses_instead_of_leaving_old_queue_running():
    value, _ = queue()
    state, commands = ['playing'], []
    def control(source, action):
        commands.append((source, action))
        state[0] = 'paused'
        return True
    bridge = SimpleNamespace(
        sessions=lambda: (WindowsSessionSnapshot('QQMusic.exe', track(value, state[0]), frozenset(('play','pause'))),),
        control=control)
    manager = MusicProviderManager(PetSettings(), platform_name='win32', windows_bridge=bridge,
        qq_queue_mode=SimpleNamespace(acquire=lambda: False, release=lambda: True),
        client_finder=lambda *_: None)
    manager.qq_playlist = value
    manager.active_provider, manager._online_qq_selected = 'qq', True
    result = manager.control('qq', 'play')
    assert not result.success and result.status.track.playback_status == 'paused'
    assert not value.auto_advance and commands == [('QQMusic.exe','pause')]


def online_queue(*, accept=True):
    events, selected = [], []
    class VisibleBackend(Backend):
        def set(self, value):
            events.append('mode')
            # The real QQ provider rejects actions after its controls go offscreen.
            if 'background' in events:
                return False
            return super().set(value)
    mode = QQQueueMode(VisibleBackend(accept=accept))
    songs = tuple(SongCandidate('qq', f'歌曲{i}', '陈楚生', identifier=str(i)) for i in range(1,31))
    def dispatch(_exe, identifier):
        events.append('dispatch')
        selected[:] = [next(song for song in songs if song.identifier == identifier)]
    def read():
        return TrackInfo(selected[0].title, '陈楚生', '', 'playing') if selected else None
    player = QQMusicOnlinePlayer(PetSettings(), read, verify_timeout=0,
        client_finder=lambda *_: Path('QQMusic.exe'), dispatcher=dispatch,
        catalog=SimpleNamespace(search=lambda *_args, **_kwargs: songs),
        backgrounder=lambda _:events.append('background'))
    playlist = QQPlaylist(player, queue_mode=mode, shuffle=lambda _:None,
                          stopper=lambda:events.append('pause'))
    return playlist, mode, events


def test_real_online_player_acquires_mode_before_minimizing_and_arms_exact_queue():
    playlist, mode, events = online_queue()
    result = playlist.create()
    assert result.success and mode.isolated and playlist.auto_advance
    assert len(playlist.entries) == 30 and playlist.index == 0
    assert events == ['dispatch', 'mode', 'background']
    assert playlist.move(1).success and playlist.index == 1
    assert events.count('mode') == 1  # Already verified single mode needs no new button action.


def test_failed_mode_action_pauses_before_backgrounding_and_explains_setup():
    playlist, mode, events = online_queue(accept=False)
    result = playlist.create()
    assert not result.success and not mode.isolated and not playlist.auto_advance
    assert result.error_code is MusicPlaybackError.UI_AUTOMATION_UNAVAILABLE
    assert '已找到 QQ 音乐' in result.message and '底部播放栏' in result.message
    assert len(playlist.entries) == 30 and playlist.index == 0
    assert events == ['dispatch', 'mode', 'pause', 'background']


@pytest.mark.parametrize('failure', ['false', 'exception', 'lookup_exception'])
def test_false_or_throwing_invoke_uses_legacy_action(failure):
    events = []
    def invoke():
        events.append('invoke')
        if failure == 'exception':
            raise OSError('unsupported')
        return False
    def legacy():
        events.append('legacy')
        return True
    def pattern(kind):
        if kind == 1:
            if failure == 'lookup_exception':
                raise OSError('pattern lookup failed')
            return SimpleNamespace(Invoke=invoke)
        return SimpleNamespace(DoDefaultAction=legacy)
    api = SimpleNamespace(PatternId=SimpleNamespace(InvokePattern=1, LegacyIAccessiblePattern=2))
    _WindowsQQModeNativeBackend(lambda:None)._invoke(api, SimpleNamespace(GetPattern=pattern))
    assert events == (['legacy'] if failure == 'lookup_exception' else ['invoke','legacy'])


def test_both_false_mode_actions_fail_instead_of_claiming_success():
    api = SimpleNamespace(PatternId=SimpleNamespace(InvokePattern=1, LegacyIAccessiblePattern=2))
    action = SimpleNamespace(Invoke=lambda:False, DoDefaultAction=lambda:False)
    with pytest.raises(OSError, match='拒绝'):
        _WindowsQQModeNativeBackend(lambda:None)._invoke(api, SimpleNamespace(GetPattern=lambda _:action))


@pytest.mark.parametrize('target,code', [(SINGLE,'1'),('顺序播放','2'),('列表循环','3'),('随机播放','4')])
def test_native_mode_command_uses_only_validated_running_client_and_fixed_numeric_args(tmp_path, monkeypatch, target, code):
    from onepic_desktop_pet import music_background
    from onepic_desktop_pet.qqmusic_queue_mode import dispatch_native_mode
    client=tmp_path/'QQMusic.exe'
    client.touch()
    calls=[]
    monkeypatch.setattr(music_background,'music_client_windows',lambda path:(123,) if path==client else ())
    assert dispatch_native_mode(client,target,popen=lambda *args,**kwargs:calls.append((args,kwargs)))
    assert calls[0][0] == ([str(client),'/playmode',code],)
    assert calls[0][1]['shell'] is False
    assert not dispatch_native_mode(client,'单曲播放',popen=lambda *_a,**_k:pytest.fail('unverified native mode'))
    monkeypatch.setattr(music_background,'music_client_windows',lambda _path:())
    assert not dispatch_native_mode(client,target,popen=lambda *_a,**_k:pytest.fail('client not running'))
    other=tmp_path/'OtherMusic.exe'
    other.touch()
    assert not dispatch_native_mode(other,target,popen=lambda *_a,**_k:pytest.fail('wrong executable'))


def test_native_mode_must_be_read_back_before_accessibility_can_be_skipped(monkeypatch):
    from onepic_desktop_pet import qqmusic_queue_mode as module
    backend=_WindowsQQModeNativeBackend(lambda:Path('QQMusic.exe'))
    monkeypatch.setattr(module,'dispatch_native_mode',lambda *_:True)
    backend.read=lambda:SINGLE
    backend._controls=lambda _api:pytest.fail('native readback already confirmed')
    assert backend.set(SINGLE)


def test_accepted_native_command_with_unchanged_mode_never_succeeds(monkeypatch):
    from onepic_desktop_pet import qqmusic_queue_mode as module
    backend=_WindowsQQModeNativeBackend(lambda:Path('QQMusic.exe'))
    monkeypatch.setattr(module,'dispatch_native_mode',lambda *_:True)
    ticks=iter((0.,2.))
    monkeypatch.setattr(module,'time',SimpleNamespace(monotonic=lambda:next(ticks),sleep=lambda _:None))
    monkeypatch.setitem(sys.modules,'uiautomation',SimpleNamespace(UIAutomationInitializerInThread=nullcontext))
    backend.read=lambda:'随机播放'
    backend._controls=lambda _api:()
    assert not backend.set(SINGLE)


def test_cancellation_before_native_mode_never_dispatches(monkeypatch):
    from onepic_desktop_pet import qqmusic_queue_mode as module
    backend=_WindowsQQModeNativeBackend(lambda:Path('QQMusic.exe'),cancelled=lambda:True)
    monkeypatch.setattr(module,'dispatch_native_mode',lambda *_:pytest.fail('cancelled'))
    assert not backend.set(SINGLE)


def test_closed_native_command_window_does_not_hide_the_real_qq_root(monkeypatch):
    from onepic_desktop_pet import music_background
    monkeypatch.setattr(music_background,'music_client_windows',lambda _path:(100,200))
    root=object()
    def control(hwnd):
        if hwnd==100:
            raise OSError('window closed during native dispatch')
        return root
    backend=_WindowsQQModeNativeBackend(lambda:Path('QQMusic.exe'))
    assert backend._roots(SimpleNamespace(ControlFromHandle=control)) == [root]


def test_mode_readback_rejects_conflicting_player_bars(monkeypatch):
    fake=SimpleNamespace(UIAutomationInitializerInThread=nullcontext)
    monkeypatch.setitem(sys.modules,'uiautomation',fake)
    region=SimpleNamespace(GetChildren=lambda:[SimpleNamespace(Name='下一曲'),SimpleNamespace(Name='上一曲')])
    parent=SimpleNamespace(GetParentControl=lambda:region)
    button=SimpleNamespace(ControlTypeName='ButtonControl',GetParentControl=lambda:parent)
    backend=_WindowsQQModeNativeBackend(lambda:Path('QQMusic.exe'))
    backend._controls=lambda _api:((SINGLE,button),('随机播放',button))
    with pytest.raises(OSError,match='不可访问'):
        backend.read()
