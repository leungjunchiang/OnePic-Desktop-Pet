from __future__ import annotations

from datetime import datetime, timedelta

from onepic_desktop_pet.diary import DailyCompanionStats
from onepic_desktop_pet.focus_analytics import FocusAnalyticsStore
from onepic_desktop_pet.work_report import build_work_report
from onepic_desktop_pet.work_timer import WorkTimerModel


def test_work_report_is_account_scoped_and_does_not_create_png(tmp_path) -> None:
    now = datetime(2026, 8, 23, 12, 0)
    analytics = FocusAnalyticsStore(
        path=tmp_path / "focus.json",
        now_provider=lambda: now,
        persist=True,
    )
    analytics.record_session(45 * 60, started_at=now - timedelta(minutes=50), completed=True)
    timer = WorkTimerModel(
        path=tmp_path / "timer.json",
        now_provider=lambda: now,
        monotonic_provider=lambda: 100.0,
        persist=True,
    )
    daily = DailyCompanionStats(
        path=tmp_path / "daily.json",
        now_provider=lambda: now,
        persist=True,
    )
    daily.record_focus(45 * 60, completed=True)
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}

    report = build_work_report(
        analytics,
        timer,
        daily,
        best_buddy="小梁家的六毛",
        now=now,
    )

    after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before
    assert report["day"]["total_seconds"] == 45 * 60
    assert report["day"]["completed_rounds"] == 1
    assert report["week"]["total_seconds"] == 45 * 60
    assert report["month"]["total_seconds"] == 45 * 60
    assert report["best_buddy"] == "小梁家的六毛"
    assert "不能测量" in report["sleep_note"]
