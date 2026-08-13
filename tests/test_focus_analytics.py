from __future__ import annotations

from datetime import datetime, timedelta

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
