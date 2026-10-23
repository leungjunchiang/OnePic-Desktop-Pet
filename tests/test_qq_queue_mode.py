"""验证原队列隔离、模式读回/恢复、失败暂停，以及完整单曲回绕才续播。"""
from types import SimpleNamespace
from onepic_desktop_pet.qqmusic_queue_mode import QQQueueMode, SINGLE
from test_qqmusic_playlist import queue, track
from onepic_desktop_pet.music_control import MusicProviderManager, WindowsSessionSnapshot
from onepic_desktop_pet.config import PetSettings


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
    original = value.entries
    result = value.create()
    assert not result.success and not value.auto_advance
    assert pauses == ['pause'] and value.entries == original


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
