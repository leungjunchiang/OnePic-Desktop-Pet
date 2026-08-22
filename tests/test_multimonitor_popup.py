"""验证多显示器右键弹窗使用全局逻辑坐标并支持负坐标。"""

from PySide6.QtCore import QPoint, QRect, QSize

from onepic_desktop_pet.window import (
    clamp_global_popup_position,
    context_menu_position_for_pet,
)


def test_popup_clamp_preserves_negative_left_monitor_coordinates():
    available = QRect(-1920, -200, 1920, 1200)
    point = clamp_global_popup_position(QPoint(-50, 900), QSize(400, 300), available)
    assert point == QPoint(-400, 700)


def test_popup_clamp_stays_on_right_monitor():
    available = QRect(2560, 0, 1920, 1080)
    point = clamp_global_popup_position(QPoint(4400, 1000), QSize(400, 300), available)
    assert point == QPoint(4080, 780)


def test_macos_context_menu_falls_back_to_pet_screen_when_event_coordinate_jumps():
    external = QRect(2560, 0, 1920, 1080)
    point = context_menu_position_for_pet(
        QPoint(100, 100),
        QPoint(3200, 700),
        external,
        macos=True,
    )
    assert point == QPoint(3200, 700)


def test_macos_context_menu_keeps_valid_external_display_coordinate():
    external = QRect(2560, 0, 1920, 1080)
    point = context_menu_position_for_pet(
        QPoint(3000, 400),
        QPoint(3200, 700),
        external,
        macos=True,
    )
    assert point == QPoint(3000, 400)
