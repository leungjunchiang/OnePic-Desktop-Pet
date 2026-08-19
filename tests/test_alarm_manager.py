"""Alarm scheduling tests; deliberately independent of Qt and network."""

from datetime import datetime, timedelta

from onepic_desktop_pet.alarm_manager import (
    AlarmManager,
    REPEAT_DAILY,
    REPEAT_WEEKDAYS,
)
from onepic_desktop_pet.todo_manager import REMINDER_ALARM, REMINDER_NONE, REMINDER_PET, TodoManager


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def test_one_off_alarm_claims_once_and_dismisses(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 9, 0))
    manager = AlarmManager(tmp_path / "alarms.json", now_provider=clock)
    alarm = manager.add("开始学习", "2026-08-19T09:00:00")

    assert [item.id for item in manager.claim_due()] == [alarm.id]
    assert manager.claim_due() == []
    assert manager.active()[0].id == alarm.id

    manager.dismiss(alarm.id)
    assert manager.active() == []
    assert manager.get(alarm.id).enabled is False


def test_snooze_requeues_the_same_alarm_without_duplicate_claim(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 9, 0))
    manager = AlarmManager(tmp_path / "alarms.json", now_provider=clock)
    alarm = manager.add("开工", clock.value)
    manager.claim_due()
    manager.snooze(alarm.id, 10)

    clock.value += timedelta(minutes=9, seconds=59)
    assert manager.claim_due() == []
    clock.value += timedelta(seconds=1)
    assert [item.id for item in manager.claim_due()] == [alarm.id]


def test_daily_alarm_is_claimed_once_per_day(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 9, 1))
    manager = AlarmManager(tmp_path / "alarms.json", now_provider=clock)
    alarm = manager.add("每日开工", "2026-08-19T09:00:00", repeat_rule=REPEAT_DAILY)

    assert [item.id for item in manager.claim_due()] == [alarm.id]
    manager.dismiss(alarm.id)
    assert manager.claim_due() == []

    clock.value = datetime(2026, 8, 20, 8, 59)
    assert manager.claim_due() == []
    clock.value = datetime(2026, 8, 20, 9, 1)
    assert [item.id for item in manager.claim_due()] == [alarm.id]


def test_weekday_alarm_skips_weekends(tmp_path) -> None:
    # 2026-08-22 is Saturday; the previous weekday occurrence is already
    # outside the default grace window, so it must not ring on the weekend.
    clock = Clock(datetime(2026, 8, 22, 9, 5))
    manager = AlarmManager(tmp_path / "alarms.json", now_provider=clock)
    alarm = manager.add("工作日开工", "2026-08-17T09:00:00", repeat_rule=REPEAT_WEEKDAYS)

    assert manager.claim_due() == []
    assert manager.get(alarm.id).active is False


def test_stale_one_off_alarm_is_not_replayed_after_restart(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 15, 0))
    manager = AlarmManager(tmp_path / "alarms.json", now_provider=clock)
    alarm = manager.add("错过的闹钟", "2026-08-19T12:00:00")

    assert manager.claim_due() == []
    assert manager.get(alarm.id).enabled is False


def test_dnd_skips_regular_alarm_but_allows_explicit_breakthrough(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 9, 0))
    manager = AlarmManager(tmp_path / "alarms.json", now_provider=clock)
    quiet = manager.add("普通提醒", clock.value)
    urgent = manager.add("重要闹钟", clock.value, allow_during_dnd=True)

    claimed = manager.claim_due(allow_during_dnd=False)
    assert [item.id for item in claimed] == [urgent.id]
    assert manager.get(quiet.id).active is False


def test_alarm_state_survives_reload(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 9, 0))
    path = tmp_path / "alarms.json"
    manager = AlarmManager(path, now_provider=clock)
    alarm = manager.add("持久化闹钟", clock.value, repeat_rule=REPEAT_DAILY, sound_enabled=True)
    manager.claim_due()

    reloaded = AlarmManager(path, now_provider=clock)
    restored = reloaded.get(alarm.id)
    assert restored is not None
    assert restored.active is True
    assert restored.sound_enabled is True


def test_alarm_enable_toggle_persists_and_controls_dispatch(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 9, 0))
    path = tmp_path / "alarms.json"
    manager = AlarmManager(path, now_provider=clock)
    alarm = manager.add("可关闭的闹钟", clock.value, repeat_rule=REPEAT_DAILY)

    manager.set_enabled(alarm.id, False)
    assert manager.get(alarm.id).enabled is False
    assert manager.claim_due() == []

    reloaded = AlarmManager(path, now_provider=clock)
    assert reloaded.get(alarm.id).enabled is False

    reloaded.set_enabled(alarm.id, True)
    assert reloaded.get(alarm.id).enabled is True
    assert [item.id for item in reloaded.claim_due()] == [alarm.id]


def test_todo_alarm_is_mirrored_without_duplicate_rows(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 9, 0))
    alarms = AlarmManager(tmp_path / "alarms.json", now_provider=clock)
    todos = TodoManager(tmp_path / "todos.json", now_provider=clock)
    task = todos.add(
        "组会",
        date="2026-08-19",
        time="09:00",
        reminder_mode=REMINDER_ALARM,
        reminder=True,
    )

    alarms.sync_todo(task, reminder_mode=task.reminder_mode)
    alarms.sync_todo(task, reminder_mode=task.reminder_mode)
    mirrored = [item for item in alarms.items if item.source_todo_id == task.id]
    assert len(mirrored) == 1
    assert mirrored[0].sound_enabled is True
    assert mirrored[0].max_ring_seconds == 60

    task = todos.update(task.id, reminder_mode=REMINDER_PET)
    alarms.sync_todo(task, reminder_mode=task.reminder_mode)
    assert not [item for item in alarms.items if item.source_todo_id == task.id]


def test_new_timed_todo_defaults_to_quiet_pet_reminder_and_can_disable(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 9, 0))
    todos = TodoManager(tmp_path / "todos.json", now_provider=clock)
    task = todos.add("普通待办", date="2026-08-19", time="10:00")
    assert task.reminder_mode == REMINDER_PET
    assert task.reminder is True
    task = todos.update(task.id, reminder_mode=REMINDER_NONE)
    assert task.reminder_mode == REMINDER_NONE
    assert task.reminder is False

