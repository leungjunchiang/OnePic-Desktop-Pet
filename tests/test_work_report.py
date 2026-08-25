from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.diary import DailyCompanionStats
from onepic_desktop_pet.focus_analytics import FocusAnalyticsStore
from onepic_desktop_pet.work_report import (
    ReportBarChart,
    WorkReportDialog,
    _nice_duration_ticks,
    build_work_report,
)
from onepic_desktop_pet.work_timer import WorkTimerModel


def test_work_report_is_a_normal_minimizable_window() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = WorkReportDialog(lambda: {})
    flags = dialog.windowFlags()

    assert int(flags) & 0x0F == int(Qt.WindowType.Window)
    assert flags & Qt.WindowType.WindowMinimizeButtonHint
    assert flags & Qt.WindowType.WindowSystemMenuHint
    assert flags & Qt.WindowType.WindowCloseButtonHint
    assert not flags & Qt.WindowType.WindowStaysOnTopHint
    assert dialog.parent() is None

    dialog.show()
    app.processEvents()
    dialog.showMinimized()
    app.processEvents()
    assert dialog.isMinimized()
    dialog.showNormal()
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


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
    assert report["current_streak_days"] == 1
    assert report["day"]["week_total_seconds"] == report["week"]["total_seconds"]
    assert report["day"]["rest_state"] == "清醒 / 暂未工作"
    assert report["day"]["started_rounds"] == 1
    assert report["day"]["completion_rate"] == 100.0
    assert report["day"]["high_quality_seconds"] == 45 * 60
    # The visible report uses period totals; high_quality_seconds remains an
    # internal compatibility field for older callers and data migrations.
    assert report["month"]["total_seconds"] == 45 * 60
    assert report["best_buddy"] == "小梁家的六毛"
    assert "不能测量" in report["sleep_note"]
    assert report["current_status"] == "idle"

    room_report = build_work_report(
        analytics,
        timer,
        daily,
        focus_snapshot={
            "status": "focus",
            "session_seconds": 12 * 60,
            "today_seconds": 60 * 60,
            "week_seconds": 2 * 60 * 60,
            "room_id": "room-1",
        },
        now=now,
    )
    assert room_report["current_status"] == "focus"
    assert room_report["day"]["total_seconds"] == 60 * 60
    assert room_report["week"]["total_seconds"] == 2 * 60 * 60
    assert room_report["day"]["focus_session_seconds"] == 12 * 60
    assert room_report["day"]["focus_room_id"] == "room-1"


def test_report_does_not_render_cumulative_checkpoint_as_live_today(tmp_path) -> None:
    """A stale cumulative session must not inflate today's raw ledger."""

    now = datetime(2026, 8, 24, 15, 0)
    analytics = FocusAnalyticsStore(
        path=tmp_path / "focus.json",
        now_provider=lambda: now,
        persist=True,
    )
    analytics.record_session(
        60 * 60,
        started_at=now - timedelta(hours=1),
        completed=True,
    )
    timer = WorkTimerModel(
        path=tmp_path / "timer.json",
        now_provider=lambda: now,
        monotonic_provider=lambda: 100.0,
        persist=True,
    )
    assert timer.start()
    timer._monotonic = lambda: 100.0 + 3 * 60 * 60  # type: ignore[method-assign]
    assert timer.checkpoint()
    # The session still contains three hours, but the current running segment
    # has just restarted and therefore has no live elapsed time.
    assert timer.session_seconds() == 3 * 60 * 60
    assert timer.current_elapsed_seconds() == 0

    report = build_work_report(
        analytics,
        timer,
        DailyCompanionStats(path=tmp_path / "daily.json", now_provider=lambda: now, persist=True),
        focus_snapshot={
            "status": "focus",
            "session_seconds": timer.session_seconds(),
            "today_seconds": timer.today_seconds(),
            "session_started_at": now.isoformat(),
        },
        now=now,
    )

    assert report["day"]["total_seconds"] == 60 * 60
    assert report["week"]["total_seconds"] == 60 * 60
    assert report["day"]["focus_session_seconds"] == 0
    assert all(int(item.get("seconds", 0) or 0) < 3 * 60 * 60 for item in report["day"]["focus_intervals"])


def test_report_live_segment_updates_hourly_rhythm_from_real_start(tmp_path) -> None:
    """The open work segment must appear in the current hour, not its old row."""

    now = datetime(2026, 8, 24, 10, 20)
    analytics = FocusAnalyticsStore(
        path=tmp_path / "focus.json",
        now_provider=lambda: now,
        persist=True,
    )
    analytics.record_session(30 * 60, started_at=now - timedelta(hours=1), completed=True)
    timer = WorkTimerModel(
        path=tmp_path / "timer.json",
        now_provider=lambda: now,
        monotonic_provider=lambda: 100.0,
        persist=True,
    )
    assert timer.start()
    timer._monotonic = lambda: 100.0 + 20 * 60  # type: ignore[method-assign]

    report = build_work_report(
        analytics,
        timer,
        DailyCompanionStats(path=tmp_path / "daily.json", now_provider=lambda: now, persist=True),
        focus_snapshot={
            "status": "focus",
            "session_seconds": timer.session_seconds(),
            "today_seconds": timer.today_seconds(),
            "session_started_at": (now - timedelta(minutes=20)).isoformat(),
        },
        now=now,
    )

    hourly = {int(item["hour"]): int(item["seconds"] or 0) for item in report["day"]["hourly"]}
    assert hourly[9] == 30 * 60
    assert hourly[10] >= 20 * 60


def test_report_bar_chart_keeps_axis_labels_inside_widget() -> None:
    app = QApplication.instance() or QApplication([])
    chart = ReportBarChart(
        [
            {"date": "2026-08-24", "label": "8/24", "weekday": "周一", "seconds": 2 * 60 * 60},
            {"date": "2026-08-25", "label": "8/25", "weekday": "周二", "seconds": None, "is_future": True},
            {"date": "2026-08-26", "label": "8/26", "weekday": "周三", "seconds": None, "is_future": True},
        ]
    )
    chart.resize(720, 320)
    chart.show()
    app.processEvents()

    visible_bars = [rect for rect in chart._bar_rects if not rect.isNull()]
    assert visible_bars
    # The x-axis/date labels are painted below the plot. If the bottom
    # adjustment accidentally expands the plot, the bars reach outside the
    # widget and those labels are clipped again.
    assert max(rect.bottom() for rect in visible_bars) <= chart.rect().bottom() - 50

    chart.close()
    chart.deleteLater()
    app.processEvents()


def test_report_duration_ticks_leave_headroom_and_hourly_tooltip_is_explicit() -> None:
    assert _nice_duration_ticks(60 * 60)[-1] == 2 * 60 * 60
    assert _nice_duration_ticks(3 * 60 * 60)[-1] == 4 * 60 * 60
    assert _nice_duration_ticks(2 * 60 * 60 + 9 * 60)[-1] == 3 * 60 * 60

    app = QApplication.instance() or QApplication([])
    chart = ReportBarChart(
        [{"hour": hour, "seconds": 68 * 60 if hour == 16 else 0} for hour in range(24)],
        hourly=True,
    )
    assert chart._tooltip_for(16) == "16:00–17:00\n本月累计专注：1小时8分钟"
    chart.close(); chart.deleteLater(); app.processEvents()


def test_report_bar_chart_scales_tallest_bar_below_axis_ceiling() -> None:
    app = QApplication.instance() or QApplication([])
    chart = ReportBarChart(
        [
            {"date": "2026-08-24", "label": "8/24", "weekday": "周一", "seconds": 3 * 60 * 60 + 2 * 60},
            {"date": "2026-08-25", "label": "8/25", "weekday": "周二", "seconds": None, "is_future": True},
        ]
    )
    chart.resize(720, 320)
    chart.show()
    app.processEvents()

    assert chart._axis_upper == 4 * 60 * 60
    visible_bars = [rect for rect in chart._bar_rects if not rect.isNull()]
    assert visible_bars
    assert min(rect.top() for rect in visible_bars) > chart._plot_rect.top()

    chart.close(); chart.deleteLater(); app.processEvents()
