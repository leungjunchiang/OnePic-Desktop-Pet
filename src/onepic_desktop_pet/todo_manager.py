"""Local, durable tasks behind 今日小纸条."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable, Iterable
from uuid import uuid4

from .local_data import local_data_path, read_json, write_json_atomic
from .time_service import now_local, parse_date, today_key


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

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TodoItem":
        return cls(
            id=str(value.get("id") or uuid4().hex),
            title=str(value.get("title") or "未命名事项").strip()[:240],
            date=str(value.get("date") or today_key())[:10],
            time=str(value.get("time") or "").strip()[:5] or None,
            important=bool(value.get("important", False)),
            completed=bool(value.get("completed", False)),
            reminder=bool(value.get("reminder", False)),
            created_at=str(value.get("created_at") or datetime.now().astimezone().isoformat()),
            completed_at=str(value.get("completed_at") or "") or None,
            work_seconds=max(0, int(value.get("work_seconds", 0) or 0)),
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
    ) -> TodoItem:
        title = " ".join(str(title).split())[:240]
        if not title:
            raise ValueError("任务标题不能为空")
        item = TodoItem(
            id=item_id or uuid4().hex,
            title=title,
            date=parse_date(date, self._now).isoformat(),
            time=str(time or "").strip()[:5] or None,
            important=bool(important),
            reminder=bool(reminder),
            created_at=now_local(self._now).isoformat(),
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

    def update(self, item_id: str, **changes: Any) -> TodoItem:
        item = self.get(item_id)
        if item is None:
            raise KeyError(item_id)
        allowed = {"title", "date", "time", "important", "completed", "reminder", "work_seconds"}
        for key, value in changes.items():
            if key not in allowed:
                continue
            if key == "date":
                value = parse_date(value, self._now).isoformat()
            elif key == "title":
                value = " ".join(str(value).split())[:240]
            elif key == "time":
                value = str(value or "").strip()[:5] or None
            elif key in {"important", "completed", "reminder"}:
                value = bool(value)
            elif key == "work_seconds":
                value = max(0, int(value))
            setattr(item, key, value)
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
                moved.append(item)
        if moved:
            self._save()
        return moved
