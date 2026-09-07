from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from onepic_desktop_pet.focus_analytics import (
    AccountFocusStore,
    FocusAnalyticsStore,
    FocusQualityTracker,
    score_focus_quality,
)


def test_focus_quality_explains_switches_and_away_time() -> None:
    deep = score_focus_quality(50 * 60, 0, 0)
    noisy = score_focus_quality(50 * 60, 8, 2)
    assert deep.score > noisy.score
    assert deep.label == "很深的一轮"
    assert noisy.label == "切换有点多"


def test_focus_tracker_counts_category_switches_and_absence() -> None:
    tracker = FocusQualityTracker()
    tracker.start("coding")
    tracker.note_application_switch("coding")
    tracker.note_application_switch("office")
    tracker.note_application_switch("reading")
    tracker.note_away()
    assert tracker.snapshot() == {"application_switches": 2, "away_count": 1}


def test_continuity_summary_and_next_day_review_are_local(tmp_path) -> None:
    now = datetime(2026, 8, 13, 12, 0)
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=True)
    store.record_session(40 * 60, started_at=now - timedelta(minutes=50), completed=True)
    store.record_session(20 * 60, started_at=now - timedelta(hours=2), completed=True)
    store.record_session(30 * 60, started_at=now - timedelta(days=1), completed=True)
    store.set_tomorrow_task("先完成论文第三节")

    summary = store.summary()
    assert summary.today_rounds == 2
    assert summary.current_streak_days == 2
    assert summary.weekly_total_seconds == 90 * 60
    assert summary.difference_vs_yesterday_seconds == 30 * 60
    assert store.tomorrow_task() == "先完成论文第三节"
    assert store.snapshot()["first_task_today"] == ""

    tomorrow = FocusAnalyticsStore(
        path=tmp_path / "focus.json",
        now_provider=lambda: now + timedelta(days=1),
        persist=True,
    )
    assert tomorrow.today_first_task() == "先完成论文第三节"


def test_period_summary_projects_day_week_and_month_without_network(tmp_path) -> None:
    now = datetime(2026, 8, 13, 12, 0)
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=True)
    store.record_session(40 * 60, started_at=now - timedelta(minutes=50), completed=True)
    store.record_session(20 * 60, started_at=now - timedelta(days=1), completed=True)
    store.record_session(30 * 60, started_at=now - timedelta(days=10), completed=True)

    day = store.period_summary("day", now)
    week = store.period_summary("week", now)
    month = store.period_summary("month", now)

    assert day["total_seconds"] == 40 * 60
    assert day["completed_rounds"] == 1
    assert week["total_seconds"] == 60 * 60
    assert week["active_days"] == 2
    assert month["total_seconds"] == 90 * 60
    assert month["active_days"] == 3
    assert len(month["daily"]) == 31
    today_row = next(row for row in month["daily"] if row["date"] == "2026-08-13")
    assert today_row["weekday"] == "周四"
    assert today_row["display_label"] == "8/13 周四"
    assert all(row["seconds"] is None for row in month["daily"] if row["date"] > "2026-08-13")


def test_range_summary_uses_one_union_for_overlapping_devices(tmp_path) -> None:
    now = datetime(2026, 9, 7, 12, 0)
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=True)
    store.record_session(
        2 * 60 * 60,
        started_at=datetime(2026, 9, 2, 9, 0),
        completed=True,
        device_id="mac",
    )
    store.record_session(
        2 * 60 * 60,
        started_at=datetime(2026, 9, 2, 10, 0),
        completed=True,
        device_id="win",
    )

    report = store.range_summary(
        datetime(2026, 9, 2),
        datetime(2026, 9, 9),
    )

    assert report["total_seconds"] == 3 * 60 * 60
    assert report["active_days"] == 1
    assert len(report["daily"]) == 7
    assert report["daily"][0]["seconds"] == 3 * 60 * 60
    assert report["daily"][1]["seconds"] == 0


def test_period_summary_projects_current_calendar_year_without_network(tmp_path) -> None:
    now = datetime(2026, 8, 13, 12, 0)
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=True)
    store.record_session(60 * 60, started_at=datetime(2026, 1, 2, 9, 0), completed=True)
    store.record_session(2 * 60 * 60, started_at=datetime(2026, 4, 4, 10, 0), completed=True)
    store.record_session(30 * 60, started_at=datetime(2026, 8, 12, 9, 0), completed=True)
    store.record_session(60 * 60, started_at=now - timedelta(hours=2), completed=True)

    year = store.period_summary("year", now)

    assert year["period"] == "year"
    assert year["start"] == "2026-01-01"
    assert year["total_seconds"] == 4 * 60 * 60 + 30 * 60
    assert year["active_days"] == 4
    assert len(year["daily"]) == 365
    assert len(year["hourly"]) == 24
    assert next(row for row in year["daily"] if row["date"] == "2026-01-02")["seconds"] == 60 * 60
    assert next(row for row in year["daily"] if row["date"] == "2026-12-01")["seconds"] is None


def test_period_summary_derives_report_metrics_from_account_records(tmp_path) -> None:
    now = datetime(2026, 8, 13, 12, 0)
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=True)
    session_id = "session-with-pause"
    store.record_session(
        20 * 60,
        started_at=now - timedelta(minutes=65),
        completed=False,
        record_id=f"{session_id}:1200",
    )
    store.record_session(
        40 * 60,
        started_at=now - timedelta(minutes=40),
        completed=True,
        record_id=f"{session_id}:3600",
    )
    store.record_session(30 * 60, started_at=now - timedelta(days=1), completed=False)

    day = store.period_summary("day", now)

    # The two paused/resumed segments are one started session, not two.
    assert day["started_rounds"] == 1
    assert day["completed_rounds"] == 1
    assert day["completion_rate"] == 100.0
    assert day["average_session_seconds"] == 60 * 60
    assert day["high_quality_seconds"] == 60 * 60
    assert day["longest_focus_seconds"] == 60 * 60
    assert day["first_started_at"] != "暂无记录"
    assert day["last_ended_at"] != "暂无记录"
    assert day["daily"][-1]["is_today"] is True


def test_period_summary_uses_one_session_grain_for_average_and_max(tmp_path) -> None:
    now = datetime(2026, 8, 23, 12, 0)
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=False)
    store.record_session(35 * 60, started_at=now - timedelta(hours=2), completed=False, record_id="first:1")
    store.record_session(70 * 60, started_at=now - timedelta(hours=1), completed=False, record_id="second:1")

    report = store.period_summary("day", now)

    assert report["started_rounds"] == 2
    assert report["average_session_seconds"] <= report["longest_focus_seconds"]
    assert report["deep_focus_seconds"] <= report["total_seconds"]


def test_derived_account_snapshot_is_not_focus_evidence(tmp_path) -> None:
    now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=False)
    store.merge_remote_state(
        focus_date="2026-08-23",
        today_seconds=2 * 3600,
        week_start="2026-08-17",
        week_seconds=53 * 3600,
    )

    week = store.period_summary("week", now)
    month = store.period_summary("month", now)

    assert week["total_seconds"] == 0
    assert month["total_seconds"] == 0


def test_period_summary_exposes_hourly_distribution_and_trust_state(tmp_path) -> None:
    now = datetime(2026, 8, 13, 18, 0)
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=True)
    store.record_session(30 * 60, started_at=datetime(2026, 8, 13, 9, 15), completed=True)
    store.record_session(45 * 60, started_at=datetime(2026, 8, 13, 15, 30), completed=True)

    day = store.period_summary("day", now)
    hourly = {int(row["hour"]): int(row["seconds"]) for row in day["hourly"]}

    assert hourly[9] == 30 * 60
    assert hourly[15] == 30 * 60
    assert hourly[16] == 15 * 60
    assert sum(hourly.values()) == 75 * 60
    assert day["data_quality"]["trusted"] is True
    assert day["hourly"][9]["label"] == "09:00"


def test_long_daily_focus_above_eight_hours_is_still_trusted(tmp_path) -> None:
    now = datetime(2026, 8, 13, 23, 30)
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=True)
    store.record_session(12 * 60 * 60, started_at=datetime(2026, 8, 13, 8, 0), completed=True)

    day = store.period_summary("day", now)

    assert day["total_seconds"] == 12 * 60 * 60
    assert day["data_quality"]["trusted"] is True
    assert day["data_quality"]["untrusted_days"] == []


def test_beijing_raw_timestamp_and_stale_remote_snapshot_do_not_inflate_today(tmp_path) -> None:
    now = datetime(2026, 8, 24, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=True)
    # 00:30Z is 08:30 in Beijing.  A legacy parser that used the machine
    # timezone could put this interval on the wrong calendar day.
    store.record_session(
        60 * 60,
        started_at=datetime(2026, 8, 24, 0, 30, tzinfo=timezone.utc),
        record_id="beijing:1",
    )
    store.merge_remote_state(
        focus_date="2026-08-24",
        today_seconds=5 * 3600 + 11 * 60,
        week_start="2026-08-24",
        week_seconds=53 * 3600,
    )
    assert store.period_summary("day", now)["total_seconds"] == 60 * 60
    assert store.period_summary("week", now)["total_seconds"] == 60 * 60
    assert store.period_summary("day", now)["daily"][0]["display_label"] == "8/24 周一"


def test_raw_focus_facts_force_a_new_day_to_zero_over_stale_remote_cache(tmp_path) -> None:
    now = datetime(2026, 8, 29, 0, 13, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=True)
    store.record_session(
        45 * 60,
        started_at=datetime(2026, 8, 28, 20, 0, tzinfo=timezone(timedelta(hours=8))),
        record_id="previous-day:1",
    )
    # This is the midnight corruption seen in the field: a derived daily row
    # exists even though the current day has no interval fact.
    store.merge_remote_history([{"focus_date": "2026-08-29", "seconds": 5 * 3600 + 11 * 60}])

    assert store.period_summary("day", now)["total_seconds"] == 0
    assert store.summary(now).today_seconds == 0
    assert "2026-08-29" not in store._state["days"]


def test_reconcile_derived_totals_splits_cross_midnight_fact(tmp_path) -> None:
    tz = timezone(timedelta(hours=8))
    now = datetime(2026, 8, 29, 0, 20, tzinfo=tz)
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=True)
    store.record_session(
        20 * 60,
        started_at=datetime(2026, 8, 28, 23, 50, tzinfo=tz),
        record_id="cross-day:1",
    )
    store.reconcile_derived_totals(now)

    assert store.period_summary("day", now)["total_seconds"] == 10 * 60
    assert store.period_summary("day", now - timedelta(days=1))["total_seconds"] == 10 * 60


def test_overlong_raw_fact_is_excluded_and_reported(tmp_path) -> None:
    now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=False)
    store._state["records"] = [{
        "date": "2026-08-27",
        "started_at": "2026-08-27T11:00:00+08:00",
        "seconds": 25 * 60 * 60,
        "record_id": "overlong:1",
    }]

    report = store.period_summary("week", now)
    assert report["total_seconds"] == 0
    assert any("overlong_interval" in item for item in report["data_quality"]["consistency_errors"])


def test_synced_daily_history_is_not_focus_evidence(tmp_path) -> None:
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=True)
    # Daily history and profile aggregates are both derived caches.  Neither
    # may create focus time when no raw interval was received.
    store.merge_remote_state(
        focus_date="2026-08-24",
        today_seconds=2 * 3600,
        week_start="2026-08-24",
        week_seconds=15 * 3600 + 24 * 60,
    )
    store.merge_remote_history([{"focus_date": "2026-08-24", "seconds": 2 * 3600}])

    assert store.period_summary("day", now)["total_seconds"] == 0
    assert store.period_summary("week", now)["total_seconds"] == 0
    assert store.period_summary("month", now)["total_seconds"] == 0
    assert store.period_summary("day", now)["local_evidence"] is False


def test_week_and_month_keep_future_dates_as_missing_values(tmp_path) -> None:
    now = datetime(2026, 8, 24, 9, 0, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=False)
    week = store.period_summary("week", now)
    month = store.period_summary("month", now)
    assert len(week["daily"]) == 7
    assert all(row["seconds"] is None for row in week["daily"][1:])
    assert len(month["daily"]) == 31
    assert all(row["seconds"] is None for row in month["daily"][24:])
    assert all(row["status"] == "future" for row in month["daily"][24:])


def test_period_summary_excludes_legacy_cumulative_records_from_charts(tmp_path) -> None:
    path = tmp_path / "focus.json"
    path.write_text(
        json.dumps(
            {
                "days": {},
                "records": [
                    {"date": "2026-08-20", "started_at": "2026-08-20T10:00:00", "seconds": 3600},
                    {"date": "2026-08-20", "started_at": "2026-08-20T10:30:00", "seconds": 7200},
                    {"date": "2026-08-20", "started_at": "2026-08-20T11:00:00", "seconds": 10800},
                ],
            }
        ),
        encoding="utf-8",
    )
    store = FocusAnalyticsStore(
        path=path,
        now_provider=lambda: datetime(2026, 8, 21, 12, 0),
        persist=True,
    )

    month = store.period_summary("month")

    assert month["total_seconds"] == 0
    assert month["high_quality_seconds"] == 0
    assert sum(int(row["seconds"]) for row in month["hourly"]) == 0
    assert month["data_quality"]["trusted"] is False
    assert "2026-08-20" in month["data_quality"]["untrusted_days"]


def test_period_summary_caps_overlapping_quality_time_to_effective_total(tmp_path) -> None:
    now = datetime(2026, 8, 21, 12, 0)
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=False)
    store.record_session(60 * 60, started_at=datetime(2026, 8, 20, 10, 0), completed=True)
    store.record_session(2 * 60 * 60, started_at=datetime(2026, 8, 20, 10, 30), completed=True)

    week = store.period_summary("week", now)

    assert week["total_seconds"] == 150 * 60
    assert week["high_quality_seconds"] <= week["total_seconds"]
    assert week["high_quality_seconds"] == week["total_seconds"]


def test_remote_focus_segments_are_facts_not_daily_maxima(tmp_path) -> None:
    now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(
        path=tmp_path / "focus.json",
        now_provider=lambda: now,
        persist=True,
    )
    changed = store.merge_remote_segments(
        {
            "segments": [
                {
                    "segment_id": "remote-1",
                    "session_id": "remote-session",
                    "start_at": "2026-08-26T01:00:00Z",
                    "end_at": "2026-08-26T02:30:00Z",
                    "completed": True,
                }
            ]
        }
    )
    assert changed is True
    day = store.period_summary("day", now)
    assert day["total_seconds"] == 90 * 60
    assert sum(int(item["seconds"]) for item in day["hourly"]) == day["total_seconds"]
    assert day["focus_intervals"][0]["started_at"].startswith("2026-08-26T09:00")


def test_remote_delta_merge_failure_keeps_cursor_and_facts_retryable(tmp_path) -> None:
    now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=True)
    store.set_focus_segments_sync_cursor("before")

    success, changed, count = store.merge_remote_segments_checked({
        "segments": [{
            "segment_id": "open-remote",
            "session_id": "open-session",
            "start_at": "2026-09-07T09:00:00+08:00",
            "end_at": None,
            "device_id": "device-b",
        }],
    })

    assert (success, changed, count) == (False, False, 0)
    assert store.focus_segments_sync_cursor() == "before"
    assert store.focus_segments() == []


def test_remote_delta_merge_persistence_failure_does_not_ack_cursor(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=False)
    store.set_focus_segments_sync_cursor("before")
    monkeypatch.setattr(store, "_save", lambda: (_ for _ in ()).throw(OSError("disk full")))

    success, changed, count = store.merge_remote_segments_checked({
        "segments": [{
            "segment_id": "sealed-remote",
            "session_id": "sealed-session",
            "start_at": "2026-09-07T09:00:00+08:00",
            "end_at": "2026-09-07T10:00:00+08:00",
            "device_id": "device-b",
        }],
    })

    assert (success, changed, count) == (False, False, 0)
    assert store.focus_segments_sync_cursor() == "before"
    assert store.focus_segments() == []


def test_yesterday_remote_devices_union_to_three_hours_without_id_collision(tmp_path) -> None:
    now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=True)
    success, changed, count = store.merge_remote_segments_checked({
        "segments": [
            {
                "segment_id": "device-a-yesterday",
                "session_id": "session-a",
                "start_at": "2026-09-06T09:00:00+08:00",
                "end_at": "2026-09-06T10:00:00+08:00",
                "device_id": "device-a",
            },
            {
                "segment_id": "device-b-yesterday",
                "session_id": "session-b",
                "start_at": "2026-09-06T14:00:00+08:00",
                "end_at": "2026-09-06T16:00:00+08:00",
                "device_id": "device-b",
            },
        ],
    })

    assert (success, changed, count) == (True, True, 2)
    assert store.period_summary("day", now - timedelta(days=1))["total_seconds"] == 3 * 60 * 60
    assert {item.device_id for item in store.focus_segments()} == {"device-a", "device-b"}
    assert {item.segment_id for item in store.focus_segments()} == {
        "device-a-yesterday",
        "device-b-yesterday",
    }


def test_focus_segment_sync_cursor_persists_without_changing_focus_facts(tmp_path) -> None:
    now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    path = tmp_path / "focus.json"
    store = FocusAnalyticsStore(path=path, now_provider=lambda: now, persist=True)
    store.record_session(
        30 * 60,
        started_at=datetime(2026, 8, 26, 9, 0, tzinfo=timezone(timedelta(hours=8))),
        record_id="local-1",
    )
    facts_before = store.focus_segments_payload()

    assert store.focus_segments_sync_cursor() is None
    assert store.set_focus_segments_sync_cursor("2026-08-26T04:00:00+00:00") is True
    assert store.set_focus_segments_sync_cursor("2026-08-26T04:00:00+00:00") is False

    reloaded = FocusAnalyticsStore(path=path, now_provider=lambda: now, persist=True)
    assert reloaded.focus_segments_sync_cursor() == "2026-08-26T04:00:00+00:00"
    assert reloaded.focus_segments_payload() == facts_before


def test_focus_segment_upload_does_not_strand_older_closed_facts(tmp_path) -> None:
    now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(
        path=tmp_path / "focus.json",
        now_provider=lambda: now,
        persist=True,
    )
    for index in range(120):
        store.record_session(
            60,
            started_at=now - timedelta(days=1, minutes=index + 1),
            record_id=f"closed-{index}",
        )

    payload = store.focus_segments_payload()

    assert len(payload) == 120
    assert payload[0]["segment_id"] == "closed-0"
    assert payload[-1]["segment_id"] == "closed-119"


def test_focus_segment_upload_ack_suppresses_unchanged_rows_and_remote_echo(tmp_path) -> None:
    now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(
        path=tmp_path / "focus.json",
        now_provider=lambda: now,
        persist=True,
        device_id="device-a",
    )
    store.record_session(
        60,
        started_at=now - timedelta(minutes=2),
        record_id="local-a",
    )
    store.merge_remote_segments({
        "segments": [{
            "segment_id": "remote-b",
            "session_id": "remote-session",
            "start_at": "2026-09-07T08:00:00+08:00",
            "end_at": "2026-09-07T09:00:00+08:00",
            "device_id": "device-b",
        }],
    })

    first = store.focus_segments_payload()
    assert [item["segment_id"] for item in first] == ["local-a"]
    assert store.acknowledge_focus_segments_upload(first)
    for _ in range(100):
        assert store.focus_segments_payload() == []

    store.record_session(
        60,
        started_at=now - timedelta(minutes=1),
        record_id="local-c",
    )
    assert [item["segment_id"] for item in store.focus_segments_payload()] == ["local-c"]

    reloaded = FocusAnalyticsStore(
        path=tmp_path / "focus.json",
        now_provider=lambda: now,
        persist=True,
        device_id="device-a",
    )
    assert [item["segment_id"] for item in reloaded.focus_segments_payload()] == ["local-c"]


def test_focus_segment_upload_ack_failure_keeps_batch_retryable(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(
        path=tmp_path / "focus.json",
        now_provider=lambda: now,
        persist=False,
        device_id="device-a",
    )
    store.record_session(60, started_at=now - timedelta(minutes=1), record_id="local-a")
    batch = store.focus_segments_payload()
    monkeypatch.setattr(store, "_save", lambda: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError):
        store.acknowledge_focus_segments_upload(batch)
    assert store.focus_segments_payload() == batch


def test_integrity_audit_requeues_only_acknowledged_local_server_gaps(tmp_path) -> None:
    now = datetime(2026, 9, 7, 21, 30, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(
        path=tmp_path / "focus.json",
        now_provider=lambda: now,
        persist=True,
        device_id="device-a",
    )
    store.record_session(3600, started_at=now - timedelta(hours=3), record_id="local-a")
    store.record_session(1800, started_at=now - timedelta(hours=2), record_id="local-b")
    store.merge_remote_segments({
        "segments": [{
            "segment_id": "remote-c",
            "session_id": "remote-session",
            "start_at": "2026-09-07T12:00:00+08:00",
            "end_at": "2026-09-07T13:00:00+08:00",
            "device_id": "device-b",
        }],
    })
    upload = store.focus_segments_payload()
    assert {row["segment_id"] for row in upload} == {"local-a", "local-b"}
    assert store.acknowledge_focus_segments_upload(upload)

    manifest = store.focus_segment_integrity_manifest()
    assert manifest == ["local-a", "local-b"]
    success, requeued = store.apply_focus_segment_integrity_audit({
        "_requested_segment_ids": manifest,
        "checked_count": 2,
        "present_count": 1,
        "missing_count": 1,
        "missing_segment_ids": ["local-a"],
        "server_total_count": 236,
    })

    assert success is True
    assert requeued == 1
    assert [row["segment_id"] for row in store.focus_segments_payload()] == ["local-a"]
    assert store.focus_segment_integrity_manifest() == []


def test_integrity_audit_malformed_or_unpersisted_reply_preserves_acknowledgements(
    tmp_path, monkeypatch
) -> None:
    now = datetime(2026, 9, 7, 21, 30, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(
        path=tmp_path / "focus.json",
        now_provider=lambda: now,
        persist=False,
        device_id="device-a",
    )
    store.record_session(60, started_at=now - timedelta(minutes=2), record_id="local-a")
    upload = store.focus_segments_payload()
    assert store.acknowledge_focus_segments_upload(upload)
    manifest = store.focus_segment_integrity_manifest()

    assert store.apply_focus_segment_integrity_audit({
        "_requested_segment_ids": manifest,
        "checked_count": 1,
        "present_count": 1,
        "missing_count": 1,
        "missing_segment_ids": ["local-a"],
    }) == (False, 0)
    assert store.focus_segments_payload() == []

    monkeypatch.setattr(store, "_save", lambda: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        store.apply_focus_segment_integrity_audit({
            "_requested_segment_ids": manifest,
            "checked_count": 1,
            "present_count": 0,
            "missing_count": 1,
            "missing_segment_ids": ["local-a"],
        })
    assert store.focus_segments_payload() == []


def test_legacy_acknowledgements_trigger_one_bounded_recovery_backfill(tmp_path) -> None:
    now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    path = tmp_path / "focus.json"
    store = FocusAnalyticsStore(
        path=path,
        now_provider=lambda: now,
        persist=True,
        device_id="device-a",
    )
    store.record_session(60, started_at=now - timedelta(minutes=3), record_id="local-a")
    store.merge_remote_segments({
        "segments": [{
            "segment_id": "remote-b",
            "session_id": "remote-session",
            "start_at": "2026-09-07T08:00:00+08:00",
            "end_at": "2026-09-07T09:00:00+08:00",
            "device_id": "device-b",
        }],
    })
    first = store.focus_segments_payload()
    store.acknowledge_focus_segments_upload(first)

    # Simulate the pre-grant release: fingerprints exist, but no version was
    # recorded to prove that the RPC transaction really accepted them.
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["account_state"].pop("focus_segment_upload_ack_version", None)
    raw["account_state"].pop("focus_segment_upload_repair_pending", None)
    path.write_text(json.dumps(raw), encoding="utf-8")

    recovered = FocusAnalyticsStore(
        path=path,
        now_provider=lambda: now,
        persist=True,
        device_id="device-a",
    )
    repair = recovered.focus_segments_payload()
    assert recovered.focus_segments_sync_mode() == "recovery_backfill"
    assert {item["segment_id"] for item in repair} == {"local-a", "remote-b"}

    recovered.acknowledge_focus_segments_upload(repair)
    assert recovered.focus_segments_sync_mode() == "delta"
    assert recovered.focus_segments_payload() == []


def test_version_two_acknowledgements_are_requeued_for_explicit_server_ack(tmp_path) -> None:
    now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    path = tmp_path / "focus.json"
    store = FocusAnalyticsStore(
        path=path,
        now_provider=lambda: now,
        persist=True,
        device_id="device-a",
    )
    store.record_session(60, started_at=now - timedelta(minutes=1), record_id="local-a")
    batch = store.focus_segments_payload()
    assert store.acknowledge_focus_segments_upload(batch)

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["account_state"]["focus_segment_upload_ack_version"] = 2
    raw["account_state"]["focus_segment_upload_repair_pending"] = False
    path.write_text(json.dumps(raw), encoding="utf-8")

    recovered = FocusAnalyticsStore(
        path=path,
        now_provider=lambda: now,
        persist=True,
        device_id="device-a",
    )
    assert recovered.focus_segments_sync_mode() == "recovery_backfill"
    assert [item["segment_id"] for item in recovered.focus_segments_payload()] == ["local-a"]


def test_overlapping_raw_focus_intervals_are_counted_once(tmp_path) -> None:
    now = datetime(2026, 8, 21, 12, 0)
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=False)
    store.record_session(60 * 60, started_at=datetime(2026, 8, 20, 10, 0))
    store.record_session(2 * 60 * 60, started_at=datetime(2026, 8, 20, 10, 30))

    # 10:00–12:30 is 2.5 hours; the overlapping 10:30–11:00 portion must
    # not be added twice.
    assert store._state["days"]["2026-08-20"]["seconds"] == 150 * 60
    assert len(store._state["records"]) == 2


def test_multi_device_facts_keep_attribution_and_report_account_union(tmp_path) -> None:
    now = datetime(2026, 8, 21, 12, 0)
    store = FocusAnalyticsStore(
        path=tmp_path / "focus.json",
        now_provider=lambda: now,
        persist=True,
        device_id="device-a",
    )
    store.record_session(
        60 * 60,
        started_at=datetime(2026, 8, 21, 9, 0),
        record_id="session-a:3600",
    )
    store.set_device_id("device-b")
    store.record_session(
        60 * 60,
        started_at=datetime(2026, 8, 21, 9, 30),
        record_id="session-b:3600",
    )

    diagnostics = store.focus_device_diagnostics("day", now)

    assert [item["device_id"] for item in diagnostics["devices"]] == ["device-a", "device-b"]
    assert [item["seconds"] for item in diagnostics["devices"]] == [3600, 3600]
    assert diagnostics["raw_sum_seconds"] == 7200
    assert diagnostics["effective_union_seconds"] == 90 * 60
    assert diagnostics["overlap_seconds"] == 30 * 60
    assert store.period_summary("day", now)["total_seconds"] == 90 * 60

    persisted = json.loads((tmp_path / "focus.json").read_text(encoding="utf-8"))
    assert [item["device_id"] for item in persisted["records"]] == ["device-a", "device-b"]


def test_duplicate_remote_device_segment_remains_idempotent(tmp_path) -> None:
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=True)
    payload = {
        "segments": [{
            "segment_id": "device-b-session:1800",
            "session_id": "device-b-session",
            "start_at": "2026-08-21T09:00:00+08:00",
            "end_at": "2026-08-21T09:30:00+08:00",
            "device_id": "device-b",
            "completed": True,
        }]
    }

    assert store.merge_remote_segments(payload) is True
    assert store.merge_remote_segments(payload) is False
    assert len(store.focus_segments()) == 1
    assert store.focus_segments()[0].device_id == "device-b"
    assert store.period_summary("day", now)["total_seconds"] == 30 * 60


def test_legacy_impossible_day_does_not_report_false_38_hour_difference(tmp_path) -> None:
    path = tmp_path / "focus.json"
    path.write_text(
        json.dumps({"days": {"2026-08-20": {"seconds": 136814}}, "records": []}),
        encoding="utf-8",
    )
    store = FocusAnalyticsStore(path=path, now_provider=lambda: datetime(2026, 8, 21, 12, 0), persist=True)

    summary = store.summary()
    assert summary.yesterday_seconds == 0
    assert summary.difference_vs_yesterday_seconds == 0
    assert summary.weekly_total_seconds == 0


def test_legacy_cumulative_checkpoints_are_excluded_from_day_comparison(tmp_path) -> None:
    path = tmp_path / "focus.json"
    path.write_text(
        json.dumps(
            {
                "days": {},
                "records": [
                    {"date": "2026-08-20", "started_at": "2026-08-20T10:00:00", "seconds": 3600},
                    {"date": "2026-08-20", "started_at": "2026-08-20T10:30:00", "seconds": 7200},
                    {"date": "2026-08-20", "started_at": "2026-08-20T11:00:00", "seconds": 10800},
                ],
            }
        ),
        encoding="utf-8",
    )
    store = FocusAnalyticsStore(
        path=path,
        now_provider=lambda: datetime(2026, 8, 21, 12, 0),
        persist=True,
    )

    summary = store.summary()
    assert store._state["days"]["2026-08-20"]["seconds_untrusted"] is True
    assert summary.yesterday_seconds is None
    assert summary.difference_vs_yesterday_seconds is None


def test_focus_day_boundary_is_beijing_midnight(tmp_path) -> None:
    # 16:00 UTC is 00:00 the next day in Beijing.
    now = datetime(2026, 8, 20, 16, 30, tzinfo=timezone.utc)
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=False)
    store.record_session(
        30 * 60,
        started_at=datetime(2026, 8, 20, 16, 0, tzinfo=timezone.utc),
        completed=True,
    )

    summary = store.summary()
    assert summary.date == "2026-08-21"
    assert summary.weekly_total_seconds == 30 * 60
    assert summary.yesterday_seconds == 0


def test_pause_longer_than_ten_minutes_is_the_only_interruption(tmp_path) -> None:
    now = datetime(2026, 8, 21, 9, 0)
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=False)
    store.begin_focus_session(at=now)
    store.pause_focus_session(at=now + timedelta(minutes=5))
    store.begin_focus_session(at=now + timedelta(minutes=14))
    assert store.snapshot()["current_interruptions"] == 0
    store.pause_focus_session(at=now + timedelta(minutes=20))
    store.begin_focus_session(at=now + timedelta(minutes=31))
    assert store.snapshot()["current_interruptions"] == 1
    assert store.snapshot()["today_interruptions"] == 1



def test_account_totals_are_not_rendered_without_raw_sessions(tmp_path) -> None:
    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(
        path=tmp_path / "focus.json",
        now_provider=lambda: now,
        persist=False,
    )

    changed = store.merge_remote_state(
        focus_date="2026-08-22",
        today_seconds=42 * 60,
        lifetime_seconds=8 * 3600,
        week_start="2026-08-17",
        week_seconds=3 * 3600,
    )

    assert changed
    snapshot = store.snapshot()
    assert snapshot["today_seconds"] == 0
    assert snapshot["weekly_total_seconds"] == 0


def test_account_totals_do_not_accept_a_previous_week(tmp_path) -> None:
    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=False)

    store.merge_remote_state(
        focus_date="2026-08-22",
        today_seconds=60,
        week_start="2026-08-10",
        week_seconds=99 * 3600,
    )
    assert store.snapshot()["weekly_total_seconds"] == 0


def test_server_daily_history_does_not_create_focus_time_on_new_computer(tmp_path) -> None:
    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=False)
    store.merge_remote_history({
        "days": [
            {"focus_date": "2026-08-21", "seconds": 11 * 3600},
            {"focus_date": "2026-08-22", "seconds": 3 * 3600},
        ]
    })

    summary = store.summary()
    assert summary.yesterday_seconds == 0
    assert summary.difference_vs_yesterday_seconds == 0


def test_full_week_derived_cache_is_ignored_without_raw_session(tmp_path) -> None:
    path = tmp_path / "focus.json"
    path.write_text(
        json.dumps({
            "days": {"2026-08-24": {"seconds": 604800}},
            "records": [],
            "account_state": {
                "focus_date": "2026-08-29",
                "focus_today_seconds": 604800,
                "focus_week_start": "2026-08-24",
                "focus_week_seconds": 604800,
            },
        }),
        encoding="utf-8",
    )
    now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(path=path, now_provider=lambda: now, persist=True)

    assert store.period_summary("day", now)["total_seconds"] == 0
    assert store.period_summary("week", now)["total_seconds"] == 0
    assert store.summary(now).weekly_total_seconds == 0


def test_focus_analytics_switches_to_an_isolated_account_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(now_provider=lambda: now, persist=True)

    assert store.switch_account("account-a")
    store.record_session(90, started_at=now, completed=True)
    assert store.summary().weekly_total_seconds == 90

    assert store.switch_account("account-b")
    assert store.summary().weekly_total_seconds == 0

    assert store.switch_account("account-a")
    assert store.summary().weekly_total_seconds == 90


def test_account_focus_store_merges_two_devices_and_deduplicates_overlap(tmp_path) -> None:
    now = datetime(2026, 9, 7, 13, 0, tzinfo=timezone(timedelta(hours=8)))
    store = AccountFocusStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=True)
    ok, changed, count = store.merge_remote_segments_checked({
        "segments": [
            {
                "segment_id": "a",
                "session_id": "sa",
                "start_at": "2026-09-07T09:00:00+08:00",
                "end_at": "2026-09-07T10:05:06+08:00",
                "device_id": "mac",
            },
            {
                "segment_id": "b",
                "session_id": "sb",
                "start_at": "2026-09-07T10:05:06+08:00",
                "end_at": "2026-09-07T12:16:50+08:00",
                "device_id": "win",
            },
        ],
    })
    assert (ok, changed, count) == (True, True, 2)
    assert store.account_today_seconds(now) == 3 * 60 * 60 + 16 * 60 + 50

    overlap = AccountFocusStore(path=tmp_path / "overlap.json", now_provider=lambda: now, persist=False)
    overlap.merge_remote_segments({
        "segments": [
            {
                "segment_id": "a-overlap",
                "session_id": "sa",
                "start_at": "2026-09-07T09:00:00+08:00",
                "end_at": "2026-09-07T10:00:00+08:00",
                "device_id": "mac",
            },
            {
                "segment_id": "b-overlap",
                "session_id": "sb",
                "start_at": "2026-09-07T09:30:00+08:00",
                "end_at": "2026-09-07T11:00:00+08:00",
                "device_id": "win",
            },
        ]
    })
    assert overlap.account_today_seconds(now) == 2 * 60 * 60


def test_account_focus_store_delta_upsert_preserves_previous_devices(tmp_path) -> None:
    now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    store = AccountFocusStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=False)
    first = {"segments": [
        {"segment_id": "a", "session_id": "sa", "start_at": "2026-09-07T09:00:00+08:00", "end_at": "2026-09-07T10:00:00+08:00", "device_id": "mac"},
        {"segment_id": "b", "session_id": "sb", "start_at": "2026-09-07T11:00:00+08:00", "end_at": "2026-09-07T12:00:00+08:00", "device_id": "win"},
    ]}
    second = {"segments": [
        {"segment_id": "c", "session_id": "sc", "start_at": "2026-09-07T13:00:00+08:00", "end_at": "2026-09-07T13:30:00+08:00", "device_id": "mac"},
    ]}
    assert store.merge_remote_segments_checked(first)[0]
    assert store.merge_remote_segments_checked(second)[0]
    assert {segment.segment_id for segment in store.focus_segments()} == {"a", "b", "c"}


def test_account_focus_store_live_projection_is_not_uploaded_or_persisted(tmp_path) -> None:
    now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    path = tmp_path / "focus.json"
    store = AccountFocusStore(path=path, now_provider=lambda: now, persist=True)
    from onepic_desktop_pet.focus_segments import FocusSegment

    store.set_live_projection_segments([
        FocusSegment(
            segment_id="live-b",
            session_id="sb",
            start_at=now - timedelta(minutes=30),
            end_at=None,
            device_id="win",
        )
    ])
    assert store.account_today_seconds(now) == 30 * 60
    assert store.focus_segments_payload() == []
    reloaded = AccountFocusStore(path=path, now_provider=lambda: now, persist=True)
    assert reloaded.live_projection_segments() == []
    assert reloaded.account_today_seconds(now) == 0


def test_account_focus_store_drops_stale_live_projection_before_union(tmp_path) -> None:
    now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    store = AccountFocusStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=False)
    from onepic_desktop_pet.focus_segments import FocusSegment

    store.set_live_projection_segments([
        FocusSegment(
            segment_id="live-stale",
            session_id="stale-session",
            start_at=now - timedelta(minutes=30),
            end_at=None,
            device_id="mac",
        )
    ])
    store._live_projection_expires_at = 0.0
    assert store.account_today_seconds(now) == 0


def test_account_focus_store_does_not_double_count_sealed_and_live_overlap(tmp_path) -> None:
    now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    store = AccountFocusStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=False)
    store.merge_remote_segments({
        "segments": [{
            "segment_id": "sealed-a",
            "session_id": "sealed-session",
            "start_at": "2026-09-07T09:00:00+08:00",
            "end_at": "2026-09-07T11:00:00+08:00",
            "device_id": "mac",
        }]
    })
    from onepic_desktop_pet.focus_segments import FocusSegment

    store.set_live_projection_segments([
        FocusSegment(
            segment_id="live-b",
            session_id="live-session",
            start_at=datetime(2026, 9, 7, 9, 0, tzinfo=timezone(timedelta(hours=8))),
            end_at=None,
            device_id="win",
        )
    ])
    assert store.account_today_seconds(now) == 3 * 60 * 60
