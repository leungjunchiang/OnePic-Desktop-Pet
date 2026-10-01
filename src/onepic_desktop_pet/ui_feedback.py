"""共享按钮响应和页内反馈；普通成功提示不创建独立系统对话框。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QPushButton

ACTION_BUTTON_STYLE = """
QPushButton{background:#d7ece8;color:#204c4a;border:1px solid #adcac3;border-radius:8px;}
QPushButton:hover{background:#b5dcd2;color:#173f39;border-color:#5b998b;}
QPushButton:pressed{background:#95c6b9;color:#163b32;border-color:#3e7b6c;}
QPushButton:disabled{background:#e8eef0;color:#5b6c74;border-color:#cbd5d9;}
QPushButton[actionBusy="true"]{background:#d5e4ee;color:#294e68;border-color:#789dad;}
QPushButton#minorRefresh{background:transparent;border:0;padding:2px 5px;min-height:20px;}
QPushButton#minorRefresh:hover{background:#d8eae4;color:#173f39;}
QPushButton#minorRefresh:pressed{background:#bbd8ce;}
"""


def decorate_buttons(root):
    buttons = root.findChildren(QPushButton)
    if isinstance(root, QPushButton):
        buttons.append(root)
    for button in buttons:
        button.setCursor(Qt.CursorShape.PointingHandCursor)


def readable_milk_tea_label(text, font):
    """菜单字体缺少兼容杯子字形时只去掉图标，始终保留奶茶文字。"""
    if "🥤" in text and not QFontMetrics(font).inFontUcs4(ord("🥤")):
        return text.replace("🥤", "").strip()
    return text


def begin_button_work(button, text):
    if button.property("actionBusy"):
        return False
    button.setProperty("workLabel", button.text())
    button.setProperty("workEnabled", button.isEnabled())
    button.setProperty("actionBusy", True)
    button.setText(text)
    button.setEnabled(False)
    button.style().unpolish(button); button.style().polish(button)
    button.repaint()
    return True


def end_button_work(button):
    button.setProperty("actionBusy", False)
    button.setText(str(button.property("workLabel") or ""))
    button.setEnabled(bool(button.property("workEnabled")))
    button.style().unpolish(button); button.style().polish(button)


def show_inline_feedback(parent, title, message):
    """复用页面状态/桌宠气泡，否则创建有父级的短暂标签，不激活任何窗口。"""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QLabel, QWidget, QFormLayout
    text = str(message or title)
    for name in ("_set_status", "_show_status", "show_speech"):
        callback = getattr(parent, name, None)
        if callable(callback):
            callback(text)
            return
    if not isinstance(parent, QWidget):
        return
    label = getattr(parent,"_inline_feedback_label", None)
    if label is None:
        label = QLabel(parent)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setStyleSheet("background:#e1efec;color:#24564e;border-radius:8px;padding:8px;")
        parent._inline_feedback_label = label
        layout = parent.layout()
        if isinstance(layout,QFormLayout): layout.addRow(label)
        elif layout is not None: layout.addWidget(label)
        else: label.setGeometry(10,max(10,parent.height()-100),max(100,parent.width()-20),80)
        timer = QTimer(label);timer.setSingleShot(True);timer.timeout.connect(label.hide)
        parent._inline_feedback_timer=timer
    label.setText(text);label.show()
    parent._inline_feedback_timer.start(6000)
