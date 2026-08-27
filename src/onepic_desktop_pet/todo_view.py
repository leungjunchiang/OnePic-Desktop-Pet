"""统一本地待办视图；主列表只渲染用户明确指定的事项时间。

Manual Todos remain the source of truth for tasks. Countdown and anniversary
records are projected into this view while they are close enough to be useful;
no duplicate Todo record is created.
Creation metadata remains available to detail views but is never used as a
main-list event date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable
import re


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
    priority: int | None = None
    queue_position: int | None = None
    read: bool = False
    due_at: str | None = None
    reminder: bool = False
    reminder_minutes_before: int = 10
    reminder_mode: str = "none"
    highlight: bool = False


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


def todo_event_parts(item: Any) -> tuple[str, str | None]:
    """Return the event date/time used by Todo views.

    ``due_at`` is the actual event/deadline. ``remind_at`` is deliberately
    excluded: it is only the internal notification schedule and must never be
    rendered as if the event itself happened at that time. The legacy
    ``date``/``time`` fields remain the fallback for old records.
    """

    due_value = str(getattr(item, "due_at", None) or "").strip()
    if due_value:
        try:
            parsed = datetime.fromisoformat(due_value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone()
            return parsed.date().isoformat(), parsed.strftime("%H:%M")
        except (TypeError, ValueError, OverflowError):
            # A malformed due_at should not make a legacy Todo disappear.
            pass

    # A record with only ``remind_at`` has no event date. Its date field can
    # be the day on which the reminder was configured, so it must not leak
    # into the sticky-note display as if it were the event date.
    if getattr(item, "remind_at", None) and not getattr(item, "time", None):
        return "", None

    # ``date`` is a compatibility field.  It may contain the creation day for
    # old records even though the user never scheduled an event.
    if not bool(getattr(item, "date_explicit", False)):
        return "", None

    date_value = str(getattr(item, "date", "") or "")[:10]
    time_value = str(getattr(item, "time", None) or "").strip()[:5] or None
    return date_value, time_value


def display_todo_title(item: Any) -> str:
    """Return the user title without leaking a legacy creation-date prefix.

    Old builds occasionally prefixed a title in the renderer, and a few
    records may have persisted that exact prefix.  Only remove it when the
    date is an exact natural-day match for ``created_at``; user-entered dates
    remain untouched.  This is display-only and never rewrites storage.
    """

    title = str(getattr(item, "title", "") or "").strip()
    created = str(getattr(item, "created_at", "") or "")[:10]
    match = re.match(r"^(\d{4}-\d{2}-\d{2})\s*[·•]\s*", title)
    if match and created and match.group(1) == created:
        return title[match.end():].strip()
    return title


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
        event_date, event_time = todo_event_parts(item)
        title = display_todo_title(item)
        text = title
        if event_time:
            text += f" · {event_time}"
        if show_future_dates and today_date and event_date and event_date != today_date:
            text = f"{_todo_date_label(event_date, today_date)} · {text}"
        result.append(
            TodoViewItem(
                id=str(item.id), title=title, date=event_date,
                time=event_time,
                important=bool(getattr(item, "important", False)),
                highlight=bool(getattr(item, "highlight", False)),
                completed=bool(getattr(item, "completed", False)),
                created_at=str(getattr(item, "created_at", "") or ""),
                work_seconds=max(0, int(getattr(item, "work_seconds", 0) or 0)),
                source_type="todo", source_id=str(item.id), display_text=text,
                priority=getattr(item, "priority", None),
                queue_position=getattr(item, "queue_position", None),
                read=bool(getattr(item, "read", False)),
                due_at=getattr(item, "due_at", None),
                reminder=bool(getattr(item, "reminder", False)),
                reminder_minutes_before=max(
                    0, int(getattr(item, "reminder_minutes_before", 10) or 0)
                ),
                reminder_mode=str(
                    getattr(item, "reminder_mode", "")
                    or ("pet" if getattr(item, "reminder", False) else "none")
                ),
            )
        )

    for item in countdowns:
        if bool(getattr(item, "completed", False)):
            continue
        remaining = int(countdown_remaining(item))
        threshold = max(0, min(365, int(getattr(item, "show_before_days", 7) or 0)))
        # ``show_on_desktop`` is an explicit user override.  Older builds
        # stored this flag but the shared Todo projection silently ignored
        # it, so an important date could remain visible in Todo Center while
        # never reaching the desktop strip.
        if remaining > threshold and not bool(getattr(item, "show_on_desktop", False)):
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
        if (
            remaining > threshold
            and not bool(getattr(item, "show_on_desktop", False))
        ) or acknowledged == next_date.isoformat():
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
