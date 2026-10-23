"""验证点歌地址引导、后台不弹歌单、上方锚定及优先暂停不重新续播。"""

import os
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from onepic_desktop_pet.music_playback import MusicPlaybackOutcome, SongPlaybackResult
from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.music_control import (
    MusicControlResult, MusicControlState, MusicController, MusicProviderStatus, TrackInfo,
)
from onepic_desktop_pet.window import PetWindow


def test_random_menu_entry_calls_online_player_instead_of_browser():
    calls = []
    pet = SimpleNamespace(
        settings=SimpleNamespace(qq_music_path='QQMusic.exe'),
        music_controller=SimpleNamespace(play_qq_song=lambda: calls.append('online') or True),
        show_speech=lambda *_: None)
    assert PetWindow.play_random_song(pet) == '陈楚生随机歌曲'
    assert calls == ['online']


def test_missing_qq_address_goes_to_target_field_and_cancel_does_not_play():
    calls = []
    pet = SimpleNamespace(settings=SimpleNamespace(qq_music_path=''),
        open_settings=lambda source, **kwargs: calls.append((source, kwargs)),
        music_controller=SimpleNamespace(play_qq_song=lambda: calls.append('play') or True),
        show_speech=lambda *_: None)
    assert '地址' in PetWindow.play_random_song(pet)
    assert len(calls) == 1 and calls[0][1] == {'section': 'qq_music'}


def test_saving_qq_address_from_random_action_continues_playback_once():
    calls = []
    pet = SimpleNamespace(settings=SimpleNamespace(qq_music_path=''),
        music_controller=SimpleNamespace(play_qq_song=lambda: calls.append('play') or True),
        show_speech=lambda *_: None)
    def settings(source, **kwargs):
        calls.append(kwargs['section'])
        pet.settings.qq_music_path = 'QQMusic.exe'
    pet.open_settings = settings
    assert PetWindow.play_random_song(pet) == '陈楚生随机歌曲'
    assert calls == ['qq_music', 'play']


def test_settings_scrolls_to_and_focuses_qq_address_without_touching_credentials():
    from PySide6.QtWidgets import QApplication
    from onepic_desktop_pet.chat import AISettingsDialog
    app = QApplication.instance() or QApplication([])
    credentials = SimpleNamespace(get=lambda *_: '', has=lambda *_: False)
    dialog = AISettingsDialog(PetSettings(), credentials)
    dialog.show()
    app.processEvents()
    dialog.focus_qq_music_path()
    app.processEvents()
    field = dialog.qq_music_path
    viewport = dialog._settings_scroll.viewport()
    assert viewport.rect().contains(field.mapTo(viewport, field.rect().center()))
    assert dialog._settings_scroll.verticalScrollBar().value() > 0
    assert field.hasFocus()
    dialog.close(); dialog.deleteLater(); app.processEvents()


def test_playlist_is_above_pet_and_shortcuts_and_follows_anchor(monkeypatch):
    from PySide6.QtCore import QRect
    from test_window import _create_window
    app, pet = _create_window()
    monkeypatch.setattr(pet, '_screen_geometry', lambda: QRect(0, 0, 1800, 1200))
    pet.move(700, 900)
    pet.show_music_playlist()
    app.processEvents()
    dialog = pet._music_playlist_dialog
    assert dialog.frameGeometry().bottom() < min(pet.y(), pet.quick_panel.y())
    before = dialog.pos()
    pet.move(400, 800)
    pet._position_accessories()
    app.processEvents()
    assert dialog.pos() != before
    assert dialog.frameGeometry().bottom() < min(pet.y(), pet.quick_panel.y())
    dialog.resize(350, 450)
    app.processEvents()
    assert dialog.frameGeometry().bottom() < min(pet.y(), pet.quick_panel.y())
    pet.move(400, 5)
    pet._position_accessories()
    app.processEvents()
    assert not dialog.frameGeometry().intersects(pet.geometry())
    assert not dialog.frameGeometry().intersects(pet.quick_panel.geometry())
    dialog.close()
    pet.close(); pet.deleteLater(); app.processEvents()


def test_chat_exact_song_and_pause_use_shared_controller():
    calls = []
    pet = SimpleNamespace(music_controller=SimpleNamespace(
        play_qq_song=lambda *args: calls.append(('song', args)) or True,
        perform=lambda action: calls.append(('control', action)) or True))
    reply = PetWindow._handle_chat_local_command(pet, '播放陈楚生的《山楂花》')
    assert '正在处理' in reply.text
    assert pet._music_chat_feedback_pending
    PetWindow._handle_chat_local_command(pet, '暂停音乐')
    assert calls == [('song', ('山楂花', '陈楚生')), ('control', 'pause')]


def test_unverified_dispatch_reports_reason_without_headphones(monkeypatch):
    speech, activities, chat = [], [], []
    pet = SimpleNamespace(
        settings=object(), _music_chat_feedback_pending=True,
        _chat_notice=chat.append,
        _change_ambient_activity=activities.append,
        _set_temporary_activity=lambda *args: activities.append(args),
        _show_emotion=lambda *_: None,
        show_speech=lambda message, *_: speech.append(message))
    monkeypatch.setattr('onepic_desktop_pet.window.save_settings', lambda _: None)
    result = SongPlaybackResult(True, 'qq', '', '陈楚生', '已发出请求，尚未确认。',
        outcome=MusicPlaybackOutcome.PLAYBACK_STARTED_UNVERIFIED)
    PetWindow._music_control_result(pet, result)
    assert activities == []
    assert speech == chat == [result.message]


def test_confirmed_playback_triggers_companion_headphones(monkeypatch):
    activities = []
    pet = SimpleNamespace(
        settings=object(), _music_chat_feedback_pending=False,
        _set_temporary_activity=lambda *args: activities.append(args),
        _show_emotion=lambda *_: None,
        show_speech=lambda *_: None)
    monkeypatch.setattr('onepic_desktop_pet.window.save_settings', lambda _: None)
    result = SongPlaybackResult(True, 'qq', '', '陈楚生', 'QQ 音乐正在播放。',
        current_title='山楂花', current_artist='陈楚生',
        outcome=MusicPlaybackOutcome.PLAYBACK_CONFIRMED)
    PetWindow._music_control_result(pet, result)
    assert activities == [('headphones', 120_000)]


def test_new_playlist_confirmation_does_not_open_a_window(monkeypatch):
    opened, speech = [], []
    pet = SimpleNamespace(settings=object(), _music_chat_feedback_pending=False,
        _set_temporary_activity=lambda *_: None, _show_emotion=lambda *_: None,
        show_music_playlist=lambda: opened.append(True),
        show_speech=lambda text, *_: speech.append(text))
    monkeypatch.setattr('onepic_desktop_pet.window.save_settings', lambda _: None)
    result = SongPlaybackResult(True, 'qq', '', '陈楚生', 'full technical feedback',
        current_title='山高水长', current_artist='陈楚生', playlist_created=True, playlist_size=30,
        outcome=MusicPlaybackOutcome.PLAYBACK_CONFIRMED)
    PetWindow._music_control_result(pet, result)
    assert opened == []
    assert speech == ['已在后台播放《山高水长》♪']


def test_automatic_next_song_does_not_show_speech_or_open_window(monkeypatch):
    opened, speech = [], []
    pet = SimpleNamespace(settings=object(), _music_chat_feedback_pending=False,
        _set_temporary_activity=lambda *_: None, _show_emotion=lambda *_: None,
        show_music_playlist=lambda: opened.append(True), show_speech=lambda *args: speech.append(args))
    monkeypatch.setattr('onepic_desktop_pet.window.save_settings', lambda _: None)
    result = SongPlaybackResult(True, 'qq', '', '陈楚生', 'automatic next',
        current_title='山高水长', current_artist='陈楚生', playlist_size=30,
        outcome=MusicPlaybackOutcome.PLAYBACK_CONFIRMED)
    PetWindow._music_control_result(pet, result)
    assert not opened and not speech


def test_confirmed_pause_clears_the_listening_pose():
    changes = []
    pet = SimpleNamespace(
        _ambient_activity='headphones', _music_chat_feedback_pending=False,
        activity_timer=SimpleNamespace(stop=lambda: changes.append('stop')),
        _change_ambient_activity=changes.append, show_speech=lambda *_: None)
    status = MusicProviderStatus('qq', MusicControlState.CONTROL_READY, '',
                                track=TrackInfo('山楂花', '陈楚生', '', 'paused'))
    PetWindow._music_control_result(pet, MusicControlResult(True, 'qq', 'pause', '已暂停。', status))
    assert changes == ['stop', 'none'] and pet._manual_activity_until == 0


def test_real_qt_worker_is_serial_and_delivers_final_result():
    import time
    from threading import Event
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    entered, release = Event(), Event()
    result = SongPlaybackResult(False, 'qq', '', '陈楚生', '测试失败反馈。')
    def play(*_):
        entered.set()
        assert release.wait(2)
        return result
    controller = MusicController(PetSettings(), SimpleNamespace(play_qq_online_song=play))
    received = []
    controller.result_ready.connect(received.append)
    try:
        assert controller.play_qq_song()
        assert entered.wait(1)
        assert not controller.play_qq_song()
        release.set()
        deadline = time.monotonic() + 2
        while controller.busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert not controller.busy
        assert received == [result]
    finally:
        release.set()
        controller.shutdown()


def test_pause_interrupts_busy_round_and_is_delivered_after_worker_finishes():
    import time
    from threading import Event
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    entered = Event()
    queue = SimpleNamespace(auto_advance=True)
    commands = []
    paused = MusicControlResult(True, 'qq', 'pause', '已暂停',
        MusicProviderStatus('qq', MusicControlState.CONTROL_READY, '',
                            track=TrackInfo('', '', '', 'paused')))
    def play(*_):
        entered.set()
        deadline = time.monotonic() + 2
        while not QThread.currentThread().isInterruptionRequested() and time.monotonic() < deadline:
            time.sleep(.005)
        # 模拟一个晚返回的确认；优先暂停不能被该结果重新激活。
        queue.auto_advance = True
        return SongPlaybackResult(True, 'qq', '', '陈楚生', 'late success',
            outcome=MusicPlaybackOutcome.PLAYBACK_CONFIRMED)
    def control(_provider, action):
        commands.append(action)
        queue.auto_advance = False
        return paused
    manager = SimpleNamespace(play_qq_online_song=play, qq_playlist=queue, control=control)
    controller = MusicController(PetSettings(), manager)
    received = []
    controller.result_ready.connect(received.append)
    try:
        assert controller.play_qq_song() and entered.wait(1)
        assert controller.perform('pause')
        assert not queue.auto_advance
        deadline = time.monotonic() + 3
        while controller.busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        assert not controller.busy and commands == ['pause']
        assert received == [paused]
        assert not queue.auto_advance and not controller._playlist_timer.isActive()
    finally:
        controller.shutdown()
