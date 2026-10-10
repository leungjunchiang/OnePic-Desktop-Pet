"""音频退出门禁同时覆盖线程和进程后端。
主动快捷入口与被动通知隔离，Qt 清理门禁和原生错误诊断回归。"""
from types import SimpleNamespace
from threading import Event
import io
import logging
import time
import pytest
from PySide6.QtCore import QThread, QObject, Qt, QtMsgType
from PySide6.QtTest import QTest
from test_passive_notifications import pet


@pytest.mark.parametrize('reason', ['游戏中', '会议中', '演示或录屏中', '全屏工作中'])
def test_double_click_and_hover_remain_user_actions(pet, monkeypatch, reason):
    app, window = pet
    monkeypatch.setattr('onepic_desktop_pet.window.detect_quiet_mode', lambda: SimpleNamespace(blocked=True, reason=reason))
    QTest.mouseDClick(window, Qt.MouseButton.LeftButton)
    assert window.quick_panel.isVisible()
    window.quick_panel._set_report_button_visible(True)
    assert window.quick_panel.report_button.isVisible()
    window.quick_panel._show_hint(window.quick_panel.work_button)
    assert window.quick_panel.hover_hint.isVisible()
    assert window._passive_surfaces_blocked()
    assert not window._notify_instant_interaction('cheer:blocked', 'cheer', '搭子', '新互动')
    assert not window._interaction_hint_text
    window.show_work_controls()
    assert window.work_controls.isVisible() and not window.quick_panel.isVisible()


def test_manual_mist_can_reveal_existing_suppressed_effect(pet, monkeypatch):
    from onepic_desktop_pet.local_burst_effect import LocalEffectKind
    app, window = pet
    monkeypatch.setattr('onepic_desktop_pet.window.detect_quiet_mode', lambda: SimpleNamespace(blocked=True))
    window._local_effect_manager.request_state(LocalEffectKind.BLUE)
    assert not window._local_burst_effect.isVisible()
    assert window._toggle_color_mist_world()
    assert window._local_burst_effect.isVisible()
    assert window._local_effect_manager.color_mist_world_active
    window._sync_fullscreen_visibility(mode='fullscreen')
    assert not window._local_burst_effect.isVisible()
    window._local_effect_tick(window._local_effect_manager._now()+0.1)
    assert not window._local_burst_effect.isVisible()


def test_explicit_show_cannot_resurrect_during_shutdown(pet):
    app, window = pet
    window._close_in_progress=True
    window.show_quick_panel()
    assert not window.quick_panel.isVisible()


def test_not_running_is_not_enough_to_destroy_qthread(monkeypatch):
    from onepic_desktop_pet import qt_lifecycle as module
    calls=[]
    thread=SimpleNamespace(isRunning=lambda:False, wait=lambda ms:(calls.append(ms),False)[1])
    monkeypatch.setattr(module,'child_qthreads',lambda *roots:(thread,))
    assert module.running_threads(None)==(thread,)
    assert not module.wait_for_thread(thread,1000)
    assert calls==[0,0]


def test_audio_exit_gate_keeps_live_worker_until_native_drain(pet,monkeypatch):
    from onepic_desktop_pet import alarm_audio_service as module
    app, window=pet
    gate=Event(); entered=Event()
    class Worker(QThread):
        def run(self):
            entered.set();gate.wait(3)
    thread=Worker();thread.start();assert entered.wait(1)
    class Job:
        thread_owner=thread
        @property
        def closed(self):return not self.thread_owner.isRunning()
        def stop(self):pass
    monkeypatch.setattr(module,'_QT_AUDIO_JOBS',{Job()})
    try:
        assert not module.prepare_audio_shutdown()
    finally:
        gate.set();assert thread.wait(2000)
    assert module.prepare_audio_shutdown()
    thread.deleteLater()


def test_actual_audio_bridge_is_drained_before_global_release(pet,monkeypatch):
    from onepic_desktop_pet import alarm_audio_service as module
    from PySide6.QtTest import QSignalSpy
    from PySide6 import QtMultimedia
    app, window=pet; gate=Event(); entered=Event(); initialized_threads=[]
    playback_state=QtMultimedia.QMediaPlayer.PlaybackState
    # 保留实际 QObject/Slot/命令桥，仅替换媒体设备；不播放声音。
    class DelayedOutput:
        def __init__(self,parent):
            initialized_threads.append(QThread.currentThread())
            entered.set();gate.wait(3)
        def setVolume(self,value):pass
    class Player:
        PlaybackState=playback_state
        def __init__(self,parent):
            for name in ('playbackStateChanged','mediaStatusChanged','errorOccurred','positionChanged'):
                setattr(self,name,SimpleNamespace(connect=lambda slot:None))
        def setAudioOutput(self,output):pass
        def stop(self):pass
    monkeypatch.setattr(QtMultimedia,'QAudioOutput',DelayedOutput)
    monkeypatch.setattr(QtMultimedia,'QMediaPlayer',Player)
    backend=module.QtThreadAudio()
    try:
        deadline=time.monotonic()+2
        while not entered.is_set() and time.monotonic()<deadline:
            app.processEvents()
            time.sleep(.01)
        assert entered.is_set()
        assert initialized_threads==[backend.thread_owner]
        assert backend in module._QT_AUDIO_JOBS
        assert not module.prepare_audio_shutdown()
        finished=QSignalSpy(backend.finished)
        gate.set()
        deadline=time.monotonic()+2
        while not backend.closed and time.monotonic()<deadline:
            app.processEvents()
            time.sleep(.01)
        assert finished.count()==1
        assert backend not in module._QT_AUDIO_JOBS and backend.closed
    finally:
        gate.set()
        if not backend.closed:
            backend.stop();backend.thread_owner.wait(2000)


def test_application_exit_retries_before_qapplication_quit(pet,monkeypatch):
    from onepic_desktop_pet.app import DesktopPetApplication
    app,window=pet; calls=[]
    controller=DesktopPetApplication.__new__(DesktopPetApplication);QObject.__init__(controller)
    controller._quit_started=True;controller._quit_prepared=True;controller._quit_retry_scheduled=False
    controller._content_update_worker=controller._program_update_check_worker=controller._program_update_download_worker=None
    controller.window=SimpleNamespace(close=lambda:True)
    controller.qt_app=SimpleNamespace(quit=lambda:calls.append('quit'))
    controller._schedule_quit_retry=lambda:calls.append('retry')
    monkeypatch.setattr('onepic_desktop_pet.alarm_audio_service.prepare_audio_shutdown',lambda:False)
    controller._continue_quit()
    assert calls==['retry']


def test_qt_fatal_diagnostic_preserves_native_reason_and_thread(monkeypatch,caplog):
    from onepic_desktop_pet import app as module
    installed=[];delegated=[];traces=[];stream=io.StringIO()
    monkeypatch.setattr(module,'_QT_MESSAGE_HANDLER',None)
    monkeypatch.setattr(module,'qInstallMessageHandler',lambda handler:(installed.append(handler),lambda *a:delegated.append(a))[1])
    monkeypatch.setattr(module,'_FAULT_HANDLER_STREAM',stream)
    monkeypatch.setattr(module.faulthandler,'dump_traceback',lambda **kw:traces.append(kw))
    module._install_qt_message_diagnostics();module._install_qt_message_diagnostics()
    assert len(installed)==1
    with caplog.at_level(logging.CRITICAL):
        installed[0](QtMsgType.QtFatalMsg,SimpleNamespace(category='qt.core',function='QThread::~QThread'),'QThread: Destroyed while thread is still running')
    assert 'QThread: Destroyed while thread is still running' in caplog.text
    assert 'QThread: Destroyed' in stream.getvalue() and traces and delegated


def test_update_repr_does_not_repeat_entire_release_history():
    from onepic_desktop_pet.program_updates import ProgramRelease
    release=ProgramRelease('1','v1','https://example.test','setup.exe','https://example.test/setup',0,None,None,release_notes='history'*10000)
    assert len(repr(release))<1000 and release.release_notes=='history'*10000


def test_hidden_shortcut_cannot_replay_old_hover_hint(pet):
    app, window=pet
    window.show_quick_panel()
    window.quick_panel.hide()
    window.quick_panel._show_hint(window.quick_panel.work_button)
    assert not window.quick_panel.hover_hint.isVisible()
