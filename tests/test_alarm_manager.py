"""Alarm scheduling tests; deliberately independent of Qt and network."""

import json
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


def test_alarm_times_are_aligned_to_the_start_of_the_minute(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 9, 0, 12))
    manager = AlarmManager(tmp_path / "alarms.json", now_provider=clock)
    alarm = manager.add("整秒闹钟", "2026-08-19T09:15:42")

    assert alarm.trigger_at.startswith("2026-08-19T09:15:00")

    manager.update(alarm.id, trigger_at="2026-08-19T09:20:59")
    assert alarm.trigger_at.startswith("2026-08-19T09:20:00")


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


def test_stale_one_off_alarm_is_skipped_without_a_late_popup(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 12, 10, 1))
    manager = AlarmManager(tmp_path / "alarms.json", now_provider=clock)
    alarm = manager.add("错过的闹钟", "2026-08-19T12:00:00")

    assert manager.claim_due() == []
    restored = manager.get(alarm.id)
    assert restored is not None
    assert restored.enabled is True
    assert restored.active is False
    assert restored.snooze_until is None
    assert restored.last_triggered_slot.startswith("2026-08-19T12:00:00")

    clock.value += timedelta(minutes=30)
    assert manager.claim_due() == []


def test_explicit_snooze_still_retries_after_a_delayed_scheduler_tick(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 12, 0))
    manager = AlarmManager(tmp_path / "alarms.json", now_provider=clock)
    alarm = manager.add("主动贪睡", clock.value)
    manager.claim_due()
    manager.snooze(alarm.id, 10)

    clock.value += timedelta(minutes=21)
    assert manager.claim_due() == []
    restored = manager.get(alarm.id)
    assert restored is not None
    assert restored.snooze_until.startswith("2026-08-19T12:51:00")

    clock.value += timedelta(minutes=30)
    assert [item.id for item in manager.claim_due()] == [alarm.id]


def test_persisted_daily_alarm_does_not_catch_up_after_restart(tmp_path) -> None:
    """Restarting in the afternoon must not replay a missed morning slot."""

    path = tmp_path / "alarms.json"
    before = Clock(datetime(2026, 8, 26, 9, 0))
    first = AlarmManager(path, now_provider=before)
    alarm = first.add(
        "工作日开工",
        "2026-08-26T10:00:00",
        repeat_rule=REPEAT_DAILY,
    )

    after = Clock(datetime(2026, 8, 26, 17, 0))
    restarted = AlarmManager(path, now_provider=after)
    assert restarted.claim_due() == []
    restored = restarted.get(alarm.id)
    assert restored is not None
    assert restored.snooze_until is None
    assert restored.last_triggered_slot.startswith("2026-08-26T10:00:00")

    after.value = datetime(2026, 8, 27, 10, 0)
    assert [item.id for item in restarted.claim_due()] == [alarm.id]


def test_fired_alarm_is_not_replayed_after_restart(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 12, 0))
    path = tmp_path / "alarms.json"
    manager = AlarmManager(path, now_provider=clock)
    alarm = manager.add("未处理的闹钟", clock.value)
    manager.claim_due()

    # The firing card may be left open when the process is killed.  Restart
    # must not resurrect its popup/audio or turn the old slot into a retry.
    clock.value += timedelta(hours=3)
    reloaded = AlarmManager(path, now_provider=clock)
    assert reloaded.claim_due() == []
    restored = reloaded.get(alarm.id)
    assert restored is not None
    assert restored.active is False
    assert restored.snooze_until is None
    assert reloaded.active() == []


def test_daily_alarm_retry_does_not_fire_the_original_slot_again(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 9, 0))
    manager = AlarmManager(tmp_path / "alarms.json", now_provider=clock)
    alarm = manager.add("每日重试", clock.value, repeat_rule=REPEAT_DAILY)
    manager.claim_due()

    clock.value += timedelta(minutes=10, seconds=1)
    assert manager.claim_due() == []
    clock.value += timedelta(minutes=30)
    assert [item.id for item in manager.claim_due()] == [alarm.id]
    manager.dismiss(alarm.id)

    # The retry is the same day's occurrence; it must not immediately replay
    # the original 09:00 slot after the user closes the retry card.
    assert manager.claim_due() == []

    clock.value = datetime(2026, 8, 20, 9, 0)
    assert [item.id for item in manager.claim_due()] == [alarm.id]


def test_due_alarms_only_claim_one_foreground_alarm(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 9, 0))
    manager = AlarmManager(tmp_path / "alarms.json", now_provider=clock)
    first = manager.add("第一个", clock.value)
    second = manager.add("第二个", clock.value)

    assert [item.id for item in manager.claim_due()] == [first.id]
    assert manager.get(first.id).active is True
    assert manager.get(second.id).active is False


def test_dnd_skips_regular_alarm_but_allows_explicit_breakthrough(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 9, 0))
    manager = AlarmManager(tmp_path / "alarms.json", now_provider=clock)
    quiet = manager.add("普通提醒", clock.value)
    urgent = manager.add("重要闹钟", clock.value, allow_during_dnd=True)

    claimed = manager.claim_due(allow_during_dnd=False)
    assert [item.id for item in claimed] == [urgent.id]
    assert manager.get(quiet.id).active is False


def test_alarm_configuration_survives_reload_without_runtime_popup_state(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 9, 0))
    path = tmp_path / "alarms.json"
    manager = AlarmManager(path, now_provider=clock)
    alarm = manager.add("持久化闹钟", clock.value, repeat_rule=REPEAT_DAILY, sound_enabled=True)
    manager.claim_due()

    reloaded = AlarmManager(path, now_provider=clock)
    restored = reloaded.get(alarm.id)
    assert restored is not None
    assert restored.active is False
    assert restored.sound_enabled is True
    assert restored.last_triggered_slot == alarm.last_triggered_slot
    assert reloaded.active() == []


def test_alarm_json_never_persists_active_runtime_state(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 9, 0))
    path = tmp_path / "alarms.json"
    manager = AlarmManager(path, now_provider=clock)
    alarm = manager.add("不持久化响铃中", clock.value)
    manager.claim_due()

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored[0]["active"] is False
    assert stored[0]["last_triggered_slot"] == alarm.last_triggered_slot


def test_legacy_active_row_is_normalized_without_replaying_old_slot(tmp_path) -> None:
    path = tmp_path / "alarms.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "legacy-10am",
                    "title": "旧闹钟",
                    "trigger_at": "2026-08-19T10:00:00",
                    "repeat_rule": REPEAT_DAILY,
                    "enabled": True,
                    "sound_enabled": True,
                    "sound_id": "old-custom",
                    "active": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    manager = AlarmManager(
        path,
        now_provider=Clock(datetime(2026, 8, 19, 15, 0)),
    )

    alarm = manager.get("legacy-10am")
    assert alarm is not None
    assert alarm.active is False
    assert alarm.snooze_until is None
    assert alarm.last_triggered_slot.startswith("2026-08-19T10:00:00")
    assert manager.claim_due() == []


def test_reenabling_after_today_slot_has_passed_waits_for_the_next_occurrence(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 9, 0))
    manager = AlarmManager(tmp_path / "alarms.json", now_provider=clock)
    alarm = manager.add("重新开启", "2026-08-19T10:00:00", repeat_rule=REPEAT_DAILY)

    clock.value = datetime(2026, 8, 19, 15, 0)
    manager.set_enabled(alarm.id, False)
    manager.set_enabled(alarm.id, True)
    assert manager.claim_due() == []
    assert manager.get(alarm.id).last_triggered_slot.startswith("2026-08-19T10:00:00")

    clock.value = datetime(2026, 8, 20, 10, 0)
    assert [item.id for item in manager.claim_due()] == [alarm.id]


def test_schedule_generation_changes_when_schedule_is_replaced(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 9, 0))
    manager = AlarmManager(tmp_path / "alarms.json", now_provider=clock)
    alarm = manager.add("代际令牌", clock.value)
    initial = alarm.schedule_generation

    manager.update(alarm.id, trigger_at="2026-08-19T10:00:00")
    assert alarm.schedule_generation > initial
    updated = alarm.schedule_generation
    manager.set_enabled(alarm.id, False)
    manager.set_enabled(alarm.id, True)
    assert alarm.schedule_generation > updated


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
    mirrored = [item for item in alarms.items if item.source_todo_id == task.id]
    assert len(mirrored) == 1
    assert mirrored[0].enabled is False
    assert mirrored[0].disabled_reason == "todo_reminder_changed"


def test_disabled_alarm_survives_next_day_and_reload(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 18, 54))
    path = tmp_path / "alarms.json"
    manager = AlarmManager(path, now_provider=clock)
    alarm = manager.add("下班", clock.value)
    manager.dismiss(alarm.id)
    clock.value = datetime(2026, 8, 20, 9, 0)
    restored = AlarmManager(path, now_provider=clock).get(alarm.id)
    assert restored is not None
    assert restored.enabled is False
    assert restored.disabled_reason == "dismissed"


def test_user_disabled_todo_alarm_is_not_reopened_by_startup_sync(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 9, 0))
    alarms = AlarmManager(tmp_path / "alarms.json", now_provider=clock)
    todos = TodoManager(tmp_path / "todos.json", now_provider=clock)
    task = todos.add("组会", date="2026-08-19", time="10:00", reminder_mode=REMINDER_ALARM, reminder=True)
    alarms.sync_todo(task, reminder_mode=REMINDER_ALARM)
    alarms.set_enabled(f"todo:{task.id}", False)
    alarms.sync_todo(task, reminder_mode=REMINDER_ALARM)
    restored = alarms.get(f"todo:{task.id}")
    assert restored is not None
    assert restored.enabled is False
    assert restored.disabled_reason == "user"


def test_new_timed_todo_defaults_to_quiet_pet_reminder_and_can_disable(tmp_path) -> None:
    clock = Clock(datetime(2026, 8, 19, 9, 0))
    todos = TodoManager(tmp_path / "todos.json", now_provider=clock)
    task = todos.add("普通待办", date="2026-08-19", time="10:00")
    assert task.reminder_mode == REMINDER_PET
    assert task.reminder is True
    task = todos.update(task.id, reminder_mode=REMINDER_NONE)
    assert task.reminder_mode == REMINDER_NONE
    assert task.reminder is False
