"""系统会话结束支持：Windows 会话消息、macOS 原生退出请求和跨平台限时退出。

Windows 原生桥只处理 WM_QUERYENDSESSION / WM_ENDSESSION，不改变普通关窗、
托盘或手动退出；macOS 原生 Quit AppleEvent 在 Qt 的关闭窗口检查前接入本地保存路径。
系统确认退出后不等待网络或析构运行中的 Qt 线程，避免阻止系统关机或重启。
"""

from __future__ import annotations

from ctypes import wintypes
import logging
import os
import sys
import threading
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter

LOGGER = logging.getLogger(__name__)
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
MAC_QUIT_EVENT = (int.from_bytes(b"aevt", "big"), int.from_bytes(b"quit", "big"))


class ConfirmedShutdownDeadline:
    """Independent deadline survives a blocked GUI; never run Qt teardown here."""

    def __init__(self, *, seconds: float = 3.0, exit_grace_seconds: float = 0.05,
                 exit_process: Callable = os._exit):
        self._saved = threading.Event()
        self._seconds = seconds
        self._exit_grace_seconds = max(0.0, float(exit_grace_seconds))
        self._exit_process = exit_process
        self._thread = threading.Thread(
            target=self._run, name="lili-confirmed-system-exit", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        saved = self._saved.wait(self._seconds)
        # Give the native OS event handler time to return before forcing exit.
        if saved:
            threading.Event().wait(self._exit_grace_seconds)
        # os._exit deliberately avoids Python/Qt destructors with live TLS or
        # QThreads. The OS has already committed to ending this process.
        self._exit_process(0)

    def local_save_finished(self) -> None:
        self._saved.set()


_mac_quit_target_class = None


def _mac_quit_target_type():
    global _mac_quit_target_class
    if _mac_quit_target_class is None:
        from Foundation import NSObject
        from objc import selector

        class LiliMacQuitEventTarget(NSObject):
            def receiveQuitEvent_withReplyEvent_(self, _event, _reply):
                # An empty reply acknowledges the request. In particular, do
                # not call NSApp.terminate_() and report Qt's NSTerminateCancel
                # back to loginwindow before the bounded exit has run.
                try:
                    self.confirmed()
                except Exception:
                    LOGGER.exception("[SystemShutdown] native Mac quit preparation failed")

            receiveQuitEvent_withReplyEvent_ = selector(
                receiveQuitEvent_withReplyEvent_, signature=b"v@:@@",
            )

        _mac_quit_target_class = LiliMacQuitEventTarget
    return _mac_quit_target_class


class MacSessionShutdownBridge:
    """Handle only the process's Quit AppleEvent; keep Qt's AppKit delegate."""

    def __init__(self, confirmed: Callable[[], None]):
        from AppKit import NSApplication, NSApplicationDidFinishLaunchingNotification
        from Foundation import NSAppleEventManager, NSNotificationCenter

        self._manager = NSAppleEventManager.sharedAppleEventManager()
        self._center = NSNotificationCenter.defaultCenter()
        self._target = _mac_quit_target_type().alloc().init()
        self._target.confirmed = confirmed
        # AppKit installs its default event handlers during launch. Reassert
        # our one handler afterwards when QApplication hasn't entered exec yet.
        self._observer = self._center.addObserverForName_object_queue_usingBlock_(
            NSApplicationDidFinishLaunchingNotification, NSApplication.sharedApplication(),
            None, self._on_app_launched,
        )
        try:
            self._register()
        except Exception:
            self._center.removeObserver_(self._observer)
            raise

    def _register(self):
        self._manager.setEventHandler_andSelector_forEventClass_andEventID_(
            self._target, b"receiveQuitEvent:withReplyEvent:", *MAC_QUIT_EVENT,
        )

    def _on_app_launched(self, _notification):
        try:
            self._register()
        except Exception:
            LOGGER.exception("[SystemShutdown] could not reassert Mac quit handler")

    def close(self):
        """Remove the process-owned handler during final ordinary teardown."""
        if self._observer is None:
            return
        self._center.removeObserver_(self._observer)
        self._observer = None
        self._manager.removeEventHandlerForEventClass_andEventID_(*MAC_QUIT_EVENT)


class WindowsSessionShutdownBridge(QAbstractNativeEventFilter):
    """Intercept session messages before Qt's close-to-tray fallback can veto."""

    def __init__(self, confirmed: Callable[[], None]):
        super().__init__()
        self._confirmed_callback = confirmed
        self._query_pending = False
        self._confirmed = False

    def handle_message(self, message: int, wparam: int) -> tuple[bool, int]:
        if message == WM_QUERYENDSESSION:
            self._query_pending = True
            # No disk logging here: the query reply must not wait for a worker
            # holding the logging lock or a slow filesystem.
            return True, 1
        if message != WM_ENDSESSION:
            return False, 0
        if not wparam:
            self._query_pending = False
            LOGGER.info("[SystemShutdown] cancelled; application remains running")
            return True, 0
        if not self._confirmed:
            self._confirmed = True
            try:
                self._confirmed_callback()
                LOGGER.info("[SystemShutdown] confirmed; local save attempted, bounded exit armed")
            except Exception:
                LOGGER.exception("[SystemShutdown] preparation failed")
        return True, 0

    def nativeEventFilter(self, event_type, message):
        if sys.platform != "win32" or bytes(event_type) not in (
            b"windows_generic_MSG", b"windows_dispatcher_MSG"
        ):
            return False, 0
        native = wintypes.MSG.from_address(int(message))
        return self.handle_message(native.message, native.wParam)
