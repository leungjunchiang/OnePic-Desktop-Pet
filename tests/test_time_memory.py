"""Local time-memory feature tests; no Qt, network or real user directory."""

from datetime import datetime, timedelta

from onepic_desktop_pet.anniversary_manager import AnniversaryManager
from onepic_desktop_pet.countdown_manager import CountdownManager
from onepic_desktop_pet.daily_record_manager import AUTO_CHECKIN_SECONDS, DailyRecordManager
from onepic_desktop_pet.daily_summary_service import DailySummaryService
from onepic_desktop_pet.local_data import read_json, write_json_atomic
from onepic_desktop_pet.reminder_manager import ReminderManager
from onepic_desktop_pet.structured_actions import LocalActionExecutor, extract_action
from onepic_desktop_pet.timeline_manager import TimelineManager
from onepic_desktop_pet.time_memory import TimeMemory
from onepic_desktop_pet.time_service import days_until, next_yearly_occurrence, parse_date
from onepic_desktop_pet.todo_manager import TodoManager
from onepic_desktop_pet.work_session_manager import WorkSessionManager


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def test_atomic_json_round_trip(tmp_path) -> None:
    path = tmp_path / "records.json"
    write_json_atomic(path, {"title": "六毛", "ok": True})
    assert read_json(path, {})["title"] == "六毛"
    assert not list(tmp_path.glob(".*.tmp"))


def test_parse_today_tomorrow_and_midnight() -> None:
    clock = Clock(datetime(2026, 8, 15, 23, 59))
    assert parse_date("today", clock).isoformat() == "2026-08-15"
    assert parse_date("tomorrow", clock).isoformat() == "2026-08-16"
    clock.value += timedelta(minutes=2)
    assert parse_date("today", clock).isoformat() == "2026-08-16"


def test_create_todo_has_required_fields_and_persists(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 9, 42))
    manager = TodoManager(tmp_path / "todos.json", now_provider=clock)
    item = manager.add("修改论文第三节", time="15:00", important=True, reminder=True)
    assert item.date == "2026-08-15"
    assert item.created_at and item.completed_at is None and item.work_seconds == 0
    reloaded = TodoManager(tmp_path / "todos.json", now_provider=clock)
    assert reloaded.get(item.id).title == "修改论文第三节"


def test_complete_todo_sets_completed_at(tmp_path) -> None:
    manager = TodoManager(tmp_path / "todos.json")
    item = manager.add("跑回归")
    manager.complete(item.id)
    assert manager.get(item.id).completed
    assert manager.get(item.id).completed_at


def test_completed_todo_remains_available_for_today_note(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 9, 42))
    memory = TimeMemory(tmp_path, now_provider=clock)
    item = memory.todos.add("完成后仍然保留")
    memory.select_task(item.id)
    memory.complete_task(item.id)
    assert memory.current_task_id == item.id
    assert memory.todos.today()[0].completed is True
    assert memory.todos.get(item.id) is not None


def test_important_todo_is_selected_and_work_is_attributed(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 10, 0))
    memory = TimeMemory(tmp_path, now_provider=clock)
    task = memory.todos.add("写机制部分", important=True)
    memory.select_task(task.id)
    memory.record_focus(125, completed_session=True)
    assert memory.todos.get(task.id).work_seconds == 125
    assert memory.summary.today()["focus_seconds"] == 125


def test_daily_record_auto_checkin_at_fifteen_minutes(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 9, 42))
    manager = DailyRecordManager(tmp_path / "daily.json", now_provider=clock)
    manager.record_focus(AUTO_CHECKIN_SECONDS - 1)
    assert not manager.get().checked_in
    manager.record_focus(1)
    assert manager.get().checked_in
    assert manager.get().sessions == 2


def test_checkout_generates_real_diary_summary(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 22, 16))
    memory = TimeMemory(tmp_path, now_provider=clock)
    memory.todos.add("投稿", important=True)
    memory.record_focus(4 * 3600 + 26 * 60)
    result = memory.finish_today()
    assert result["focus"] == "4小时26分钟"
    assert "4小时26分钟" in memory.records.get().diary
    assert memory.timeline.has_source("checkout:2026-08-15")


def test_rest_day_is_not_counted_as_work_day(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 12, 0))
    records = DailyRecordManager(tmp_path / "daily.json", now_provider=clock)
    records.set_rest_day(True)
    stats = records.stats(start=clock.value.date(), end=clock.value.date())
    assert stats["work_days"] == 0


def test_week_and_month_stats_use_calendar_boundaries(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 12, 0))
    manager = DailyRecordManager(tmp_path / "daily.json", now_provider=clock)
    manager.record_focus(600, date_key="2026-08-10")
    manager.record_focus(1200, date_key="2026-08-01")
    assert manager.week_stats("2026-08-15")["focus_seconds"] == 600
    assert manager.month_stats("2026-08-15")["focus_seconds"] == 1800


def test_reminder_due_once_and_snooze(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 14, 50))
    manager = ReminderManager(tmp_path / "reminders.json", now_provider=clock)
    item = manager.add("开会", "2026-08-15T15:00:00")
    assert manager.due() == []
    clock.value = datetime(2026, 8, 15, 15, 1)
    assert manager.due()[0].id == item.id
    manager.mark_notified(item.id)
    assert manager.due() == []
    manager.snooze(item.id, 10)
    assert manager.due() == []


def test_countdown_remaining_days_edit_and_delete(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 12, 0))
    manager = CountdownManager(tmp_path / "countdowns.json", now_provider=clock)
    item = manager.add("论文投稿", "2026-09-01", show_on_desktop=True)
    assert manager.remaining_days(item) == 17
    manager.update(item.id, title="终稿投稿", pinned=True)
    assert manager.find("终稿").pinned
    assert manager.delete(item.id)
    assert manager.items == ()


def test_countdown_completion_is_recorded_once_in_timeline(tmp_path) -> None:
    memory = TimeMemory(tmp_path, now_provider=Clock(datetime(2026, 8, 15, 12, 0)))
    item = memory.countdowns.add("答辩", "2026-08-16")
    assert memory.complete_countdown(item.id)
    assert memory.complete_countdown(item.id)
    assert len([event for event in memory.timeline.events if event.source == f"countdown:{item.id}"]) == 1


def test_yearly_anniversary_rolls_to_next_year(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 12, 0))
    manager = AnniversaryManager(tmp_path / "anniversaries.json", now_provider=clock)
    item = manager.add("六毛来到桌面的第一天", "2025-08-15", repeat="yearly")
    assert manager.remaining_days(item) == 0
    clock.value = datetime(2026, 8, 16, 12, 0)
    assert manager.next_date(item).isoformat() == "2027-08-15"
    assert next_yearly_occurrence("2025-08-15", clock).isoformat() == "2027-08-15"


def test_timeline_event_query_is_curated_and_sorted(tmp_path) -> None:
    manager = TimelineManager(tmp_path / "timeline.json")
    manager.add("投出论文", date="2026-09-01", event_type="project", important=True)
    manager.add("第一次专注", date="2026-08-15", event_type="milestone")
    assert manager.query(event_type="project")[0].title == "投出论文"
    assert manager.query()[0].date == "2026-09-01"


def test_structured_action_extracts_only_explicit_json() -> None:
    assert extract_action("有没有人告诉你") is None
    action = extract_action('```json\n{"action":"create_todo","tasks":[{"title":"跑回归"}]}\n```')
    assert action["action"] == "create_todo"


def test_structured_create_todo_action_is_local_and_durable(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 12, 0))
    memory = TimeMemory(tmp_path, now_provider=clock)
    result = memory.actions.execute({"action": "create_todo", "tasks": [{"title": "买数据线", "important": True}]})
    assert result is not None and memory.todos.find("买数据线").important
    assert TodoManager(tmp_path / "todos.json", now_provider=clock).find("买数据线") is not None


def test_structured_query_today_uses_local_truth(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 12, 0))
    memory = TimeMemory(tmp_path, now_provider=clock)
    memory.record_focus(900)
    result = memory.actions.execute({"action": "query_today"})
    assert result.data["focus_seconds"] == 900
    assert "猜" not in str(result.data)


def test_task_carry_over_is_explicit(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 12, 0))
    manager = TodoManager(tmp_path / "todos.json", now_provider=clock)
    item = manager.add("整理表4", date="2026-08-14")
    assert manager.today() == []
    manager.carry_over([item.id])
    assert manager.today()[0].title == "整理表4"


def test_daily_summary_reports_pending_without_mutating_task_state(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 12, 0))
    memory = TimeMemory(tmp_path, now_provider=clock)
    memory.todos.add("还没做")
    summary = memory.summary.today()
    assert summary["pending_tasks"] == ["还没做"]
    assert memory.todos.find("还没做").completed is False
