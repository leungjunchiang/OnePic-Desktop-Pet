"""验证被动检查不吞随机或选曲点击、最新请求按歌曲 ID 执行、暂停与退出优先。"""
from threading import Event
import time
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication
from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.music_control import MusicController
from test_qqmusic_playlist import song


@pytest.mark.parametrize('intent', ['select', 'random', 'pause', 'shutdown', 'changed_playlist'])
def test_user_request_during_slow_passive_poll_is_serialized_and_never_lost(intent):
    app = QApplication.instance() or QApplication([])
    entered, release = Event(), Event()
    calls = []
    queue = SimpleNamespace(entries=tuple(song(i) for i in range(1,4)), auto_advance=True)
    def control(_provider, action):
        calls.append(action)
        if action == 'playlist_tick':
            entered.set()
            assert release.wait(2)
        return None
    manager = SimpleNamespace(qq_playlist=queue, control=control,
        play_qq_playlist_index=lambda index: calls.append(('select', index)),
        play_qq_online_song=lambda *args: calls.append(('random', args)))
    controller = MusicController(PetSettings(), manager)
    try:
        assert controller.perform('playlist_tick') and entered.wait(1)
        assert controller.play_qq_playlist_index(0)
        assert controller.play_qq_playlist_index(2)  # Latest intent supersedes the first click.
        assert not queue.auto_advance
        if intent == 'random':
            assert controller.play_qq_song()
        elif intent == 'pause':
            assert controller.perform('pause')
        elif intent == 'changed_playlist':
            queue.entries = tuple(reversed(queue.entries))
        release.set()
        if intent == 'shutdown':
            controller.shutdown()
        deadline = time.monotonic()+2
        while controller.busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.002)
        assert not controller.busy
        expected = {'select': [('select', 2)], 'random': [('random', ('', '陈楚生'))],
                    'pause': ['pause'], 'shutdown': [], 'changed_playlist': [('select', 0)]}
        assert calls == ['playlist_tick'] + expected[intent]
    finally:
        release.set()
        controller.shutdown()
        app.processEvents()


def test_playlist_double_click_immediately_shows_feedback_only_when_request_is_accepted():
    from PySide6.QtCore import QObject, Signal
    from onepic_desktop_pet.music_playlist_ui import MusicPlaylistDialog
    app = QApplication.instance() or QApplication([])
    queue = SimpleNamespace(entries=(song(1), song(2)), index=0, round_number=1, auto_advance=False)
    class Controller(QObject):
        result_ready = Signal(object)
        busy_changed = Signal(bool)
        busy = False
        manager = SimpleNamespace(qq_playlist=queue)
        accepted = False
        calls = []
        def play_qq_playlist_index(self, index):
            self.calls.append(index)
            return self.accepted
    controller = Controller()
    dialog = MusicPlaylistDialog(controller)
    try:
        dialog.songs.itemDoubleClicked.emit(dialog.songs.item(1))
        assert dialog.state.text() == '已就绪'
        controller.accepted = True
        dialog.songs.itemDoubleClicked.emit(dialog.songs.item(1))
        assert dialog.state.text() == '正在切歌…' and controller.calls == [1, 1]
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
