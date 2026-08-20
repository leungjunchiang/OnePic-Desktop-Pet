"""验证待办主列表只显示任务事件时间，不泄漏创建日期。"""

from types import SimpleNamespace

from onepic_desktop_pet.todo_view import collect_todo_view, display_todo_title


def _item(title: str, created_at: str, due_at: str | None = None):
    return SimpleNamespace(
        id="todo-1",
        title=title,
        created_at=created_at,
        due_at=due_at,
        remind_at=None,
        date="",
        time=None,
        important=False,
        completed=False,
        work_seconds=0,
        priority=None,
        queue_position=None,
        read=False,
        reminder=False,
        reminder_minutes_before=10,
        reminder_mode="none",
    )


def test_main_todo_view_never_uses_created_at_as_event_date():
    item = _item("8.21 13:00可退票", "2026-08-19T08:30:00+08:00")
    view = collect_todo_view(
        [item], [], [],
        countdown_remaining=lambda _item: 0,
        anniversary_remaining=lambda _item: 0,
        anniversary_next_date=lambda _item: None,
        today_date="2026-08-20",
        show_future_dates=True,
    )[0]
    assert view.display_text == "8.21 13:00可退票"
    assert "2026-08-19" not in view.display_text


def test_legacy_date_prefix_is_removed_only_when_it_matches_created_day():
    same_day = _item("2026-08-19 · 整理数据", "2026-08-19T14:32:00+08:00")
    user_date = _item("2026-08-19 · 七月十四", "2026-08-18T14:32:00+08:00")
    assert display_todo_title(same_day) == "整理数据"
    assert display_todo_title(user_date) == "2026-08-19 · 七月十四"
