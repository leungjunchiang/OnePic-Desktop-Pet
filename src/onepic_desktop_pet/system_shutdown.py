"""Windows 会话结束桥：查询立即同意，确认后本地落盘并限时退出。

只处理操作系统的 WM_QUERYENDSESSION / WM_ENDSESSION，不改变普通关窗、
托盘或手动退出。查询取消不停止计时；确认后不等待网络或析构运行中的 Qt
线程，避免阻止系统更新重启。硬退出仅在系统已确认结束会话时启用。
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


class ConfirmedShutdownDeadline:
    """Independent deadline survives a blocked GUI; never run Qt teardown here."""

    def __init__(self, *, seconds: float = 3.0, exit_process: Callable = os._exit):
        self._saved = threading.Event()
        self._seconds = seconds
        self._exit_process = exit_process
        self._thread = threading.Thread(
            target=self._run, name="lili-confirmed-system-exit", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        saved = self._saved.wait(self._seconds)
        # Give the native ENDSESSION handler a chance to return its reply.
        if saved:
            threading.Event().wait(0.05)
        # os._exit deliberately avoids Python/Qt destructors with live TLS or
        # QThreads. The OS has already committed to ending this process.
        self._exit_process(0)

    def local_save_finished(self) -> None:
        self._saved.set()


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
