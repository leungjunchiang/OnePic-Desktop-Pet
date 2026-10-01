"""北京时间展示、自然日、06:00 开工、计划墙钟和跨系统时区的真实回归测试。"""

from datetime import date, datetime, timedelta, timezone
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.time_service import (
    BEIJING_TIMEZONE, beijing_day_start, format_clock, now_beijing,
    parse_date, parse_datetime, parse_server_datetime, parse_timestamp, today_key,
)
from onepic_desktop_pet.discipline import DisciplineEngine, DisciplineStore, discipline_events, get_actual_work_start
from onepic_desktop_pet.focus_segments import FocusSegment, aggregate_focus_time


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("value,expected", [
    ("2026-10-01T03:36:00Z", "11:36"),
    ("2026-10-01T03:36:00+00:00", "11:36"),
    ("2026-10-01T11:36:00+08:00", "11:36"),
    ("2026-10-01T12:36:00+09:00", "11:36"),
    ("2026-09-30T20:36:00-07:00", "11:36"),
])
def test_absolute_event_time_preserves_instant_without_double_conversion(value, expected):
    assert format_clock(value) == expected
    assert parse_datetime(value).timestamp() == datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def test_midnight_grouping_and_explicit_naive_contracts():
    stamp = parse_server_datetime("2026-09-30T18:30:00Z")
    assert stamp.isoformat() == "2026-10-01T02:30:00+08:00"
    assert parse_date(stamp) == parse_date("2026-09-30T18:30:00Z") == date(2026, 10, 1)
    assert parse_datetime("2026-10-01T03:36:00").hour == 3  # legacy local wall time
    assert parse_server_datetime("2026-10-01T03:36:00").hour == 11  # explicit server contract
    assert parse_timestamp("bad") is None and format_clock("bad") == ""
    assert beijing_day_start(date(2026, 10, 1)).astimezone(timezone.utc).isoformat() == "2026-09-30T16:00:00+00:00"


@pytest.mark.parametrize("offset", [0, 9, -7])
def test_clock_provider_business_day_ignores_os_zone(offset):
    instant = datetime(2026, 9, 30, 18, 30, tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=offset)))
    assert today_key(lambda: instant) == "2026-10-01"
    assert now_beijing(lambda: instant).hour == 2


@pytest.mark.parametrize("zone", ["UTC", "Asia/Tokyo", "America/Los_Angeles"])
def test_separate_process_host_zone_cannot_change_calendar(zone):
    # Only this child process's TZ changes. No system timezone is modified.
    code = '''
import time
if hasattr(time, "tzset"): time.tzset()
from datetime import datetime, timezone
from onepic_desktop_pet.time_service import *
from onepic_desktop_pet.discipline import local_work_time
d=datetime(2026,9,30,18,30,tzinfo=timezone.utc)
assert today_key(lambda:d)=="2026-10-01"
assert local_work_time(d).strftime("%Y-%m-%d %H:%M")=="2026-10-01 02:30"
assert format_clock("2026-10-01T03:36:00Z")=="11:36"
assert now_beijing().utcoffset().total_seconds()==28800
'''
    subprocess.run([sys.executable, "-c", code], env={**os.environ, "TZ": zone, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}, check=True, timeout=30)


def segment(start, end, identity="s"):
    return FocusSegment(identity, identity, parse_datetime(start), parse_datetime(end), "pc1")


def test_first_genuine_session_after_beijing_six_and_duration_unchanged():
    rows = [segment("2026-09-30T18:00:00Z", "2026-09-30T19:00:00Z", "early"),
            segment("2026-09-30T21:55:00Z", "2026-09-30T22:20:00Z", "cross-six"),
            segment("2026-10-01T00:52:00Z", "2026-10-01T01:52:00Z", "real")]
    now = parse_datetime("2026-10-01T05:00:00Z")
    start = get_actual_work_start([], date(2026, 10, 1), sessions=rows, now=now)
    assert format_clock(start) == "08:52"
    assert get_actual_work_start([], date(2026, 10, 1), sessions=rows[:2], now=now) is None
    total = aggregate_focus_time(rows, beijing_day_start(date(2026, 10, 1)), beijing_day_start(date(2026, 10, 2)), now=now)
    assert total.total_seconds == 8700  # no timezone arithmetic applied to durations


def test_plan_nine_is_beijing_nine_and_lateness_sixteen():
    store = DisciplineStore("tz-test", persist=False)
    store.update_settings({"mode": "normal", "start_time": "09:00"})
    engine = DisciplineEngine(store)
    engine.record_work_event("start_work", 0, 0, parse_datetime("2026-10-01T01:16:00Z"))
    assert store.settings.start_at(date(2026, 10, 1)).isoformat() == "2026-10-01T09:00:00+08:00"
    assert store.events_for_day(date(2026, 10, 1))[-1]["metadata"]["minutes_late"] == 16


def test_exemption_after_midnight_is_today_and_suppresses_discipline():
    store = DisciplineStore("tz-test", persist=False)
    store.update_settings({"mode": "normal"})
    moment = parse_server_datetime("2026-09-30T19:36:00Z")
    assert store.exempt_today(moment)
    assert store.is_exempt(date(2026, 10, 1)) and not store.is_exempt(date(2026, 9, 30))
    assert format_clock(store.events_for_day(date(2026, 10, 1))[0]["occurred_at"]) == "03:36"
    assert DisciplineEngine(store).record_work_event("start_work", 0, 0, parse_datetime("2026-10-01T01:16:00Z")) == []


def test_mixed_offset_ledger_order_and_latest_finish():
    store = DisciplineStore("tz-test", persist=False)
    for identity, stamp in [("later", "2026-10-01T03:36:00Z"), ("earlier", "2026-10-01T09:36:00+08:00")]:
        store.events.append({"id": identity, "event_type": "finish_work", "event_date": "2026-10-01", "occurred_at": stamp})
    rows = store.events_for_day(date(2026, 10, 1))
    assert [row["id"] for row in rows] == ["earlier", "later"]
    assert [row["id"] for row in discipline_events(rows)] == ["later"]


def test_week_uses_beijing_monday_and_splits_natural_midnight():
    instant = parse_datetime("2026-10-04T16:30:00Z")
    assert instant.weekday() == 0
    store = DisciplineStore("tz-test", persist=False)
    store.append_event("rest_day", instant)
    assert store.events_for_week(instant.date())[0]["event_date"] == "2026-10-05"
    row = segment("2026-09-30T15:50:00Z", "2026-09-30T16:10:00Z")
    for day in [date(2026, 9, 30), date(2026, 10, 1)]:
        assert aggregate_focus_time([row], beijing_day_start(day), beijing_day_start(day + timedelta(days=1)), now=parse_datetime("2026-10-01T02:00:00Z")).total_seconds == 600


def test_today_and_history_ledger_render_real_server_utc_event(app, monkeypatch):
    import onepic_desktop_pet.discipline_ui as ui
    moment = parse_datetime("2026-10-01T04:00:00Z")
    monkeypatch.setattr(ui, "local_work_time", lambda: moment)
    store = DisciplineStore("tz-test", persist=False)
    store.events.append({"id": "utc-exempt", "event_type": "rest_day", "event_date": "2026-10-01", "occurred_at": "2026-10-01T03:36:00Z", "metadata": {}})
    panel = ui.DisciplineWorkspace(store, DisciplineEngine(store), lambda: (0, 0))
    panel._render_summaries()
    assert "11:36" in panel.today_events.text() and "03:36" not in panel.today_events.text()
    panel.show()
    panel.tabs.setCurrentWidget(panel.records)
    panel.records.setCurrentIndex(2)
    app.processEvents()
    panel.ledger.selectRow(0)
    panel._show_history_day()
    assert "11:36" in panel.history_detail.text() and "03:36" not in panel.history_detail.text()
    panel.close()


def test_qt_alarm_editor_displays_beijing_and_keeps_same_instant(app):
    from onepic_desktop_pet.alarm_manager import Alarm
    from onepic_desktop_pet.alarm_ui import AlarmEditDialog
    panel = AlarmEditDialog([], Alarm("utc-alarm", "测试", "2026-10-01T03:36:00Z"))
    assert panel.trigger.dateTime().toString("yyyy-MM-dd HH:mm") == "2026-10-01 11:36"
    assert panel.trigger.dateTime().toSecsSinceEpoch() == int(parse_datetime("2026-10-01T03:36:00Z").timestamp())
    panel.close()


def test_report_and_discipline_share_actual_start_and_calendar(tmp_path):
    from onepic_desktop_pet.focus_analytics import FocusAnalyticsStore
    from onepic_desktop_pet.work_timer import WorkTimerModel
    from onepic_desktop_pet.diary import DailyCompanionStats
    from onepic_desktop_pet.work_report import build_work_report
    now = parse_datetime("2026-10-01T04:00:00Z")
    analytics = FocusAnalyticsStore(path=tmp_path / "focus.json", now_provider=lambda: now, persist=False)
    analytics.record_session(3600, started_at=parse_datetime("2026-10-01T00:52:00Z"), completed=True)
    timer = WorkTimerModel(path=tmp_path / "timer.json", now_provider=lambda: now, persist=False)
    daily = DailyCompanionStats(path=tmp_path / "daily.json", now_provider=lambda: now, persist=False)
    report = build_work_report(analytics, timer, daily, now=now.astimezone(timezone(timedelta(hours=-7))))
    store = DisciplineStore("tz-test", persist=False)
    engine = DisciplineEngine(store, focus_sessions_provider=analytics.focus_segments, now_provider=lambda: now)
    assert report["day"]["actual_work_start"] == format_clock(engine.actual_work_start(now.date())) == "08:52"


def test_cached_interaction_day_uses_instant_but_settlement_keeps_business_date():
    store = DisciplineStore("tz-test", persist=False)
    store.coach_messages.append({"id": "legacy-date", "event_date": "2026-09-30", "occurred_at": "2026-09-30T18:30:00Z", "read": False})
    assert store.coach_messages_for_day(date(2026, 9, 30)) == []
    assert store.coach_messages_for_day(date(2026, 10, 1))[0]["id"] == "legacy-date"
    store.mark_coach_messages_read(date(2026, 10, 1))
    assert store.coach_messages[0]["read"]
    store.events.append({"id": "settlement", "event_type": "daily_report", "event_date": "2026-09-30", "occurred_at": "2026-09-30T16:00:01Z"})
    assert store.events_for_day(date(2026, 9, 30))[0]["id"] == "settlement"
