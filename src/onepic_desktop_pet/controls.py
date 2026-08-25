"""提供不依赖系统托盘的六毛快捷面板、原生音乐控制、工作气泡和尺寸调节器。

设置入口只在用户点击快捷口袋按钮时发出 ``user_action`` 来源，供主窗口统一校验。
播放、暂停、切歌和随机播放分别发出明确命令，不用“打开音乐客户端”冒充播放控制。
快捷口袋使用代码绘制的红黄蓝矢量图标，不依赖平台 Emoji 或低清位图。
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QActionGroup, QBrush, QColor, QCursor, QGuiApplication, QIcon, QPainter, QPen, QPixmap
from .work_timer import format_elapsed_clock

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


CONTROL_STYLE = """
QWidget#floatingPanel, QDialog#floatingPanel { background: rgba(238, 244, 247, 218); color: #27313d;
border: 1px solid rgba(75, 96, 112, 120); border-radius: 15px;
font-family: "PingFang SC", "Microsoft YaHei UI", sans-serif; }
QWidget#quickActionDock { background: rgba(250, 248, 242, 238); color: #24475b;
border: 1px solid rgba(75, 96, 112, 72); border-radius: 19px;
font-family: "PingFang SC", "Microsoft YaHei UI", sans-serif; }
QWidget#quickActionDock QPushButton { background: rgba(239, 246, 247, 150); color: #24475b;
border: 1px solid rgba(40, 125, 158, 22); border-radius: 12px; padding: 0px; }
QWidget#quickActionDock QPushButton:hover { background: rgba(255, 244, 216, 228);
border: 1px solid rgba(231, 74, 79, 145); }
QWidget#quickActionDock QPushButton:pressed { background: rgba(217, 238, 241, 235);
border: 1px solid rgba(40, 125, 158, 135); }
/* Keep the report action's icon tile identical to the other shortcuts even
   when a native style does not inherit the dock selector. */
QPushButton#quickAction_report { background: rgba(239, 246, 247, 150); color: #24475b;
border: 1px solid rgba(40, 125, 158, 22); border-radius: 12px; padding: 0px; }
QPushButton#quickAction_report:hover { background: rgba(255, 244, 216, 228);
border: 1px solid rgba(231, 74, 79, 145); }
QPushButton#quickAction_report:pressed { background: rgba(217, 238, 241, 235);
border: 1px solid rgba(40, 125, 158, 135); }
QLabel#quickActionHint { background: rgba(255, 253, 247, 245); color: #111111;
border: 1px solid rgba(75, 96, 112, 95); border-radius: 8px;
padding: 4px 9px; font-size: 11px; }
QWidget#workControlDock { background: rgba(248, 252, 253, 242); color: #24475b;
border: 1px solid rgba(40, 125, 158, 118); border-radius: 13px;
font-family: "PingFang SC", "Microsoft YaHei UI", sans-serif; }
QWidget#workControlDock QPushButton { background: rgba(231, 243, 246, 235); color: #24475b;
border: 1px solid rgba(40, 125, 158, 75); border-radius: 9px; padding: 5px 10px; }
QWidget#workControlDock QPushButton:hover { background: #fff4d8; border: 2px solid #e74a4f; }
QWidget#workControlDock QPushButton#finishWorkButton { background: rgba(241, 244, 245, 225); color: #5f6b73;
border: 1px solid rgba(95, 107, 115, 60); }
QWidget#workControlDock QPushButton#finishWorkButton:hover { background: #fff0ee; color: #b94b51; border: 1px solid #e7a0a4; }
QPushButton { background: rgba(74, 126, 151, 225); color: white; border: none; border-radius: 10px;
padding: 8px 12px; font-weight: 600; }
QPushButton:hover { background: #376a82; }
QLabel { border: none; background: transparent; }
QLabel#workDurationHint { background: rgba(246, 251, 251, 235); color: #24475b;
border: 1px solid rgba(40, 125, 158, 86); border-radius: 10px;
padding: 3px 8px; font-size: 11px; }
QLabel#workDurationHint[paused="true"] { background: rgba(255, 240, 238, 242); color: #b94b51;
border: 1px solid rgba(231, 74, 79, 155); }
QLabel#visitStatusHint { background: rgba(246, 251, 251, 235); color: #24475b;
border: 1px solid rgba(40, 125, 158, 86); border-radius: 10px;
padding: 3px 8px; font-size: 11px; }
"""


class WorkControlBubble(QWidget):
    """六毛右键弹出的轻量工作控制条，不属于待办或完整菜单。"""

    start_requested = Signal()
    pause_requested = Signal()
    resume_requested = Signal()
    finish_requested = Signal()

    def __init__(self) -> None:
        super().__init__(None)
        self.setObjectName("workControlDock")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(CONTROL_STYLE)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(5)
        self._duration_visible = True
        self.duration_label = QLabel("本轮未开始")
        self.duration_label.setObjectName("workDurationLabel")
        self.duration_label.setMinimumWidth(110)
        self.duration_label.setVisible(True)
        self.pause_button = QPushButton("暂停工作")
        self.pause_button.setObjectName("pauseWorkButton")
        self.pause_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.finish_button = QPushButton("结束工作")
        self.finish_button.setObjectName("finishWorkButton")
        self.finish_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._session_status = "idle"
        self.pause_button.clicked.connect(self._toggle_session)
        self.finish_button.clicked.connect(self.finish_requested.emit)
        layout.addWidget(self.duration_label)
        layout.addWidget(self.pause_button)
        layout.addWidget(self.finish_button)
        self.set_session_status("idle")

    def set_session_status(self, status: str) -> None:
        """Render only the action(s) valid for IDLE, FOCUSING or PAUSED."""

        self._session_status = status if status in {"idle", "focus", "rest"} else "idle"
        self.pause_button.setText(
            {
                "idle": "开始工作",
                "focus": "暂停工作",
                "rest": "继续工作",
            }[self._session_status]
        )
        self.finish_button.setVisible(self._session_status != "idle")
        self.duration_label.setVisible(self._duration_visible)
        if self._session_status == "idle":
            self.duration_label.setText("本轮未开始")
        self.adjustSize()

    def set_session_duration(self, text: str) -> None:
        """Show the live duration so the current work session is never opaque."""

        clean = str(text or "").strip() or "本轮未开始"
        self.duration_label.setText(clean)
        self.duration_label.setToolTip(clean)
        self.duration_label.setVisible(self._duration_visible)
        self.adjustSize()

    def set_duration_visible(self, visible: bool) -> None:
        """Show or hide the optional live duration without changing timer state."""

        self._duration_visible = bool(visible)
        self.duration_label.setVisible(self._duration_visible)
        if not self._duration_visible:
            self.duration_label.setToolTip("")
        self.adjustSize()

    def _toggle_session(self) -> None:
        if self._session_status == "idle":
            self.start_requested.emit()
        elif self._session_status == "rest":
            self.resume_requested.emit()
        else:
            self.pause_requested.emit()


class WorkDurationBubble(QLabel):
    """跟随六毛脚边显示统一的本日工作时长的轻量状态标签。"""

    def __init__(self) -> None:
        super().__init__(None)
        self.setObjectName("workDurationHint")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(100)
        self.setStyleSheet(CONTROL_STYLE)
        self.setProperty("paused", False)
        self.hide()

    def set_session(self, status: str, seconds: int, visible: bool) -> None:
        """Project the shared calendar-day snapshot; never owns a timer."""

        normalized = status if status in {"focus", "rest"} else "idle"
        active = bool(visible) and normalized in {"focus", "rest"}
        if active:
            paused = normalized == "rest"
            self.setProperty("paused", paused)
            text = f"今日已工作 {format_elapsed_clock(seconds)}"
            if paused:
                text += " · 已暂停"
            self.setText(text)
            self.setToolTip("当前工作计时" + ("已暂停" if paused else "正在计时"))
        else:
            self.setText("")
            self.setToolTip("")
        self.setVisible(active)
        self.adjustSize()
        if active:
            self.style().unpolish(self)
            self.style().polish(self)


class VisitStatusBubble(QLabel):
    """跟随六毛左下角显示轻量串门状态，不创建大窗口。"""

    def __init__(self) -> None:
        super().__init__(None)
        self.setObjectName("visitStatusHint")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(CONTROL_STYLE)
        self.hide()

    def set_visitor(self, nickname: str | None) -> None:
        """Show or hide the current visitor label."""

        name = str(nickname or "搭子").strip()[:40]
        if not name:
            self.hide()
            self.setText("")
            return
        self.setText(f"{name}正在串门")
        self.adjustSize()
        self.show()


class CoffeeScenePrompt(QWidget):
    """Non-modal coffee timeout prompt that never decides work for the user."""

    continue_requested = Signal()
    finish_requested = Signal()

    def __init__(self) -> None:
        super().__init__(None)
        self.setObjectName("workControlDock")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(CONTROL_STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        self.message = QLabel("咖啡喝完啦，半小时到了。")
        self.message.setWordWrap(True)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setStyleSheet("QLabel { color: #24475b; padding: 2px 4px; }")
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        continue_button = QPushButton("继续工作")
        finish_button = QPushButton("结束工作")
        continue_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        finish_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        finish_button.setObjectName("finishWorkButton")
        continue_button.clicked.connect(self.continue_requested.emit)
        finish_button.clicked.connect(self.finish_requested.emit)
        buttons.addWidget(continue_button)
        buttons.addWidget(finish_button)
        layout.addWidget(self.message)
        layout.addLayout(buttons)
        self.hide()

    def set_message(self, text: str) -> None:
        self.message.setText(str(text or "咖啡喝完啦，半小时到了。"))
        self.adjustSize()


def _quick_icon(kind: str, *, active: bool = False) -> QIcon:
    """Draw a small DPI-independent red/yellow/blue shortcut icon."""

    pixmap = QPixmap(72, 72)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    blue = QColor("#287d9e")
    red = QColor("#e74a4f")
    yellow = QColor("#f2c84b")
    ink = QColor("#24475b")
    painter.setPen(QPen(blue, 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
    if kind == "chat":
        painter.setBrush(QBrush(blue)); painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(10, 13, 48, 36, 13, 13)
        painter.drawPolygon([QPoint(20, 47), QPoint(18, 61), QPoint(34, 49)])
        painter.setBrush(QBrush(red)); painter.drawEllipse(48, 8, 15, 15)
    elif kind == "work":
        painter.drawEllipse(10, 10, 52, 52)
        painter.setPen(QPen(red, 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(36, 36, 36, 20 if active else 24)
        painter.drawLine(36, 36, 49, 44)
        painter.setBrush(QBrush(yellow)); painter.setPen(Qt.PenStyle.NoPen); painter.drawEllipse(30, 30, 12, 12)
    elif kind == "social":
        painter.setBrush(QBrush(yellow)); painter.setPen(Qt.PenStyle.NoPen); painter.drawEllipse(8, 13, 25, 25); painter.drawEllipse(39, 13, 25, 25)
        painter.setBrush(QBrush(blue)); painter.drawRoundedRect(7, 39, 28, 20, 9, 9); painter.drawRoundedRect(37, 39, 28, 20, 9, 9)
        painter.setBrush(QBrush(red)); painter.drawEllipse(51, 6, 14, 14)
    elif kind == "music":
        painter.setPen(QPen(red, 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(43, 13, 43, 49); painter.drawLine(43, 13, 59, 9); painter.drawEllipse(17, 43, 25, 16); painter.drawEllipse(40, 43, 25, 16)
        painter.setBrush(QBrush(blue)); painter.setPen(Qt.PenStyle.NoPen); painter.drawEllipse(7, 7, 13, 13)
        painter.setBrush(QBrush(yellow)); painter.drawEllipse(55, 52, 10, 10)
    elif kind == "todo":
        painter.setPen(QPen(blue, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        painter.drawRoundedRect(12, 9, 48, 55, 10, 10)
        painter.setBrush(QBrush(yellow)); painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(21, 19, 10, 10, 3, 3)
        painter.drawRoundedRect(21, 35, 10, 10, 3, 3)
        painter.setPen(QPen(red, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(38, 24, 54, 24)
        painter.drawLine(38, 40, 54, 40)
        painter.drawLine(22, 53, 28, 59)
        painter.drawLine(28, 59, 39, 48)
    elif kind == "food":
        # Small tray + cup: the same red/yellow/blue line language as the
        # other shortcut icons, without turning the shortcut into a menu of
        # every food item.
        painter.setPen(QPen(blue, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(QBrush(QColor("#d9eef1")))
        painter.drawRoundedRect(10, 39, 52, 13, 6, 6)
        painter.drawArc(18, 39, 36, 16, 0, 180 * 16)
        painter.setBrush(QBrush(yellow)); painter.setPen(QPen(blue, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawRoundedRect(24, 15, 24, 24, 6, 6)
        painter.drawArc(44, 20, 16, 14, -90 * 16, 180 * 16)
        painter.setPen(QPen(red, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(31, 6, 10, 13, 0, 180 * 16)
        painter.setBrush(QBrush(red)); painter.setPen(Qt.PenStyle.NoPen); painter.drawEllipse(51, 8, 11, 11)
    elif kind == "report":
        painter.setPen(QPen(blue, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(QBrush(QColor("#eaf5f3")))
        painter.drawRoundedRect(11, 10, 50, 53, 9, 9)
        painter.setPen(QPen(red, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(21, 49, 21, 39)
        painter.drawLine(35, 49, 35, 29)
        painter.drawLine(49, 49, 49, 20)
        painter.setPen(QPen(yellow, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(20, 22, 30, 22)
    else:  # settings compatibility icon
        painter.setPen(QPen(blue, 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for angle in range(0, 360, 45):
            painter.save(); painter.translate(36, 36); painter.rotate(angle); painter.drawLine(0, -23, 0, -30); painter.restore()
        painter.setBrush(QBrush(blue)); painter.setPen(Qt.PenStyle.NoPen); painter.drawEllipse(15, 15, 42, 42)
        painter.setBrush(QBrush(yellow)); painter.drawEllipse(27, 27, 18, 18)
        painter.setBrush(QBrush(red)); painter.drawEllipse(52, 7, 12, 12)
    painter.setPen(QPen(ink, 1)); painter.end()
    return QIcon(pixmap)


class QuickControlPanel(QWidget):
    """跟随六毛移动的图标快捷坞；选择后或闲置八秒会自动收起。"""

    chat_requested = Signal()
    work_requested = Signal()
    work_report_requested = Signal()
    todo_requested = Signal()
    social_requested = Signal()
    music_requested = Signal()
    music_playlist_requested = Signal()
    chen_artist_requested = Signal()
    artist_music_service_requested = Signal(str)
    music_control_requested = Signal(str)
    food_requested = Signal(str)
    supply_requested = Signal()
    settings_requested = Signal()
    size_requested = Signal()
    rename_requested = Signal()
    content_update_requested = Signal()
    program_update_requested = Signal()

    def __init__(self, pet_name: str = "六毛") -> None:
        super().__init__(None)
        pet_name = pet_name.strip() or "六毛"
        self.setObjectName("quickActionDock")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(CONTROL_STYLE)
        self.hover_hint = QLabel(None)
        self.hover_hint.setObjectName("quickActionHint")
        self.hover_hint.setWindowFlags(
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.hover_hint.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.hover_hint.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.hover_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hover_hint.setStyleSheet(CONTROL_STYLE)
        self.hover_hint.hide()
        self._hint_button: QPushButton | None = None
        self._window_behavior_callback: Callable[..., object] | None = None
        self._artist_music_service = "auto"
        # Native macOS Qt windows can omit Enter/Leave delivery until the
        # first click when the panel is a non-activating Tool window. Polling
        # the pointer over our shortcuts keeps the label a true hover
        # affordance on both macOS and Windows, without relying on clicks.
        self._hover_poll_timer = QTimer(self)
        self._hover_poll_timer.setInterval(60)
        self._hover_poll_timer.timeout.connect(self._poll_hover_button)
        self._report_hide_timer = QTimer(self)
        self._report_hide_timer.setSingleShot(True)
        self._report_hide_timer.setInterval(160)
        self._report_hide_timer.timeout.connect(self._hide_report_if_pointer_outside)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)
        self.hide_timer.timeout.connect(self._hide_hint)
        self._ignore_initial_enter = False
        layout = QHBoxLayout(self); layout.setContentsMargins(10, 9, 10, 9); layout.setSpacing(8)
        self.title = QLabel(f"{pet_name}快捷口袋")
        self.title.setVisible(False)
        self.chat_button = self._button("chat", "聊聊", self.chat_requested)
        self.work_button = self._button("work", "开始工作", self.work_requested)
        work_column = QWidget(self)
        work_column.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        work_layout = QVBoxLayout(work_column)
        work_layout.setContentsMargins(0, 0, 0, 0)
        work_layout.setSpacing(4)
        work_layout.addWidget(self.work_button)
        # Keep the report action inside the same dock and layout as the work
        # action.  A separate top-level window could survive the dock's hide
        # event on macOS and drift away from the work shortcut after a move.
        self.report_button = self._button("report", "工作报告", None)
        self.report_button.clicked.connect(self._open_report_from_button)
        self.report_button.hide()
        work_layout.addWidget(self.report_button)
        self.todo_button = self._button("todo", "待办", self.todo_requested)
        self.social_button = self._button("social", "搭子自习室", self.social_requested)
        self.music_button = self._button("music", "音乐", None)
        self.food_button = self._button("food", "喂食", None)
        self.music_button.clicked.connect(self._show_music_menu)
        self.food_button.clicked.connect(self._show_food_menu)
        self._quick_buttons = (
            self.chat_button,
            self.todo_button,
            self.social_button,
            self.music_button,
            self.food_button,
        )
        self._hover_buttons = self._quick_buttons + (self.work_button, self.report_button)
        for button in self._quick_buttons:
            layout.addWidget(button)
            button.setMouseTracking(True)
            button.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
            button.installEventFilter(self)
        layout.insertWidget(1, work_column)
        for button in (self.work_button, self.report_button):
            button.setMouseTracking(True)
            button.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
            button.installEventFilter(self)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # A non-activating macOS Tool window may not deliver child Enter/Leave
        # events until it has been clicked.  Observe application mouse moves
        # as well so merely passing over a button is enough to update the
        # label; the timer below remains a fallback for native event gaps.
        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self)


    @staticmethod
    def _button(kind: str, tooltip: str, signal: object | None) -> QPushButton:
        button = QPushButton()
        button.setObjectName(f"quickAction_{kind}")
        button.setIcon(_quick_icon(kind))
        button.setIconSize(QSize(22, 22))
        button.setFixedSize(42, 42)
        button.setFlat(True)
        button.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        button.setAutoDefault(False)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        if signal is not None:
            button.clicked.connect(lambda: signal.emit())
        return button

    def set_window_behavior_callback(self, callback: Callable[..., object] | None) -> None:
        """Let the owning window apply the same non-activating native panel rules."""

        self._window_behavior_callback = callback

    def set_artist_music_service(self, service: str) -> None:
        """Refresh the check mark used by the quick music submenu."""

        value = str(service or "auto").strip().casefold()
        self._artist_music_service = value if value in {
            "auto", "netease", "qq", "apple", "kugou", "qishui"
        } else "auto"

    def set_work_action_label(self, label: str) -> None:
        """Refresh the dynamic work action shown in the shortcut panel."""

        label = label.strip() or "开始工作"
        self.work_button.setToolTip(label)
        self.work_button.setAccessibleName(label)
        self.work_button.setIcon(_quick_icon("work", active=label == "暂停工作"))
        if self._hint_button is self.work_button:
            self._show_hint(self.work_button)

    def _open_report_from_button(self) -> None:
        """Close the shortcut dock, then open the report on the next Qt turn."""

        self._report_hide_timer.stop()
        self.hide()
        QTimer.singleShot(0, self.work_report_requested.emit)

    def _collapse_report_surface(self) -> None:
        """Hide the report action together with its parent shortcut dock."""

        self._hover_poll_timer.stop()
        self._report_hide_timer.stop()
        self._hide_hint()
        self.report_button.hide()
        self.adjustSize()

    def hide(self) -> None:
        """Hide the shortcut dock and its report action together."""

        self._collapse_report_surface()
        super().hide()

    def _set_report_button_visible(self, visible: bool) -> None:
        """Show the work-report secondary action only while work is hovered."""

        target = bool(visible)
        if self.report_button.isVisible() == target:
            return
        if target:
            self._report_hide_timer.stop()
            self.report_button.show()
            # The report button is a child of ``work_column``. Resize the
            # dock immediately so its position and auto-hide geometry include
            # the newly visible row on every platform.
            self.adjustSize()
            self._hover_poll_timer.start()
        else:
            if self._hint_button is self.report_button:
                self._hide_hint()
            self.report_button.hide()
            self.adjustSize()

    def _schedule_report_hide(self) -> None:
        """Give the pointer a short bridge from the main button to the report."""

        if self.report_button.isVisible():
            self._report_hide_timer.start()

    def _hide_report_if_pointer_outside(self) -> None:
        """Hide the floating report button only after leaving both hover targets."""

        button = self._button_at_global_pos(QCursor.pos())
        if button in (self.work_button, self.report_button):
            return
        self._set_report_button_visible(False)

    def _position_report_button(self) -> None:
        """Keep the legacy positioning hook harmless after layout integration."""

        # ``report_button`` now lives in ``work_column`` and follows normal
        # Qt layout geometry.  Callers from older window-positioning code can
        # still invoke this method safely.
        self.adjustSize()

    def position_report_button(self) -> None:
        """Compatibility hook for callers that reposition the shortcut dock."""

        if self.report_button.isVisible():
            self._position_report_button()


    def _show_hint(self, button: QPushButton) -> None:
        """Show a small six-mao label without changing the dock layout."""

        text = button.toolTip().strip()
        if not text:
            return
        self._hint_button = button
        self.hover_hint.setText(text)
        self.hover_hint.adjustSize()
        # Keep the report label above its tile so it does not add another
        # visual row below the shortcut dock near the bottom edge of a screen.
        is_secondary = button is self.report_button
        above = button.mapToGlobal(QPoint(button.width() // 2, -self.hover_hint.height() - 7))
        below = button.mapToGlobal(QPoint(button.width() // 2, button.height() + 7))
        preferred = above if is_secondary else below
        fallback = below if is_secondary else above
        x = preferred.x() - self.hover_hint.width() // 2
        y = preferred.y()
        app = QGuiApplication.instance()
        screen = app.screenAt(preferred) if app is not None else None
        if screen is not None:
            area = screen.availableGeometry()
            x = min(max(x, area.left() + 4), area.right() - self.hover_hint.width() - 4)
            if is_secondary and y < area.top() + 4:
                y = fallback.y()
            elif not is_secondary and y + self.hover_hint.height() > area.bottom() - 4:
                y = fallback.y()
            y = min(max(y, area.top() + 4), area.bottom() - self.hover_hint.height() - 4)
        self.hover_hint.move(x, y)
        # Configure the native window before showing it.  Reconfiguring a
        # visible macOS Tool/ToolTip window can make AppKit hide the first
        # hover hint, which made the label appear only after a click.
        if sys.platform == "darwin" and self._window_behavior_callback is not None:
            self._window_behavior_callback(self.hover_hint, always_on_top=True)
        self.hover_hint.show()
        # The hint is a separate top-level window. Raise it after applying
        # the native non-activating style so it stays above the shortcut dock
        # on macOS as well as Windows without taking keyboard focus.
        # ``ToolTip`` windows are ordered by AppKit/Windows themselves.  A
        # manual raise on macOS can make the desktop pet briefly become the
        # active application, which is exactly when the hint disappears.
        if sys.platform != "darwin":
            self.hover_hint.raise_()

    def _hide_hint(self) -> None:
        """Hide the hover label when the pointer leaves a shortcut."""

        self._hint_button = None
        self.hover_hint.hide()

    def _button_at_global_pos(self, position: QPoint) -> QPushButton | None:
        """Return the shortcut currently under the native pointer."""

        for button in self._hover_buttons:
            if not button.isVisible():
                continue
            if button.rect().contains(button.mapFromGlobal(position)):
                return button
        return None

    @staticmethod
    def _event_global_position(event) -> QPoint:
        """Read a mouse event's global position across Qt 5/6 event APIs."""

        global_position = getattr(event, "globalPosition", None)
        if callable(global_position):
            return global_position().toPoint()
        global_pos = getattr(event, "globalPos", None)
        if callable(global_pos):
            return global_pos()
        return QCursor.pos()

    def _set_hover_button(self, button: QPushButton | None) -> None:
        """Keep exactly one hover label in sync with the pointer."""

        if button is None:
            if self._hint_button is not None:
                self._hide_hint()
            self._schedule_report_hide()
            return
        if button in (self.work_button, self.report_button):
            self._report_hide_timer.stop()
            self._set_report_button_visible(True)
        else:
            self._schedule_report_hide()
        if self._hint_button is not button or not self.hover_hint.isVisible():
            self._show_hint(button)

    def _poll_hover_button(self) -> None:
        """Repair missing native hover events without changing focus."""

        self._set_hover_button(self._button_at_global_pos(QCursor.pos()))

    def eventFilter(self, watched, event) -> bool:
        if self.isVisible() and event.type() in {
            QEvent.Type.MouseMove,
            QEvent.Type.HoverMove,
        }:
            # This branch also handles events delivered to the application
            # filter, where watched is not one of our six buttons.
            self._set_hover_button(
                self._button_at_global_pos(self._event_global_position(event))
            )
        if watched in getattr(self, "_hover_buttons", ()):
            if event.type() in {
                QEvent.Type.Enter,
                QEvent.Type.HoverEnter,
                QEvent.Type.MouseMove,
            }:
                self._set_hover_button(watched)
            elif event.type() in {QEvent.Type.Leave, QEvent.Type.HoverLeave}:
                QTimer.singleShot(0, self._poll_hover_button)
        return super().eventFilter(watched, event)

    def _show_music_menu(self) -> None:
        """Open only actionable music controls; no Now Playing panel."""

        menu = QMenu(self)
        for label, command in (
            ("播放 / 暂停", "toggle"),
            ("上一首", "previous"),
            ("下一首", "next"),
        ):
            action = menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, value=command: self._choose(
                    self.music_control_requested, value
                )
            )
        menu.addSeparator()
        artist = menu.addAction("听陈楚生…")
        artist.triggered.connect(lambda _checked=False: self._choose(self.chen_artist_requested))
        platform_menu = menu.addMenu("音乐平台")
        platform_group = QActionGroup(platform_menu)
        platform_group.setExclusive(True)
        for key, label in (
            ("auto", "跟随系统默认"),
            ("netease", "网易云音乐"),
            ("qq", "QQ 音乐"),
            ("apple", "Apple Music"),
            ("kugou", "酷狗音乐"),
            ("qishui", "汽水音乐"),
        ):
            action = platform_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self._artist_music_service == key)
            platform_group.addAction(action)
            action.triggered.connect(
                lambda _checked=False, value=key: self._choose(
                    self.artist_music_service_requested, value
                )
            )
        menu.exec(self.music_button.mapToGlobal(self.music_button.rect().bottomLeft()))

    def _show_settings_menu(self) -> None:
        """Keep low-frequency settings behind the single gear entry."""

        menu = QMenu(self)
        for label, signal in (
            ("调整大小", self.size_requested),
            ("主人称呼", self.rename_requested),
        ):
            action = menu.addAction(label)
            action.triggered.connect(lambda _checked=False, chosen=signal: self._choose(chosen))

        updates = menu.addMenu("更新与关于")
        content = updates.addAction("检查补充内容更新")
        content.triggered.connect(lambda _checked=False: self._choose(self.content_update_requested))
        program = updates.addAction("更新到最新版本…")
        program.triggered.connect(lambda _checked=False: self._choose(self.program_update_requested))
        version = updates.addAction("当前版本信息")
        version.setEnabled(False)

        settings = menu.addAction("设置中心…")
        settings.triggered.connect(lambda _checked=False: self._choose(self.settings_requested))
        menu.exec(self.food_button.mapToGlobal(self.food_button.rect().bottomLeft()))

    def set_food_inventory(self, inventory: dict[str, int]) -> None:
        """Refresh the food pocket snapshot used by the next quick click."""

        self._food_inventory = {str(key): max(0, int(value or 0)) for key, value in (inventory or {}).items()}

    def _show_food_menu(self) -> None:
        """Show a lightweight food pocket; full supply management stays elsewhere."""

        menu = QMenu(self)
        labels = (
            ("coffee", "☕ 普通咖啡", "喝了继续干 30 分钟"),
            ("expensive_coffee", "☕ 昂贵咖啡", "喝了认真干，满2小时再得普通咖啡"),
            ("milk_tea", "🧋 奶茶", "想歇会儿就喝"),
            ("cake", "🍰 小蛋糕", "想庆祝就吃"),
            ("tea", "🍵 茶", "坐下来待一会儿"),
        )
        for key, label, tip in labels:
            count = int(getattr(self, "_food_inventory", {}).get(key, 0))
            action = menu.addAction(f"{label} × {count}")
            action.setToolTip(tip)
            action.setEnabled(count > 0)
            action.triggered.connect(lambda _checked=False, item_key=key: self._choose(self.food_requested, item_key))
        menu.addSeparator()
        supply = menu.addAction("去六毛补给站…")
        supply.triggered.connect(lambda _checked=False: self._choose(self.supply_requested))
        menu.exec(self.food_button.mapToGlobal(self.food_button.rect().bottomLeft()))

    def set_pet_name(self, pet_name: str) -> None:
        """昵称保存后同步快捷口袋标题。"""

        self.title.setText(f"{pet_name.strip() or '六毛'}快捷口袋")

    def _choose(self, signal: object, source: str | None = None) -> None:
        """先收起口袋再发出操作信号，避免新窗口被它遮挡。"""

        self.hide()
        if source is None:
            signal.emit()
        else:
            signal.emit(source)

    def showEvent(self, event) -> None:
        """每次显示重新开始八秒自动收起计时。"""

        super().showEvent(event)
        self._hover_poll_timer.start()
        QTimer.singleShot(0, self._poll_hover_button)
        # Native/offscreen Qt may synthesize an enterEvent while a newly
        # positioned top-level panel is being shown. Do not interpret that
        # synthetic event as active mouse use; real pointer entry after the
        # first event-loop turn still pauses the timer as before.
        self._ignore_initial_enter = True
        self.hide_timer.start(8000)
        # Keep the guard through the first short event-loop burst. Some
        # platform plugins deliver the synthetic enterEvent after queued
        # zero-delay callbacks, which would otherwise stop the timer again.
        QTimer.singleShot(500, self._clear_initial_enter_guard)

    def _clear_initial_enter_guard(self) -> None:
        self._ignore_initial_enter = False

    def hideEvent(self, event) -> None:
        # Collapse the child report action and hover surfaces along with the
        # dock. This is intentionally done on the parent hide event as well as
        # in ``hide()`` so every auto-hide path has the same result.
        self._collapse_report_surface()
        super().hideEvent(event)

    def enterEvent(self, event) -> None:
        """鼠标操作期间暂停自动收起。"""

        if not self._ignore_initial_enter:
            self.hide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        """鼠标离开后给用户三秒余量再收起。"""

        self.hide_timer.start(3000)
        self._schedule_report_hide()
        super().leaveEvent(event)

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        # The report action is laid out inside the dock and therefore moves
        # with it automatically; no detached native window needs repositioning.


class SizeControlDialog(QDialog):
    """在 100–360 像素间实时、连续调节宠物高度。"""

    value_changed = Signal(int)

    def __init__(self, value: int, parent: QWidget | None = None, pet_name: str = "六毛") -> None:
        super().__init__(parent)
        pet_name = pet_name.strip() or "六毛"
        self.setObjectName("floatingPanel")
        self.setWindowTitle(f"{pet_name}大小")
        self.setStyleSheet(CONTROL_STYLE)
        layout = QVBoxLayout(self)
        self.label = QLabel(); self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.slider = QSlider(Qt.Orientation.Horizontal); self.slider.setRange(100, 360); self.slider.setValue(value)
        self.slider.valueChanged.connect(self._changed)
        layout.addWidget(self.label); layout.addWidget(self.slider)
        close = QPushButton("完成"); close.clicked.connect(self.accept); layout.addWidget(close)
        self._changed(value)

    def _changed(self, value: int) -> None:
        self.label.setText(f"当前高度：{value} 像素")
        self.value_changed.emit(value)
