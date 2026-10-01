"""显示不激活窗口的搭子提醒；正文校验、单窗内容更新与关闭生命周期统一处理。"""

from __future__ import annotations

import sys
import time

from PySide6.QtCore import Qt, QTimer, Signal, QRectF
from PySide6.QtGui import QCursor, QMouseEvent, QPainter, QColor, QPen
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
    dismissed = Signal()

    def __init__(
        self, title: str, detail: str, *, mini_title: str = "",
        remaining_ms: int = TOTAL_DURATION_MS,
        parent=None,
    ) -> None:
        super().__init__(parent)
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
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(
            "QFrame#buddyReminderToast{background:#173229;color:white;border:1px solid #4d8067;"
            "border-radius:13px;} QLabel{color:white;} QPushButton{color:#d7f2df;border:0;"
            "background:transparent;font-size:18px;}"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 10, 10)
        self.title_label = QLabel(str(title)[:100])
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("font-size:14px;font-weight:650;")
        self._detail = str(detail).strip()[:1200]
        self.detail_label = QLabel(self._detail)
        self.detail_label.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_label.setWordWrap(True)
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

    def paintEvent(self, event) -> None:
        """透明顶层 QFrame 不依赖系统样式填充，确保文字后有完整可读底板。"""
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#4d8067"), 1))
        painter.setBrush(QColor("#173229"))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 13, 13)

    def set_content(self, title: str, detail: str, *, mini_title: str) -> None:
        """合并到已有窗口，重新展示完整正文，不创建第二个通知窗口。"""
        self._detail = str(detail).strip()[:1200]
        self._mini_title = str(mini_title)[:42]
        self.title_label.setText(str(title)[:100])
        self.title_label.setWordWrap(True)
        self.detail_label.setText(self._detail)
        self.detail_label.setVisible(bool(self._detail))
        self.setFixedWidth(310)
        self.setWindowOpacity(1.0)
        self.clock.elapsed_ms = 0
        self._last_tick = time.monotonic()
        self.adjustSize()

    def _set_windows_no_activate(self, *, tool_window: bool = True) -> None:
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
        # 显式配置的闹钟复用此策略，同时保留任务栏入口；普通Toast使用ToolWindow。
        set_style(hwnd, -20, style | 0x08000000 | (0x00000080 if tool_window else 0x00040000))

    def show_passive(self, *, stack_index: int = 0) -> None:
        """Place and show the toast without changing the foreground HWND."""

        if not self._detail or not self.title_label.text().strip():
            return
        self.adjustSize()
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
            self.title_label.setWordWrap(False)
            width = max(150, min(230, self.title_label.fontMetrics().horizontalAdvance(self._mini_title) + 72))
            self.setFixedWidth(width)
            self.title_label.setText(self.title_label.fontMetrics().elidedText(
                self._mini_title, Qt.TextElideMode.ElideRight, width - 64))
            self.setWindowOpacity(0.86)
            self.adjustSize()

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
        self.dismissed.emit()
