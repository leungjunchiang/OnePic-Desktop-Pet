"""验证训导事件、逐日计划、提醒开关、账号合并与监督隐私边界。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from onepic_desktop_pet.discipline import (
    BEIJING_TIMEZONE,
    DisciplineEngine,
    DisciplineSettings,
    DisciplineStore,
)


def _time(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=BEIJING_TIMEZONE)


def _enabled_store(tmp_path, mode="normal") -> DisciplineStore:
    store = DisciplineStore("account-a", path=tmp_path / "discipline.json")
    settings = DisciplineSettings.from_dict({"mode": mode})
    store.update_settings(settings)
    return store


def test_settings_are_account_local_and_sync_merge_protects_newer_local_values(tmp_path) -> None:
    store = _enabled_store(tmp_path)
    store.settings.start_time = "08:30"
    store.update_settings(store.settings)
    store.append_event("start_work", _time(30, 8, 30))

    old_remote = {
        "client_updated_at": "2026-09-29T00:00:00+08:00",
        "settings": {"mode": "off", "start_time": "10:00"},
        "events": [],
    }
    store.merge_remote(old_remote)
    assert store.settings.mode == "normal"
    assert store.settings.start_time == "08:30"

    reloaded = DisciplineStore("account-a", path=store.path)
    assert reloaded.settings.start_time == "08:30"
    assert len(reloaded.events) == 1


def test_remote_settings_and_append_only_events_merge_idempotently(tmp_path) -> None:
    store = DisciplineStore("account-a", path=tmp_path / "discipline.json")
    store.merge_remote({
        "client_updated_at": "2026-09-29T09:00:00+08:00",
        "settings": {"mode": "officer", "weekly_target_minutes": 2100},
        "events": [{
            "id": "event-1", "event_type": "late_start", "event_date": "2026-09-29",
            "occurred_at": "2026-09-29T09:45:00+08:00",
            "metadata": {"rule_key": "2026-09-29:late_start", "minutes_late": 45},
            "requires_explanation": True, "explanation": None,
        }],
    })
    store.merge_remote({
        "events": [{
            "id": "different-device-id", "event_type": "late_start", "event_date": "2026-09-29",
            "occurred_at": "2026-09-29T09:45:00+08:00",
            "metadata": {"rule_key": "2026-09-29:late_start", "minutes_late": 45},
            "requires_explanation": True, "explanation": {"reason": "commute", "note": ""},
        }],
    })
    assert len(store.events) == 1
    assert store.events[0]["explanation"]["reason"] == "commute"
    assert "2026-09-29:late_start" in store.fired_rules


def test_late_rules_escalate_once_and_start_is_only_counted_once_per_day(tmp_path) -> None:
    store = _enabled_store(tmp_path, "officer")
    engine = DisciplineEngine(store)
    first = engine.evaluate(0, 0, _time(30, 9, 10))
    repeated = engine.evaluate(0, 0, _time(30, 9, 11))
    second = engine.evaluate(0, 0, _time(30, 9, 30))
    third = engine.evaluate(0, 0, _time(30, 10, 0))
    assert len(first) == 1
    assert repeated == []
    assert len(second) == len(third) == 1

    engine.record_work_event("start_work", 0, 0, _time(30, 9, 37), metadata={"session_id": "s1"})
    engine.record_work_event("start_work", 0, 0, _time(30, 11, 0), metadata={"session_id": "s2"})
    late_events = [row for row in store.events_for_day(_time(30, 12).date()) if row["event_type"] == "late_start"]
    assert len(late_events) == 1
    assert late_events[0]["metadata"]["minutes_late"] == 37


def test_break_nodes_and_explanation_are_rule_based_and_snooze_suppresses_notices(tmp_path) -> None:
    store = _enabled_store(tmp_path, "officer")
    engine = DisciplineEngine(store)
    engine.record_work_event("start_break", 0, 0, _time(30, 10), metadata={"session_key": "session-1"})
    assert [n.title for n in engine.break_notices(_time(30, 10, 18))] == ["训导主任：还有 2 分钟"]
    assert [n.title for n in engine.break_notices(_time(30, 10, 20))] == ["训导主任：课间结束"]
    assert [n.title for n in engine.break_notices(_time(30, 10, 30))] == ["训导主任：休息已 30 分钟"]
    engine.store.settings.snooze_until = _time(30, 23, 59).isoformat()
    assert engine.break_notices(_time(30, 10, 45)) == []

    engine.store.settings.snooze_until = ""
    engine.record_work_event("end_break", 0, 0, _time(30, 10, 45), metadata={"session_key": "session-1"})
    long_breaks = [row for row in store.events_for_day(_time(30, 12).date()) if row["event_type"] == "long_break"]
    assert len(long_breaks) == 1
    assert long_breaks[0]["requires_explanation"] is True


def test_finish_records_daily_summary_and_optional_weekly_carry(tmp_path) -> None:
    store = _enabled_store(tmp_path, "officer")
    store.settings.carry_across_weeks = True
    store.update_settings(store.settings)
    engine = DisciplineEngine(store)
    engine.record_work_event("start_work", 4 * 3600, 20 * 3600, _time(25, 9, 0))
    notices = engine.record_work_event("finish_work", 4 * 3600, 20 * 3600, _time(25, 17, 0))
    assert any(notice.event_type == "weekly_shortfall" for notice in notices)
    report = next(row for row in store.events_for_day(_time(25, 17).date()) if row["event_type"] == "daily_report")
    assert report["metadata"]["today_seconds"] == 4 * 3600
    assert report["metadata"]["weekly_remaining_seconds"] == 10 * 3600

    next_week = engine.progress(0, 0, _time(28, 9, 0))
    assert next_week.weekly_target_seconds == 40 * 3600
    assert next_week.weekly_remaining_seconds == 40 * 3600


def test_normal_mode_keeps_default_week_boundary_and_finish_gap_is_deduplicated(tmp_path) -> None:
    store = _enabled_store(tmp_path, "normal")
    engine = DisciplineEngine(store)
    engine.record_work_event("finish_work", 4 * 3600, 20 * 3600, _time(25, 17, 0))
    again = engine.record_work_event("finish_work", 4 * 3600, 20 * 3600, _time(25, 17, 1))
    assert any(row["event_type"] == "weekly_shortfall" for row in store.events)
    assert not any(notice.event_type == "focus_shortfall" for notice in again)
    assert len([row for row in store.events if row["event_type"] == "daily_report"]) == 1
    assert engine.progress(0, 0, _time(28, 9, 0)).weekly_target_seconds == 30 * 3600


def test_server_migration_isolates_discipline_and_gates_reports_on_explicit_consent() -> None:
    migration = (
        Path(__file__).resolve().parents[1] / "supabase" / "migrations"
        / "20260930100000_lili_discipline_state.sql"
    ).read_text(encoding="utf-8")
    assert "alter table public.lili_discipline_settings enable row level security" in migration
    assert "alter table public.lili_discipline_events enable row level security" in migration
    assert "excluded.client_updated_at > public.lili_discipline_settings.client_updated_at" in migration
    assert "create unique index if not exists lili_discipline_events_dedupe_idx" in migration
    assert "where a.owner_id = p_owner_id and a.supervisor_id = me" in migration
    assert "public.lili_are_buddies(me, p_owner_id)" in migration
    assert "lili_mark_discipline_report_read" in migration
    supervisor_report = migration.split(
        "create or replace function public.lili_discipline_supervisor_report", 1
    )[1].split("create or replace function public.lili_mark_discipline_report_read", 1)[0]
    assert "'metadata', e.metadata" not in supervisor_report
    assert "'metadata', jsonb_build_object(" in supervisor_report
    assert "octet_length(p_settings::text) > 16384" in migration
    assert "octet_length(item::text) > 8192" in migration


def test_per_day_schedule_preserves_legacy_settings_and_overnight_work():
    legacy = DisciplineSettings.from_dict({"start_time": "08:30", "finish_time": "17:30"})
    day = _time(30, 9).date()
    assert legacy.start_at(day).strftime("%H:%M") == "08:30"
    assert legacy.finish_at(day).strftime("%H:%M") == "17:30"
    schedule = DisciplineSettings.from_dict({
        "daily_start_times": {"wed": "22:00", "thu": "invalid"},
        "daily_finish_times": {"wed": "06:00"},
    })
    assert schedule.finish_at(day).date() == day + timedelta(days=1)
    assert schedule.start_at(day + timedelta(days=1)).strftime("%H:%M") == "09:00"


def test_normal_rule_opt_out_suppresses_reminders_without_removing_history(tmp_path):
    store = _enabled_store(tmp_path)
    store.update_settings({**vars(store.settings), "reminder_rules": {"start": False, "early": False, "daily": False, "weekly": False}})
    engine = DisciplineEngine(store)
    assert not engine.evaluate(0, 0, _time(30, 10))
    assert not engine.record_work_event("start_work", 0, 0, _time(30, 10))
    assert any(row["event_type"] == "late_start" for row in store.events)
    assert not engine.record_work_event("finish_work", 3600, 3600, _time(30, 15))
    assert any(row["event_type"] == "daily_report" for row in store.events)
    assert not any(row.get("requires_explanation") for row in store.events)


def test_daily_rest_summary_and_rest_day_do_not_create_lateness(tmp_path):
    store = _enabled_store(tmp_path)
    engine = DisciplineEngine(store)
    engine.record_work_event("start_break", 0, 0, _time(30, 10), metadata={"session_key": "round"})
    engine.record_work_event("end_break", 0, 0, _time(30, 10, 15), metadata={"session_key": "round"})
    summary = engine.daily_summary(_time(30, 10).date(), 0, 0)
    assert summary["rest_seconds"] == 900
    engine.record_work_event("start_work", 0, 0, _time(26, 14))
    assert not any(row["event_type"] == "late_start" for row in store.events_for_day(_time(26, 14).date()))
