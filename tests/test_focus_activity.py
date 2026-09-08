from datetime import datetime, timedelta, timezone

from onepic_desktop_pet.focus_activity import (
    AUTO_PAUSE_DISPLAY_OFF,
    AUTO_PAUSE_IDLE,
    AUTO_PAUSE_LOCK,
    AUTO_PAUSE_SLEEP,
    FocusActivityGuard,
)
from onepic_desktop_pet.input_activity import InputIdleSnapshot


class FakeClock:
    def __init__(self) -> None:
        self.wall = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
        self.mono = 100.0

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.mono += seconds


def _guard(clock, idle=0.0, session=None):
    session_state = dict(session or {})
    return FocusActivityGuard(
        idle_provider=lambda: InputIdleSnapshot(
            idle,
            True,
            "fake",
        ),
        session_provider=lambda: dict(session_state),
        now_provider=lambda: clock.wall,
        monotonic_provider=lambda: clock.mono,
    )


def test_idle_below_threshold_does_not_pause() -> None:
    clock = FakeClock()
    guard = _guard(clock, idle=599)
    assert guard.poll(working=True, auto_pause_on_idle=True, idle_threshold_seconds=600) is None


def test_idle_threshold_uses_effective_cutoff_not_discovery_time() -> None:
    clock = FakeClock()
    guard = _guard(clock, idle=607)
    decision = guard.poll(
        working=True,
        auto_pause_on_idle=True,
        idle_threshold_seconds=600,
    )
    assert decision is not None
    assert decision.reason == AUTO_PAUSE_IDLE
    assert decision.effective_at == clock.wall - timedelta(seconds=7)


def test_unavailable_idle_probe_is_not_treated_as_recent_input() -> None:
    clock = FakeClock()
    guard = FocusActivityGuard(
        idle_provider=lambda: InputIdleSnapshot(None, False, "fake", "failed"),
        session_provider=lambda: {},
        now_provider=lambda: clock.wall,
        monotonic_provider=lambda: clock.mono,
    )
    assert guard.poll(working=True, auto_pause_on_idle=True, idle_threshold_seconds=600) is None
    assert guard.last_snapshot.available is False
    assert guard.last_snapshot.idle_seconds is None


def test_lock_display_off_and_sleep_events_pause_immediately() -> None:
    clock = FakeClock()
    guard = _guard(clock)
    for event, reason in (
        ("lock", AUTO_PAUSE_LOCK),
        ("display_off", AUTO_PAUSE_DISPLAY_OFF),
        ("suspend", AUTO_PAUSE_SLEEP),
    ):
        decision = guard.handle_event(event, working=True, at=clock.wall)
        assert decision is not None
        assert decision.reason == reason
        assert decision.effective_at == clock.wall


def test_resume_event_never_requests_auto_resume() -> None:
    clock = FakeClock()
    guard = _guard(clock)
    assert guard.handle_event("resume", working=False, at=clock.wall) is None
    assert guard.handle_event("unlock", working=False, at=clock.wall) is None


def test_long_wall_clock_gap_is_conservatively_sealed_at_last_probe() -> None:
    clock = FakeClock()
    guard = _guard(clock, idle=0)
    assert guard.poll(working=True, auto_pause_on_idle=True, idle_threshold_seconds=600) is None
    previous = clock.wall
    clock.wall += timedelta(hours=1)
    decision = guard.poll(working=True, auto_pause_on_idle=True, idle_threshold_seconds=600)
    assert decision is not None
    assert decision.reason == AUTO_PAUSE_SLEEP
    assert decision.effective_at == previous
