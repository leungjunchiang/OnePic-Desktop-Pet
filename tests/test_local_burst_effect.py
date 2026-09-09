from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.local_burst_effect import (
    EXCLUSION_FEATHER_MARGIN,
    EXCLUSION_HARD_MARGIN,
    EffectExclusionRegion,
    LocalBurstEffectWindow,
    OVERLAY_HEIGHT_RATIO,
    OVERLAY_WIDTH_RATIO,
    paint_local_effect,
    paint_red_burst,
)
from onepic_desktop_pet.state_effects import LocalEffectKind


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


def test_all_state_effect_presets_render_with_the_same_local_pipeline() -> None:
    for kind in (
        LocalEffectKind.RED,
        LocalEffectKind.GOLD,
        LocalEffectKind.BLUE,
        LocalEffectKind.PURPLE,
        LocalEffectKind.GREEN,
        LocalEffectKind.CYAN,
    ):
        _app()
        canvas = QPixmap(384, 272)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        paint_local_effect(
            painter,
            QRectF(canvas.rect()),
            kind=kind,
            progress=0.52,
            pet_rect=QRectF(144, 52, 160, 160),
            stage="entry",
            phase=0.52,
        )
        painter.end()
        assert not canvas.isNull()
        assert canvas.toImage().pixelColor(192, 212).alpha() > 0


def test_face_safe_region_and_bubble_exclusion_are_clear() -> None:
    image = _render(0.52, exclusions=(QRectF(150, 205, 84, 42),)).toImage()
    # Smoke is reduced to transparent over the approximate face.
    assert image.pixelColor(192, 92).alpha() < 60
    # A status bubble exclusion remains transparent with its margin already
    # applied by the window layer in the real path.
    assert image.pixelColor(192, 220).alpha() == 0


def test_visible_pill_exclusion_is_rounded_and_tightly_bounded() -> None:
    region = EffectExclusionRegion.from_rect(
        QRectF(100, 100, 120, 30),
        radius=15,
    )
    hard_bounds = region.hard_path.boundingRect()
    feather_bounds = region.feather_path.boundingRect()
    assert hard_bounds.left() == 100 - EXCLUSION_HARD_MARGIN
    assert hard_bounds.right() == 220 + EXCLUSION_HARD_MARGIN
    assert feather_bounds.left() == 100 - EXCLUSION_HARD_MARGIN - EXCLUSION_FEATHER_MARGIN
    assert feather_bounds.right() == 220 + EXCLUSION_HARD_MARGIN + EXCLUSION_FEATHER_MARGIN
    # The capsule's rounded corner is not treated like a rectangular widget
    # corner, while its center remains fully protected.
    assert not region.hard_path.contains(QPointF(96, 96))
    assert region.hard_path.contains(QPointF(160, 115))


def test_rounded_pill_feather_keeps_effect_outside_the_pill() -> None:
    _app()
    canvas = QPixmap(384, 272)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    paint_local_effect(
        painter,
        QRectF(canvas.rect()),
        kind=LocalEffectKind.PURPLE,
        progress=0.52,
        pet_rect=QRectF(144, 52, 160, 160),
        exclusion_regions=(EffectExclusionRegion.from_rect(QRectF(150, 205, 84, 42), radius=21),),
        stage="entry",
        phase=0.52,
    )
    painter.end()
    image = canvas.toImage()
    # The center is protected by the rounded hard path for smoke/particles;
    # the opaque bubble itself is raised above the overlay, so its ground
    # glow may continue underneath it.  Nearby outside space is not swallowed
    # by a large rectangular exclusion.
    region = EffectExclusionRegion.from_rect(QRectF(150, 205, 84, 42), radius=21)
    assert region.contains_hard(QPointF(192, 220))
    assert image.pixelColor(145, 220).alpha() > 0


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


def test_translucent_effect_surface_is_cleared_when_frame_becomes_inactive() -> None:
    app = _app()
    window = LocalBurstEffectWindow()
    window.trigger(QRect(100, 100, 160, 160))
    app.processEvents()
    painted = window.grab().toImage()
    assert any(
        painted.pixelColor(x, y).alpha() > 0
        for y in range(0, painted.height(), 8)
        for x in range(0, painted.width(), 8)
    )

    # Keep the native surface visible while asking paintEvent to draw an
    # inactive frame. This reproduces the Retina backing-store case where an
    # uncleared translucent widget used to retain dark fog contours.
    window._active = False
    window.update()
    app.processEvents()
    cleared = window.grab().toImage()
    assert all(
        cleared.pixelColor(x, y).alpha() == 0
        for y in range(0, cleared.height(), 8)
        for x in range(0, cleared.width(), 8)
    )
    window.hide()
    window.close()
