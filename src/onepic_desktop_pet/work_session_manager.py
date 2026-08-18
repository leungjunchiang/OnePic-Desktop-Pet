"""Durable task-attributed focus sessions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable
from uuid import uuid4

from .local_data import local_data_path, read_json, write_json_atomic
from .time_service import now_local


@dataclass
class WorkSession:
    id: str
    date: str
    started_at: str
    ended_at: str
    seconds: int
    task_id: str | None = None
    completed: bool = False

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WorkSession":
        return cls(
            id=str(value.get("id") or uuid4().hex),
            date=str(value.get("date") or str(value.get("started_at", ""))[:10]),
            started_at=str(value.get("started_at") or ""),
            ended_at=str(value.get("ended_at") or ""),
            seconds=max(0, int(value.get("seconds", 0) or 0)),
            task_id=str(value.get("task_id") or "") or None,
            completed=bool(value.get("completed", False)),
        )


class WorkSessionManager:
    def __init__(self, path=None, *, now_provider: Callable[[], datetime] | None = None, persist: bool = True) -> None:
        self.path = path or local_data_path("work_sessions.json")
        self._now = now_provider or (lambda: datetime.now().astimezone())
        self.persist = bool(persist)
        raw = read_json(self.path, [])
        self._sessions = [WorkSession.from_dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    @property
    def sessions(self) -> tuple[WorkSession, ...]:
        return tuple(self._sessions)

    def _save(self) -> None:
        if self.persist:
            write_json_atomic(self.path, [asdict(item) for item in self._sessions])

    def record(self, seconds: int, *, task_id: str | None = None, started_at: str | None = None, completed: bool = False) -> WorkSession | None:
        seconds = max(0, int(seconds))
        if seconds <= 0:
            return None
        ended = now_local(self._now)
        started = started_at or (ended.timestamp() - seconds)
        if isinstance(started, (int, float)):
            start_text = datetime.fromtimestamp(float(started), ended.tzinfo).isoformat()
        else:
            start_text = str(started)
        item = WorkSession(uuid4().hex, ended.date().isoformat(), start_text, ended.isoformat(), seconds, task_id, bool(completed))
        self._sessions.append(item)
        self._save()
        return item

    def for_date(self, date_key: str) -> list[WorkSession]:
        return [item for item in self._sessions if item.date == date_key]

    def total_seconds(self, date_key: str | None = None) -> int:
        return sum(item.seconds for item in (self.for_date(date_key) if date_key else self._sessions))
