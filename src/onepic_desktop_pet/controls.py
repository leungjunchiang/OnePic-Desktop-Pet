"""提供不依赖系统托盘的六毛快捷面板、原生音乐控制、工作气泡和尺寸调节器。

设置入口只在用户点击快捷口袋按钮时发出 ``user_action`` 来源，供主窗口统一校验。
播放、暂停、切歌和歌曲状态分别发出明确命令，不用“打开音乐客户端”冒充播放控制。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
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


class QuickControlPanel(QWidget):
    """双击宠物才出现的常用入口；选择后或闲置八秒会自动收起。"""

    chat_requested = Signal()
    work_requested = Signal()
    social_requested = Signal()
    music_requested = Signal()
    music_control_requested = Signal(str)
    size_requested = Signal()

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
        layout = QVBoxLayout(self); layout.setContentsMargins(10, 9, 10, 9)
        self.title = QLabel(f"{pet_name}快捷口袋"); self.title.setAlignment(Qt.AlignmentFlag.AlignCenter); layout.addWidget(self.title)
        chat_button = QPushButton("聊聊")
        chat_button.clicked.connect(lambda: self._choose(self.chat_requested))
        layout.addWidget(chat_button)

        self.work_button = QPushButton("开始工作")
        self.work_button.clicked.connect(lambda: self._choose(self.work_requested))
        layout.addWidget(self.work_button)

        social_button = QPushButton("搭子自习室")
        # This panel is intentionally limited to the five high-frequency
        # entrances; the actual room is opened by the parent window.
        social_button.clicked.connect(lambda: self._choose(self.social_requested))
        layout.addWidget(social_button)

        self.music_button = QPushButton("音乐")
        self.music_button.clicked.connect(self._show_music_menu)
        layout.addWidget(self.music_button)
        self.music_status = QLabel("当前播放：暂无")
        self.music_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.music_status.setStyleSheet("color: #607487; font-size: 11px;")
        layout.addWidget(self.music_status)

        size_button = QPushButton("调整大小")
        size_button.clicked.connect(lambda: self._choose(self.size_requested))
        layout.addWidget(size_button)

    def set_work_action_label(self, label: str) -> None:
        """Refresh the dynamic work action shown in the shortcut panel."""

        self.work_button.setText(label.strip() or "开始工作")

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

