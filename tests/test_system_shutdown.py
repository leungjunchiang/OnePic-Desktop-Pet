"""系统重启查询、取消、本地专注落盘及限时退出回归；不执行真实关机。"""

import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ONEPIC_USE_DEMO_ASSETS", "1")

from PySide6.QtCore import QObject, QTimer
from onepic_desktop_pet.system_shutdown import (
    WindowsSessionShutdownBridge, ConfirmedShutdownDeadline,
    WM_QUERYENDSESSION, WM_ENDSESSION,
)


def test_query_and_cancel_do_not_stop_or_save_application():
    calls = []
    bridge = WindowsSessionShutdownBridge(lambda: calls.append("confirmed"))
    assert bridge.handle_message(WM_QUERYENDSESSION, 0) == (True, 1)
    assert bridge.handle_message(WM_QUERYENDSESSION, 0) == (True, 1)
    assert bridge.handle_message(WM_ENDSESSION, 0) == (True, 0)
    assert calls == []
    assert not bridge._query_pending and not bridge._confirmed
    assert bridge.handle_message(0x0010, 0) == (False, 0)  # Ordinary WM_CLOSE.
    bridge.handle_message(WM_QUERYENDSESSION, 0)
    bridge.handle_message(WM_ENDSESSION, 1)
    bridge.handle_message(WM_ENDSESSION, 1)  # Multiple native child windows.
    assert calls == ["confirmed"]


def test_confirmed_callback_error_cannot_veto_system_exit(caplog):
    def fail():
        raise OSError("disk unavailable")
    bridge = WindowsSessionShutdownBridge(fail)
    assert bridge.handle_message(WM_ENDSESSION, 1) == (True, 0)
    assert "preparation failed" in caplog.text


@pytest.mark.parametrize("save_finished", [False, True])
def test_deadline_survives_blocked_main_thread(save_finished):
    exited = threading.Event()
    codes = []
    deadline = ConfirmedShutdownDeadline(
        seconds=0.15, exit_process=lambda code: (codes.append(code), exited.set())
    )
    if save_finished:
        deadline.local_save_finished()
    # No Qt event processing: exit must not depend on the GUI being responsive.
    assert exited.wait(1)
    assert codes == [0]


def test_os_exit_never_closes_windows_or_waits_workers(monkeypatch):
    from onepic_desktop_pet import app as module
    from PySide6.QtWidgets import QApplication
    qt_app = QApplication.instance() or QApplication([])
    calls = []
    timer = QTimer(); timer.start(100_000)
    controller = module.DesktopPetApplication.__new__(module.DesktopPetApplication)
    QObject.__init__(controller)
    controller._system_shutdown_confirmed = False
    controller.settings = SimpleNamespace()
    controller.window = SimpleNamespace(
        findChildren=lambda cls: [timer], x=lambda: 40, y=lambda: 50,
        _social_heartbeat_thread=SimpleNamespace(stop=lambda: calls.append("stop")),
        shutdown_work_timer=lambda **kwargs: calls.append(kwargs),
        close=lambda: pytest.fail("normal window close must not run"),
    )
    monkeypatch.setattr(module, "ConfirmedShutdownDeadline", lambda: SimpleNamespace(
        local_save_finished=lambda: calls.append("ready")))
    monkeypatch.setattr(module, "save_settings", lambda settings: calls.append("settings"))
    monkeypatch.setattr(module, "wait_for_thread", lambda *a: pytest.fail("no worker wait"))
    controller._confirm_system_shutdown()
    controller._confirm_system_shutdown()
    controller._continue_quit()
    assert calls == ["stop", {"system_shutdown": True}, "settings", "ready"]
    assert not timer.isActive()
    assert controller._quit_started and controller.window.application_exit_requested
    assert controller.window._system_shutdown_confirmed


def test_manual_exit_waits_asynchronously_so_os_queries_can_be_delivered(monkeypatch):
    from onepic_desktop_pet import app as module
    waits = []; retries = []
    controller = module.DesktopPetApplication.__new__(module.DesktopPetApplication)
    QObject.__init__(controller)
    controller._quit_started = True
    controller._quit_prepared = True
    controller._quit_retry_scheduled = False
    controller._content_update_worker = object()
    controller._program_update_check_worker = object()
    controller._program_update_download_worker = object()
    controller.qt_app = None
    controller._schedule_quit_retry = lambda: retries.append(True)
    monkeypatch.setattr(module, "wait_for_thread", lambda worker, ms: (waits.append(ms), False)[1])
    controller._continue_quit()
    assert waits == [0, 0, 0]
    assert retries == [True]


def test_local_shutdown_preserves_focus_and_retryable_facts_without_network(tmp_path, monkeypatch):
    from test_window import _create_window
    from onepic_desktop_pet.focus_analytics import FocusAnalyticsStore
    from onepic_desktop_pet.work_timer import WorkTimerModel, BEIJING_TIMEZONE
    app, window = _create_window()
    moment = [datetime(2026, 10, 3, 9, tzinfo=BEIJING_TIMEZONE)]
    tick = [100.0]
    timer_path = tmp_path / "timer.json"
    focus_path = tmp_path / "focus.json"
    window.work_timer = WorkTimerModel(path=timer_path,
        now_provider=lambda: moment[0], monotonic_provider=lambda: tick[0])
    window.focus_analytics = FocusAnalyticsStore(path=focus_path, persist=True,
        now_provider=lambda: moment[0])
    window._recorded_focus_session_seconds = 0
    def forbidden(*args, **kwargs):
        pytest.fail("OS shutdown must not start network or refresh UI")
    monkeypatch.setattr(window, "_schedule_social_tick", forbidden)
    monkeypatch.setattr(window, "_sync_economy_events", forbidden)
    monkeypatch.setattr(window.focus_session, "pause", forbidden)
    window.work_timer.start()
    tick[0] += 407; moment[0] += timedelta(seconds=407)
    try:
        window.shutdown_work_timer(system_shutdown=True)
        window.shutdown_work_timer(system_shutdown=True)
        assert not window.work_timer.is_running
        pending = window.focus_analytics.focus_segments_payload()
        assert len(pending) == 1
        before = window.work_timer.today_seconds()
        assert before == 407
        moment[0] += timedelta(hours=2); tick[0] += 7200
        recovered = WorkTimerModel(path=timer_path,
            now_provider=lambda: moment[0], monotonic_provider=lambda: tick[0])
        recovered_store = FocusAnalyticsStore(path=focus_path, persist=True,
            now_provider=lambda: moment[0])
        assert recovered.today_seconds() == before
        assert not recovered.is_running
        assert recovered_store.focus_segments_payload() == pending
        fact = recovered_store.focus_segments()[0]
        assert int((fact.end_at - fact.start_at).total_seconds()) == 407
    finally:
        window.close(); window.deleteLater(); app.processEvents()


def test_local_pause_still_persists_if_fact_seal_fails(tmp_path, monkeypatch):
    from test_window import _create_window
    from onepic_desktop_pet.work_timer import WorkTimerModel
    app, window = _create_window()
    window.work_timer = WorkTimerModel(path=tmp_path / "timer.json")
    window.work_timer.start()
    def fail(*args, **kwargs):
        raise OSError("fact store failure")
    monkeypatch.setattr(window, "_record_focus_segment", fail)
    try:
        with pytest.raises(OSError):
            window.shutdown_work_timer(system_shutdown=True)
        assert not window.work_timer.is_running
        assert not WorkTimerModel(path=tmp_path / "timer.json").is_running
    finally:
        window.close(); window.deleteLater(); app.processEvents()


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 native messages")
def test_native_session_messages_exit_child_despite_stuck_transport(tmp_path):
    # Only this isolated child receives synthetic Win32 messages. Never send
    # shutdown/logoff to the user's system or test process.
    script = r'''
import ctypes, os, sys, threading, time
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget
from onepic_desktop_pet.system_shutdown import *
from onepic_desktop_pet.work_timer import WorkTimerModel
app=QApplication([]); window=QWidget(); hwnd=int(window.winId())
timer=WorkTimerModel(path=Path(sys.argv[1])/'timer.json'); timer.start()
def confirmed():
    deadline=ConfirmedShutdownDeadline(seconds=0.4)
    timer.pause(reason='shutdown'); deadline.local_save_finished()
bridge=WindowsSessionShutdownBridge(confirmed); app.installNativeEventFilter(bridge)
ctypes.windll.user32.SendMessageW.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_size_t,ctypes.c_ssize_t]
ctypes.windll.user32.SendMessageW.restype=ctypes.c_ssize_t
send=lambda msg,wp:ctypes.windll.user32.SendMessageW(hwnd,msg,wp,0)
def exercise():
    assert send(WM_QUERYENDSESSION,0)==1
    send(WM_ENDSESSION,0); assert timer.is_running
    assert send(WM_QUERYENDSESSION,0)==1
    send(WM_ENDSESSION,1)
    while True: time.sleep(60) # GUI cannot service a QTimer-based deadline.
threading.Thread(target=lambda:time.sleep(60),daemon=False).start()
QTimer.singleShot(0,exercise); app.exec()
'''
    started = time.monotonic()
    child = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
        env={**os.environ, "QT_QPA_PLATFORM": "windows",
             "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        capture_output=True, timeout=10)
    assert child.returncode == 0, child.stderr.decode(errors="replace")
    assert time.monotonic() - started < 10
    from onepic_desktop_pet.work_timer import WorkTimerModel
    assert not WorkTimerModel(path=tmp_path / "timer.json").is_running
