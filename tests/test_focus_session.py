"""FocusSessionManager is the only timer source used by the study room."""

from datetime import datetime

from onepic_desktop_pet.focus_session import FocusSessionManager
from onepic_desktop_pet.work_timer import WorkTimerModel


def test_focus_session_snapshot_tracks_existing_work_timer(tmp_path) -> None:
    timer = WorkTimerModel(path=tmp_path / "timer.json")
    manager = FocusSessionManager(timer)

    assert manager.snapshot().status == "idle"
    assert manager.start() is True
    snapshot = manager.snapshot()
    assert snapshot.status == "focus"
    assert snapshot.is_running
    assert snapshot.session_started_at is not None

    assert manager.pause() is True
    assert manager.snapshot().status == "rest"
    assert manager.snapshot().state == "paused_manual"
    assert manager.finish() == manager.snapshot().today_seconds


def test_focus_session_snapshot_can_use_reconciled_day_projection(tmp_path) -> None:
    """All consumers can share a corrected calendar-day total."""

    timer = WorkTimerModel(path=tmp_path / "timer.json")
    manager = FocusSessionManager(timer)
    manager.set_today_seconds_provider(lambda: 1234)

    assert manager.snapshot().today_seconds == 1234
    assert manager.start() is True
    assert manager.snapshot().today_seconds == 1234
