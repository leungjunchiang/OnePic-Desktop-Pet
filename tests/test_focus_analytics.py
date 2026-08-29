from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from onepic_desktop_pet.focus_analytics import FocusAnalyticsStore, FocusQualityTracker, score_focus_quality


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


def test_overlapping_raw_focus_intervals_are_counted_once(tmp_path) -> None:
    now = datetime(2026, 8, 21, 12, 0)
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=False)
    store.record_session(60 * 60, started_at=datetime(2026, 8, 20, 10, 0))
    store.record_session(2 * 60 * 60, started_at=datetime(2026, 8, 20, 10, 30))

    # 10:00–12:30 is 2.5 hours; the overlapping 10:30–11:00 portion must
    # not be added twice.
    assert store._state["days"]["2026-08-20"]["seconds"] == 150 * 60
    assert len(store._state["records"]) == 2


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
