"""Regression tests for complete action/outfit sprite compositing."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.accessories import draw_activity_overlay


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
