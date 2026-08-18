"""Composition root for Lili's local plan-now/record-now/remember-later data."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable

from .anniversary_manager import AnniversaryManager
from .countdown_manager import CountdownManager
from .daily_record_manager import DailyRecordManager
from .daily_summary_service import DailySummaryService
from .reminder_manager import ReminderManager
from .structured_actions import LocalActionExecutor
from .sticky_note_manager import StickyNoteManager
from .timeline_manager import TimelineManager
from .todo_manager import TodoManager
from .todo_view import TodoViewItem, collect_todo_view
from .work_session_manager import WorkSessionManager


class TimeMemory:
    """One small dependency container shared by the Todo views and chat."""

    def __init__(self, base=None, *, now_provider: Callable[[], datetime] | None = None, persist: bool = True) -> None:
        def path(name: str):
            if base is not None:
                from pathlib import Path
                return Path(base) / name
            from .local_data import local_data_path
            return local_data_path(name)

        self.todos = TodoManager(path("todos.json"), now_provider=now_provider, persist=persist)
        self.records = DailyRecordManager(path("daily_records.json"), now_provider=now_provider, persist=persist)
        self.sessions = WorkSessionManager(path("work_sessions.json"), now_provider=now_provider, persist=persist)
        self.reminders = ReminderManager(path("reminders.json"), now_provider=now_provider, persist=persist)
        self.sticky_note = StickyNoteManager(path("sticky_note.json"), now_provider=now_provider, persist=persist)
        self.countdowns = CountdownManager(path("countdowns.json"), now_provider=now_provider, persist=persist)
        self.anniversaries = AnniversaryManager(path("anniversaries.json"), now_provider=now_provider, persist=persist)
        self.timeline = TimelineManager(path("timeline_events.json"), now_provider=now_provider, persist=persist)
        self.summary = DailySummaryService(self.todos, self.records, self.sessions)
        self.actions = LocalActionExecutor(self.todos, self.countdowns, self.anniversaries, self.timeline, self.summary, self.reminders)
        self.current_task_id: str | None = None
        self._now = now_provider

    def now(self) -> datetime:
        """Return the same clock used by every local time-memory store."""

        value = self._now() if self._now is not None else datetime.now().astimezone()
        return value

    def select_task(self, task_id: str | None) -> None:
        self.current_task_id = str(task_id) if task_id else None

    def todo_view_today(self) -> list[TodoViewItem]:
        """Return ordinary Todos plus near-term countdowns/anniversaries."""

        return collect_todo_view(
            self.todos.today(), self.countdowns.items, self.anniversaries.items,
            countdown_remaining=self.countdowns.remaining_days,
            anniversary_remaining=self.anniversaries.remaining_days,
            anniversary_next_date=self.anniversaries.next_date,
        )

    def todo_view_upcoming(self, days: int = 7) -> list[TodoViewItem]:
        """Return today plus near-term scheduled todos for the compact strip."""

        today = self.now().date()
        latest = today + timedelta(days=max(0, int(days)))
        scheduled = []
        for item in self.todos.items:
            if item.completed:
                continue
            try:
                item_date = date.fromisoformat(str(item.date)[:10])
            except (TypeError, ValueError):
                continue
            if today <= item_date <= latest:
                scheduled.append(item)
        return collect_todo_view(
            scheduled, self.countdowns.items, self.anniversaries.items,
            countdown_remaining=self.countdowns.remaining_days,
            anniversary_remaining=self.anniversaries.remaining_days,
            anniversary_next_date=self.anniversaries.next_date,
            today_date=today.isoformat(),
            show_future_dates=True,
        )

    def get_todo_view_item(self, item_id: str) -> TodoViewItem | None:
        return next((item for item in self.todo_view_upcoming() if item.id == str(item_id)), None)

    def complete_todo_view_item(self, item_id: str, completed: bool = True) -> bool:
        """Complete a projected event without duplicating it into todos.json."""

        item = self.get_todo_view_item(item_id)
        if item is None:
            return False
        if item.source_type == "todo":
            if completed:
                self.complete_task(item.source_id)
            else:
                self.todos.complete(item.source_id, False)
            return True
        if not completed:
            return False
        if item.source_type == "countdown":
            return self.complete_countdown(item.source_id)
        if item.source_type == "anniversary":
            self.anniversaries.acknowledge(item.source_id)
            return True
        return False

    def delete_todo_view_item(self, item_id: str) -> bool:
        item = self.get_todo_view_item(item_id)
        if item is None:
            return False
        if item.source_type == "todo":
            return self.todos.delete(item.source_id)
        if item.source_type == "countdown":
            return self.countdowns.delete(item.source_id)
        if item.source_type == "anniversary":
            return self.anniversaries.delete(item.source_id)
        return False

    def record_focus(self, seconds: int, *, completed_session: bool = False, task_id: str | None = None, started_at: datetime | None = None) -> None:
        """Commit one real timer segment to both the task and daily record."""

        seconds = max(0, int(seconds))
        if seconds <= 0:
            return
        selected = task_id if task_id is not None else self.current_task_id
        self.sessions.record(seconds, task_id=selected, completed=completed_session)
        self.todos.add_work_seconds(selected, seconds)
        self.records.record_focus(seconds, started_at=started_at)
        self.summary.refresh_tasks()

    def complete_task(self, task_id: str) -> bool:
        item = self.todos.complete(task_id)
        self.summary.refresh_tasks()
        if item.important:
            key = f"important:{item.id}"
            if not self.timeline.has_source(key):
                self.timeline.add(
                    f"完成今日重点：{item.title}",
                    event_type="milestone",
                    source=key,
                    related_task_id=item.id,
                    important=True,
                )
        return True

    def finish_today(self, note: str = "") -> dict:
        summary = self.summary.checkout(note)
        if not self.timeline.has_source(f"checkout:{summary['date']}"):
            self.timeline.add(
                f"{summary['date']} 的工作记录",
                event_type="work",
                source=f"checkout:{summary['date']}",
                description=f"专注{summary['focus']}，完成{summary['completed_tasks']}/{summary['total_tasks']}项。",
            )
        return summary

    def complete_countdown(self, countdown_id: str) -> bool:
        item = self.countdowns.get(countdown_id)
        if item is None:
            return False
        self.countdowns.complete(item.id)
        source = f"countdown:{item.id}"
        if not self.timeline.has_source(source):
            self.timeline.add(
                f"完成倒计时：{item.title}",
                event_type="milestone",
                source=source,
                related_countdown_id=item.id,
                important=True,
            )
        return True
