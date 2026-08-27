"""Local time-memory feature tests; no Qt, network or real user directory."""

from datetime import datetime, timedelta

import pytest

from onepic_desktop_pet.anniversary_manager import AnniversaryManager
from onepic_desktop_pet.countdown_manager import CountdownManager
from onepic_desktop_pet.daily_record_manager import AUTO_CHECKIN_SECONDS, DailyRecordManager
from onepic_desktop_pet.daily_summary_service import DailySummaryService
from onepic_desktop_pet.local_data import read_json, write_json_atomic
from onepic_desktop_pet.reminder_manager import ReminderManager
from onepic_desktop_pet.sticky_note_manager import StickyNoteManager
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


def test_upcoming_todo_view_shows_tomorrow_with_a_date_label(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 12, 0))
    memory = TimeMemory(tmp_path, now_provider=clock)
    task = memory.todos.add("写论文", date="tomorrow", time="03:00", reminder=True)

    assert memory.todo_view_today() == []
    upcoming = memory.todo_view_upcoming()
    item = next(entry for entry in upcoming if entry.id == task.id)
    assert item.display_text == "明天 · 写论文 · 03:00"
    assert memory.get_todo_view_item(task.id).display_text == item.display_text


def test_manual_todo_projection_can_restore_read_item(tmp_path) -> None:
    """The explicit desktop “显示待办” command can reopen a read task."""

    memory = TimeMemory(tmp_path, persist=False)
    task = memory.todos.add("已读但仍未完成")
    memory.todos.mark_read(task.id, True)

    assert memory.get_todo_view_item(task.id) is None
    restored = memory.get_todo_view_item(task.id, include_read=True)
    assert restored is not None
    assert restored.source_id == task.id
    assert restored.read is True


def test_sticky_todo_survives_midnight_and_untimed_notes_need_manual_read(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 18, 0, 5))
    memory = TimeMemory(tmp_path, now_provider=clock)
    untimed = memory.todos.add("跨天也要留着", date="2026-08-17")
    timed = memory.todos.add("昨天的定时事项", date="2026-08-17", time="23:30")

    ids = {item.id for item in memory.todo_view_upcoming()}
    assert untimed.id in ids
    assert timed.id in ids  # within the 24-hour post-due grace period

    memory.read_todo_view_item(untimed.id)
    assert untimed.id not in {item.id for item in memory.todo_view_upcoming()}
    assert memory.todos.get(untimed.id).completed is False

    clock.value = datetime(2026, 8, 19, 0, 0)
    assert timed.id not in {item.id for item in memory.todo_view_upcoming()}


def test_timed_todo_is_kept_for_24_hours_after_due(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 18, 23, 29))
    memory = TimeMemory(tmp_path, now_provider=clock)
    task = memory.todos.add("保留一天", date="2026-08-18", time="23:30")
    assert task.id in {item.id for item in memory.todo_view_upcoming()}
    clock.value = datetime(2026, 8, 19, 23, 29)
    assert task.id in {item.id for item in memory.todo_view_upcoming()}
    clock.value = datetime(2026, 8, 19, 23, 31)
    assert task.id not in {item.id for item in memory.todo_view_upcoming()}


def test_todo_priority_and_default_reminder_lead_time_persist(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 18, 9, 0))
    manager = TodoManager(tmp_path / "todos.json", now_provider=clock)
    task = manager.add(
        "提前提醒",
        date="2026-08-18",
        time="15:00",
        reminder=True,
        priority=1,
    )
    assert task.priority == 1
    assert task.remind_at[11:16] == "14:50"
    reloaded = TodoManager(tmp_path / "todos.json", now_provider=clock)
    assert reloaded.get(task.id).reminder_minutes_before == 10


def test_todo_highlight_is_independent_and_persists(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 18, 9, 0))
    manager = TodoManager(tmp_path / "todos.json", now_provider=clock)
    task = manager.add("需要强调", highlight=True)
    assert task.highlight is True
    view = TimeMemory(tmp_path, now_provider=clock).get_todo_view_item(task.id)
    assert view is not None and view.highlight is True

    manager.update(task.id, highlight=False)
    reloaded = TodoManager(tmp_path / "todos.json", now_provider=clock)
    assert reloaded.get(task.id).highlight is False


def test_todo_views_show_event_time_not_reminder_time(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 18, 9, 0))
    memory = TimeMemory(tmp_path, now_provider=clock)
    task = memory.todos.add(
        "贵阳站",
        date="2026-08-19",
        time="13:00",
        reminder=True,
        reminder_minutes_before=10,
    )

    view_item = memory.get_todo_view_item(task.id)
    assert view_item is not None
    assert view_item.time == "13:00"
    assert "13:00" in view_item.display_text
    assert "12:50" not in view_item.display_text


def test_reminder_only_todo_does_not_turn_notification_into_event_time(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 18, 9, 0))
    memory = TimeMemory(tmp_path, now_provider=clock)
    task = memory.todos.add(
        "只提醒我",
        date="2026-08-18",
        remind_at="2026-08-18T09:50:00",
        reminder=True,
    )

    assert task.due_at is None
    assert task.time is None
    view_item = memory.get_todo_view_item(task.id)
    assert view_item is not None
    assert view_item.date == ""
    assert view_item.time is None
    assert view_item.display_text == "只提醒我"


def test_legacy_inline_event_date_is_recovered_from_title(tmp_path) -> None:
    from onepic_desktop_pet.todo_manager import TodoItem

    recovered = TodoItem.from_dict(
        {
            "id": "legacy-event",
            "title": "贵阳站 8.19 13:00",
            "date": "2026-08-18",
            "time": "13:00",
            "due_at": "2026-08-18T13:00:00",
            "remind_at": "2026-08-18T12:50:00",
            "reminder": True,
            "created_at": "2026-08-18T09:00:00+08:00",
        }
    )
    assert recovered.title == "贵阳站"
    assert recovered.date == "2026-08-19"
    assert recovered.time == "13:00"
    assert recovered.due_at == "2026-08-19T13:00:00"
    assert recovered.remind_at == "2026-08-19T12:50:00"


def test_create_todo_has_required_fields_and_persists(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 9, 42))
    manager = TodoManager(tmp_path / "todos.json", now_provider=clock)
    item = manager.add("修改论文第三节", time="15:00", important=True, reminder=True)
    assert item.date == "2026-08-15"
    assert item.created_at and item.completed_at is None and item.work_seconds == 0
    reloaded = TodoManager(tmp_path / "todos.json", now_provider=clock)
    assert reloaded.get(item.id).title == "修改论文第三节"


def test_sticky_note_is_separate_and_restart_safe(tmp_path) -> None:
    note = StickyNoteManager(tmp_path / "sticky_note.json")
    note.update("记得把材料发给老师")
    reloaded = StickyNoteManager(tmp_path / "sticky_note.json")
    assert reloaded.text == "记得把材料发给老师"
    assert reloaded.path.name == "sticky_note.json"


def test_complete_todo_sets_completed_at(tmp_path) -> None:
    manager = TodoManager(tmp_path / "todos.json")
    item = manager.add("跑回归")
    manager.complete(item.id)
    assert manager.get(item.id).completed
    assert manager.get(item.id).completed_at


def test_restore_todo_does_not_replay_an_expired_alarm(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 12, 0))
    memory = TimeMemory(tmp_path, now_provider=clock)
    item = memory.todos.add(
        "恢复论文",
        date="2026-08-19",
        time="10:00",
        reminder=True,
        reminder_mode="alarm",
    )
    memory.complete_task(item.id)

    assert memory.restore_todo(item.id)
    restored = memory.todos.get(item.id)
    assert restored is not None
    assert restored.completed is False
    assert restored.reminder_suppressed is True
    assert memory.alarms.get(f"todo:{item.id}") is None


def test_restore_todo_keeps_a_future_alarm_scheduled(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 9, 0))
    memory = TimeMemory(tmp_path, now_provider=clock)
    item = memory.todos.add(
        "下午论文",
        date="2026-08-19",
        time="13:00",
        reminder=True,
        reminder_mode="alarm",
    )
    memory.complete_task(item.id)

    assert memory.restore_todo(item.id)
    restored = memory.todos.get(item.id)
    assert restored is not None
    assert restored.reminder_suppressed is False
    alarm = memory.alarms.get(f"todo:{item.id}")
    assert alarm is not None
    assert alarm.enabled is True


def test_new_todo_accepts_and_persists_reminder_suppressed(tmp_path) -> None:
    manager = TodoManager(tmp_path / "todos.json")
    item = manager.add(
        "恢复后的事项",
        date="2026-08-19",
        time="15:32",
        reminder_mode="alarm",
        reminder_suppressed=True,
    )

    assert item.reminder_suppressed is True
    reloaded = TodoManager(tmp_path / "todos.json")
    assert reloaded.get(item.id).reminder_suppressed is True


def test_todo_queue_inserts_normalizes_and_persists(tmp_path) -> None:
    manager = TodoManager(tmp_path / "todos.json")
    items = [manager.add(f"事项{index}") for index in range(1, 7)]

    manager.set_queue_position(items[0].id, 1)
    manager.set_queue_position(items[1].id, 2)
    manager.set_queue_position(items[2].id, 2)
    queued = manager.queued_items()
    assert [item.title for item in queued[:3]] == ["事项1", "事项3", "事项2"]
    assert [item.title for item in queued] == [
        "事项1", "事项3", "事项2", "事项4", "事项5", "事项6"
    ]
    assert [item.queue_position for item in queued] == [1, 2, 3, 4, 5, 6]

    manager.set_queue_position(items[3].id, 1)
    assert [item.title for item in manager.queued_items()] == [
        "事项4", "事项1", "事项3", "事项2", "事项5", "事项6"
    ]
    manager.set_queue_position(items[4].id, 5)
    manager.set_queue_position(items[5].id, 5)
    assert len(manager.queued_items()) == 6
    assert [item.queue_position for item in manager.queued_items()] == [1, 2, 3, 4, 5, 6]

    manager.complete(items[3].id)
    assert [item.queue_position for item in manager.queued_items()] == [1, 2, 3, 4, 5]
    reloaded = TodoManager(tmp_path / "todos.json")
    assert [item.title for item in reloaded.queued_items()] == [
        "事项1", "事项3", "事项2", "事项6", "事项5"
    ]


def test_todo_queue_caps_new_unfinished_items_at_ten(tmp_path) -> None:
    manager = TodoManager(tmp_path / "todos.json")
    for index in range(10):
        manager.add(f"第{index + 1}件")
    with pytest.raises(ValueError, match="10件"):
        manager.add("第11件")
    manager.complete(manager.queued_items()[0].id)
    restored = manager.add("第11件")
    assert restored.queue_position == 10
    assert [item.queue_position for item in manager.queued_items()] == list(range(1, 11))


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


def test_desktop_todo_projection_keeps_explicitly_pinned_far_event(tmp_path) -> None:
    """An event explicitly marked for the desktop bypasses its lead window."""

    clock = Clock(datetime(2026, 8, 15, 12, 0))
    memory = TimeMemory(tmp_path, now_provider=clock, persist=False)
    event = memory.countdowns.add(
        "长期重要日期",
        "2026-09-20",
        show_before_days=0,
        show_on_desktop=True,
    )

    items = memory.todo_view_desktop()
    assert [item.id for item in items] == [f"countdown:{event.id}"]


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


def test_anniversary_can_be_edited_and_deleted(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 12, 0))
    manager = AnniversaryManager(tmp_path / "anniversaries.json", now_provider=clock)
    item = manager.add("旧纪念日", "2026-09-01", repeat="none")
    manager.update(item.id, title="新纪念日", date="2026-09-02", repeat="yearly", show_before_days=14)
    assert manager.find("新纪念日").date == "2026-09-02"
    assert manager.find("新纪念日").repeat == "yearly"
    assert manager.find("新纪念日").show_before_days == 14
    assert manager.delete(item.id)
    assert manager.items == ()


def test_near_term_events_flow_into_one_todo_view_without_copying(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 12, 0))
    memory = TimeMemory(tmp_path, now_provider=clock)
    manual = memory.todos.add("修改论文")
    near = memory.countdowns.add("论文投稿", "2026-08-22")
    far = memory.countdowns.add("答辩", "2026-08-30")
    expired = memory.countdowns.add("旧截止", "2026-08-14")
    anniversary = memory.anniversaries.add("演唱会", "2026-08-20", repeat="none")

    view = memory.todo_view_today()
    ids = {item.id for item in view}
    assert manual.id in ids
    assert f"countdown:{near.id}" in ids
    assert f"countdown:{expired.id}" in ids
    assert f"countdown:{far.id}" not in ids
    assert next(item for item in view if item.source_id == near.id).display_text == "论文投稿 · 还有7天"
    assert next(item for item in view if item.source_id == anniversary.id).display_text == "演唱会 · 还有5天"
    assert len(memory.todos.items) == 1

    assert memory.complete_todo_view_item(f"countdown:{near.id}")
    assert memory.countdowns.get(near.id).completed is True
    assert memory.complete_todo_view_item(f"anniversary:{anniversary.id}")
    assert f"anniversary:{anniversary.id}" not in {item.id for item in memory.todo_view_today()}


def test_event_show_before_days_can_be_customized(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 12, 0))
    memory = TimeMemory(tmp_path, now_provider=clock)
    item = memory.countdowns.add("月底事项", "2026-08-25", show_before_days=14)
    assert f"countdown:{item.id}" in {entry.id for entry in memory.todo_view_today()}
    memory.countdowns.update(item.id, show_before_days=3)
    assert f"countdown:{item.id}" not in {entry.id for entry in memory.todo_view_today()}


def test_timeline_event_query_is_curated_and_sorted(tmp_path) -> None:
    manager = TimelineManager(tmp_path / "timeline.json")
    manager.add("投出论文", date="2026-09-01", event_type="project", important=True)
    manager.add("第一次专注", date="2026-08-15", event_type="milestone")
    assert manager.query(event_type="project")[0].title == "投出论文"
    assert manager.query()[0].date == "2026-09-01"


def test_timeline_event_can_be_deleted_without_affecting_other_records(tmp_path) -> None:
    manager = TimelineManager(tmp_path / "timeline.json")
    keep = manager.add("保留记录")
    remove = manager.add("删除记录")
    assert manager.delete(remove.id)
    assert [item.id for item in manager.events] == [keep.id]
    assert manager.delete(remove.id) is False


def test_structured_action_extracts_only_explicit_json() -> None:
    assert extract_action("有没有人告诉你") is None
    action = extract_action('先记一下：```json\n{"action":"create_todo","tasks":[{"title":"跑回归"},{"title":"发材料"}]}\n```')
    assert action["action"] == "create_todo"
    assert len(action["tasks"]) == 2


def test_structured_create_todo_action_is_local_and_durable(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 12, 0))
    memory = TimeMemory(tmp_path, now_provider=clock)
    result = memory.actions.execute({"action": "create_todo", "tasks": [{"title": "买数据线", "important": True}]})
    assert result is not None and memory.todos.find("买数据线").important
    assert TodoManager(tmp_path / "todos.json", now_provider=clock).find("买数据线") is not None


def test_chat_todo_action_writes_reminder_and_merges_similar_task(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 12, 0))
    memory = TimeMemory(tmp_path, now_provider=clock)
    old = memory.todos.add("9点论文", time="09:00", reminder=True)
    result = memory.actions.execute(
        {
            "action": "create_todo",
            "tasks": [
                {
                    "title": "修改论文",
                    "date": "2026-08-16",
                    "time": "09:30",
                    "reminder": True,
                    "source": "chat",
                }
            ],
        }
    )
    assert result is not None and result.ok is True
    assert len(memory.todos.items) == 1
    task = memory.todos.get(old.id)
    assert task is not None and task.title == "修改论文"
    assert task.date == "2026-08-16" and task.time == "09:30"
    assert len(memory.reminders.items) == 1
    assert memory.reminders.items[0].source_id == task.id


def test_chat_todo_update_complete_and_delete_are_real_local_operations(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 15, 12, 0))
    memory = TimeMemory(tmp_path, now_provider=clock)
    task = memory.todos.add("整理材料")
    updated = memory.actions.execute(
        {"action": "update_todo", "target": "整理材料", "time": "20:00", "reminder": True}
    )
    assert updated is not None and updated.ok
    assert memory.todos.get(task.id).time == "20:00"
    assert len(memory.reminders.items) == 1
    completed = memory.actions.execute({"action": "complete_todo", "target": "整理材料"})
    assert completed is not None and completed.ok and memory.todos.get(task.id).completed
    deleted = memory.actions.execute({"action": "delete_todo", "target": "整理材料"})
    assert deleted is not None and deleted.ok
    assert memory.todos.get(task.id) is None
    assert memory.reminders.items == ()


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
