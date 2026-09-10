"""Small yearly/one-off anniversary store."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Callable
from uuid import uuid4

from .local_data import local_data_path, read_json, write_json_atomic
from .time_service import days_until, next_yearly_occurrence, now_local, parse_date


@dataclass
class Anniversary:
    id: str
    title: str
    date: str
    repeat: str = "none"
    category: str = "personal"
    show_on_desktop: bool = False
    reminder_offsets: list[str] | None = None
    show_before_days: int = 7
    acknowledged_date: str | None = None
    created_at: str = ""
    note: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Anniversary":
        repeat = str(value.get("repeat") or "none")
        return cls(str(value.get("id") or uuid4().hex), str(value.get("title") or "未命名纪念日")[:240], str(value.get("date") or ""), repeat if repeat in {"none", "yearly"} else "none", str(value.get("category") or "personal")[:30], bool(value.get("show_on_desktop", False)), [str(item) for item in value.get("reminder_offsets", []) if item] if isinstance(value.get("reminder_offsets", []), list) else [], max(0, min(365, int(value.get("show_before_days", 7) or 0))), str(value.get("acknowledged_date") or "") or None, str(value.get("created_at") or datetime.now().astimezone().isoformat()), str(value.get("note") or "")[:500])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AnniversaryManager:
    def __init__(self, path=None, *, now_provider: Callable[[], datetime] | None = None, persist: bool = True) -> None:
        self.path = path or local_data_path("anniversaries.json")
        self._now = now_provider or (lambda: datetime.now().astimezone())
        self.persist = bool(persist)
        raw = read_json(self.path, [])
        self._items = [Anniversary.from_dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    @property
    def items(self) -> tuple[Anniversary, ...]:
        return tuple(self._items)

    def _save(self) -> None:
        if self.persist:
            write_json_atomic(self.path, [item.to_dict() for item in self._items])

    def add(self, title: str, value: str | date, *, repeat: str = "none", category: str = "personal", show_on_desktop: bool = False, reminder_offsets: list[str] | None = None, show_before_days: int = 7, note: str = "") -> Anniversary:
        day = parse_date(value, self._now)
        item = Anniversary(uuid4().hex, str(title).strip()[:240], day.isoformat(), repeat if repeat in {"none", "yearly"} else "none", str(category)[:30], bool(show_on_desktop), list(reminder_offsets or []), max(0, min(365, int(show_before_days))), None, now_local(self._now).isoformat(), str(note)[:500])
        self._items.append(item)
        self._save()
        return item

    def next_date(self, item: Anniversary | str) -> date:
        value = self.get(item) if isinstance(item, str) else item
        if value is None:
            raise KeyError(item)
        day = date.fromisoformat(value.date)
        return next_yearly_occurrence(day, self._now) if value.repeat == "yearly" else day

    def remaining_days(self, item: Anniversary | str) -> int:
        return (self.next_date(item) - now_local(self._now).date()).days

    def get(self, item_id: str) -> Anniversary | None:
        return next((item for item in self._items if item.id == str(item_id)), None)

    def find(self, title: str) -> Anniversary | None:
        text = str(title).strip().casefold()
        return next((item for item in self._items if item.title.casefold() == text), None) or next((item for item in self._items if text and text in item.title.casefold()), None)

    def update(self, item_id: str, **changes: Any) -> Anniversary:
        item = self.get(item_id)
        if item is None:
            raise KeyError(item_id)
        for key, value in changes.items():
            if key == "date":
                value = parse_date(value, self._now).isoformat()
            elif key == "repeat":
                value = str(value) if str(value) in {"none", "yearly"} else "none"
            elif key in {"title", "category", "note"}:
                value = str(value)[: (240 if key == "title" else 500)]
            elif key == "show_on_desktop":
                value = bool(value)
            elif key == "reminder_offsets":
                value = [str(entry) for entry in value if entry] if isinstance(value, list) else []
            elif key == "show_before_days":
                value = max(0, min(365, int(value)))
            elif key == "acknowledged_date":
                value = str(value or "")[:10] or None
            if hasattr(item, key):
                setattr(item, key, value)
        self._save()
        return item

    def acknowledge(self, item_id: str) -> Anniversary:
        """Dismiss the current occurrence without deleting a yearly date."""

        item = self.get(item_id)
        if item is None:
            raise KeyError(item_id)
        item.acknowledged_date = self.next_date(item).isoformat()
        self._save()
        return item

    def delete(self, item_id: str) -> bool:
        before = len(self._items)
        self._items = [item for item in self._items if item.id != str(item_id)]
        if len(self._items) != before:
            self._save()
            return True
        return False

    def desktop_items(self, limit: int = 1) -> list[tuple[Anniversary, int]]:
        visible = [item for item in self._items if item.show_on_desktop]
        visible.sort(key=lambda item: (self.remaining_days(item), item.title))
        return [(item, self.remaining_days(item)) for item in visible[:max(1, int(limit))]]
