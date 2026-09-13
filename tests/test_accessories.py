"""Regression tests for complete action/outfit sprite compositing."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.accessories import (
    LOGIN_REWARD_OUTFIT,
    draw_activity_overlay,
    login3_action_sprite_path,
)


def _alpha_bbox(pixmap: QPixmap) -> tuple[int, int, int, int] | None:
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    left, top, right, bottom = image.width(), image.height(), -1, -1
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() > 0:
                left, top = min(left, x), min(top, y)
                right, bottom = max(right, x), max(bottom, y)
    if right < left:
        return None
    return left, top, right + 1, bottom + 1


def test_complete_sprite_keeps_full_bounds_on_200_percent_display() -> None:
    """A 2x backing pixmap must not enlarge and crop the full action sprite."""

    app = QApplication.instance() or QApplication([])
    source = QPixmap(400, 400)
    source.fill(Qt.GlobalColor.transparent)
    source.setDevicePixelRatio(2.0)
    result = draw_activity_overlay(source, activity="guitar")
    bbox = _alpha_bbox(result)
    assert app is not None
    assert result.devicePixelRatio() == 2.0
    assert bbox is not None
    assert bbox[0] > 0 and bbox[1] > 0
    assert bbox[2] < result.width() and bbox[3] < result.height()


def test_night_limited_activity_uses_the_dedicated_transparent_sprite() -> None:
    """夜间限定造型走完整素材映射，不改变永久娃衣装备。"""

    from onepic_desktop_pet.accessories import SPECIAL_LIMITED_ACTIVITY_SPRITES

    assert SPECIAL_LIMITED_ACTIVITY_SPRITES["night-study-limited"] == (
        "assets/pet/night-limited/00-night-study-clean.png"
    )


def test_taunt_activity_uses_the_server_punishment_sprite() -> None:
    from onepic_desktop_pet.accessories import SPECIAL_LIMITED_ACTIVITY_SPRITES

    assert SPECIAL_LIMITED_ACTIVITY_SPRITES["taunt"] == "assets/pet/special/taunt-pet.jpg"


def test_taunt_sprite_removes_the_reference_matte() -> None:
    app = QApplication.instance() or QApplication([])
    source = QPixmap(300, 300)
    source.fill(Qt.GlobalColor.transparent)
    result = draw_activity_overlay(source, activity="taunt")
    assert app is not None
    assert result.toImage().pixelColor(0, 0).alpha() == 0
    assert _alpha_bbox(result) is not None


def test_login3_actions_are_outfit_scoped_and_use_complete_transparent_sprites() -> None:
    """login-3 的工作/休息动作不应泄漏到其他娃衣。"""

    app = QApplication.instance() or QApplication([])
    source = QPixmap(560, 500)
    source.fill(Qt.GlobalColor.transparent)

    assert login3_action_sprite_path("work-flow", LOGIN_REWARD_OUTFIT.key).endswith(
        "login-rewards/actions/report.png"
    )
    assert login3_action_sprite_path("work-flow", "hour-01") is None

    result = draw_activity_overlay(
        source,
        activity="work-flow",
        outfit=LOGIN_REWARD_OUTFIT.key,
    )
    assert app is not None
    assert result.toImage().pixelColor(0, 0).alpha() == 0
    assert _alpha_bbox(result) is not None
