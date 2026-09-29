"""显示不激活窗口的搭子提醒：短时完整提示、迷你胶囊及自动消失。"""

from __future__ import annotations

import sys
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton


FULL_DURATION_MS = 12_000
TOTAL_DURATION_MS = 180_000


class ReminderToastClock:
    """Pause both stage timers while the pointer rests over the reminder."""

    def __init__(self, *, remaining_ms: int = TOTAL_DURATION_MS) -> None:
        self.elapsed_ms = 0
        self.remaining_ms = max(1, min(TOTAL_DURATION_MS, int(remaining_ms)))
        self.hovered = False

    def advance(self, milliseconds: int) -> str:
        if not self.hovered:
            self.elapsed_ms += max(0, int(milliseconds))
        if self.elapsed_ms >= self.remaining_ms:
            return "hidden"
        return "full" if self.elapsed_ms < FULL_DURATION_MS else "mini"


class BuddyReminderToast(QFrame):
    """A top-level, click-through-to-action notification that never takes focus."""

    open_requested = Signal()

    def __init__(
        self, title: str, detail: str, *, mini_title: str = "",
        remaining_ms: int = TOTAL_DURATION_MS,
    ) -> None:
        super().__init__(None)
        self.clock = ReminderToastClock(remaining_ms=remaining_ms)
        self._mini_title = str(mini_title or title)[:42]
        self.setObjectName("buddyReminderToast")
        self.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(
            "QFrame#buddyReminderToast{background:#173229;color:white;border:1px solid #4d8067;"
            "border-radius:13px;} QLabel{color:white;} QPushButton{color:#d7f2df;border:0;"
            "background:transparent;font-size:18px;}"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 10, 10)
        self.title_label = QLabel(str(title)[:100])
        self.title_label.setStyleSheet("font-size:14px;font-weight:650;")
        self.detail_label = QLabel(str(detail)[:120])
        self.detail_label.setStyleSheet("font-size:11px;color:#cee6d4;")
        from PySide6.QtWidgets import QVBoxLayout
        text_column = QVBoxLayout()
        text_column.setSpacing(2)
        text_column.addWidget(self.title_label)
        text_column.addWidget(self.detail_label)
        row.addLayout(text_column, 1)
        close = QPushButton("×")
        close.setObjectName("dismissReminder")
        close.setFixedSize(24, 24)
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.clicked.connect(self.close)
        row.addWidget(close)
        self.setFixedWidth(310)
        self._last_tick = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._tick)

    def _set_windows_no_activate(self) -> None:
        if sys.platform != "win32":
            return
        import ctypes
        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        get_style = user32.GetWindowLongPtrW
        set_style = user32.SetWindowLongPtrW
        get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        set_style.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        set_style.restype = ctypes.c_ssize_t
        style = get_style(hwnd, -20)
        set_style(hwnd, -20, style | 0x08000000 | 0x00000080)  # WS_EX_NOACTIVATE | TOOLWINDOW

    def show_passive(self, *, stack_index: int = 0) -> None:
        """Place and show the toast without changing the foreground HWND."""

        self._set_windows_no_activate()
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 22, area.top() + 24 + max(0, stack_index) * 78)
        self.show()
        if sys.platform == "win32":
            import ctypes
            user32 = ctypes.windll.user32
            user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.SetWindowPos.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_uint,
            ]
            user32.ShowWindow(ctypes.c_void_p(int(self.winId())), 4)  # SW_SHOWNOACTIVATE
            user32.SetWindowPos(
                ctypes.c_void_p(int(self.winId())), ctypes.c_void_p(-1),
                self.x(), self.y(), 0, 0,
                0x0001 | 0x0002 | 0x0010,  # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
            )
        self._last_tick = time.monotonic()
        self._timer.start()

    def _tick(self) -> None:
        current = time.monotonic()
        stage = self.clock.advance(int((current - self._last_tick) * 1000))
        self._last_tick = current
        if stage == "hidden":
            self.close()
        elif stage == "mini" and self.detail_label.isVisible():
            self.detail_label.hide()
            self.title_label.setText(self._mini_title)
            self.setFixedWidth(150)
            self.setWindowOpacity(0.86)

    def enterEvent(self, event) -> None:
        self.clock.hovered = True
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.clock.hovered = False
        self._last_tick = time.monotonic()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.close()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_requested.emit()
            self.close()
            return
        super().mousePressEvent(event)

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)
