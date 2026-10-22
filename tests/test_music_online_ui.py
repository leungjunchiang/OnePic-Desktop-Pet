"""验证聊天和菜单确实走后台点歌入口，未确认投递不触发六毛听歌动作。"""

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
        music_controller=SimpleNamespace(play_qq_song=lambda: calls.append('online') or True),
        show_speech=lambda *_: None)
    assert PetWindow.play_random_song(pet) == '陈楚生随机歌曲'
    assert calls == ['online']


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
