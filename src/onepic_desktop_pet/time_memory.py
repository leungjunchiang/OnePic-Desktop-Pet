"""Composition root for Lili's local plan-now/record-now/remember-later data."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from .anniversary_manager import AnniversaryManager
from .alarm_manager import AlarmManager
from .alarm_sounds import AlarmSoundLibrary
from .countdown_manager import CountdownManager
from .daily_record_manager import DailyRecordManager
from .daily_summary_service import DailySummaryService
from .reminder_manager import ReminderManager
from .structured_actions import LocalActionExecutor
from .sticky_note_manager import StickyNoteManager
from .timeline_manager import TimelineManager
from .todo_manager import TodoManager
from .todo_view import TodoViewItem, collect_todo_view
from .time_service import now_local, parse_datetime
from .work_session_manager import WorkSessionManager


class TimeMemory:
    """One small dependency container shared by the Todo views and chat."""

    def __init__(
        self,
        base=None,
        *,
        account_id: str | None = None,
        now_provider: Callable[[], datetime] | None = None,
        persist: bool = True,
    ) -> None:
        self._base = base
        self._account_id = str(account_id or "").strip()
        self._now_provider = now_provider
        self._persist = bool(persist)
        def path(name: str):
            if base is not None:
                from pathlib import Path
                return Path(base) / name
            from .local_data import account_local_data_path
            return account_local_data_path(name, self._account_id)

        self.todos = TodoManager(path("todos.json"), now_provider=now_provider, persist=persist)
        self.records = DailyRecordManager(path("daily_records.json"), now_provider=now_provider, persist=persist)
        self.sessions = WorkSessionManager(path("work_sessions.json"), now_provider=now_provider, persist=persist)
        self.reminders = ReminderManager(path("reminders.json"), now_provider=now_provider, persist=persist)
        self.alarms = AlarmManager(path("alarms.json"), now_provider=now_provider, persist=persist)
        self.alarm_sounds = AlarmSoundLibrary(
            (Path(base) if base is not None else path("alarm_sounds").parent),
            persist=persist,
        )
        self.sticky_note = StickyNoteManager(path("sticky_note.json"), now_provider=now_provider, persist=persist)
        self.countdowns = CountdownManager(path("countdowns.json"), now_provider=now_provider, persist=persist)
        self.anniversaries = AnniversaryManager(path("anniversaries.json"), now_provider=now_provider, persist=persist)
        self.timeline = TimelineManager(path("timeline_events.json"), now_provider=now_provider, persist=persist)
        self.summary = DailySummaryService(self.todos, self.records, self.sessions)
        self.actions = LocalActionExecutor(
            self.todos, self.countdowns, self.anniversaries, self.timeline,
            self.summary, self.reminders, self.alarms,
        )
        self.current_task_id: str | None = None
        self._now = now_provider
        # Migrate legacy due-time reminders to the Todo's persisted lead time
        # and repair the real notification queue on startup.
        for item in self.todos.items:
            self.sync_todo_reminder(item)

    def switch_account(self, account_id: str | None) -> bool:
        """Reload all local tasks, alarms and reminders for one account."""

        if self._base is not None:
            return False
        target = str(account_id or "").strip()
        if target == self._account_id:
            return False
        self.__init__(
            account_id=target,
            now_provider=self._now_provider,
            persist=self._persist,
        )
        return True

    def now(self) -> datetime:
        """Return the same clock used by every local time-memory store."""

        # Keep the composition root on the same aware-local datetime contract
        # as the managers it coordinates.  Test clocks and legacy callers may
        # return naive datetimes; normalising here prevents comparisons with
        # parsed due/reminder timestamps from silently falling back.
        return now_local(self._now)

    def select_task(self, task_id: str | None) -> None:
        self.current_task_id = str(task_id) if task_id else None

    def sync_todo_reminder(self, item: object) -> None:
        """Keep quiet reminders and audible Todo alarms mutually exclusive."""

        item_id = str(getattr(item, "id", "") or "")
        if not item_id:
            return
        mode = str(getattr(item, "reminder_mode", "") or "").strip().lower()
        if not mode:
            mode = "pet" if bool(getattr(item, "reminder", False)) else "none"
        if bool(getattr(item, "completed", False)):
            self.reminders.complete_for_source(item_id)
            self.alarms.sync_todo(item, reminder_mode="none")
            return
        if bool(getattr(item, "reminder_suppressed", False)):
            # A restored item whose original reminder time has already passed
            # must not replay an old alert immediately.  Editing its reminder
            # clears this flag and schedules the newly chosen time.
            self.reminders.remove_for_source(item_id)
            self.alarms.sync_todo(item, reminder_mode="none")
            return
        if mode == "alarm":
            self.reminders.remove_for_source(item_id)
            try:
                self.alarms.sync_todo(item, reminder_mode=mode)
            except (TypeError, ValueError):
                self.alarms.sync_todo(item, reminder_mode="none")
            return
        if mode == "none" or not bool(getattr(item, "reminder", False)):
            self.reminders.remove_for_source(item_id)
            self.alarms.sync_todo(item, reminder_mode="none")
            return
        due = getattr(item, "remind_at", None) or getattr(item, "due_at", None)
        if due:
            try:
                self.reminders.upsert_for_source(
                    str(getattr(item, "title", "待办")), due, source_id=item_id
                )
            except (TypeError, ValueError):
                # A malformed legacy time must not prevent the desktop pet
                # from starting. The Todo remains visible for manual repair.
                self.reminders.remove_for_source(item_id)
            self.alarms.sync_todo(item, reminder_mode="none")
        else:
            # A date-only sticky note may remain visible forever, but it has no
            # clock time at which a notification could be delivered.
            self.reminders.remove_for_source(item_id)
            self.alarms.sync_todo(item, reminder_mode="none")

    def todo_view_today(self) -> list[TodoViewItem]:
        """Return today's sticky notes plus near-term countdowns/anniversaries.

        A timed note remains visible for 24 hours after its due time.  An
        untimed note is intentionally carried across calendar days until the
        user completes or manually marks it as read.
        """

        today = self.now().date()
        scheduled = self._visible_todos_until(today)

        return collect_todo_view(
            scheduled, self.countdowns.items, self.anniversaries.items,
            countdown_remaining=self.countdowns.remaining_days,
            anniversary_remaining=self.anniversaries.remaining_days,
            anniversary_next_date=self.anniversaries.next_date,
        )

    def todo_view_upcoming(
        self,
        days: int = 7,
        *,
        include_read: bool = False,
    ) -> list[TodoViewItem]:
        """Return the compact sticky-note projection, including recent overdue work.

        The normal desktop projection excludes read items so the accessory can
        disappear after the user has acknowledged every note.  An explicit
        ``显示待办`` command may request the same unfinished projection again,
        including acknowledged items; keeping that override here avoids the
        command appearing to do nothing when every remaining task is already
        marked read.
        """

        today = self.now().date()
        latest = today + timedelta(days=max(0, int(days)))
        scheduled = self._visible_todos_until(latest, include_read=include_read)
        return collect_todo_view(
            scheduled, self.countdowns.items, self.anniversaries.items,
            countdown_remaining=self.countdowns.remaining_days,
            anniversary_remaining=self.anniversaries.remaining_days,
            anniversary_next_date=self.anniversaries.next_date,
            today_date=today.isoformat(),
            show_future_dates=True,
        )

    def todo_view_desktop(
        self,
        *,
        include_read: bool = True,
    ) -> list[TodoViewItem]:
        """Return the canonical projection for the resident desktop strip.

        Keep this named entry point separate from the detailed Todo Center's
        tab partitioning.  The compact strip, startup restore, account
        rebinding and manual ``显示待办`` command must all ask the same
        projection for their content; otherwise a newly selected account can
        show events in Todo Center while the resident panel stays empty.
        """

        return self.todo_view_upcoming(include_read=include_read)

    def _visible_todos_until(
        self,
        latest: date,
        *,
        include_read: bool = False,
    ) -> list[object]:
        """Select durable Todo records for desktop/center time views.

        The old implementation filtered by ``item.date >= today``.  That
        made a sticky note disappear at midnight even when it was still an
        unfinished task.  Date-only notes now remain until explicitly read;
        timed notes remain through the 24-hour grace period after due time.
        """

        current = self.now()
        result: list[object] = []
        for item in self.todos.items:
            if item.completed or (
                item.read and not include_read
            ) or self.todos.auto_hidden(item, now=current):
                continue
            try:
                item_date = date.fromisoformat(str(item.date)[:10])
            except (TypeError, ValueError):
                continue
            if item.time or item.due_at:
                due_text = item.due_at or f"{item.date}T{item.time}:00"
                try:
                    due = parse_datetime(due_text, self._now)
                except (TypeError, ValueError):
                    due = None
                if due is not None and due.date() <= latest:
                    result.append(item)
                elif due is None and item_date <= latest:
                    result.append(item)
            elif item_date <= latest:
                # No exact time means a true sticky note: it does not expire
                # just because its calendar date has passed.
                result.append(item)
        return result

    def get_todo_view_item(
        self,
        item_id: str,
        *,
        include_read: bool = False,
    ) -> TodoViewItem | None:
        return next(
            (
                item
                for item in self.todo_view_desktop(include_read=include_read)
                if item.id == str(item_id)
            ),
            None,
        )

    def complete_todo_view_item(
        self,
        item_id: str,
        completed: bool = True,
        *,
        include_read: bool = False,
    ) -> bool:
        """Complete a projected event without duplicating it into todos.json."""

        item = self.get_todo_view_item(item_id, include_read=include_read)
        if item is None:
            return False
        if item.source_type == "todo":
            if completed:
                self.complete_task(item.source_id)
            else:
                self.restore_todo(item.source_id)
            return True
        if not completed:
            return False
        if item.source_type == "countdown":
            return self.complete_countdown(item.source_id)
        if item.source_type == "anniversary":
            self.anniversaries.acknowledge(item.source_id)
            return True
        return False

    def read_todo_view_item(self, item_id: str, read: bool = True) -> bool:
        """Mark an ordinary sticky Todo as read without completing it."""

        item = self.get_todo_view_item(item_id)
        if item is None or item.source_type != "todo":
            return False
        self.todos.mark_read(item.source_id, read)
        return True

    def read_todo(self, item_id: str, read: bool = True) -> bool:
        """Direct counterpart used by Todo Center's read-notes view."""

        item = self.todos.get(item_id)
        if item is None:
            return False
        self.todos.mark_read(item.id, read)
        return True

    def delete_todo_view_item(self, item_id: str, *, include_read: bool = False) -> bool:
        item = self.get_todo_view_item(item_id, include_read=include_read)
        if item is None:
            return False
        if item.source_type == "todo":
            deleted = self.todos.delete(item.source_id)
            if deleted:
                self.alarms.delete(f"todo:{item.source_id}")
                self.reminders.remove_for_source(item.source_id)
            return deleted
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
        if item.completed:
            self.reminders.complete_for_source(item.id)
            self.alarms.sync_todo(item, reminder_mode="none")
        else:
            self.sync_todo_reminder(item)
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

    def restore_todo(self, task_id: str) -> bool:
        """Restore one completed Todo without replaying an expired alert."""

        item = self.todos.get(task_id)
        if item is None:
            return False
        now = self.now()
        reminder_at = getattr(item, "remind_at", None) or getattr(item, "due_at", None)
        reminder_expired = False
        if reminder_at:
            try:
                reminder_expired = parse_datetime(reminder_at, self._now) <= now
            except (TypeError, ValueError, OverflowError):
                reminder_expired = False
        restored = self.todos.update(
            item.id,
            completed=False,
            read=False,
            read_at=None,
            reminder_suppressed=reminder_expired,
        )
        self.sync_todo_reminder(restored)
        self.summary.refresh_tasks()
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
