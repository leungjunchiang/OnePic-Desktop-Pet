from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.aura_effects import (
    AURA_MODE_AUTO,
    AURA_MODE_MANUAL,
    AURA_MODE_OFF,
    AuraKind,
    AuraRenderer,
    AuraTransitionController,
    AuraVisualState,
    resolve_aura_state,
    resolve_auto_aura,
)
from onepic_desktop_pet.behavior import PetState
from onepic_desktop_pet.emotion_effects import draw_emotion_effect


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _source(width: int = 160, height: int = 160, dpr: float = 1.0) -> QPixmap:
    _app()
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def test_none_and_null_are_safe_noops() -> None:
    renderer = AuraRenderer()
    source = _source()
    assert renderer.render(source, AuraVisualState()) is source
    null = QPixmap()
    assert renderer.render(null, AuraVisualState(AuraKind.RED, 1, 1)) is null


def test_all_kinds_render_without_mutating_source() -> None:
    source = _source()
    before = source.toImage()
    renderer = AuraRenderer()
    for kind in (AuraKind.RED, AuraKind.GOLD, AuraKind.BLUE, AuraKind.PURPLE):
        result = renderer.render(source, AuraVisualState(kind, 0.6, 0.35), phase=3)
        assert not result.isNull()
        assert result.size() == source.size()
        assert result.devicePixelRatio() == source.devicePixelRatio()
    assert source.toImage() == before


def test_visual_state_and_resolver_clamp_inputs() -> None:
    state = AuraVisualState("red", -4, 999, "bad")
    assert state.kind is AuraKind.RED
    assert state.intensity == 0.0
    assert state.opacity == 1.0
    assert state.phase == 0.0
    assert resolve_auto_aura(PetState.ANNOYED).kind is AuraKind.RED
    assert resolve_aura_state(PetState.IDLE, AURA_MODE_OFF).kind is AuraKind.NONE
    assert resolve_aura_state(PetState.IDLE, AURA_MODE_MANUAL, "purple").kind is AuraKind.PURPLE
    assert resolve_aura_state(PetState.CURIOUS, AURA_MODE_AUTO).kind is AuraKind.PURPLE


def test_transition_controller_does_not_restart_for_phase_updates() -> None:
    controller = AuraTransitionController()
    controller.set_target(AuraVisualState(AuraKind.BLUE, 0.5, 0.3, 0))
    controller.tick()
    progress = controller.progress
    controller.set_target(AuraVisualState(AuraKind.BLUE, 0.5, 0.3, 7))
    assert controller.progress == progress
    controller.set_target(AuraVisualState(AuraKind.RED, 0.6, 0.4, 7))
    assert controller.progress == 0.0
    assert controller.needs_animation


def test_cache_hits_and_bounded_size_across_dpr_and_resize() -> None:
    renderer = AuraRenderer(max_cache_entries=8)
    for index in range(30):
        source = _source(120 + index * 7, 140 + index * 5, 1.0 if index % 2 else 2.0)
        renderer.render(source, AuraVisualState(AuraKind.BLUE, 0.5, 0.35), phase=index)
    assert renderer.cache_size <= 8
    source = _source(160, 160, 1.25)
    state = AuraVisualState(AuraKind.GOLD, 0.5, 0.35, 4)
    renderer.render(source, state)
    renderer.render(source, state)
    assert renderer.cache_hits >= 1


def test_renderer_has_no_qtimer_or_qthread_dependency() -> None:
    import onepic_desktop_pet.aura_effects as aura_effects

    assert not hasattr(aura_effects, "QTimer")
    assert not hasattr(aura_effects, "QThread")


def test_aura_and_emotion_layers_can_be_composed() -> None:
    source = _source()
    renderer = AuraRenderer()
    aura = renderer.render(source, AuraVisualState(AuraKind.RED, 0.65, 0.4), phase=2)
    composed = draw_emotion_effect(aura, PetState.ANNOYED, phase=2)
    assert not aura.isNull()
    assert not composed.isNull()
    assert composed.size() == source.size()


def test_aura_remains_visible_inside_character_only_window_mask() -> None:
    """The native character mask must not clip every visible Aura pixel."""

    source = _source(180, 180)
    painter = QPainter(source)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#f7cf43"))
    painter.drawEllipse(30, 20, 120, 150)
    painter.end()

    renderer = AuraRenderer()
    result = renderer.render(
        source,
        AuraVisualState(AuraKind.PURPLE, 0.68, 0.58),
        phase=3,
    )
    before = source.toImage()
    after = result.toImage()
    changed_inside_character = 0
    for y in range(before.height()):
        for x in range(before.width()):
            original = before.pixelColor(x, y)
            if original.alpha() and after.pixelColor(x, y) != original:
                changed_inside_character += 1
    assert changed_inside_character > 100
