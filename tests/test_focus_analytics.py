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
    assert summary.yesterday_seconds is None
    assert summary.difference_vs_yesterday_seconds is None
    assert summary.weekly_total_seconds == 0


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



def test_account_totals_are_rendered_on_a_new_computer(tmp_path) -> None:
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
    assert snapshot["today_seconds"] == 42 * 60
    assert snapshot["weekly_total_seconds"] == 3 * 3600


def test_account_totals_do_not_accept_a_previous_week(tmp_path) -> None:
    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    store = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=False)

    assert not store.merge_remote_state(
        focus_date="2026-08-22",
        today_seconds=60,
        week_start="2026-08-10",
        week_seconds=99 * 3600,
    )
    assert store.snapshot()["weekly_total_seconds"] == 0
