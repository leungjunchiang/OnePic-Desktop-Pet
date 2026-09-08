"""Local, event-driven burst effects for the desktop pet.

The old Aura renderer was a persistent character-frame compositing layer.  It
is intentionally not used here: a burst is a short-lived, local overlay that
has its own visual timeline and disappears completely after the event.

This module owns no business state, focus state, network client, or worker
thread.  The renderer is a pure QPainter routine; ``LocalBurstEffectWindow``
only provides a reusable, mouse-transparent Qt surface and one bounded timer
while the effect is visible.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtGui import QRegion
from PySide6.QtWidgets import QApplication, QWidget

from .state_effects import LocalEffectKind, normalize_effect_kind

LOGGER = logging.getLogger(__name__)

RED_BURST_DURATION_MS = 4_000
ENTRY_DURATION_MS = 900
DEFAULT_RELEASE_DURATION_MS = 900
OVERLAY_WIDTH_RATIO = 2.4
OVERLAY_HEIGHT_RATIO = 1.7
PET_CENTER_Y_RATIO = 0.43
EXCLUSION_MARGIN = 12


# x, y, rx, ry, phase, strength.  Positions are normalized to the overlay,
# and deliberately bias the larger plumes toward the feet and side edges.
RED_FOG_SEEDS: tuple[tuple[float, float, float, float, float, float], ...] = (
    (0.24, 0.69, 0.20, 0.16, 0.10, 0.92),
    (0.35, 0.78, 0.25, 0.13, 1.40, 0.78),
    (0.18, 0.52, 0.15, 0.17, 2.30, 0.66),
    (0.77, 0.69, 0.21, 0.16, 0.70, 0.90),
    (0.64, 0.79, 0.25, 0.14, 2.00, 0.82),
    (0.86, 0.52, 0.15, 0.18, 3.10, 0.64),
    (0.30, 0.48, 0.17, 0.16, 4.00, 0.48),
    (0.70, 0.47, 0.18, 0.16, 4.80, 0.50),
    (0.50, 0.34, 0.25, 0.12, 5.60, 0.28),
)


# x, y, rise, drift, size, phase, strength.  These are fixed so every frame
# moves continuously rather than re-rolling a new cloud of particles.
RED_PARTICLE_SEEDS: tuple[tuple[float, float, float, float, float, float, float], ...] = (
    (0.22, 0.78, 0.34, 0.07, 0.018, 0.30, 0.92),
    (0.31, 0.83, 0.52, 0.05, 0.014, 1.10, 0.74),
    (0.40, 0.76, 0.44, 0.08, 0.016, 2.20, 0.80),
    (0.57, 0.80, 0.38, 0.06, 0.018, 3.30, 0.86),
    (0.66, 0.76, 0.55, 0.07, 0.014, 4.10, 0.76),
    (0.76, 0.82, 0.31, 0.05, 0.017, 5.20, 0.90),
    (0.28, 0.72, 0.68, 0.06, 0.011, 0.80, 0.60),
    (0.70, 0.70, 0.62, 0.05, 0.012, 2.80, 0.62),
    (0.47, 0.85, 0.47, 0.05, 0.012, 4.70, 0.58),
)


@dataclass(frozen=True)
class LocalEffectPreset:
    """Visual-only parameters shared by all local effect kinds."""

    kind: LocalEffectKind
    primary_color: str
    secondary_color: str
    highlight_color: str
    dark_color: str
    fog_speed: float
    particle_style: str
    particle_count: int
    glow_strength: float
    fog_strength: float
    shockwave_strength: float

    @property
    def palette(self) -> tuple[QColor, ...]:
        return _preset_palette(self.kind)


LOCAL_EFFECT_PRESETS: dict[LocalEffectKind, LocalEffectPreset] = {
    LocalEffectKind.RED: LocalEffectPreset(
        LocalEffectKind.RED, "#E94B45", "#F45B45", "#FFA060", "#A92E3B",
        1.18, "ember", 9, 1.0, 1.0, 1.0,
    ),
    LocalEffectKind.GOLD: LocalEffectPreset(
        LocalEffectKind.GOLD, "#F2C14E", "#E8A63A", "#FFF1A8", "#B97621",
        0.86, "spark", 9, 1.10, 0.78, 0.92,
    ),
    LocalEffectKind.BLUE: LocalEffectPreset(
        LocalEffectKind.BLUE, "#64B5E8", "#3F8EC7", "#BDEAFF", "#275F91",
        0.52, "glow", 4, 0.78, 0.66, 0.62,
    ),
    LocalEffectKind.PURPLE: LocalEffectPreset(
        LocalEffectKind.PURPLE, "#9270D5", "#7053AE", "#D8C5FF", "#493577",
        0.98, "mystic", 6, 0.94, 0.84, 0.88,
    ),
    LocalEffectKind.GREEN: LocalEffectPreset(
        LocalEffectKind.GREEN, "#55C99D", "#329D78", "#B7F4D7", "#25705B",
        0.72, "leaf", 5, 0.86, 0.76, 0.72,
    ),
}


@lru_cache(maxsize=8)
def _preset_palette(kind: LocalEffectKind) -> tuple[QColor, ...]:
    preset = LOCAL_EFFECT_PRESETS[kind]
    return tuple(
        QColor(color)
        for color in (
            preset.primary_color,
            preset.secondary_color,
            preset.dark_color,
            preset.highlight_color,
            preset.highlight_color,
        )
    )


RED_PALETTE = LOCAL_EFFECT_PRESETS[LocalEffectKind.RED].palette


def _alpha(color: QColor, value: float) -> QColor:
    result = QColor(color)
    result.setAlpha(max(0, min(255, round(value))))
    return result


def _ease_out_cubic(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return 1.0 - (1.0 - value) ** 3


def _ease_in_cubic(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value**3


def _ease_in_out_sine(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return -(math.cos(math.pi * value) - 1.0) / 2.0


def _burst_envelope(progress: float) -> float:
    """Rise quickly, hold briefly, and fade with a soft tail."""

    if progress <= 0.23:
        return _ease_out_cubic(progress / 0.23)
    if progress <= 0.68:
        return 1.0
    return 1.0 - _ease_in_cubic((progress - 0.68) / 0.32)


def _draw_soft_ellipse(
    painter: QPainter,
    center: QPointF,
    rx: float,
    ry: float,
    color: QColor,
    alpha: float,
) -> None:
    """Draw one soft, elliptical radial-gradient cloud without blur effects."""

    if rx <= 0 or ry <= 0 or alpha <= 0:
        return
    painter.save()
    painter.translate(center)
    painter.scale(rx, ry)
    gradient = QRadialGradient(QPointF(0.0, 0.0), 1.0)
    gradient.setColorAt(0.0, _alpha(color, alpha))
    gradient.setColorAt(0.42, _alpha(color, alpha * 0.62))
    gradient.setColorAt(0.78, _alpha(color, alpha * 0.18))
    gradient.setColorAt(1.0, _alpha(color, 0.0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawEllipse(QRectF(-1.0, -1.0, 2.0, 2.0))
    painter.restore()


def _draw_ring_layer(
    painter: QPainter,
    center: QPointF,
    rx: float,
    ry: float,
    color: QColor,
    highlight: QColor,
    alpha: float,
    strength: float = 1.0,
) -> None:
    """Draw the broad, bright three-part ground platform."""

    alpha *= strength
    _draw_soft_ellipse(painter, center, rx * 1.20, ry * 2.7, color, alpha * 0.30)
    _draw_soft_ellipse(painter, center, rx * 1.02, ry * 1.75, color, alpha * 0.52)
    _draw_soft_ellipse(painter, center, rx, ry, color, alpha * 0.82)

    painter.save()
    painter.setPen(QPen(_alpha(highlight, alpha * 0.82), max(1.2, ry * 0.13)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QRectF(center.x() - rx, center.y() - ry, rx * 2.0, ry * 2.0))
    painter.setPen(QPen(_alpha(color, alpha * 0.65), max(1.0, ry * 0.28)))
    painter.drawEllipse(QRectF(center.x() - rx * 0.72, center.y() - ry * 0.68, rx * 1.44, ry * 1.36))
    painter.restore()


def _draw_shockwave(
    painter: QPainter,
    center: QPointF,
    base_rx: float,
    base_ry: float,
    progress: float,
    envelope: float,
    color: QColor,
    strength: float,
) -> None:
    if not 0.10 <= progress <= 0.50:
        return
    local = (progress - 0.10) / 0.40
    expansion = _ease_out_cubic(local)
    alpha = 175.0 * (1.0 - expansion) * envelope * strength
    rx = base_rx * (0.82 + expansion * 1.00)
    ry = base_ry * (0.82 + expansion * 0.72)
    painter.save()
    painter.setPen(QPen(_alpha(color, alpha), max(1.4, base_ry * 0.16)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QRectF(center.x() - rx, center.y() - ry, rx * 2, ry * 2))
    painter.restore()


def _draw_flash(
    painter: QPainter,
    pet_rect: QRectF,
    progress: float,
    color: QColor,
) -> None:
    if progress > 0.16:
        return
    local = _ease_out_cubic(progress / 0.16)
    alpha = 185.0 * (1.0 - local)
    center = pet_rect.center() + QPointF(0.0, pet_rect.height() * 0.08)
    _draw_soft_ellipse(
        painter,
        center,
        pet_rect.width() * (0.16 + local * 0.16),
        pet_rect.height() * (0.13 + local * 0.12),
        color,
        alpha,
    )
    painter.save()
    painter.setPen(QPen(_alpha(color, alpha * 0.78), max(1.0, pet_rect.width() * 0.012)))
    for index in range(8):
        angle = index * math.pi / 4.0 + 0.15
        start = center + QPointF(math.cos(angle), math.sin(angle)) * pet_rect.width() * 0.08
        end = center + QPointF(math.cos(angle), math.sin(angle)) * pet_rect.width() * (0.16 + local * 0.08)
        painter.drawLine(start, end)
    painter.restore()


def _draw_plume(
    painter: QPainter,
    center: QPointF,
    rx: float,
    ry: float,
    seed_index: int,
    strength: float,
    preset: LocalEffectPreset,
) -> None:
    colors = preset.palette
    primary = colors[seed_index % 4]
    secondary = colors[(seed_index + 1) % 4]
    highlight = colors[4]
    # Overlapping soft ellipses make one irregular plume instead of a row of
    # obvious circles.  All three layers remain SourceOver for a smoky body.
    _draw_soft_ellipse(painter, center, rx * 1.18, ry * 1.04, primary, 54.0 * strength)
    _draw_soft_ellipse(
        painter,
        center + QPointF(-rx * 0.22, -ry * 0.12),
        rx * 0.84,
        ry * 0.78,
        secondary,
        76.0 * strength,
    )
    _draw_soft_ellipse(
        painter,
        center + QPointF(rx * 0.26, -ry * 0.20),
        rx * 0.52,
        ry * 0.58,
        highlight,
        47.0 * strength,
    )


def _draw_particle(
    painter: QPainter,
    center: QPointF,
    radius: float,
    alpha: float,
    bright: QColor,
    *,
    ember: bool = False,
    style: str = "glow",
) -> None:
    _draw_soft_ellipse(painter, center, radius * 3.6, radius * 3.6, bright, alpha * 0.28)
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_alpha(bright, alpha))
    painter.drawEllipse(center, radius, radius)
    if ember or style in {"spark", "mystic", "leaf"}:
        painter.setPen(QPen(_alpha(QColor("#FFB56F"), alpha * 0.65), max(0.8, radius * 0.55)))
        painter.drawLine(center + QPointF(-radius * 0.7, radius * 1.4), center + QPointF(radius * 0.7, -radius * 1.4))
    if style in {"spark", "mystic"}:
        painter.drawLine(center + QPointF(-radius * 1.4, 0), center + QPointF(radius * 1.4, 0))
    if style == "leaf":
        painter.drawLine(center + QPointF(-radius, -radius), center + QPointF(radius, radius))
    painter.restore()


def _face_safe_rect(pet_rect: QRectF) -> QRectF:
    return QRectF(
        pet_rect.left() + pet_rect.width() * 0.35,
        pet_rect.top() + pet_rect.height() * 0.12,
        pet_rect.width() * 0.30,
        pet_rect.height() * 0.30,
    )


def _apply_face_safety(painter: QPainter, pet_rect: QRectF) -> None:
    """Reduce already-drawn smoke alpha over the approximate face region."""

    face = _face_safe_rect(pet_rect)
    painter.save()
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
    painter.fillRect(face, QColor(0, 0, 0, 180))
    painter.restore()


def paint_local_effect(
    painter: QPainter,
    bounds: QRectF,
    *,
    kind: LocalEffectKind | str = LocalEffectKind.RED,
    progress: float,
    pet_rect: QRectF,
    exclusion_rects: Iterable[QRectF] = (),
    stage: str = "entry",
    phase: float = 0.0,
    opacity_scale: float = 1.0,
) -> None:
    """Paint one local effect frame into an already-created overlay.

    ``bounds`` and ``pet_rect`` are logical coordinates in the overlay.  The
    function never changes window geometry and never performs random sampling.
    ``stage`` is one of ``entry``, ``sustain`` or ``release``.  All three
    stages use the same parameterized renderer; only their envelope differs.
    """

    kind = normalize_effect_kind(kind)
    preset = LOCAL_EFFECT_PRESETS.get(kind)
    if preset is None:
        return
    progress = max(0.0, min(1.0, float(progress)))
    opacity_scale = max(0.0, min(1.0, float(opacity_scale)))
    if bounds.isEmpty() or pet_rect.isEmpty() or opacity_scale <= 0:
        return

    painter.save()
    clip = QRegion(bounds.toAlignedRect())
    for exclusion in exclusion_rects:
        if not exclusion.isEmpty():
            clip = clip.subtracted(QRegion(exclusion.toAlignedRect()))
    painter.setClipRegion(clip, Qt.ClipOperation.ReplaceClip)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    if stage == "sustain":
        envelope = 0.84 + math.sin(phase * 0.92) * 0.08
        bloom = 1.0
    elif stage == "release":
        envelope = 1.0 - _ease_in_cubic(progress)
        bloom = 1.0
    else:
        envelope = _burst_envelope(progress)
        bloom = _ease_out_cubic(min(1.0, max(0.0, (progress - 0.16) / 0.28)))
    envelope *= opacity_scale
    ground = QPointF(pet_rect.center().x(), pet_rect.bottom() - pet_rect.height() * 0.015)
    base_rx = pet_rect.width() * 0.78
    base_ry = pet_rect.height() * 0.115
    palette = preset.palette

    _draw_ring_layer(
        painter,
        ground,
        base_rx,
        base_ry,
        palette[0],
        palette[3],
        168.0 * envelope,
        preset.glow_strength,
    )
    if stage == "entry":
        _draw_shockwave(
            painter,
            ground,
            base_rx,
            base_ry,
            progress,
            envelope,
            palette[1],
            preset.shockwave_strength,
        )

    smoke_strength = envelope * bloom
    for index, (x, y, rx, ry, offset, seed_strength) in enumerate(RED_FOG_SEEDS):
        drift_x = math.sin(phase * preset.fog_speed + offset) * bounds.width() * 0.030
        drift_y = -math.sin(phase * preset.fog_speed * 0.78 + offset) * bounds.height() * 0.035
        breathe = 1.0 + math.sin(phase * 0.92 + offset) * 0.09
        center = QPointF(bounds.left() + bounds.width() * x + drift_x, bounds.top() + bounds.height() * y + drift_y)
        _draw_plume(
            painter,
            center,
            bounds.width() * rx * breathe,
            bounds.height() * ry * breathe,
            index,
            smoke_strength * seed_strength * preset.fog_strength,
            preset,
        )

    if stage == "entry":
        _draw_flash(painter, pet_rect, progress, palette[3])
    _apply_face_safety(painter, pet_rect)

    particle_strength = envelope
    if stage == "entry":
        particle_strength *= min(1.0, max(0.0, (progress - 0.08) / 0.20))
    for index, (x, y, rise, drift, size, offset, seed_strength) in enumerate(RED_PARTICLE_SEEDS):
        if index >= preset.particle_count:
            break
        travel = min(1.0, (progress if stage != "sustain" else 0.65) * 1.18)
        px = bounds.left() + bounds.width() * x + math.sin(phase * 0.75 + offset) * bounds.width() * drift
        py = bounds.top() + bounds.height() * y - bounds.height() * rise * travel
        radius = max(1.5, bounds.width() * size * (1.0 + 0.08 * math.sin(phase + offset)))
        particle_color = palette[3] if index % 3 == 0 else palette[1]
        particle_alpha = 155.0 * particle_strength * seed_strength * max(0.0, 1.0 - progress * 0.34)
        if _face_safe_rect(pet_rect).contains(QPointF(px, py)):
            continue
        _draw_particle(
            painter,
            QPointF(px, py),
            radius,
            particle_alpha,
            particle_color,
            ember=index % 3 == 0,
            style=preset.particle_style,
        )
    painter.restore()


def paint_red_burst(
    painter: QPainter,
    bounds: QRectF,
    *,
    progress: float,
    pet_rect: QRectF,
    exclusion_rects: Iterable[QRectF] = (),
) -> None:
    """Backward-compatible red entry-burst wrapper for tests/integrations."""

    paint_local_effect(
        painter,
        bounds,
        kind=LocalEffectKind.RED,
        progress=progress,
        pet_rect=pet_rect,
        exclusion_rects=exclusion_rects,
        stage="entry",
        phase=progress * math.tau * 1.18,
    )


class LocalBurstEffectWindow(QWidget):
    """Reusable local effect surface for both entry bursts and sustain."""

    finished = Signal()
    progressed = Signal(float)

    def __init__(self) -> None:
        super().__init__(None)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._started_at = 0.0
        self._duration_ms = RED_BURST_DURATION_MS
        self._stage_started_at = 0.0
        self._release_duration_ms = DEFAULT_RELEASE_DURATION_MS
        self._active = False
        self._manual = True
        self._stage = "entry"
        self._kind = LocalEffectKind.RED
        self._previous_kind: LocalEffectKind | None = None
        self._previous_stage_started_at = 0.0
        self._crossfade_started_at = 0.0
        self._crossfade_duration_ms = 360
        self._pet_global_rect = QRect()
        self._pet_rect = QRectF()
        self._exclusion_global_rects: tuple[QRect, ...] = ()
        self._exclusion_rects: tuple[QRectF, ...] = ()
        self._always_on_top = False
        self._configure_flags(False)
        self.resize(1, 1)

    @property
    def timer(self) -> QTimer:
        """Expose the single timer for lifecycle tests and diagnostics."""

        return self._timer

    @property
    def active(self) -> bool:
        return self._active

    @property
    def current_kind(self) -> LocalEffectKind:
        return self._kind

    @property
    def managed(self) -> bool:
        return self._active and not self._manual

    @property
    def stage(self) -> str:
        return self._stage

    @property
    def overlay_size(self) -> tuple[int, int]:
        return self.width(), self.height()

    def _configure_flags(self, always_on_top: bool) -> None:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
        )
        if always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        if int(self.windowFlags()) != int(flags):
            was_visible = self.isVisible()
            self.setWindowFlags(flags)
            if was_visible:
                self.show()
        self._always_on_top = bool(always_on_top)

    def _screen_bounds(self, center: QPointF) -> QRect | None:
        application = QApplication.instance()
        if application is None:
            return None
        screen = application.screenAt(center.toPoint()) or application.primaryScreen()
        return screen.availableGeometry() if screen is not None else None

    def _set_geometry_from_anchor(self) -> None:
        if self._pet_global_rect.isEmpty():
            return
        width = max(80, round(self._pet_global_rect.width() * OVERLAY_WIDTH_RATIO))
        height = max(80, round(self._pet_global_rect.height() * OVERLAY_HEIGHT_RATIO))
        center = QPointF(self._pet_global_rect.center())
        x = round(center.x() - width / 2.0)
        y = round(center.y() - height * PET_CENTER_Y_RATIO)
        screen_bounds = self._screen_bounds(center)
        if screen_bounds is not None:
            if width <= screen_bounds.width():
                x = min(max(x, screen_bounds.left()), screen_bounds.right() - width + 1)
            if height <= screen_bounds.height():
                y = min(max(y, screen_bounds.top()), screen_bounds.bottom() - height + 1)
        self.setGeometry(x, y, width, height)
        self._pet_rect = QRectF(
            self._pet_global_rect.x() - x,
            self._pet_global_rect.y() - y,
            self._pet_global_rect.width(),
            self._pet_global_rect.height(),
        )
        self._exclusion_rects = tuple(
            QRectF(rect.x() - x, rect.y() - y, rect.width(), rect.height())
            for rect in self._exclusion_global_rects
        )

    def reposition(self, pet_global_rect: QRect, exclusion_rects: Iterable[QRect] = ()) -> None:
        """Re-anchor an active effect without changing its animation phase."""

        if pet_global_rect.isEmpty():
            return
        self._pet_global_rect = QRect(pet_global_rect)
        self._exclusion_global_rects = tuple(
            QRect(rect).adjusted(-EXCLUSION_MARGIN, -EXCLUSION_MARGIN, EXCLUSION_MARGIN, EXCLUSION_MARGIN)
            for rect in exclusion_rects
            if not rect.isEmpty()
        )
        self._set_geometry_from_anchor()
        if self._active:
            self.update()

    def trigger(
        self,
        pet_global_rect: QRect,
        exclusion_rects: Iterable[QRect] = (),
        *,
        kind: LocalEffectKind | str = LocalEffectKind.RED,
        always_on_top: bool = False,
        show_window: bool = True,
        managed: bool = False,
    ) -> None:
        """Start or restart the same overlay instance.

        The default remains the original one-shot red burst.  Managed state
        effects use the same surface but keep it in sustain until the manager
        asks for a release.
        """

        try:
            self._configure_flags(always_on_top)
            self.reposition(pet_global_rect, exclusion_rects)
            now = time.monotonic()
            self._started_at = now
            self._stage_started_at = now
            self._kind = normalize_effect_kind(kind)
            if self._kind is LocalEffectKind.NONE:
                self.stop()
                return
            self._previous_kind = None
            self._manual = not managed
            self._stage = "entry"
            self._active = True
            self._timer.start()
            self.update()
            if show_window:
                self.show()
                self.raise_()
        except Exception:
            LOGGER.exception("[LocalEffect] failed to show local effect")
            self.stop()

    def begin_managed(
        self,
        kind: LocalEffectKind | str,
        pet_global_rect: QRect,
        exclusion_rects: Iterable[QRect] = (),
        *,
        always_on_top: bool = False,
        show_window: bool = True,
    ) -> None:
        self.trigger(
            pet_global_rect,
            exclusion_rects,
            kind=kind,
            always_on_top=always_on_top,
            show_window=show_window,
            managed=True,
        )

    def switch_managed(self, kind: LocalEffectKind | str) -> None:
        """Crossfade the active effect into another preset without a blank frame."""

        target = normalize_effect_kind(kind)
        if target is LocalEffectKind.NONE:
            self.release(DEFAULT_RELEASE_DURATION_MS)
            return
        if not self._active:
            return
        if target is self._kind:
            return
        now = time.monotonic()
        self._previous_kind = self._kind
        self._previous_stage_started_at = self._stage_started_at
        self._kind = target
        self._stage = "entry"
        self._stage_started_at = now
        self._crossfade_started_at = now
        self._manual = False
        self._timer.start()
        self.update()

    def set_sustain(self) -> None:
        if self._active and not self._manual:
            self._stage = "sustain"
            self._stage_started_at = time.monotonic()
            self.update()

    def release(self, duration_ms: int = DEFAULT_RELEASE_DURATION_MS) -> None:
        if not self._active:
            return
        self._manual = False
        self._stage = "release"
        self._stage_started_at = time.monotonic()
        self._release_duration_ms = max(220, int(duration_ms))
        self.update()

    def stop(self) -> None:
        self._timer.stop()
        self._active = False
        self.hide()
        self.update()

    def _tick(self) -> None:
        if not self._active:
            self._timer.stop()
            return
        now = time.monotonic()
        self.progressed.emit(now)
        if self._manual and (now - self._started_at) * 1000.0 >= self._duration_ms:
            self.stop()
            self.finished.emit()
            return
        if not self._manual and self._stage == "release":
            if (now - self._stage_started_at) * 1000.0 >= self._release_duration_ms:
                self.stop()
                self.finished.emit()
                return
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self._active:
            return
        painter = QPainter(self)
        try:
            now = time.monotonic()
            bounds = QRectF(self.rect())
            stage_elapsed = (now - self._stage_started_at) * 1000.0
            if self._manual:
                progress = stage_elapsed / self._duration_ms
                paint_local_effect(
                    painter, bounds, kind=self._kind, progress=progress,
                    pet_rect=self._pet_rect, exclusion_rects=self._exclusion_rects,
                    stage="entry", phase=stage_elapsed / 1000.0,
                )
            elif self._stage == "release":
                progress = stage_elapsed / max(1, self._release_duration_ms)
                paint_local_effect(
                    painter, bounds, kind=self._kind, progress=progress,
                    pet_rect=self._pet_rect, exclusion_rects=self._exclusion_rects,
                    stage="release", phase=stage_elapsed / 1000.0,
                )
            else:
                current_painter_saved = False
                if self._previous_kind is not None:
                    fade = min(
                        1.0,
                        max(0.0, (now - self._crossfade_started_at) * 1000.0 / self._crossfade_duration_ms),
                    )
                    painter.save()
                    painter.setOpacity(1.0 - fade)
                    paint_local_effect(
                        painter, bounds, kind=self._previous_kind, progress=0.5,
                        pet_rect=self._pet_rect, exclusion_rects=self._exclusion_rects,
                        stage="sustain", phase=(now - self._previous_stage_started_at),
                    )
                    painter.restore()
                    if fade >= 1.0:
                        self._previous_kind = None
                    painter.save()
                    current_painter_saved = True
                    painter.setOpacity(fade)
                else:
                    fade = 1.0
                stage = "entry" if self._stage == "entry" else "sustain"
                progress = stage_elapsed / ENTRY_DURATION_MS if stage == "entry" else 0.0
                paint_local_effect(
                    painter, bounds, kind=self._kind, progress=progress,
                    pet_rect=self._pet_rect, exclusion_rects=self._exclusion_rects,
                    stage=stage, phase=stage_elapsed / 1000.0,
                )
                if current_painter_saved:
                    painter.restore()
        except Exception:
            LOGGER.exception("[LocalEffect] paint failed; hiding local effect")
            self.stop()
        finally:
            painter.end()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.stop()
        event.accept()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt API
        # Any external hide (fullscreen takeover, explicit hide, or shutdown)
        # is also an animation cancellation. This prevents a hidden 30 FPS
        # timer from continuing behind the desktop.
        if self._active:
            self._active = False
            self._timer.stop()
        super().hideEvent(event)
