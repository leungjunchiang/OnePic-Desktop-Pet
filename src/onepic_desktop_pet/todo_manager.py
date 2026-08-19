"""Local, durable tasks behind the compact Todo view."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import re
from typing import Any, Callable, Iterable
from uuid import uuid4

from .local_data import local_data_path, read_json, write_json_atomic
from .time_service import now_local, parse_date, parse_datetime, today_key


_LEGACY_INLINE_EVENT_RE = re.compile(
    r"(?<!\d)(?P<month>\d{1,2})\s*[./-]\s*(?P<day>\d{1,2})"
    r"\s+(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)(?!\d)"
)


REMINDER_NONE = "none"
REMINDER_PET = "pet"
REMINDER_ALARM = "alarm"
REMINDER_MODES = {REMINDER_NONE, REMINDER_PET, REMINDER_ALARM}


def normalize_reminder_mode(value: Any, *, legacy_reminder: bool = False) -> str:
    """Normalize the three user-visible reminder levels.

    Files written before the mode field existed keep their old boolean
    semantics: ``reminder=True`` becomes a quiet pet reminder and false
    remains no reminder. New records choose the mode explicitly.
    """

    text = str(value or "").strip().lower()
    if text in REMINDER_MODES:
        return text
    return REMINDER_PET if legacy_reminder else REMINDER_NONE


def _recover_legacy_inline_event(
    title: str,
    *,
    created_at: str,
    date_value: str,
    time_value: str | None,
    due_value: str | None,
    remind_value: str | None,
    reminder: bool,
    reminder_minutes: int,
) -> tuple[str, str, str | None, str | None, str | None]:
    """Repair records made before compact dates were parsed.

    Older chat-created records could keep ``8.19 13:00`` in the title while
    using the creation day (or the reminder day) as ``date``/``due_at``.  A
    visible Todo should not lose the real event date just because it was
    created on another day.  This migration is deliberately narrow: it only
    acts when an inline compact date/time is present and the stored due date
    still equals the stored date.
    """

    match = _LEGACY_INLINE_EVENT_RE.search(title)
    if match is None:
        return title, date_value, time_value, due_value, remind_value
    try:
        created_dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        year = created_dt.year
    except (TypeError, ValueError, OverflowError):
        year = datetime.now().year
    try:
        event_dt = datetime(
            year,
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
        )
    except (TypeError, ValueError, OverflowError):
        return title, date_value, time_value, due_value, remind_value

    stored_due_date = str(due_value or "")[:10]
    if stored_due_date and stored_due_date != date_value:
        return title, date_value, time_value, due_value, remind_value
    recovered_date = event_dt.date().isoformat()
    recovered_time = event_dt.strftime("%H:%M")
    cleaned_title = (
        f"{title[:match.start()]} {title[match.end():]}"
    ).strip(" -·,，:：的")
    old_date = date_value
    recovered_due = f"{recovered_date}T{recovered_time}:00"

    # If the old reminder was tied to the incorrectly stored date, move it
    # with the recovered event.  A separately chosen reminder date remains
    # untouched.
    recovered_remind = remind_value
    if reminder and remind_value and str(remind_value)[:10] == old_date:
        recovered_remind = (
            event_dt - timedelta(minutes=reminder_minutes)
        ).isoformat()
    return cleaned_title, recovered_date, recovered_time, recovered_due, recovered_remind


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
    # ``priority`` is intentionally optional: ``None`` keeps the old
    # time-based ordering.  1/2/3 mean high/medium/low when the user chooses
    # an explicit priority.
    priority: int | None = None
    # Explicit position in the user's current work queue.  Unlike the legacy
    # high/medium/low priority, this is a strict 1..5 order; None means the
    # item remains in the normal date-sorted list.
    queue_position: int | None = None
    # ``read`` is separate from ``completed``.  A sticky note can be read and
    # dismissed without falsely claiming that the work is finished.
    read: bool = False
    read_at: str | None = None
    reminder_minutes_before: int = 10
    reminder_mode: str = REMINDER_NONE
    alarm_sound_id: str = "system"
    alarm_volume: int = 60
    alarm_snooze_minutes: int = 10
    # Set only when a completed item is restored after its old reminder time.
    # It preserves the user's reminder choice without replaying stale alerts.
    reminder_suppressed: bool = False
    source: str = "local"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TodoItem":
        date_value = str(value.get("date") or today_key())[:10]
        time_value = str(value.get("time") or "").strip()[:5] or None
        due_value = str(value.get("due_at") or "").strip() or None
        remind_value = str(value.get("remind_at") or "").strip() or None
        raw_priority = value.get("priority")
        try:
            priority = int(raw_priority) if raw_priority is not None else None
        except (TypeError, ValueError):
            priority = None
        if priority not in {1, 2, 3}:
            priority = None
        raw_queue_position = value.get("queue_position")
        try:
            queue_position = int(raw_queue_position) if raw_queue_position is not None else None
        except (TypeError, ValueError):
            queue_position = None
        if queue_position not in {1, 2, 3, 4, 5}:
            queue_position = None
        try:
            reminder_minutes = max(
                0, min(24 * 60, int(value.get("reminder_minutes_before", 10) or 0))
            )
        except (TypeError, ValueError):
            reminder_minutes = 10
        created_at = str(value.get("created_at") or datetime.now().astimezone().isoformat())
        reminder = bool(value.get("reminder", False))
        reminder_mode = normalize_reminder_mode(
            value.get("reminder_mode"), legacy_reminder=reminder
        )
        try:
            alarm_volume = max(0, min(100, int(value.get("alarm_volume", 60) or 0)))
        except (TypeError, ValueError):
            alarm_volume = 60
        try:
            alarm_snooze = max(1, min(120, int(value.get("alarm_snooze_minutes", 10) or 10)))
        except (TypeError, ValueError):
            alarm_snooze = 10
        title_value = str(value.get("title") or "未命名事项").strip()[:240]
        title_value, date_value, time_value, due_value, remind_value = (
            _recover_legacy_inline_event(
                title_value,
                created_at=created_at,
                date_value=date_value,
                time_value=time_value,
                due_value=due_value,
                remind_value=remind_value,
                reminder=reminder,
                reminder_minutes=reminder_minutes,
            )
        )
        # Older task files only had date/time.  Keep them fully usable and
        # materialize the new explicit timestamps when enough information is
        # available, without changing the visible legacy fields.
        if not due_value and time_value:
            due_value = f"{date_value}T{time_value}:00"
        if not remind_value and reminder and time_value:
            remind_value = due_value
        # Releases before the configurable lead-time field stored reminders at
        # the due time.  Migrate that exact legacy shape to the new default of
        # ten minutes before, while preserving an explicitly earlier reminder.
        if reminder and due_value:
            try:
                due_dt = datetime.fromisoformat(due_value.replace("Z", "+00:00"))
                if due_dt.tzinfo is None:
                    due_dt = due_dt.astimezone()
                remind_dt = (
                    datetime.fromisoformat(remind_value.replace("Z", "+00:00"))
                    if remind_value
                    else None
                )
                if remind_dt is not None and remind_dt.tzinfo is None:
                    remind_dt = remind_dt.astimezone()
                if remind_dt is None or remind_dt == due_dt:
                    remind_value = (due_dt - timedelta(minutes=reminder_minutes)).isoformat()
            except (TypeError, ValueError, OverflowError):
                pass
        return cls(
            id=str(value.get("id") or uuid4().hex),
            title=title_value,
            date=date_value,
            time=time_value,
            important=bool(value.get("important", False)),
            completed=bool(value.get("completed", False)),
            reminder=reminder,
            created_at=created_at,
            completed_at=str(value.get("completed_at") or "") or None,
            work_seconds=max(0, int(value.get("work_seconds", 0) or 0)),
            due_at=due_value,
            remind_at=remind_value,
            priority=priority,
            queue_position=queue_position,
            read=bool(value.get("read", value.get("dismissed", False))),
            read_at=str(value.get("read_at") or "") or None,
            reminder_minutes_before=reminder_minutes,
            reminder_mode=reminder_mode,
            alarm_sound_id=str(value.get("alarm_sound_id") or "system")[:40],
            alarm_volume=alarm_volume,
            alarm_snooze_minutes=alarm_snooze,
            reminder_suppressed=bool(value.get("reminder_suppressed", False)),
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
        priority: int | None = None,
        queue_position: int | None = None,
        reminder_minutes_before: int = 10,
        reminder_mode: str = REMINDER_PET,
        alarm_sound_id: str = "system",
        alarm_volume: int = 60,
        alarm_snooze_minutes: int = 10,
        reminder_suppressed: bool = False,
        source: str = "local",
    ) -> TodoItem:
        title = " ".join(str(title).split())[:240]
        if not title:
            raise ValueError("任务标题不能为空")
        parsed_date = parse_date(date, self._now).isoformat()
        clean_time = str(time or "").strip()[:5] or None
        parsed_due = parse_datetime(due_at, self._now) if due_at else None
        parsed_remind = parse_datetime(remind_at, self._now) if remind_at else None
        try:
            clean_priority = int(priority) if priority is not None else None
        except (TypeError, ValueError):
            clean_priority = None
        if clean_priority not in {1, 2, 3}:
            clean_priority = None
        try:
            clean_queue_position = int(queue_position) if queue_position is not None else None
        except (TypeError, ValueError):
            clean_queue_position = None
        if clean_queue_position not in {1, 2, 3, 4, 5}:
            clean_queue_position = None
        clean_mode = normalize_reminder_mode(reminder_mode, legacy_reminder=bool(reminder))
        try:
            clean_reminder_minutes = max(0, min(24 * 60, int(reminder_minutes_before)))
        except (TypeError, ValueError):
            clean_reminder_minutes = 10
        # A reminder with no separate due time is still shown in the Todo
        # strip, so a request like “明天9:30提醒我改论文” is immediately
        # visible instead of living only in reminders.json.
        display_time = clean_time
        if parsed_due is not None:
            parsed_date = parsed_due.date().isoformat()
            display_time = parsed_due.strftime("%H:%M")
        # A reminder-only record has no event time. Keep its notification
        # timestamp exclusively in ``remind_at``; showing it as ``time``
        # would make the desktop sticky note claim that the event itself is
        # happening when the reminder fires.
        due_value = parsed_due.isoformat() if parsed_due else (
            f"{parsed_date}T{display_time}:00" if display_time else None
        )
        remind_value = parsed_remind.isoformat() if parsed_remind else None
        if remind_value is None and clean_mode != REMINDER_NONE and due_value:
            try:
                reminder_due = parse_datetime(due_value, self._now)
                remind_value = (
                    reminder_due - timedelta(minutes=clean_reminder_minutes)
                ).isoformat()
            except (TypeError, ValueError):
                remind_value = None
        item = TodoItem(
            id=item_id or uuid4().hex,
            title=title,
            date=parsed_date,
            time=display_time,
            important=bool(important),
            reminder=clean_mode != REMINDER_NONE,
            created_at=now_local(self._now).isoformat(),
            due_at=due_value,
            remind_at=remind_value,
            priority=clean_priority,
            queue_position=None,
            reminder_minutes_before=clean_reminder_minutes,
            reminder_mode=clean_mode,
            alarm_sound_id=str(alarm_sound_id or "system")[:40],
            alarm_volume=max(0, min(100, int(alarm_volume or 0))),
            alarm_snooze_minutes=max(1, min(120, int(alarm_snooze_minutes or 10))),
            reminder_suppressed=bool(reminder_suppressed),
            source=str(source or "local")[:40],
        )
        self._items.append(item)
        self._save()
        if clean_queue_position is not None:
            self.set_queue_position(item.id, clean_queue_position)
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
            "work_seconds", "due_at", "remind_at", "priority", "read",
            "queue_position",
            "read_at", "reminder_minutes_before", "reminder_mode", "alarm_sound_id",
            "alarm_volume", "alarm_snooze_minutes", "reminder_suppressed", "source",
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
            elif key == "reminder_mode":
                value = normalize_reminder_mode(value, legacy_reminder=item.reminder)
            elif key == "priority":
                try:
                    value = int(value) if value is not None else None
                except (TypeError, ValueError):
                    value = None
                if value not in {1, 2, 3}:
                    value = None
            elif key == "queue_position":
                try:
                    value = int(value) if value is not None else None
                except (TypeError, ValueError):
                    value = None
                if value not in {1, 2, 3, 4, 5}:
                    value = None
            elif key == "read":
                value = bool(value)
            elif key == "read_at":
                value = now_local(self._now).isoformat() if value else None
            elif key == "reminder_minutes_before":
                try:
                    value = max(0, min(24 * 60, int(value)))
                except (TypeError, ValueError):
                    value = 10
            elif key == "alarm_volume":
                try:
                    value = max(0, min(100, int(value)))
                except (TypeError, ValueError):
                    value = 60
            elif key == "alarm_snooze_minutes":
                try:
                    value = max(1, min(120, int(value)))
                except (TypeError, ValueError):
                    value = 10
            elif key == "reminder_suppressed":
                value = bool(value)
            elif key == "work_seconds":
                value = max(0, int(value))
            elif key in {"due_at", "remind_at"}:
                value = parse_datetime(value, self._now).isoformat() if value else None
            elif key == "source":
                value = str(value or "local")[:40]
            setattr(item, key, value)
        if "reminder_mode" in changes:
            item.reminder = item.reminder_mode != REMINDER_NONE
        elif "reminder" in changes:
            item.reminder_mode = REMINDER_PET if item.reminder else REMINDER_NONE
        if changed_date_or_time and not explicit_due:
            item.due_at = f"{item.date}T{item.time}:00" if item.time else None
        if item.reminder_mode != REMINDER_NONE and not explicit_remind and (
            changed_date_or_time or "reminder_minutes_before" in changes or "reminder" in changes
        ):
            if item.due_at:
                due = parse_datetime(item.due_at, self._now)
                item.remind_at = (
                    due - timedelta(minutes=item.reminder_minutes_before)
                ).isoformat()
            else:
                item.remind_at = None
        if item.reminder_mode == REMINDER_NONE:
            item.remind_at = None
            item.reminder_suppressed = False
        elif any(
            key in changes
            for key in (
                "date", "time", "due_at", "remind_at", "reminder",
                "reminder_mode", "reminder_minutes_before",
            )
        ) and "reminder_suppressed" not in changes:
            # Editing the schedule is an explicit request to make the
            # reminder live again.
            item.reminder_suppressed = False
        if item.completed and not item.completed_at:
            item.completed_at = now_local(self._now).isoformat()
        if item.completed:
            item.queue_position = None
        if not item.completed:
            item.completed_at = None
        if item.read and not item.read_at:
            item.read_at = now_local(self._now).isoformat()
        if not item.read:
            item.read_at = None
        self._save()
        if "queue_position" in changes and not item.completed:
            return self.set_queue_position(item.id, item.queue_position)
        return item

    def complete(self, item_id: str, completed: bool = True) -> TodoItem:
        item = self.update(item_id, completed=completed)
        if item.completed:
            self.normalize_queue()
        return item

    def _explicit_queue_items(self) -> list[TodoItem]:
        """Return the active, explicitly queued items in stable order."""

        candidates = [
            item
            for item in self._items
            if not item.completed
            and not item.read
            and item.queue_position in {1, 2, 3, 4, 5}
        ]
        return sorted(
            candidates,
            key=lambda item: (
                int(item.queue_position or 99),
                item.date,
                item.time or "99:99",
                item.created_at,
            ),
        )

    def queued_items(self) -> tuple[TodoItem, ...]:
        """Return the current 1..5 queue without exposing mutable internals."""

        return tuple(self._explicit_queue_items())

    def set_queue_position(self, item_id: str, position: int | None) -> TodoItem:
        """Insert/remove one pending Todo in the strict five-item queue."""

        item = self.get(item_id)
        if item is None:
            raise KeyError(item_id)
        current = [queued for queued in self._explicit_queue_items() if queued.id != item.id]
        if position in {1, 2, 3, 4, 5} and not item.completed and not item.read:
            index = min(int(position) - 1, len(current))
            current.insert(index, item)
        for candidate in self._items:
            candidate.queue_position = None
        for index, candidate in enumerate(current[:5], start=1):
            candidate.queue_position = index
        self._save()
        return item

    def reorder_queue(self, item_ids: list[str]) -> tuple[TodoItem, ...]:
        """Apply a drag/drop order atomically and normalize positions."""

        by_id = {item.id: item for item in self._explicit_queue_items()}
        ordered = [by_id[item_id] for item_id in item_ids if item_id in by_id]
        for candidate in self._explicit_queue_items():
            if candidate not in ordered:
                ordered.append(candidate)
        for candidate in self._items:
            candidate.queue_position = None
        for index, candidate in enumerate(ordered[:5], start=1):
            candidate.queue_position = index
        self._save()
        return tuple(ordered[:5])

    def normalize_queue(self) -> tuple[TodoItem, ...]:
        """Compact positions after completion, deletion, or read dismissal."""

        return self.reorder_queue([item.id for item in self._explicit_queue_items()])

    def mark_read(self, item_id: str, read: bool = True) -> TodoItem:
        """Dismiss a sticky note without changing its completion state."""

        item = self.update(item_id, read=read, read_at=(now_local(self._now).isoformat() if read else None))
        if read:
            self.normalize_queue()
        return item

    def auto_hidden(self, item: TodoItem, *, now: datetime | None = None) -> bool:
        """Whether a timed note passed its due time by more than 24 hours.

        Untimed notes intentionally never expire automatically.  The record is
        retained even after it stops being rendered so the user can recover it
        from the Todo Center instead of losing data at midnight.
        """

        if not item.time and not item.due_at:
            return False
        due_text = item.due_at or f"{item.date}T{item.time}:00"
        try:
            due = parse_datetime(due_text, self._now)
        except (TypeError, ValueError):
            return False
        current = now_local(self._now) if now is None else now
        if current.tzinfo is None:
            current = current.astimezone()
        return current > due + timedelta(hours=24)

    def delete(self, item_id: str) -> bool:
        before = len(self._items)
        self._items = [item for item in self._items if item.id != str(item_id)]
        changed = len(self._items) != before
        if changed:
            self.normalize_queue()
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
                if item.reminder_mode != REMINDER_NONE:
                    if item.due_at:
                        due = parse_datetime(item.due_at, self._now)
                        item.remind_at = (
                            due - timedelta(minutes=item.reminder_minutes_before)
                        ).isoformat()
                item.read = False
                item.read_at = None
                moved.append(item)
        if moved:
            self._save()
        return moved

