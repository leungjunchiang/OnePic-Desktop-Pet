from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.local_burst_effect import (
    LocalBurstEffectWindow,
    OVERLAY_HEIGHT_RATIO,
    OVERLAY_WIDTH_RATIO,
    paint_red_burst,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _render(progress: float, exclusions=()) -> QPixmap:
    _app()
    canvas = QPixmap(384, 272)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    paint_red_burst(
        painter,
        QRectF(canvas.rect()),
        progress=progress,
        pet_rect=QRectF(144, 52, 160, 160),
        exclusion_rects=exclusions,
    )
    painter.end()
    return canvas


def test_red_burst_has_local_ground_glow_and_is_not_full_screen() -> None:
    image = _render(0.45).toImage()
    # The platform is intentionally obvious near the feet.
    assert image.pixelColor(192, 212).alpha() > 80
    # The renderer never paints outside the supplied local overlay bounds.
    assert image.pixelColor(2, 2).alpha() == 0


def test_red_burst_has_flash_bloom_and_fade_phases() -> None:
    flash = _render(0.06).toImage()
    sustain = _render(0.52).toImage()
    fade = _render(0.98).toImage()
    assert flash.pixelColor(192, 120).alpha() > 0
    assert sustain.pixelColor(192, 212).alpha() >= flash.pixelColor(192, 212).alpha()
    assert fade.pixelColor(192, 212).alpha() < sustain.pixelColor(192, 212).alpha()


def test_face_safe_region_and_bubble_exclusion_are_clear() -> None:
    image = _render(0.52, exclusions=(QRectF(150, 205, 84, 42),)).toImage()
    # Smoke is reduced to transparent over the approximate face.
    assert image.pixelColor(192, 92).alpha() < 60
    # A status bubble exclusion remains transparent with its margin already
    # applied by the window layer in the real path.
    assert image.pixelColor(192, 220).alpha() == 0


def test_local_burst_window_is_reused_and_has_one_timer() -> None:
    _app()
    window = LocalBurstEffectWindow()
    window.trigger(QRect(100, 100, 160, 160))
    assert window.active
    assert window.timer.isActive()
    assert window.width() == round(160 * OVERLAY_WIDTH_RATIO)
    assert window.height() == round(160 * OVERLAY_HEIGHT_RATIO)
    first_id = id(window)
    window.trigger(QRect(120, 140, 160, 160))
    assert id(window) == first_id
    assert window.active
    window.stop()
    assert not window.active
    assert not window.timer.isActive()
    window.close()
