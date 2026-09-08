"""六毛的本地 Aura/氛围特效渲染层。

本模块只负责可选的 QPainter 视觉合成：红、金、蓝、紫四种雾气及其
少量确定性粒子。它不拥有业务状态、计时、网络、QTimer、QThread 或窗口，
由桌宠现有的动画 tick 提供 phase；渲染失败时由调用边界回退到原始角色帧。

Local, optional Aura rendering for the desktop pet.

Aura is deliberately a rendering concern only.  It has no timer, network
client, persistence, or business-state side effects.  The window reuses its
existing animation phase and asks this module for a composited pixmap.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QRadialGradient, QPixmap

from .behavior import PetState


AURA_MODE_AUTO = "auto"
AURA_MODE_OFF = "off"
AURA_MODE_MANUAL = "manual"
AURA_MODES = (AURA_MODE_AUTO, AURA_MODE_OFF, AURA_MODE_MANUAL)


class AuraKind(str, Enum):
    NONE = "none"
    RED = "red"
    GOLD = "gold"
    BLUE = "blue"
    PURPLE = "purple"


AURA_MANUAL_EFFECTS = tuple(kind.value for kind in AuraKind if kind is not AuraKind.NONE)


def clamp01(value: Any) -> float:
    """Return a finite float in the renderer's supported range."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return max(0.0, min(1.0, number))


def normalize_aura_kind(value: AuraKind | str | None) -> AuraKind:
    if isinstance(value, AuraKind):
        return value
    try:
        return AuraKind(str(value or AuraKind.NONE.value).strip().lower())
    except ValueError:
        return AuraKind.NONE


@dataclass(frozen=True)
class AuraVisualState:
    """Validated visual input shared by the resolver and renderer."""

    kind: AuraKind | str = AuraKind.NONE
    intensity: float = 0.0
    opacity: float = 0.0
    phase: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", normalize_aura_kind(self.kind))
        object.__setattr__(self, "intensity", clamp01(self.intensity))
        object.__setattr__(self, "opacity", clamp01(self.opacity))
        try:
            phase = float(self.phase)
        except (TypeError, ValueError):
            phase = 0.0
        object.__setattr__(self, "phase", phase % 12.0)


_AUTO_AURA: dict[PetState, AuraVisualState] = {
    PetState.ANNOYED: AuraVisualState(AuraKind.RED, 0.74, 0.62),
    PetState.HAPPY: AuraVisualState(AuraKind.GOLD, 0.65, 0.56),
    PetState.WAVE: AuraVisualState(AuraKind.GOLD, 0.58, 0.50),
    PetState.SLEEPY: AuraVisualState(AuraKind.BLUE, 0.48, 0.44),
    PetState.CURIOUS: AuraVisualState(AuraKind.PURPLE, 0.56, 0.50),
}


def resolve_auto_aura(pet_state: PetState | str, *, phase: float = 0.0) -> AuraVisualState:
    """Map the existing PetState to a conservative local visual effect."""

    try:
        state = pet_state if isinstance(pet_state, PetState) else PetState(str(pet_state))
    except ValueError:
        state = PetState.IDLE
    resolved = _AUTO_AURA.get(state, AuraVisualState())
    return AuraVisualState(resolved.kind, resolved.intensity, resolved.opacity, phase)


def resolve_aura_state(
    pet_state: PetState | str,
    mode: str = AURA_MODE_AUTO,
    manual_effect: AuraKind | str = AuraKind.BLUE,
    *,
    phase: float = 0.0,
) -> AuraVisualState:
    """Resolve settings and PetState without consulting any external service."""

    normalized_mode = str(mode or AURA_MODE_AUTO).strip().lower()
    if normalized_mode == AURA_MODE_OFF:
        return AuraVisualState(phase=phase)
    if normalized_mode == AURA_MODE_MANUAL:
        kind = normalize_aura_kind(manual_effect)
        if kind is AuraKind.NONE:
            kind = AuraKind.BLUE
        # Manual mode is an explicit preview/selection, so it must remain
        # clearly visible even when the native pet window keeps its original
        # character-only mask.
        return AuraVisualState(kind, 0.68, 0.58, phase)
    return resolve_auto_aura(pet_state, phase=phase)


class AuraTransitionController:
    """Small state machine used by the existing animation tick.

    It supplies a cross-fade description; it does not own a QTimer or any Qt
    object.  The window decides when to tick it using its existing effect
    timer.
    """

    def __init__(self) -> None:
        self._from = AuraVisualState()
        self._target = AuraVisualState()
        self._progress = 1.0

    @property
    def target(self) -> AuraVisualState:
        return self._target

    @property
    def progress(self) -> float:
        return self._progress

    @property
    def needs_animation(self) -> bool:
        return (
            self._progress < 1.0
            or self._target.kind is not AuraKind.NONE
        )

    def set_target(self, target: AuraVisualState) -> bool:
        target = AuraVisualState(target.kind, target.intensity, target.opacity, target.phase)
        # Phase is an animation input, not a visual-state transition.  Do not
        # restart a fade on every existing 90 ms effect tick.
        if (
            target.kind is self._target.kind
            and target.intensity == self._target.intensity
            and target.opacity == self._target.opacity
        ):
            self._target = target
            return False
        if target == self._target:
            return False
        # If another transition is interrupted, starting from the previous
        # target avoids retaining a long-lived object graph and keeps rapid
        # menu changes deterministic and cheap.
        self._from = self._target
        self._target = target
        self._progress = 0.0
        return True

    def tick(self, step: float = 0.16) -> bool:
        if self._progress >= 1.0:
            return False
        self._progress = min(1.0, self._progress + clamp01(step))
        return True

    def render_states(self) -> tuple[AuraVisualState, AuraVisualState, float]:
        return self._from, self._target, self._progress


@dataclass(frozen=True)
class _FogSeed:
    x: float
    y: float
    radius: float
    phase_offset: float
    weight: float


@dataclass(frozen=True)
class _ParticleSeed:
    x: float
    y: float
    phase_offset: float
    size: float


# Symmetric, edge-biased positions keep the face readable and do not require
# image segmentation or any character-specific knowledge.
_FOG_SEEDS = (
    _FogSeed(0.22, 0.62, 0.20, 0.2, 0.88),
    _FogSeed(0.35, 0.78, 0.19, 1.4, 0.78),
    _FogSeed(0.65, 0.76, 0.20, 2.0, 0.86),
    _FogSeed(0.78, 0.61, 0.19, 2.8, 0.80),
    _FogSeed(0.28, 0.44, 0.16, 3.7, 0.70),
    _FogSeed(0.72, 0.43, 0.16, 4.6, 0.70),
    _FogSeed(0.42, 0.88, 0.18, 5.3, 0.66),
    _FogSeed(0.58, 0.88, 0.18, 5.9, 0.66),
    _FogSeed(0.16, 0.80, 0.15, 6.6, 0.58),
    _FogSeed(0.84, 0.80, 0.15, 7.2, 0.58),
)

_PARTICLE_SEEDS = (
    _ParticleSeed(0.12, 0.52, 0.2, 0.010),
    _ParticleSeed(0.24, 0.28, 1.2, 0.008),
    _ParticleSeed(0.84, 0.34, 2.1, 0.009),
    _ParticleSeed(0.91, 0.70, 3.0, 0.012),
    _ParticleSeed(0.34, 0.93, 3.8, 0.008),
    _ParticleSeed(0.66, 0.93, 4.5, 0.009),
    _ParticleSeed(0.07, 0.76, 5.3, 0.007),
)

_PALETTE = {
    AuraKind.RED: (QColor("#E95A50"), QColor("#C43D46"), QColor("#FF8A5C")),
    AuraKind.GOLD: (QColor("#F4C95D"), QColor("#FFE19A"), QColor("#E8A83C")),
    AuraKind.BLUE: (QColor("#73B8E6"), QColor("#A9DDF4"), QColor("#4F8FC4")),
    AuraKind.PURPLE: (QColor("#8E70CC"), QColor("#B69AE6"), QColor("#7154AD")),
}

_DRIFT = {
    AuraKind.RED: (0.75, -0.025, 0.030),
    AuraKind.GOLD: (0.50, -0.010, 0.024),
    AuraKind.BLUE: (0.28, 0.005, 0.016),
    AuraKind.PURPLE: (0.62, -0.008, 0.022),
}

# The ring is deliberately a visual layer, not a window/geometry change.  Its
# center stays in the lower body so the existing character-only native mask
# can still reveal the effect without enlarging the click target.
_RING_STYLE = {
    AuraKind.RED: (0.835, 0.92, 0.74),
    AuraKind.GOLD: (0.825, 1.00, 0.86),
    AuraKind.BLUE: (0.845, 0.78, 0.54),
    AuraKind.PURPLE: (0.835, 0.88, 0.72),
}

_PARTICLE_COUNTS = {
    AuraKind.RED: 4,
    AuraKind.GOLD: 7,
    AuraKind.BLUE: 3,
    AuraKind.PURPLE: 4,
}


def _with_alpha(color: QColor, alpha: int) -> QColor:
    result = QColor(color)
    result.setAlpha(max(0, min(255, int(alpha))))
    return result


def _bucket(value: float, steps: int) -> float:
    return round(clamp01(value) * steps) / steps


class AuraRenderer:
    """Bounded cache of Aura-only layers plus lightweight compositing."""

    def __init__(self, max_cache_entries: int = 96) -> None:
        self.max_cache_entries = max(8, int(max_cache_entries))
        self._cache: OrderedDict[tuple[object, ...], QPixmap] = OrderedDict()
        self.cache_hits = 0
        self.cache_misses = 0

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    def clear_cache(self) -> None:
        self._cache.clear()

    @staticmethod
    def _size_bucket(source: QPixmap) -> tuple[int, int, float]:
        ratio = max(1.0, float(source.devicePixelRatio()))
        logical_width = max(1.0, source.width() / ratio)
        logical_height = max(1.0, source.height() / ratio)
        # The cache is already strictly bounded.  Keeping the exact logical
        # size avoids a SmoothTransformation of the cached layer on every
        # animation tick (the previous 16 px bucket was cheaper to store but
        # noticeably more expensive to display continuously).
        width = max(1, int(round(logical_width)))
        height = max(1, int(round(logical_height)))
        return width, height, round(ratio, 2)

    def _cache_key(self, source: QPixmap, state: AuraVisualState) -> tuple[object, ...]:
        width, height, ratio = self._size_bucket(source)
        return (
            state.kind.value,
            width,
            height,
            ratio,
            _bucket(state.intensity, 4),
            _bucket(state.opacity, 8),
            int(state.phase) % 12,
        )

    def _layer_for(self, source: QPixmap, state: AuraVisualState) -> QPixmap:
        key = self._cache_key(source, state)
        cached = self._cache.get(key)
        if cached is not None:
            self.cache_hits += 1
            self._cache.move_to_end(key)
            return cached

        self.cache_misses += 1
        logical_width, logical_height, ratio = self._size_bucket(source)
        layer = QPixmap(max(1, int(round(logical_width * ratio))), max(1, int(round(logical_height * ratio))))
        layer.setDevicePixelRatio(ratio)
        layer.fill(Qt.GlobalColor.transparent)
        self._draw_fog(layer, state)
        self._cache[key] = layer
        self._cache.move_to_end(key)
        while len(self._cache) > self.max_cache_entries:
            self._cache.popitem(last=False)
        return layer

    def _draw_fog(self, layer: QPixmap, state: AuraVisualState) -> None:
        if state.kind is AuraKind.NONE or state.opacity <= 0.0 or state.intensity <= 0.0:
            return
        colors = _PALETTE[state.kind]
        speed, vertical_bias, drift_amplitude = _DRIFT[state.kind]
        ratio = max(1.0, float(layer.devicePixelRatio()))
        width = layer.width() / ratio
        height = layer.height() / ratio
        angle = state.phase / 12.0 * math.tau
        count = min(len(_FOG_SEEDS), 4 + int(round(state.intensity * 6.0)))
        painter = QPainter(layer)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        self._draw_bottom_ring(painter, layer, state)
        for seed in _FOG_SEEDS[:count]:
            dx = math.sin(angle * speed + seed.phase_offset) * width * drift_amplitude
            dy = math.cos(angle * (speed * 0.82) + seed.phase_offset) * height * drift_amplitude
            dy += height * vertical_bias * 0.15
            cx = width * seed.x + dx
            cy = height * seed.y + dy
            radius = max(2.0, min(width, height) * seed.radius)
            center_alpha = int(235.0 * state.opacity * (0.78 + 0.22 * state.intensity) * seed.weight)
            gradient = QRadialGradient(QPointF(cx, cy), radius)
            gradient.setColorAt(0.0, _with_alpha(colors[0], center_alpha))
            gradient.setColorAt(0.42, _with_alpha(colors[1], int(center_alpha * 0.42)))
            gradient.setColorAt(0.78, _with_alpha(colors[2], int(center_alpha * 0.12)))
            gradient.setColorAt(1.0, _with_alpha(colors[2], 0))
            painter.setBrush(gradient)
            painter.drawEllipse(QPointF(cx, cy), radius, radius * 0.76)
        painter.end()

    def _draw_bottom_ring(self, painter: QPainter, layer: QPixmap, state: AuraVisualState) -> None:
        """Draw the aura's visible lower-body glow/"light platform".

        A single outline looked like a UI shadow and disappeared behind the
        character mask.  The ring is therefore built from three soft layers:
        a broad radial haze, a denser inner glow, and a restrained colored
        band.  The compositing path later repeats the cached layer through a
        face-safe clip, so the ring remains visible without changing window
        geometry or hit testing.
        """

        if state.kind is AuraKind.NONE or state.opacity <= 0.0 or state.intensity <= 0.0:
            return
        colors = _PALETTE[state.kind]
        ring_y, brightness, drift = _RING_STYLE[state.kind]
        ratio = max(1.0, float(layer.devicePixelRatio()))
        width = layer.width() / ratio
        height = layer.height() / ratio
        angle = state.phase / 12.0 * math.tau
        breathe = 0.91 + 0.09 * math.sin(angle * 0.72 + 0.8)
        cx = width * 0.5 + math.sin(angle * 0.48 + 0.25) * width * 0.006
        cy = height * ring_y + math.cos(angle * 0.55 + 0.4) * height * 0.006
        rx = width * (0.34 + 0.045 * state.intensity)
        ry = max(2.0, height * (0.062 + 0.014 * state.intensity))
        alpha = int(238.0 * state.opacity * (0.78 + 0.22 * state.intensity) * brightness * breathe)

        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)

        # Wide atmospheric halo: this is what makes the effect feel like
        # misty light instead of a crisp ellipse.
        halo = QRadialGradient(QPointF(cx, cy), max(rx, ry) * 1.24)
        halo.setColorAt(0.0, _with_alpha(colors[1], int(alpha * 0.08)))
        halo.setColorAt(0.42, _with_alpha(colors[0], int(alpha * 0.22)))
        halo.setColorAt(0.66, _with_alpha(colors[1], int(alpha * 0.78)))
        halo.setColorAt(0.84, _with_alpha(colors[2], int(alpha * 0.28)))
        halo.setColorAt(1.0, _with_alpha(colors[2], 0))
        painter.setBrush(halo)
        painter.drawEllipse(QRectF(cx - rx * 1.20, cy - ry * 1.85, rx * 2.40, ry * 3.70))

        # A denser, slightly drifting inner haze gives the ring a luminous
        # center without turning it into a solid platform.
        inner_haze = QRadialGradient(QPointF(cx, cy), max(rx, ry) * 0.92)
        inner_haze.setColorAt(0.0, _with_alpha(colors[1], int(alpha * 0.30)))
        inner_haze.setColorAt(0.50, _with_alpha(colors[0], int(alpha * 0.45)))
        inner_haze.setColorAt(0.82, _with_alpha(colors[2], int(alpha * 0.12)))
        inner_haze.setColorAt(1.0, _with_alpha(colors[2], 0))
        painter.setBrush(inner_haze)
        painter.drawEllipse(QRectF(cx - rx * 0.98, cy - ry * 1.04, rx * 1.96, ry * 2.08))

        # Broad band with an even-odd hole.  It is intentionally thicker than
        # a line so the aura reads at a glance even on small pet frames.
        outer = QRectF(cx - rx * 0.88, cy - ry * 0.72, rx * 1.76, ry * 1.44)
        thickness = max(1.6, min(width, height) * (0.010 + 0.006 * state.intensity))
        inner = outer.adjusted(thickness, thickness * 0.58, -thickness, -thickness * 0.58)
        band = QPainterPath()
        band.setFillRule(Qt.FillRule.OddEvenFill)
        band.addEllipse(outer)
        band.addEllipse(inner)
        painter.setBrush(_with_alpha(colors[1], int(alpha * 0.64)))
        painter.drawPath(band)

        # A subtle lower highlight creates the flowing, racing-light feeling
        # while remaining a few pixels wide and cheap to render.
        highlight = QPainterPath()
        highlight.setFillRule(Qt.FillRule.OddEvenFill)
        highlight_outer = QRectF(
            cx - rx * 0.72,
            cy - ry * 0.34 + math.sin(angle * 0.8) * height * 0.004,
            rx * 1.44,
            ry * 0.68,
        )
        highlight_inner = highlight_outer.adjusted(
            thickness * 1.2,
            thickness * 0.50,
            -thickness * 1.2,
            -thickness * 0.50,
        )
        highlight.addEllipse(highlight_outer)
        highlight.addEllipse(highlight_inner)
        painter.setBrush(_with_alpha(colors[1], int(alpha * (0.22 + 0.10 * drift))))
        painter.drawPath(highlight)
        painter.restore()

    def _draw_particles(
        self,
        painter: QPainter,
        source: QPixmap,
        state: AuraVisualState,
        phase: float,
        strength: float,
    ) -> None:
        if state.kind is AuraKind.NONE or state.intensity <= 0.0 or strength <= 0.0:
            return
        ratio = max(1.0, float(source.devicePixelRatio()))
        width = source.width() / ratio
        height = source.height() / ratio
        count = min(len(_PARTICLE_SEEDS), int(round(_PARTICLE_COUNTS[state.kind] * state.intensity)))
        color = _PALETTE[state.kind][2]
        angle = phase / 12.0 * math.tau
        painter.setPen(Qt.PenStyle.NoPen)
        for seed in _PARTICLE_SEEDS[:count]:
            x = width * seed.x + math.sin(angle * 0.55 + seed.phase_offset) * width * 0.012
            y = height * seed.y + math.cos(angle * 0.45 + seed.phase_offset) * height * 0.010
            size = max(1.0, min(width, height) * seed.size * (0.8 + state.intensity * 0.35))
            alpha = int(150.0 * state.opacity * strength)
            halo = QRadialGradient(QPointF(x, y), size * 3.2)
            halo.setColorAt(0.0, _with_alpha(color, int(alpha * 0.42)))
            halo.setColorAt(0.45, _with_alpha(color, int(alpha * 0.16)))
            halo.setColorAt(1.0, _with_alpha(color, 0))
            painter.setBrush(halo)
            painter.drawEllipse(QPointF(x, y), size * 3.2, size * 3.2)
            painter.setBrush(_with_alpha(color, alpha))
            painter.drawEllipse(QPointF(x, y), size, size)
            if state.kind is AuraKind.RED:
                painter.setPen(_with_alpha(_PALETTE[state.kind][1], int(alpha * 0.72)))
                painter.drawLine(QPointF(x, y + size * 0.8), QPointF(x, y - size * 2.1))
                painter.setPen(Qt.PenStyle.NoPen)
            if state.kind is AuraKind.GOLD and size >= 1.0:
                painter.setPen(_with_alpha(_PALETTE[state.kind][1], int(alpha * 0.72)))
                painter.drawLine(QPointF(x - size * 1.8, y), QPointF(x + size * 1.8, y))
                painter.drawLine(QPointF(x, y - size * 1.8), QPointF(x, y + size * 1.8))
                painter.setPen(Qt.PenStyle.NoPen)

    @staticmethod
    def _scaled_layer(layer: QPixmap, source: QPixmap) -> QPixmap:
        if layer.size() == source.size() and layer.devicePixelRatio() == source.devicePixelRatio():
            return layer
        ratio = max(1.0, float(source.devicePixelRatio()))
        scaled = layer.scaled(source.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(ratio)
        return scaled

    @staticmethod
    def _draw_visible_inner_mist(
        painter: QPainter,
        source: QPixmap,
        layer: QPixmap,
        strength: float,
    ) -> None:
        """Add a soft peripheral glow that survives the native window mask.

        The desktop pet deliberately retains a character-only QWidget mask so
        transparent Aura pixels never enlarge its click target.  A background
        layer alone is therefore clipped by the operating system.  Repainting
        a restrained portion of the same fog over the body's outer/lower area
        makes the Aura visible without changing window geometry or hit tests.
        The approximate face region stays untouched.
        """

        strength = clamp01(strength)
        if strength <= 0.0:
            return
        ratio = max(1.0, float(source.devicePixelRatio()))
        width = source.width() / ratio
        height = source.height() / ratio
        visible_area = QPainterPath()
        visible_area.addRect(QRectF(0.0, 0.0, width, height))
        face_safe = QPainterPath()
        face_safe.addRoundedRect(
            QRectF(width * 0.29, height * 0.07, width * 0.42, height * 0.40),
            width * 0.08,
            width * 0.08,
        )
        painter.save()
        painter.setClipPath(visible_area.subtracted(face_safe))
        # Source-over preserves the selected hue on yellow/red outfits much
        # better than Screen, which mostly turned the old Aura into an almost
        # imperceptible pale highlight.
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setOpacity(0.94 * strength)
        painter.drawPixmap(0, 0, layer)
        painter.restore()

    def render_transition(
        self,
        source: QPixmap,
        from_state: AuraVisualState,
        to_state: AuraVisualState,
        progress: float,
        *,
        phase: float | None = None,
    ) -> QPixmap:
        """Composite fog behind ``source`` and a few particles in front."""

        if source.isNull():
            return source
        from_state = AuraVisualState(from_state.kind, from_state.intensity, from_state.opacity, phase if phase is not None else from_state.phase)
        to_state = AuraVisualState(to_state.kind, to_state.intensity, to_state.opacity, phase if phase is not None else to_state.phase)
        progress = clamp01(progress)
        if (
            from_state.kind is AuraKind.NONE
            and to_state.kind is AuraKind.NONE
            or (progress >= 1.0 and to_state.opacity <= 0.0)
        ):
            return source

        output = QPixmap(source.size())
        output.setDevicePixelRatio(source.devicePixelRatio())
        output.fill(Qt.GlobalColor.transparent)
        painter = QPainter(output)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if from_state.kind is not AuraKind.NONE and from_state.opacity > 0.0 and progress < 1.0:
            painter.setOpacity(1.0 - progress)
            painter.drawPixmap(0, 0, self._scaled_layer(self._layer_for(source, from_state), source))
        if to_state.kind is not AuraKind.NONE and to_state.opacity > 0.0 and progress > 0.0:
            painter.setOpacity(progress)
            painter.drawPixmap(0, 0, self._scaled_layer(self._layer_for(source, to_state), source))
        painter.setOpacity(1.0)
        painter.drawPixmap(0, 0, source)
        # QWidget.setMask() clips background fog outside the character.  Keep
        # that input-safe mask, and add a face-safe inner glow so the effect is
        # still unmistakably visible on Windows and macOS.
        if from_state.kind is not AuraKind.NONE and from_state.opacity > 0.0 and progress < 1.0:
            self._draw_visible_inner_mist(
                painter,
                source,
                self._scaled_layer(self._layer_for(source, from_state), source),
                1.0 - progress,
            )
        if to_state.kind is not AuraKind.NONE and to_state.opacity > 0.0 and progress > 0.0:
            self._draw_visible_inner_mist(
                painter,
                source,
                self._scaled_layer(self._layer_for(source, to_state), source),
                progress,
            )
        self._draw_particles(painter, output, to_state, phase if phase is not None else to_state.phase, progress)
        painter.end()
        return output

    def render(self, source: QPixmap, state: AuraVisualState, *, phase: float | None = None) -> QPixmap:
        state = AuraVisualState(state.kind, state.intensity, state.opacity, phase if phase is not None else state.phase)
        if state.kind is AuraKind.NONE or state.intensity <= 0.0 or state.opacity <= 0.0:
            return source
        return self.render_transition(source, AuraVisualState(phase=state.phase), state, 1.0, phase=phase)


_DEFAULT_RENDERER = AuraRenderer()


def draw_aura_effect(
    pixmap: QPixmap,
    state: AuraVisualState,
    *,
    phase: float | None = None,
) -> QPixmap:
    return _DEFAULT_RENDERER.render(pixmap, state, phase=phase)


def draw_aura_transition_effect(
    pixmap: QPixmap,
    from_state: AuraVisualState,
    to_state: AuraVisualState,
    progress: float,
    *,
    phase: float | None = None,
) -> QPixmap:
    return _DEFAULT_RENDERER.render_transition(pixmap, from_state, to_state, progress, phase=phase)


def default_renderer() -> AuraRenderer:
    return _DEFAULT_RENDERER
