from __future__ import annotations

from onepic_desktop_pet.behavior import PetState
from onepic_desktop_pet.state_effects import (
    EffectPhase,
    LocalEffectKind,
    LocalEffectManager,
    resolve_state_effect,
)


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _manager(clock: Clock, events: list[tuple]) -> LocalEffectManager:
    return LocalEffectManager(
        on_start=lambda kind: events.append(("start", kind)),
        on_switch=lambda kind: events.append(("switch", kind)),
        on_sustain=lambda kind: events.append(("sustain", kind)),
        on_release=lambda kind, duration: events.append(("release", kind, duration)),
        on_stop=lambda: events.append(("stop",)),
        now=clock,
    )


def test_pet_state_resolver_only_maps_explicit_emotions() -> None:
    assert resolve_state_effect(PetState.ANNOYED) is LocalEffectKind.RED
    assert resolve_state_effect(PetState.HAPPY) is LocalEffectKind.GOLD
    assert resolve_state_effect(PetState.SLEEPY) is LocalEffectKind.BLUE
    assert resolve_state_effect(PetState.CURIOUS) is LocalEffectKind.PURPLE
    assert resolve_state_effect(PetState.IDLE) is LocalEffectKind.NONE
    assert resolve_state_effect(PetState.WALK) is LocalEffectKind.NONE


def test_short_happy_state_obeys_minimum_hold_then_release_grace() -> None:
    clock = Clock()
    events: list[tuple] = []
    manager = _manager(clock, events)
    manager.request(LocalEffectKind.GOLD)
    clock.advance(0.22)
    manager.tick()
    assert manager.current_kind is LocalEffectKind.GOLD
    assert events == [("start", LocalEffectKind.GOLD)]

    manager.request(LocalEffectKind.NONE)
    clock.advance(2.0)
    manager.tick()
    assert not any(event[0] == "release" for event in events)
    clock.advance(1.0)
    manager.tick()
    assert any(event[0] == "release" for event in events)


def test_long_sleepy_state_enters_once_then_sustains() -> None:
    clock = Clock()
    events: list[tuple] = []
    manager = _manager(clock, events)
    manager.request(LocalEffectKind.BLUE)
    clock.advance(0.22)
    manager.tick()
    clock.advance(0.9)
    manager.tick()
    clock.advance(20.0)
    manager.tick()
    assert [event[0] for event in events].count("start") == 1
    assert [event[0] for event in events].count("sustain") == 1
    assert manager.phase is EffectPhase.SUSTAINING


def test_red_high_priority_interrupts_blue_with_crossfade_command() -> None:
    clock = Clock()
    events: list[tuple] = []
    manager = _manager(clock, events)
    manager.request(LocalEffectKind.BLUE)
    clock.advance(0.22)
    manager.tick()
    manager.request(LocalEffectKind.RED)
    assert manager.current_kind is LocalEffectKind.RED
    assert events[-1] == ("switch", LocalEffectKind.RED)


def test_short_none_jitter_does_not_flash_blue_off_and_on() -> None:
    clock = Clock()
    events: list[tuple] = []
    manager = _manager(clock, events)
    manager.request(LocalEffectKind.BLUE)
    clock.advance(0.22)
    manager.tick()
    manager.request(LocalEffectKind.NONE)
    clock.advance(0.1)
    manager.request(LocalEffectKind.BLUE)
    manager.tick()
    assert manager.current_kind is LocalEffectKind.BLUE
    assert not any(event[0] == "release" for event in events)
