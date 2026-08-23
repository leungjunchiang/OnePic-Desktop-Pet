from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.diary import DailyCompanionStats
from onepic_desktop_pet.focus_analytics import FocusAnalyticsStore
from onepic_desktop_pet.work_report import WorkReportDialog, build_work_report
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
    assert not flags & Qt.WindowType.Tool
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
            "room_id": "room-1",
        },
        now=now,
    )
    assert room_report["current_status"] == "focus"
    assert room_report["day"]["focus_session_seconds"] == 12 * 60
    assert room_report["day"]["focus_room_id"] == "room-1"
