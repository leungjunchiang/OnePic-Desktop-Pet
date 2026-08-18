"""TodoCenter aggregation tests."""

from datetime import datetime

from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.time_memory import TimeMemory
from onepic_desktop_pet.todo_center import TodoCenterWindow


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
