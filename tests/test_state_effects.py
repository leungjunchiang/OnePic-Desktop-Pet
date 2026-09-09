from __future__ import annotations

from onepic_desktop_pet.behavior import PetState
from onepic_desktop_pet.state_effects import (
    EffectPhase,
    LocalEffectKind,
    LocalEffectManager,
    COLOR_MIST_SEQUENCE,
    COLOR_MIST_CROSSFADE_MS,
    COLOR_MIST_CYCLE_MS,
    COLOR_MIST_SLOT_MS,
    COLOR_MIST_TOTAL_MS,
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
    assert resolve_work_effect("focus", focus_blue_enabled=False) is LocalEffectKind.NONE
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


def test_color_mist_world_sequence_has_six_three_second_slots_for_ten_cycles() -> None:
    clock = Clock()
    events: list[tuple] = []
    manager = _manager(clock, events)
    assert manager.start_color_mist_world()
    assert manager.color_mist_world_active is True
    assert manager.current_kind is LocalEffectKind.RED
    assert COLOR_MIST_TOTAL_MS == 180_000
    assert len(COLOR_MIST_SEQUENCE) == 6
    for index, expected in enumerate(COLOR_MIST_SEQUENCE):
        clock.value = index * COLOR_MIST_SLOT_MS / 1000.0 + 0.01
        manager.tick()
        assert manager.current_kind is expected
    clock.value = COLOR_MIST_TOTAL_MS / 1000.0 + 0.01
    manager.tick()
    assert manager.color_mist_world_active is False


def test_color_mist_world_timeline_crossfades_without_entering_none() -> None:
    clock = Clock()
    frames: list[tuple] = []
    manager = LocalEffectManager(
        on_start=lambda _kind: None,
        on_switch=lambda _kind: None,
        on_sustain=lambda _kind: None,
        on_release=lambda _kind, _duration: None,
        on_stop=lambda: None,
        on_color_mist_world_frame=lambda current, following, mix, phase: frames.append(
            (current, following, mix, phase)
        ),
        now=clock,
    )
    manager.start_color_mist_world()
    clock.value = (COLOR_MIST_SLOT_MS - COLOR_MIST_CROSSFADE_MS / 2) / 1000.0
    manager.tick()
    current, following, mix, _phase = frames[-1]
    assert current is LocalEffectKind.RED
    assert following is LocalEffectKind.GOLD
    assert 0.0 < mix < 1.0
    clock.value = COLOR_MIST_SLOT_MS / 1000.0
    manager.tick()
    assert frames[-1][0] is LocalEffectKind.GOLD
    assert frames[-1][2] == 0.0
    assert manager.current_kind is LocalEffectKind.GOLD


def test_color_mist_world_retrigger_extends_deadline_without_restarting_timeline() -> None:
    clock = Clock()
    events: list[tuple] = []
    manager = _manager(clock, events)
    manager.start_color_mist_world()
    starts = len([event for event in events if event[0] == "start"])
    clock.value = 2.7
    manager.tick()
    kind_before = manager.current_kind
    manager.start_color_mist_world()
    assert manager.color_mist_world_active is True
    assert len([event for event in events if event[0] == "start"]) == starts
    assert len([event for event in events if event[0] == "switch"]) == 0
    assert manager.current_kind is kind_before
    clock.value = 2.7 + COLOR_MIST_TOTAL_MS / 1000.0 - 0.01
    manager.tick()
    assert manager.color_mist_world_active is True
    assert COLOR_MIST_CYCLE_MS == 18_000


def test_color_mist_world_toggle_stops_without_starting_a_second_session() -> None:
    clock = Clock()
    events: list[tuple] = []
    manager = _manager(clock, events)

    assert manager.toggle_color_mist_world() is True
    assert manager.color_mist_world_active is True
    clock.value = 4.0
    manager.tick()
    assert manager.toggle_color_mist_world() is False
    assert manager.color_mist_world_active is False


def test_color_mist_world_restores_the_latest_background_state_not_the_entry_snapshot() -> None:
    clock = Clock()
    resumed: list[LocalEffectKind] = []
    manager = LocalEffectManager(
        on_start=lambda _kind: None,
        on_switch=lambda _kind: None,
        on_sustain=lambda _kind: None,
        on_release=lambda _kind, _duration: None,
        on_stop=lambda: None,
        on_color_mist_world_resume=lambda kind: resumed.append(kind),
        now=clock,
    )
    manager.start_color_mist_world()
    manager.request_state(LocalEffectKind.BLUE)
    clock.value = COLOR_MIST_TOTAL_MS / 1000.0 + 0.01
    manager.tick()
    assert manager.color_mist_world_active is False
    assert resumed == [LocalEffectKind.BLUE]
    assert manager.current_kind is LocalEffectKind.BLUE
