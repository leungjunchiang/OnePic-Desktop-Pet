"""Curated, human-readable milestones rather than a raw application log."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable
from uuid import uuid4

from .local_data import local_data_path, read_json, write_json_atomic
from .time_service import now_local, today_key


ALLOWED_TYPES = {"manual", "work", "milestone", "anniversary", "project", "life"}


@dataclass
class TimelineEvent:
    id: str
    date: str
    time: str
    type: str
    title: str
    description: str = ""
    source: str = "manual"
    related_task_id: str | None = None
    related_countdown_id: str | None = None
    important: bool = False
    created_at: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TimelineEvent":
        kind = str(value.get("type") or "manual")
        return cls(str(value.get("id") or uuid4().hex), str(value.get("date") or today_key())[:10], str(value.get("time") or "")[:5], kind if kind in ALLOWED_TYPES else "manual", str(value.get("title") or "未命名记录")[:240], str(value.get("description") or "")[:1000], str(value.get("source") or "manual")[:120], str(value.get("related_task_id") or "") or None, str(value.get("related_countdown_id") or "") or None, bool(value.get("important", False)), str(value.get("created_at") or datetime.now().astimezone().isoformat()))


class TimelineManager:
    def __init__(self, path=None, *, now_provider: Callable[[], datetime] | None = None, persist: bool = True) -> None:
        self.path = path or local_data_path("timeline_events.json")
        self._now = now_provider or (lambda: datetime.now().astimezone())
        self.persist = bool(persist)
        raw = read_json(self.path, [])
        self._items = [TimelineEvent.from_dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    @property
    def events(self) -> tuple[TimelineEvent, ...]:
        return tuple(self._items)

    def _save(self) -> None:
        if self.persist:
            write_json_atomic(self.path, [asdict(item) for item in self._items])

    def add(self, title: str, *, date: str | None = None, time: str | None = None, event_type: str = "manual", description: str = "", source: str = "manual", related_task_id: str | None = None, related_countdown_id: str | None = None, important: bool = False) -> TimelineEvent:
        current = now_local(self._now)
        item = TimelineEvent(uuid4().hex, str(date or current.date().isoformat())[:10], str(time or current.strftime("%H:%M"))[:5], event_type if event_type in ALLOWED_TYPES else "manual", str(title).strip()[:240], str(description)[:1000], str(source)[:120], related_task_id, related_countdown_id, bool(important), current.isoformat())
        self._items.append(item)
        self._save()
        return item

    def query(self, *, year: int | None = None, month: int | None = None, event_type: str | None = None) -> list[TimelineEvent]:
        result = list(self._items)
        if year is not None:
            result = [item for item in result if item.date[:4] == str(year)]
        if month is not None:
            result = [item for item in result if item.date[5:7] == f"{int(month):02d}"]
        if event_type and event_type != "all":
            result = [item for item in result if item.type == event_type]
        return sorted(result, key=lambda item: (item.date, item.time, item.created_at), reverse=True)

    def delete(self, item_id: str) -> bool:
        before = len(self._items)
        self._items = [item for item in self._items if item.id != str(item_id)]
        if len(self._items) != before:
            self._save()
            return True
        return False

    def has_source(self, source: str) -> bool:
        return any(item.source == str(source) for item in self._items)
