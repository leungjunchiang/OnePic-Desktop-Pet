"""提供不依赖系统托盘的六毛快捷面板、工作气泡和连续尺寸调节器。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget


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
    """双击宠物即可出现的常用控制入口，Mac 刘海屏也无需寻找托盘。"""

    chat_requested = Signal()
    work_requested = Signal()
    music_requested = Signal()
    size_requested = Signal()
    settings_requested = Signal()

    def __init__(self) -> None:
        super().__init__(None)
        self.setObjectName("floatingPanel")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(CONTROL_STYLE)
        layout = QVBoxLayout(self); layout.setContentsMargins(10, 9, 10, 9)
        title = QLabel("六毛快捷口袋"); title.setAlignment(Qt.AlignmentFlag.AlignCenter); layout.addWidget(title)
        for label, signal in (
            ("聊聊", self.chat_requested), ("工作计时", self.work_requested),
            ("随机听陈楚生", self.music_requested), ("连续调节大小", self.size_requested),
            ("设置", self.settings_requested),
        ):
            button = QPushButton(label); button.clicked.connect(signal.emit); layout.addWidget(button)


class SizeControlDialog(QDialog):
    """在 100–360 像素间实时、连续调节宠物高度。"""

    value_changed = Signal(int)

    def __init__(self, value: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("floatingPanel")
        self.setWindowTitle("六毛大小")
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
