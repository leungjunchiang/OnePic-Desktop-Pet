"""TodoCenter aggregation tests."""

from datetime import datetime

from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.time_memory import TimeMemory
from onepic_desktop_pet.todo_center import TodoCenterWindow, _ItemEditor
from onepic_desktop_pet.todo_manager import REMINDER_ALARM, REMINDER_PET


def _qt_app():
    return QApplication.instance() or QApplication([])


def test_todo_center_separates_timeline_from_important_dates(tmp_path) -> None:
    app = _qt_app()
    memory = TimeMemory(
        tmp_path,
        now_provider=lambda: datetime(2026, 8, 15, 12, 0),
    )
    today = memory.todos.add("今天写论文")
    future = memory.todos.add("明天发材料", date="2026-08-16")
    near = memory.countdowns.add("Codex重置", "2026-08-20", show_before_days=7)
    far = memory.countdowns.add("开学", "2026-09-01", show_before_days=7)
    anniversary = memory.anniversaries.add("六毛纪念日", "2026-08-20")
    center = TodoCenterWindow(memory)

    items = center._all_items()
    assert {item.id for item in items} >= {
        today.id,
        future.id,
        f"countdown:{near.id}",
        f"countdown:{far.id}",
        f"anniversary:{anniversary.id}",
    }
    assert len(memory.todos.items) == 2
    by_id = {item.id: item for item in items}
    assert center._partition(by_id[today.id], "today")
    assert center._partition(by_id[future.id], "upcoming")
    assert center._partition(by_id[f"countdown:{near.id}"], "upcoming")
    assert not center._partition(by_id[f"countdown:{far.id}"], "upcoming")
    assert center._partition(by_id[f"countdown:{far.id}"], "events")
    assert center._partition(by_id[f"anniversary:{anniversary.id}"], "events")
    assert not center._partition(by_id[f"countdown:{far.id}"], "today")
    center.close()
    center.deleteLater()
    app.processEvents()


def test_todo_center_completed_view_reads_original_items(tmp_path) -> None:
    app = _qt_app()
    memory = TimeMemory(
        tmp_path,
        now_provider=lambda: datetime(2026, 8, 15, 12, 0),
    )
    item = memory.todos.add("已完成事项")
    memory.todos.complete(item.id)
    center = TodoCenterWindow(memory)
    rows = center._all_items()
    completed = next(row for row in rows if row.id == item.id)
    assert completed.completed is True
    assert center._partition(completed, "completed")
    center.close()
    center.deleteLater()
    app.processEvents()


def test_reminder_editor_preserves_selected_audible_alarm(tmp_path) -> None:
    app = _qt_app()
    memory = TimeMemory(
        tmp_path,
        now_provider=lambda: datetime(2026, 8, 19, 12, 0),
    )
    task = memory.todos.add(
        "贵阳站",
        date="2026-08-19",
        time="13:00",
        reminder_mode=REMINDER_PET,
        reminder=True,
    )
    center = TodoCenterWindow(memory)
    center_item = next(row for row in center._all_items() if row.id == task.id)
    editor = _ItemEditor(memory, center_item)
    editor.reminder_mode.setCurrentIndex(editor.reminder_mode.findData(REMINDER_ALARM))
    editor.save()

    saved = memory.todos.get(task.id)
    assert saved is not None
    assert saved.reminder_mode == REMINDER_ALARM
    assert saved.reminder is True
    mirrored = [alarm for alarm in memory.alarms.items if alarm.source_todo_id == task.id]
    assert len(mirrored) == 1
    assert mirrored[0].sound_enabled is True

    reloaded = TimeMemory(
        tmp_path,
        now_provider=lambda: datetime(2026, 8, 19, 12, 0),
    )
    restored = reloaded.todos.get(task.id)
    assert restored is not None
    assert restored.reminder_mode == REMINDER_ALARM
    reopened_center = TodoCenterWindow(reloaded)
    reopened_item = next(row for row in reopened_center._all_items() if row.id == task.id)
    reopened_editor = _ItemEditor(reloaded, reopened_item)
    assert reopened_editor.reminder_mode.currentData() == REMINDER_ALARM

    editor.close()
    editor.deleteLater()
    reopened_editor.close()
    reopened_editor.deleteLater()
    reopened_center.close()
    reopened_center.deleteLater()
    center.close()
    center.deleteLater()
    app.processEvents()
