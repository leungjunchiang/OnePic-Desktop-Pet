"""Local countdowns for future deadlines and important dates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Callable
from uuid import uuid4

from .local_data import local_data_path, read_json, write_json_atomic
from .time_service import days_until, now_local, parse_datetime


@dataclass
class Countdown:
    id: str
    title: str
    target_datetime: str
    all_day: bool = True
    category: str = "other"
    pinned: bool = False
    show_on_desktop: bool = False
    reminder_offsets: list[str] | None = None
    show_before_days: int = 7
    completed: bool = False
    created_at: str = ""
    completed_at: str | None = None
    note: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Countdown":
        return cls(
            id=str(value.get("id") or uuid4().hex),
            title=str(value.get("title") or "未命名倒计时")[:240],
            target_datetime=str(value.get("target_datetime") or ""),
            all_day=bool(value.get("all_day", True)),
            category=str(value.get("category") or "other")[:30],
            pinned=bool(value.get("pinned", False)),
            show_on_desktop=bool(value.get("show_on_desktop", False)),
            reminder_offsets=[str(item) for item in value.get("reminder_offsets", []) if item] if isinstance(value.get("reminder_offsets", []), list) else [],
            show_before_days=max(0, min(365, int(value.get("show_before_days", 7) or 0))),
            completed=bool(value.get("completed", False)),
            created_at=str(value.get("created_at") or datetime.now().astimezone().isoformat()),
            completed_at=str(value.get("completed_at") or "") or None,
            note=str(value.get("note") or "")[:500],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CountdownManager:
    def __init__(self, path=None, *, now_provider: Callable[[], datetime] | None = None, persist: bool = True) -> None:
        self.path = path or local_data_path("countdowns.json")
        self._now = now_provider or (lambda: datetime.now().astimezone())
        self.persist = bool(persist)
        raw = read_json(self.path, [])
        self._items = [Countdown.from_dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    @property
    def items(self) -> tuple[Countdown, ...]:
        return tuple(self._items)

    def _save(self) -> None:
        if self.persist:
            write_json_atomic(self.path, [item.to_dict() for item in self._items])

    def add(self, title: str, target_datetime: str | date | datetime, *, all_day: bool = True, category: str = "other", pinned: bool = False, show_on_desktop: bool = False, reminder_offsets: list[str] | None = None, show_before_days: int = 7, note: str = "") -> Countdown:
        if isinstance(target_datetime, date) and not isinstance(target_datetime, datetime):
            target = datetime.combine(target_datetime, datetime.min.time()).replace(tzinfo=now_local(self._now).tzinfo)
        else:
            target = parse_datetime(target_datetime, self._now)
        item = Countdown(uuid4().hex, str(title).strip()[:240], target.isoformat(), bool(all_day), str(category)[:30], bool(pinned), bool(show_on_desktop), list(reminder_offsets or []), max(0, min(365, int(show_before_days))), False, now_local(self._now).isoformat(), None, str(note)[:500])
        self._items.append(item)
        self._save()
        return item

    def get(self, item_id: str) -> Countdown | None:
        return next((item for item in self._items if item.id == str(item_id)), None)

    def find(self, title: str) -> Countdown | None:
        text = str(title).strip().casefold()
        return next((item for item in self._items if item.title.casefold() == text), None) or next((item for item in self._items if text and text in item.title.casefold()), None)

    def update(self, item_id: str, **changes: Any) -> Countdown:
        item = self.get(item_id)
        if item is None:
            raise KeyError(item_id)
        for key, value in changes.items():
            if key == "target_datetime":
                value = parse_datetime(value, self._now).isoformat()
            elif key in {"title", "category", "note"}:
                value = str(value)[: (240 if key == "title" else 500)]
            elif key == "reminder_offsets":
                value = [str(item) for item in value if item] if isinstance(value, list) else []
            elif key == "show_before_days":
                value = max(0, min(365, int(value)))
            elif key in {"all_day", "pinned", "show_on_desktop", "completed"}:
                value = bool(value)
            if hasattr(item, key):
                setattr(item, key, value)
        if item.completed and not item.completed_at:
            item.completed_at = now_local(self._now).isoformat()
        if not item.completed:
            item.completed_at = None
        self._save()
        return item

    def complete(self, item_id: str) -> Countdown:
        return self.update(item_id, completed=True)

    def delete(self, item_id: str) -> bool:
        before = len(self._items)
        self._items = [item for item in self._items if item.id != str(item_id)]
        if len(self._items) != before:
            self._save()
            return True
        return False

    def remaining_days(self, item: Countdown | str) -> int:
        value = self.get(item) if isinstance(item, str) else item
        if value is None:
            raise KeyError(item)
        return days_until(value.target_datetime, self._now)

    def desktop_items(self, limit: int = 3) -> list[tuple[Countdown, int]]:
        visible = [item for item in self._items if item.show_on_desktop and not item.completed]
        visible.sort(key=lambda item: (not item.pinned, self.remaining_days(item), item.title))
        return [(item, self.remaining_days(item)) for item in visible[:max(1, int(limit))]]
