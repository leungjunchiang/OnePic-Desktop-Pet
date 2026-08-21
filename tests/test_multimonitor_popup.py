"""验证多显示器右键弹窗使用全局逻辑坐标并支持负坐标。"""

from PySide6.QtCore import QPoint, QRect, QSize

from onepic_desktop_pet.window import clamp_global_popup_position


def test_popup_clamp_preserves_negative_left_monitor_coordinates():
    available = QRect(-1920, -200, 1920, 1200)
    point = clamp_global_popup_position(QPoint(-50, 900), QSize(400, 300), available)
    assert point == QPoint(-400, 700)


def test_popup_clamp_stays_on_right_monitor():
    available = QRect(2560, 0, 1920, 1080)
    point = clamp_global_popup_position(QPoint(4400, 1000), QSize(400, 300), available)
    assert point == QPoint(4080, 780)
