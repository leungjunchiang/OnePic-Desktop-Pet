"""Local, durable tasks behind the compact Todo view."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import re
from typing import Any, Callable, Iterable
from uuid import uuid4

from .local_data import local_data_path, read_json, write_json_atomic
from .time_service import now_local, parse_date, parse_datetime, today_key


@dataclass
class TodoItem:
    id: str
    title: str
    date: str
    time: str | None = None
    important: bool = False
    completed: bool = False
    reminder: bool = False
    created_at: str = ""
    completed_at: str | None = None
    work_seconds: int = 0
    due_at: str | None = None
    remind_at: str | None = None
    source: str = "local"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TodoItem":
        date_value = str(value.get("date") or today_key())[:10]
        time_value = str(value.get("time") or "").strip()[:5] or None
        due_value = str(value.get("due_at") or "").strip() or None
        remind_value = str(value.get("remind_at") or "").strip() or None
        # Older task files only had date/time.  Keep them fully usable and
        # materialize the new explicit timestamps when enough information is
        # available, without changing the visible legacy fields.
        if not due_value and time_value:
            due_value = f"{date_value}T{time_value}:00"
        if not remind_value and bool(value.get("reminder", False)) and time_value:
            remind_value = due_value
        return cls(
            id=str(value.get("id") or uuid4().hex),
            title=str(value.get("title") or "未命名事项").strip()[:240],
            date=date_value,
            time=time_value,
            important=bool(value.get("important", False)),
            completed=bool(value.get("completed", False)),
            reminder=bool(value.get("reminder", False)),
            created_at=str(value.get("created_at") or datetime.now().astimezone().isoformat()),
            completed_at=str(value.get("completed_at") or "") or None,
            work_seconds=max(0, int(value.get("work_seconds", 0) or 0)),
            due_at=due_value,
            remind_at=remind_value,
            source=str(value.get("source") or "local")[:40],
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["work_seconds"] = max(0, int(self.work_seconds))
        return value


class TodoManager:
    """CRUD, carry-over and work-time attribution for local todo items."""

    def __init__(
        self,
        path=None,
        *,
        now_provider: Callable[[], datetime] | None = None,
        persist: bool = True,
    ) -> None:
        self.path = path or local_data_path("todos.json")
        self._now = now_provider or (lambda: datetime.now().astimezone())
        self.persist = bool(persist)
        raw = read_json(self.path, [])
        if isinstance(raw, dict):
            raw = raw.get("tasks", [])
        self._items = [TodoItem.from_dict(item) for item in raw if isinstance(item, dict)]

    @property
    def items(self) -> tuple[TodoItem, ...]:
        return tuple(self._items)

    def _save(self) -> None:
        if self.persist:
            write_json_atomic(self.path, [item.to_dict() for item in self._items])

    def add(
        self,
        title: str,
        *,
        date: str | None = None,
        time: str | None = None,
        important: bool = False,
        reminder: bool = False,
        item_id: str | None = None,
        due_at: str | None = None,
        remind_at: str | None = None,
        source: str = "local",
    ) -> TodoItem:
        title = " ".join(str(title).split())[:240]
        if not title:
            raise ValueError("任务标题不能为空")
        parsed_date = parse_date(date, self._now).isoformat()
        clean_time = str(time or "").strip()[:5] or None
        parsed_due = parse_datetime(due_at, self._now) if due_at else None
        parsed_remind = parse_datetime(remind_at, self._now) if remind_at else None
        # A reminder with no separate due time is still shown in the Todo
        # strip, so a request like “明天9:30提醒我改论文” is immediately
        # visible instead of living only in reminders.json.
        display_time = clean_time
        if parsed_due is not None:
            parsed_date = parsed_due.date().isoformat()
            display_time = parsed_due.strftime("%H:%M")
        elif parsed_remind is not None and display_time is None:
            parsed_date = parsed_remind.date().isoformat()
            display_time = parsed_remind.strftime("%H:%M")
        due_value = parsed_due.isoformat() if parsed_due else (
            f"{parsed_date}T{display_time}:00" if display_time else None
        )
        remind_value = parsed_remind.isoformat() if parsed_remind else (
            due_value if reminder and display_time else None
        )
        item = TodoItem(
            id=item_id or uuid4().hex,
            title=title,
            date=parsed_date,
            time=display_time,
            important=bool(important),
            reminder=bool(reminder),
            created_at=now_local(self._now).isoformat(),
            due_at=due_value,
            remind_at=remind_value,
            source=str(source or "local")[:40],
        )
        self._items.append(item)
        self._save()
        return item

    def get(self, item_id: str) -> TodoItem | None:
        return next((item for item in self._items if item.id == str(item_id)), None)

    def find(self, query: str) -> TodoItem | None:
        text = " ".join(str(query).split()).casefold()
        if not text:
            return None
        exact = next((item for item in self._items if item.title.casefold() == text), None)
        return exact or next((item for item in self._items if text in item.title.casefold()), None)

    @staticmethod
    def _title_bigrams(value: str) -> set[str]:
        text = re.sub(r"\s+", "", str(value or "").casefold())
        text = re.sub(r"(今天|明天|后天|提醒我|提醒|备忘|记得|一下|事项|任务)", "", text)
        return {text[index : index + 2] for index in range(max(0, len(text) - 1))}

    def find_similar_pending(self, title: str, date: str | None = None) -> TodoItem | None:
        """Find a likely existing task for conversational updates.

        This deliberately uses only a shared meaningful two-character phrase
        on the same day.  It catches “9点论文” → “修改论文” while avoiding
        treating every short reminder as the same task.
        """

        query_terms = self._title_bigrams(title)
        if not query_terms:
            return None
        date_key = parse_date(date, self._now).isoformat() if date is not None else None
        candidates: list[tuple[int, TodoItem]] = []
        fallback: list[tuple[int, TodoItem]] = []
        for item in self._items:
            if item.completed:
                continue
            overlap = len(query_terms & self._title_bigrams(item.title))
            if not overlap:
                continue
            pair = (overlap, item)
            fallback.append(pair)
            if date_key is None or item.date == date_key:
                candidates.append(pair)
        # A chat request often changes the date of an existing task.  Prefer
        # same-day matches, but fall back to the best pending match so
        # “9点论文” can become “明天 09:30 修改论文” instead of duplicating it.
        if not candidates and date_key is not None:
            candidates = fallback
        if not candidates:
            return None
        candidates.sort(key=lambda pair: (-pair[0], pair[1].created_at))
        return candidates[0][1]

    def update(self, item_id: str, **changes: Any) -> TodoItem:
        item = self.get(item_id)
        if item is None:
            raise KeyError(item_id)
        allowed = {
            "title", "date", "time", "important", "completed", "reminder",
            "work_seconds", "due_at", "remind_at", "source",
        }
        changed_date_or_time = False
        explicit_due = "due_at" in changes
        explicit_remind = "remind_at" in changes
        for key, value in changes.items():
            if key not in allowed:
                continue
            if key == "date":
                value = parse_date(value, self._now).isoformat()
                changed_date_or_time = True
            elif key == "title":
                value = " ".join(str(value).split())[:240]
            elif key == "time":
                value = str(value or "").strip()[:5] or None
                changed_date_or_time = True
            elif key in {"important", "completed", "reminder"}:
                value = bool(value)
            elif key == "work_seconds":
                value = max(0, int(value))
            elif key in {"due_at", "remind_at"}:
                value = parse_datetime(value, self._now).isoformat() if value else None
            elif key == "source":
                value = str(value or "local")[:40]
            setattr(item, key, value)
        if changed_date_or_time and not explicit_due:
            item.due_at = f"{item.date}T{item.time}:00" if item.time else None
        if item.reminder and not explicit_remind and changed_date_or_time:
            item.remind_at = item.due_at
        if not item.reminder:
            item.remind_at = None
        if item.completed and not item.completed_at:
            item.completed_at = now_local(self._now).isoformat()
        if not item.completed:
            item.completed_at = None
        self._save()
        return item

    def complete(self, item_id: str, completed: bool = True) -> TodoItem:
        return self.update(item_id, completed=completed)

    def delete(self, item_id: str) -> bool:
        before = len(self._items)
        self._items = [item for item in self._items if item.id != str(item_id)]
        changed = len(self._items) != before
        if changed:
            self._save()
        return changed

    def add_work_seconds(self, item_id: str | None, seconds: int) -> TodoItem | None:
        item = self.get(item_id) if item_id else None
        if item is None or int(seconds) <= 0:
            return item
        item.work_seconds += max(0, int(seconds))
        self._save()
        return item

    def for_date(self, date: str | None = None) -> list[TodoItem]:
        key = parse_date(date, self._now).isoformat()
        return [item for item in self._items if item.date == key]

    def today(self) -> list[TodoItem]:
        return self.for_date(today_key(self._now))

    def pending(self, date: str | None = None) -> list[TodoItem]:
        return [item for item in self.for_date(date) if not item.completed]

    def important_for(self, date: str | None = None) -> TodoItem | None:
        candidates = [item for item in self.for_date(date) if item.important]
        return next((item for item in candidates if not item.completed), candidates[0] if candidates else None)

    def carry_over(self, item_ids: Iterable[str], target_date: str | None = None) -> list[TodoItem]:
        target = parse_date(target_date, self._now).isoformat()
        moved: list[TodoItem] = []
        for item_id in item_ids:
            item = self.get(str(item_id))
            if item and not item.completed:
                item.date = target
                item.due_at = f"{target}T{item.time}:00" if item.time else None
                if item.reminder:
                    item.remind_at = item.due_at
                moved.append(item)
        if moved:
            self._save()
        return moved
