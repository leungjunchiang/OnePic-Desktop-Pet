import json
from datetime import datetime, timedelta, timezone

from onepic_desktop_pet.focus_analytics import AccountFocusStore
from onepic_desktop_pet.focus_history_recovery import FocusHistoryRecovery
from onepic_desktop_pet.focus_segments import deterministic_focus_segment_id


BEIJING = timezone(timedelta(hours=8), "Asia/Shanghai")


def _scanner(tmp_path, store, now):
    account_dir = tmp_path / "account-a"
    account_dir.mkdir(exist_ok=True)
    return FocusHistoryRecovery(
        store,
        account_id="account-a",
        device_id="device-a",
        account_dir=account_dir,
        diagnostics_dir=tmp_path / "diagnostics",
        now_provider=lambda: now,
    )


def test_exact_work_session_recovers_through_pending_pipeline_and_is_idempotent(tmp_path):
    now = datetime(2026, 9, 10, 12, 0, tzinfo=BEIJING)
    (tmp_path / "account-a").mkdir()
    store = AccountFocusStore(
        path=tmp_path / "account-a" / "focus_analytics.json",
        now_provider=lambda: now,
        persist=True,
        device_id="device-a",
    )
    start = now - timedelta(hours=2)
    end = start + timedelta(minutes=40)
    (tmp_path / "account-a" / "work_sessions.json").write_text(
        json.dumps([
            {
                "id": "old-session-a",
                "started_at": start.isoformat(),
                "ended_at": end.isoformat(),
                "seconds": 40 * 60,
                "completed": True,
            }
        ]),
        encoding="utf-8",
    )

    first = _scanner(tmp_path, store, now).run(force=True)
    assert first.recovered == 1
    payload = store.focus_segments_payload()
    assert len(payload) == 1
    assert payload[0]["start_at"] == start.isoformat()
    assert payload[0]["end_at"] == end.isoformat()
    assert store.period_summary("day", now)["total_seconds"] == 40 * 60

    second = _scanner(tmp_path, store, now).run(force=True)
    assert second.recovered == 0
    assert second.duplicates == 1
    assert len(store.focus_segments()) == 1


def test_scalar_only_history_is_logged_but_never_becomes_segment(tmp_path):
    now = datetime(2026, 9, 10, 12, 0, tzinfo=BEIJING)
    account_dir = tmp_path / "account-a"
    account_dir.mkdir()
    (account_dir / "work_timer.json").write_text(
        json.dumps({"accumulated_seconds": 17939, "lifetime_seconds": 99999}),
        encoding="utf-8",
    )
    store = AccountFocusStore(
        path=account_dir / "focus_analytics.json",
        now_provider=lambda: now,
        persist=True,
        device_id="device-a",
    )

    report = _scanner(tmp_path, store, now).run(force=True)
    assert report.recovered == 0
    assert report.skip_reasons["scalar_without_interval"] == 2
    assert store.focus_segments() == []


def test_lifecycle_exact_interval_recovers_only_for_current_device(tmp_path):
    now = datetime(2026, 9, 10, 12, 0, tzinfo=BEIJING)
    account_dir = tmp_path / "account-a"
    diagnostics = tmp_path / "diagnostics"
    account_dir.mkdir()
    diagnostics.mkdir()
    start = now - timedelta(minutes=20)
    end = now - timedelta(minutes=10)
    current_id = deterministic_focus_segment_id("device-a", "session-a", start, end)
    rows = [
        {
            "event": "focus.segment.sealed",
            "device_id": "device-a",
            "session_id": "session-a",
            "segment_id": current_id,
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
        },
        {
            "event": "focus.segment.sealed",
            "device_id": "device-b",
            "session_id": "session-b",
            "segment_id": "other-account",
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
        },
    ]
    (diagnostics / "lifecycle.log").write_text(
        "\n".join(json.dumps(row) for row in rows), encoding="utf-8"
    )
    store = AccountFocusStore(
        path=account_dir / "focus_analytics.json",
        now_provider=lambda: now,
        persist=True,
        device_id="device-a",
    )

    report = _scanner(tmp_path, store, now).run(force=True)
    assert report.recovered == 1
    assert report.skip_reasons["lifecycle_other_account_or_device"] == 1
    assert [item.segment_id for item in store.focus_segments()] == [current_id]


def test_automatic_scan_runs_once_but_pending_upload_survives_restart(tmp_path):
    now = datetime(2026, 9, 10, 12, 0, tzinfo=BEIJING)
    account_dir = tmp_path / "account-a"
    account_dir.mkdir()
    start = now - timedelta(minutes=8)
    end = now
    (account_dir / "work_sessions.json").write_text(
        json.dumps([{
            "id": "offline-a",
            "started_at": start.isoformat(),
            "ended_at": end.isoformat(),
            "seconds": 8 * 60,
        }]),
        encoding="utf-8",
    )
    store = AccountFocusStore(
        path=account_dir / "focus_analytics.json",
        now_provider=lambda: now,
        persist=True,
        device_id="device-a",
    )
    first = _scanner(tmp_path, store, now).run()
    assert first.recovered == 1

    reloaded = AccountFocusStore(
        path=account_dir / "focus_analytics.json",
        now_provider=lambda: now,
        persist=True,
        device_id="device-a",
    )
    second = _scanner(tmp_path, reloaded, now).run()
    assert second.already_checked
    assert len(reloaded.focus_segments_payload()) == 1


def test_automatic_scan_rechecks_when_exact_history_source_changes(tmp_path):
    now = datetime(2026, 9, 10, 12, 0, tzinfo=BEIJING)
    account_dir = tmp_path / "account-a"
    account_dir.mkdir()
    first_start = now - timedelta(minutes=30)
    first_end = now - timedelta(minutes=20)
    second_start = now - timedelta(minutes=15)
    second_end = now - timedelta(minutes=5)
    history_path = account_dir / "work_sessions.json"
    history_path.write_text(
        json.dumps([{
            "id": "offline-a",
            "started_at": first_start.isoformat(),
            "ended_at": first_end.isoformat(),
            "seconds": 10 * 60,
        }]),
        encoding="utf-8",
    )
    store = AccountFocusStore(
        path=account_dir / "focus_analytics.json",
        now_provider=lambda: now,
        persist=True,
        device_id="device-a",
    )

    first = _scanner(tmp_path, store, now).run()
    assert first.recovered == 1

    history_path.write_text(
        json.dumps([
            {
                "id": "offline-a",
                "started_at": first_start.isoformat(),
                "ended_at": first_end.isoformat(),
                "seconds": 10 * 60,
            },
            {
                "id": "offline-b",
                "started_at": second_start.isoformat(),
                "ended_at": second_end.isoformat(),
                "seconds": 10 * 60,
            },
        ]),
        encoding="utf-8",
    )
    second = _scanner(tmp_path, store, now).run()
    assert not second.already_checked
    assert second.recovered == 1
    assert len(store.focus_segments()) == 2


def test_deterministic_segment_identity_changes_only_with_interval_identity():
    start = datetime(2026, 9, 10, 9, 0, tzinfo=BEIJING)
    end = start + timedelta(minutes=15)
    first = deterministic_focus_segment_id("device-a", "session-a", start, end)
    assert first == deterministic_focus_segment_id("device-a", "session-a", start, end)
    assert first != deterministic_focus_segment_id(
        "device-a", "session-a", start, end + timedelta(seconds=1)
    )
