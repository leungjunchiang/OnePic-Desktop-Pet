"""Failure-injection tests for the local FocusSegment durability boundary."""

from datetime import datetime, timedelta, timezone

import pytest

from onepic_desktop_pet.focus_analytics import FocusAnalyticsStore
from onepic_desktop_pet.focus_durability import (
    FocusRecoveryJournal,
)
from onepic_desktop_pet.focus_segments import FocusSegment


BEIJING = timezone(timedelta(hours=8), "Asia/Shanghai")


def _segment(segment_id: str = "seg-a") -> FocusSegment:
    start = datetime(2026, 9, 9, 9, 0, tzinfo=BEIJING)
    return FocusSegment(
        segment_id=segment_id,
        session_id="session-a",
        device_id="device-a",
        start_at=start,
        end_at=start + timedelta(minutes=30),
    )


def test_journal_replays_one_sealed_segment_and_is_idempotent(tmp_path) -> None:
    journal = FocusRecoveryJournal(tmp_path)
    segment = _segment()
    journal.append_segment(segment, reason="manual")
    journal.append_segment(segment, reason="retry")

    recovered = journal.recover(())
    assert recovered.conflicts == ()
    assert recovered.segments == (segment,)


def test_journal_reports_same_id_different_payload_conflict(tmp_path) -> None:
    journal = FocusRecoveryJournal(tmp_path)
    journal.append_segment(_segment(), reason="manual")
    changed = _segment()
    changed = FocusSegment(
        segment_id=changed.segment_id,
        session_id=changed.session_id,
        device_id=changed.device_id,
        start_at=changed.start_at,
        end_at=changed.end_at + timedelta(minutes=1),
    )

    with pytest.raises(ValueError, match="conflicting payload"):
        journal.append_segment(changed, reason="conflict")

    recovered = journal.recover(())
    assert recovered.segments == (_segment(),)


def test_store_restart_replays_wal_after_store_write_failure(tmp_path) -> None:
    path = tmp_path / "focus.json"
    now = datetime(2026, 9, 9, 12, 0, tzinfo=BEIJING)
    store = FocusAnalyticsStore(path=path, now_provider=lambda: now, persist=True)
    def fail_store_save() -> None:
        raise OSError("simulated local store failure")

    store._save = fail_store_save  # type: ignore[method-assign]
    with pytest.raises(OSError):
        store.record_session(
            30 * 60,
            started_at=datetime(2026, 9, 9, 9, 0, tzinfo=BEIJING),
            record_id="device-a:session-a:1800",
            session_id="session-a",
            device_id="device-a",
        )

    # A failed Store write must roll back the in-memory projection too; only
    # the WAL remains, so a caller cannot accidentally pause twice against a
    # phantom duplicate.
    assert store.focus_segments() == []

    # The WAL survives even though the normal JSON store did not.
    assert (tmp_path / "focus_recovery.jsonl").is_file()
    recovered = FocusAnalyticsStore(path=path, now_provider=lambda: now, persist=True)
    assert [item.segment_id for item in recovered.focus_segments()] == [
        "device-a:session-a:1800"
    ]
    assert recovered.period_summary("day", now)["total_seconds"] == 30 * 60


def test_focus_handoff_marker_clears_only_after_explicit_upload_ack(tmp_path) -> None:
    path = tmp_path / "focus.json"
    store = FocusAnalyticsStore(
        path=path,
        now_provider=lambda: datetime(2026, 9, 9, 12, 0, tzinfo=BEIJING),
        persist=True,
        device_id="device-a",
    )
    store.record_session(
        30 * 60,
        started_at=datetime(2026, 9, 9, 9, 0, tzinfo=BEIJING),
        record_id="device-a:session-a:1800",
        session_id="session-a",
    )
    assert store.has_pending_focus_handoff()
    payload = store.focus_segments_payload()
    assert [item["segment_id"] for item in payload] == ["device-a:session-a:1800"]
    assert store.acknowledge_focus_segments_upload(payload)
    assert not store.has_pending_focus_handoff()
