"""The unified local Todo view.

Manual Todos remain the source of truth for tasks. Countdown and anniversary
records are projected into this view while they are close enough to be useful;
no duplicate Todo record is created.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable


@dataclass(frozen=True)
class TodoViewItem:
    """A Todo-shaped item that can be rendered by every Todo surface."""

    id: str
    title: str
    date: str
    time: str | None
    important: bool
    completed: bool
    created_at: str
    work_seconds: int
    source_type: str
    source_id: str
    display_text: str
    remaining_days: int | None = None


def _event_label(remaining_days: int, *, annual: bool = False) -> str:
    if remaining_days < 0:
        return f"已过期{-remaining_days}天"
    if remaining_days == 0:
        return "今天"
    if remaining_days == 1:
        return "明天"
    return f"还有{remaining_days}天"


def _todo_date_label(item_date: str, today_date: str) -> str:
    """Give future scheduled todos the same date cue as countdowns."""

    try:
        remaining_days = (date.fromisoformat(item_date) - date.fromisoformat(today_date)).days
    except (TypeError, ValueError):
        return item_date
    if remaining_days == 1:
        return "明天"
    if remaining_days > 1:
        return f"还有{remaining_days}天"
    return "今天" if remaining_days == 0 else item_date


def collect_todo_view(
    todos: Iterable[Any],
    countdowns: Iterable[Any],
    anniversaries: Iterable[Any],
    *,
    countdown_remaining,
    anniversary_remaining,
    anniversary_next_date,
    today_date: str | None = None,
    show_future_dates: bool = False,
) -> list[TodoViewItem]:
    """Merge ordinary Todos and near-term countdowns/anniversaries."""

    result: list[TodoViewItem] = []
    for item in todos:
        text = str(item.title)
        if getattr(item, "time", None):
            text += f" · {item.time}"
        if show_future_dates and today_date and str(item.date) != today_date:
            text = f"{_todo_date_label(str(item.date), today_date)} · {text}"
        result.append(
            TodoViewItem(
                id=str(item.id), title=str(item.title), date=str(item.date),
                time=getattr(item, "time", None),
                important=bool(getattr(item, "important", False)),
                completed=bool(getattr(item, "completed", False)),
                created_at=str(getattr(item, "created_at", "") or ""),
                work_seconds=max(0, int(getattr(item, "work_seconds", 0) or 0)),
                source_type="todo", source_id=str(item.id), display_text=text,
            )
        )

    for item in countdowns:
        if bool(getattr(item, "completed", False)):
            continue
        remaining = int(countdown_remaining(item))
        threshold = max(0, min(365, int(getattr(item, "show_before_days", 7) or 0)))
        if remaining > threshold:
            continue
        title = str(item.title)
        result.append(
            TodoViewItem(
                id=f"countdown:{item.id}", title=title,
                date=str(item.target_datetime)[:10], time=None,
                important=bool(getattr(item, "pinned", False)), completed=False,
                created_at=str(getattr(item, "created_at", "") or ""),
                work_seconds=0, source_type="countdown", source_id=str(item.id),
                display_text=f"{title} · {_event_label(remaining)}",
                remaining_days=remaining,
            )
        )

    for item in anniversaries:
        next_date = anniversary_next_date(item)
        remaining = int(anniversary_remaining(item))
        threshold = max(0, min(365, int(getattr(item, "show_before_days", 7) or 0)))
        acknowledged = str(getattr(item, "acknowledged_date", "") or "")
        if remaining > threshold or acknowledged == next_date.isoformat():
            continue
        title = str(item.title)
        result.append(
            TodoViewItem(
                id=f"anniversary:{item.id}", title=title, date=next_date.isoformat(),
                time=None, important=False, completed=False,
                created_at=str(getattr(item, "created_at", "") or ""),
                work_seconds=0, source_type="anniversary", source_id=str(item.id),
                display_text=f"{title} · {_event_label(remaining, annual=True)}",
                remaining_days=remaining,
            )
        )
    return result
