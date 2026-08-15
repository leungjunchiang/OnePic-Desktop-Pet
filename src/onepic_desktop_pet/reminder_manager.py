"""Local reminder records and a deliberately cheap due check."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Callable
from uuid import uuid4

from .local_data import local_data_path, read_json, write_json_atomic
from .time_service import now_local, parse_datetime


@dataclass
class Reminder:
    id: str
    title: str
    due_at: str
    source_id: str | None = None
    done: bool = False
    notified: bool = False

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Reminder":
        return cls(
            id=str(value.get("id") or uuid4().hex),
            title=str(value.get("title") or "提醒")[:240],
            due_at=str(value.get("due_at") or ""),
            source_id=str(value.get("source_id") or "") or None,
            done=bool(value.get("done", False)),
            notified=bool(value.get("notified", False)),
        )


class ReminderManager:
    def __init__(self, path=None, *, now_provider: Callable[[], datetime] | None = None, persist: bool = True) -> None:
        self.path = path or local_data_path("reminders.json")
        self._now = now_provider or (lambda: datetime.now().astimezone())
        self.persist = bool(persist)
        raw = read_json(self.path, [])
        self._items = [Reminder.from_dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    @property
    def items(self) -> tuple[Reminder, ...]:
        return tuple(self._items)

    def _save(self) -> None:
        if self.persist:
            write_json_atomic(self.path, [asdict(item) for item in self._items])

    def add(self, title: str, due_at: str | datetime, *, source_id: str | None = None) -> Reminder:
        due = parse_datetime(due_at, self._now).isoformat()
        item = Reminder(uuid4().hex, str(title).strip()[:240], due, source_id)
        self._items.append(item)
        self._save()
        return item

    def due(self, *, include_notified: bool = False) -> list[Reminder]:
        current = now_local(self._now)
        result = []
        for item in self._items:
            if item.done or (item.notified and not include_notified):
                continue
            try:
                if parse_datetime(item.due_at, self._now) <= current:
                    result.append(item)
            except (TypeError, ValueError):
                continue
        return result

    def mark_notified(self, reminder_id: str) -> bool:
        item = next((item for item in self._items if item.id == str(reminder_id)), None)
        if item is None:
            return False
        item.notified = True
        self._save()
        return True

    def snooze(self, reminder_id: str, minutes: int) -> Reminder:
        item = next(item for item in self._items if item.id == str(reminder_id))
        item.due_at = (now_local(self._now) + timedelta(minutes=max(1, int(minutes)))).isoformat()
        item.notified = False
        self._save()
        return item

    def complete(self, reminder_id: str) -> bool:
        item = next((item for item in self._items if item.id == str(reminder_id)), None)
        if item is None:
            return False
        item.done = True
        self._save()
        return True

