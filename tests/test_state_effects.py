from __future__ import annotations

from onepic_desktop_pet.behavior import PetState
from onepic_desktop_pet.state_effects import (
    EffectPhase,
    LocalEffectKind,
    LocalEffectManager,
    MANIA_SEQUENCE,
    MANIA_SLOT_MS,
    MANIA_TOTAL_MS,
    resolve_event_effect,
    resolve_work_effect,
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


def test_random_pet_states_do_not_trigger_colors() -> None:
    assert resolve_state_effect(PetState.ANNOYED) is LocalEffectKind.NONE
    assert resolve_state_effect(PetState.HAPPY) is LocalEffectKind.NONE
    assert resolve_state_effect(PetState.SLEEPY) is LocalEffectKind.NONE
    assert resolve_state_effect(PetState.CURIOUS) is LocalEffectKind.NONE
    assert resolve_state_effect(PetState.IDLE) is LocalEffectKind.NONE
    assert resolve_state_effect(PetState.WALK) is LocalEffectKind.NONE


def test_explicit_work_and_event_resolvers_are_stable_and_explainable() -> None:
    assert resolve_work_effect("focus") is LocalEffectKind.BLUE
    assert resolve_work_effect("rest") is LocalEffectKind.GREEN
    assert resolve_work_effect("idle") is LocalEffectKind.NONE
    assert resolve_event_effect("annoyed") is LocalEffectKind.RED
    assert resolve_event_effect("reward") is LocalEffectKind.GOLD
    assert resolve_event_effect("playful") is LocalEffectKind.CYAN
    assert resolve_event_effect("thinking") is LocalEffectKind.PURPLE


def test_short_happy_state_obeys_minimum_hold_then_release_grace() -> None:
    clock = Clock()
    events: list[tuple] = []
    manager = _manager(clock, events)
    manager.request(LocalEffectKind.GOLD)
    clock.advance(0.32)
    manager.tick()
    assert manager.current_kind is LocalEffectKind.GOLD
    assert events == [("start", LocalEffectKind.GOLD)]

    manager.request(LocalEffectKind.NONE)
    clock.advance(2.0)
    manager.tick()
    assert not any(event[0] == "release" for event in events)
    clock.advance(8.0)
    manager.tick()
    assert any(event[0] == "release" for event in events)


def test_long_sleepy_state_enters_once_then_sustains() -> None:
    clock = Clock()
    events: list[tuple] = []
    manager = _manager(clock, events)
    manager.request(LocalEffectKind.BLUE)
    clock.advance(0.32)
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
    clock.advance(0.32)
    manager.tick()
    manager.request(LocalEffectKind.RED)
    assert manager.current_kind is LocalEffectKind.RED
    assert events[-1] == ("switch", LocalEffectKind.RED)


def test_short_none_jitter_does_not_flash_blue_off_and_on() -> None:
    clock = Clock()
    events: list[tuple] = []
    manager = _manager(clock, events)
    manager.request(LocalEffectKind.BLUE)
    clock.advance(0.32)
    manager.tick()
    manager.request(LocalEffectKind.NONE)
    clock.advance(0.1)
    manager.request(LocalEffectKind.BLUE)
    manager.tick()
    assert manager.current_kind is LocalEffectKind.BLUE
    assert not any(event[0] == "release" for event in events)


def test_mania_sequence_has_six_three_second_slots_for_ten_cycles() -> None:
    clock = Clock()
    events: list[tuple] = []
    manager = _manager(clock, events)
    assert manager.start_mania()
    assert manager.mania_active is True
    assert manager.current_kind is LocalEffectKind.RED
    assert MANIA_TOTAL_MS == 180_000
    assert len(MANIA_SEQUENCE) == 6
    for index, expected in enumerate(MANIA_SEQUENCE):
        clock.value = index * MANIA_SLOT_MS / 1000.0 + 0.01
        manager.tick()
        assert manager.current_kind is expected
    clock.value = MANIA_TOTAL_MS / 1000.0 + 0.01
    manager.tick()
    assert manager.mania_active is False


def test_mania_retrigger_reuses_policy_and_does_not_add_an_overlay_callback() -> None:
    clock = Clock()
    events: list[tuple] = []
    manager = _manager(clock, events)
    manager.start_mania()
    starts = len([event for event in events if event[0] == "start"])
    manager.start_mania()
    assert manager.mania_active is True
    assert len([event for event in events if event[0] == "start"]) == starts
    assert len([event for event in events if event[0] == "switch"]) == 1
