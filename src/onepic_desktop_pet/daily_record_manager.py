"""Daily check-in records built from real local focus/task data."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .local_data import local_data_path, read_json, write_json_atomic
from .time_service import now_local, today_key


AUTO_CHECKIN_SECONDS = 15 * 60


@dataclass
class DailyRecord:
    date: str
    checked_in: bool = False
    check_in_time: str | None = None
    check_out_time: str | None = None
    focus_seconds: int = 0
    completed_tasks: int = 0
    total_tasks: int = 0
    main_task_completed: bool = False
    sessions: int = 0
    rest_day: bool = False
    note: str = ""
    diary: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any], date_key: str | None = None) -> "DailyRecord":
        return cls(
            date=str(value.get("date") or date_key or ""),
            checked_in=bool(value.get("checked_in", False)),
            check_in_time=str(value.get("check_in_time") or "") or None,
            check_out_time=str(value.get("check_out_time") or "") or None,
            focus_seconds=max(0, int(value.get("focus_seconds", 0) or 0)),
            completed_tasks=max(0, int(value.get("completed_tasks", 0) or 0)),
            total_tasks=max(0, int(value.get("total_tasks", 0) or 0)),
            main_task_completed=bool(value.get("main_task_completed", False)),
            sessions=max(0, int(value.get("sessions", 0) or 0)),
            rest_day=bool(value.get("rest_day", False)),
            note=str(value.get("note") or "")[:500],
            diary=str(value.get("diary") or "")[:1000],
        )


class DailyRecordManager:
    def __init__(self, path=None, *, now_provider: Callable[[], datetime] | None = None, persist: bool = True) -> None:
        self.path = path or local_data_path("daily_records.json")
        self._now = now_provider or (lambda: datetime.now().astimezone())
        self.persist = bool(persist)
        raw = read_json(self.path, {})
        self._records = {
            str(key): DailyRecord.from_dict(value, str(key))
            for key, value in (raw.items() if isinstance(raw, dict) else [])
            if isinstance(value, dict)
        }

    def _save(self) -> None:
        if self.persist:
            write_json_atomic(self.path, {key: asdict(value) for key, value in self._records.items()})

    def get(self, date_key: str | None = None) -> DailyRecord:
        key = date_key or today_key(self._now)
        if key not in self._records:
            self._records[key] = DailyRecord(key)
        return self._records[key]

    def record_focus(self, seconds: int, *, started_at: datetime | None = None, date_key: str | None = None) -> DailyRecord:
        record = self.get(date_key)
        seconds = max(0, int(seconds))
        if seconds:
            record.focus_seconds += seconds
            record.sessions += 1
            record.checked_in = record.checked_in or record.focus_seconds >= AUTO_CHECKIN_SECONDS
            if record.check_in_time is None:
                record.check_in_time = (started_at or now_local(self._now)).strftime("%H:%M")
            record.check_out_time = now_local(self._now).strftime("%H:%M")
        self._save()
        return record

    def sync_tasks(self, *, completed_tasks: int, total_tasks: int, main_task_completed: bool, date_key: str | None = None) -> DailyRecord:
        record = self.get(date_key)
        record.completed_tasks = max(0, int(completed_tasks))
        record.total_tasks = max(0, int(total_tasks))
        record.main_task_completed = bool(main_task_completed)
        self._save()
        return record

    def checkout(self, *, completed_tasks: int, total_tasks: int, main_task_completed: bool, note: str = "", date_key: str | None = None) -> DailyRecord:
        record = self.sync_tasks(completed_tasks=completed_tasks, total_tasks=total_tasks, main_task_completed=main_task_completed, date_key=date_key)
        record.checked_in = record.checked_in or record.focus_seconds >= AUTO_CHECKIN_SECONDS
        record.check_out_time = now_local(self._now).strftime("%H:%M")
        record.note = str(note)[:500]
        record.diary = self.diary_text(record)
        self._save()
        return record

    def set_rest_day(self, value: bool = True, *, note: str = "", date_key: str | None = None) -> DailyRecord:
        record = self.get(date_key)
        record.rest_day = bool(value)
        record.note = str(note)[:500]
        self._save()
        return record

    @staticmethod
    def diary_text(record: DailyRecord) -> str:
        from .time_service import format_duration

        if record.rest_day:
            return "今天休息，六毛给你留个轻量记录。"
        focus = format_duration(record.focus_seconds)
        task_text = f"完成{record.completed_tasks}/{record.total_tasks}项" if record.total_tasks else "今天还没有安排任务"
        return f"今天工作{focus}，{task_text}。{record.note}".strip()

    def records_between(self, start: date, end: date) -> list[DailyRecord]:
        return [self._records[key] for key in sorted(self._records) if start.isoformat() <= key <= end.isoformat()]

    def stats(self, *, start: date, end: date) -> dict[str, Any]:
        records = self.records_between(start, end)
        active = [item for item in records if item.checked_in and not item.rest_day]
        return {
            "work_days": len(active),
            "focus_seconds": sum(item.focus_seconds for item in records),
            "completed_tasks": sum(item.completed_tasks for item in records),
            "main_task_days": sum(1 for item in records if item.main_task_completed),
            "average_focus_seconds": int(sum(item.focus_seconds for item in active) / len(active)) if active else 0,
            "most_focused_date": max(records, key=lambda item: item.focus_seconds).date if records else None,
        }

    def week_stats(self, date_key: str | None = None) -> dict[str, Any]:
        current = date.fromisoformat(date_key or today_key(self._now))
        start = current - timedelta(days=current.weekday())
        return self.stats(start=start, end=start + timedelta(days=6))

    def month_stats(self, date_key: str | None = None) -> dict[str, Any]:
        current = date.fromisoformat(date_key or today_key(self._now))
        if current.month == 12:
            following = date(current.year + 1, 1, 1)
        else:
            following = date(current.year, current.month + 1, 1)
        return self.stats(start=current.replace(day=1), end=following - timedelta(days=1))

