"""提供不依赖系统托盘的六毛快捷面板、原生音乐控制、工作气泡和尺寸调节器。

设置入口只在用户点击快捷口袋按钮时发出 ``user_action`` 来源，供主窗口统一校验。
播放、暂停、切歌和歌曲状态分别发出明确命令，不用“打开音乐客户端”冒充播放控制。
快捷口袋使用代码绘制的红黄蓝矢量图标，不依赖平台 Emoji 或低清位图。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
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
QPushButton { background: rgba(74, 126, 151, 225); color: white; border: none; border-radius: 10px;
padding: 8px 12px; font-weight: 600; }
QPushButton:hover { background: #376a82; }
QLabel { border: none; background: transparent; }
"""


class WorkControlBubble(QWidget):
    """显示暂停与结束两个明确操作，避免计时控制藏在菜单。"""

    pause_requested = Signal()
    finish_requested = Signal()

    def __init__(self) -> None:
        super().__init__(None)
        self.setObjectName("floatingPanel")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(CONTROL_STYLE)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        pause = QPushButton("暂停工作")
        finish = QPushButton("结束工作")
        pause.clicked.connect(self.pause_requested.emit)
        finish.clicked.connect(self.finish_requested.emit)
        layout.addWidget(pause); layout.addWidget(finish)


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
    else:  # settings
        painter.setPen(QPen(blue, 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for angle in range(0, 360, 45):
            painter.save(); painter.translate(36, 36); painter.rotate(angle); painter.drawLine(0, -23, 0, -30); painter.restore()
        painter.setBrush(QBrush(blue)); painter.setPen(Qt.PenStyle.NoPen); painter.drawEllipse(15, 15, 42, 42)
        painter.setBrush(QBrush(yellow)); painter.drawEllipse(27, 27, 18, 18)
        painter.setBrush(QBrush(red)); painter.drawEllipse(52, 7, 12, 12)
    painter.setPen(QPen(ink, 1)); painter.end()
    return QIcon(pixmap)


class QuickControlPanel(QWidget):
    """双击宠物才出现的常用入口；选择后或闲置八秒会自动收起。"""

    chat_requested = Signal()
    work_requested = Signal()
    social_requested = Signal()
    music_requested = Signal()
    music_control_requested = Signal(str)
    settings_requested = Signal()

    def __init__(self, pet_name: str = "六毛") -> None:
        super().__init__(None)
        pet_name = pet_name.strip() or "六毛"
        self.setObjectName("floatingPanel")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(CONTROL_STYLE)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)
        layout = QVBoxLayout(self); layout.setContentsMargins(10, 9, 10, 9); layout.setSpacing(7)
        self.title = QLabel(f"{pet_name}快捷口袋"); self.title.setAlignment(Qt.AlignmentFlag.AlignCenter); layout.addWidget(self.title)
        row = QHBoxLayout(); row.setSpacing(6)
        self.chat_button = self._button("chat", "聊聊", self.chat_requested)
        self.work_button = self._button("work", "开始工作", self.work_requested)
        self.social_button = self._button("social", "搭子自习室", self.social_requested)
        self.music_button = self._button("music", "音乐", None)
        self.settings_button = self._button("settings", "设置", self.settings_requested)
        self.music_button.clicked.connect(self._show_music_menu)
        for button in (self.chat_button, self.work_button, self.social_button, self.music_button, self.settings_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.music_status = QLabel("当前播放：暂无")
        self.music_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.music_status.setStyleSheet("color: #607487; font-size: 11px;")
        layout.addWidget(self.music_status)


    @staticmethod
    def _button(kind: str, tooltip: str, signal: object | None) -> QPushButton:
        button = QPushButton()
        button.setObjectName(f"quickAction_{kind}")
        button.setIcon(_quick_icon(kind))
        button.setIconSize(QSize(34, 34))
        button.setFixedSize(52, 52)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        if signal is not None:
            button.clicked.connect(lambda: signal.emit())
        return button

    def set_work_action_label(self, label: str) -> None:
        """Refresh the dynamic work action shown in the shortcut panel."""

        self.work_button.setToolTip(label.strip() or "开始工作")
        self.work_button.setAccessibleName(label.strip() or "开始工作")

    def set_music_status(self, text: str) -> None:
        """Show the last confirmed player track as read-only panel state."""

        self.music_status.setText(text.strip() or "当前播放：暂无")

    def _show_music_menu(self) -> None:
        """Open the second-level music controls from the single music entry."""

        menu = QMenu(self)
        for label, command in (
            ("播放 / 暂停", "toggle"),
            ("上一首", "previous"),
            ("下一首", "next"),
            ("随机听陈楚生", "random"),
        ):
            action = menu.addAction(label)
            if command == "random":
                action.triggered.connect(lambda: self._choose(self.music_requested))
            else:
                action.triggered.connect(
                    lambda _checked=False, value=command: self._choose(
                        self.music_control_requested, value
                    )
                )
        status = menu.addAction(self.music_status.text())
        status.setEnabled(False)
        menu.exec(self.music_button.mapToGlobal(self.music_button.rect().bottomLeft()))

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
        self.hide_timer.start(8000)

    def enterEvent(self, event) -> None:
        """鼠标操作期间暂停自动收起。"""

        self.hide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        """鼠标离开后给用户三秒余量再收起。"""

        self.hide_timer.start(3000)
        super().leaveEvent(event)


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

