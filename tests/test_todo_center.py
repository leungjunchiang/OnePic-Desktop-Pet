"""TodoCenter aggregation tests."""

from datetime import datetime

from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.time_memory import TimeMemory
from onepic_desktop_pet.todo_center import TodoCenterWindow


def _qt_app():
    return QApplication.instance() or QApplication([])


def test_todo_center_uses_one_shared_store_and_four_views(tmp_path) -> None:
    app = _qt_app()
    memory = TimeMemory(
        tmp_path,
        now_provider=lambda: datetime(2026, 8, 15, 12, 0),
    )
    today = memory.todos.add("今天写论文")
    future = memory.todos.add("明天发材料", date="2026-08-16")
    countdown = memory.countdowns.add("答辩", "2026-09-01")
    anniversary = memory.anniversaries.add("六毛纪念日", "2026-08-20")
    center = TodoCenterWindow(memory)

    items = center._all_items()
    assert {item.id for item in items} >= {
        today.id,
        future.id,
        f"countdown:{countdown.id}",
        f"anniversary:{anniversary.id}",
    }
    assert len(memory.todos.items) == 2
    assert center._partition(next(item for item in items if item.id == today.id), "today")
    assert center._partition(next(item for item in items if item.id == future.id), "upcoming")
    assert center._partition(
        next(item for item in items if item.id == f"countdown:{countdown.id}"),
        "events",
    )
    assert not center._partition(
        next(item for item in items if item.id == f"countdown:{countdown.id}"),
        "today",
    )
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
