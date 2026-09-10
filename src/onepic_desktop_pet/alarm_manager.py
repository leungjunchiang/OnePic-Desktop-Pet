"""基于六毛提醒时钟的本地持久闹钟；关闭、错过与删除拥有独立生命周期。

An alarm is intentionally separate from a normal Todo reminder: it stays
active until the user starts work, snoozes it, or closes it.  The manager is
UI-agnostic so Windows and macOS can use the same scheduling rules without
ever activating the pet window.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Callable
from uuid import uuid4

from .local_data import local_data_path, read_json, write_json_atomic
from .time_service import now_local, parse_datetime


REPEAT_ONCE = "once"
REPEAT_DAILY = "daily"
REPEAT_WEEKDAYS = "weekdays"

# A firing card must not remain stuck forever when Lili is closed while the
# user is away.  An already-open firing card may be retried on the same alarm;
# a scheduled occurrence that was never shown is handled separately below so
# a stale alarm cannot unexpectedly ring much later.
MISSED_ALARM_GRACE_MINUTES = 10
MISSED_ALARM_RETRY_MINUTES = 30


@dataclass
class Alarm:
    id: str
    title: str
    trigger_at: str
    repeat_rule: str = REPEAT_ONCE
    enabled: bool = True
    sound_enabled: bool = False
    sound_id: str = "system"
    volume: int = 60
    max_ring_seconds: int = 60
    snooze_minutes: int = 10
    linked_todo_id: str | None = None
    pet_action: str = "alarm"
    allow_during_dnd: bool = False
    source_todo_id: str | None = None
    # Monotonically increasing token for scheduler callbacks.  A callback
    # captured before an edit/enable/snooze operation must never be allowed
    # to claim the newly configured occurrence.
    schedule_generation: int = 1
    # Runtime-only state.  It is deliberately never restored from disk.
    active: bool = False
    last_triggered_slot: str | None = None
    snooze_until: str | None = None
    # Lifecycle metadata.  Disabling/dismissing is intentionally distinct
    # from deleting so a closed alarm remains visible after a day boundary or
    # restart.
    origin: str = "standalone"
    created_at: str = ""
    disabled_at: str | None = None
    disabled_reason: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Alarm":
        repeat = str(value.get("repeat_rule") or REPEAT_ONCE)
        if repeat not in {REPEAT_ONCE, REPEAT_DAILY, REPEAT_WEEKDAYS} and not repeat.startswith("weekly:"):
            repeat = REPEAT_ONCE
        try:
            snooze_minutes = max(1, min(120, int(value.get("snooze_minutes", 10) or 10)))
        except (TypeError, ValueError):
            snooze_minutes = 10
        try:
            volume = max(0, min(100, int(value.get("volume", 60) or 0)))
        except (TypeError, ValueError):
            volume = 60
        try:
            max_ring_seconds = max(0, min(300, int(value.get("max_ring_seconds", 60) or 0)))
        except (TypeError, ValueError):
            max_ring_seconds = 60
        try:
            schedule_generation = max(1, int(value.get("schedule_generation", 1) or 1))
        except (TypeError, ValueError):
            schedule_generation = 1
        return cls(
            id=str(value.get("id") or uuid4().hex),
            title=str(value.get("title") or "六毛闹钟")[:240],
            trigger_at=str(value.get("trigger_at") or ""),
            repeat_rule=repeat,
            enabled=bool(value.get("enabled", True)),
            sound_enabled=bool(value.get("sound_enabled", False)),
            sound_id=str(value.get("sound_id") or "system")[:40],
            volume=volume,
            max_ring_seconds=max_ring_seconds,
            snooze_minutes=snooze_minutes,
            linked_todo_id=str(value.get("linked_todo_id") or "") or None,
            pet_action=str(value.get("pet_action") or "alarm")[:40],
            allow_during_dnd=bool(value.get("allow_during_dnd", False)),
            source_todo_id=str(value.get("source_todo_id") or "") or None,
            # ``active`` means that a foreground card is currently owned by
            # this process.  Restoring it after a crash/restart resurrects a
            # stale popup and can replay an old custom ringtone.
            active=False,
            last_triggered_slot=str(value.get("last_triggered_slot") or "") or None,
            snooze_until=str(value.get("snooze_until") or "") or None,
            schedule_generation=schedule_generation,
            origin=str(value.get("origin") or ("todo" if value.get("source_todo_id") else "standalone"))[:20],
            created_at=str(value.get("created_at") or ""),
            disabled_at=str(value.get("disabled_at") or "") or None,
            disabled_reason=str(value.get("disabled_reason") or "") or None,
        )


class AlarmManager:
    """Store and resolve alarms with one local, deterministic clock."""

    def __init__(
        self,
        path=None,
        *,
        now_provider: Callable[[], datetime] | None = None,
        persist: bool = True,
    ) -> None:
        self.path = path or local_data_path("alarms.json")
        self._now = now_provider or (lambda: datetime.now().astimezone())
        self.persist = bool(persist)
        # A process must not manufacture a new afternoon retry for a daily
        # alarm whose morning slot passed while the app was not running.  A
        # retry is still allowed when this process was already alive before
        # the slot and its one-second scheduler missed the delivery window.
        self._started_at = now_local(self._now)
        raw = read_json(self.path, [])
        raw_items = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
        self._items = (
            [Alarm.from_dict(item) for item in raw_items]
            if isinstance(raw, list)
            else []
        )
        # Alarm editor times are minute-based.  Normalize rows created by
        # older builds as well, otherwise a hidden :31 (for example) would
        # continue to fire away from the user's configured minute boundary.
        precision_repaired = False
        for item in self._items:
            try:
                normalized_trigger = self._normalize_trigger_at(item.trigger_at)
            except (TypeError, ValueError):
                normalized_trigger = None
            if normalized_trigger is not None and normalized_trigger != item.trigger_at:
                item.trigger_at = normalized_trigger
                precision_repaired = True
            if item.last_triggered_slot:
                try:
                    normalized_last = self._normalize_trigger_at(item.last_triggered_slot)
                except (TypeError, ValueError):
                    normalized_last = None
                if normalized_last is not None and normalized_last != item.last_triggered_slot:
                    item.last_triggered_slot = normalized_last
                    precision_repaired = True
        # Older builds persisted the transient foreground state.  Preserve
        # the already claimed slot (written at claim time), but never restore
        # the popup/audio ownership itself.  For malformed legacy rows that
        # lack a claimed slot, the configured occurrence is the safest one to
        # mark as consumed; this prevents an old 10:00 alarm reappearing at
        # 15:00 after a restart.
        legacy_active_repaired = False
        for raw_item, item in zip(raw_items, self._items):
            if bool(raw_item.get("active", False)):
                legacy_active_repaired = True
                # A legacy active row represents a firing card, not a
                # snooze timer.  Keeping its old snooze value would turn the
                # stale card into a new afternoon retry on restart.
                item.snooze_until = None
                if not item.last_triggered_slot:
                    item.last_triggered_slot = item.trigger_at or None
        if precision_repaired or legacy_active_repaired:
            self._save()

    @property
    def items(self) -> tuple[Alarm, ...]:
        return tuple(self._items)

    def _save(self) -> None:
        if self.persist:
            payload = []
            for item in self._items:
                value = asdict(item)
                # The next process must start without an owned popup or a
                # live player.  Scheduling history remains persisted through
                # ``last_triggered_slot``/``snooze_until``.
                value["active"] = False
                payload.append(value)
            write_json_atomic(self.path, payload)

    def _normalize_trigger_at(self, value: str | datetime) -> str:
        return (
            parse_datetime(value, self._now)
            .replace(second=0, microsecond=0)
            .isoformat()
        )

    def add(
        self,
        title: str,
        trigger_at: str | datetime,
        *,
        repeat_rule: str = REPEAT_ONCE,
        enabled: bool = True,
        sound_enabled: bool = False,
        sound_id: str = "system",
        volume: int = 60,
        max_ring_seconds: int = 60,
        snooze_minutes: int = 10,
        linked_todo_id: str | None = None,
        pet_action: str = "alarm",
        allow_during_dnd: bool = False,
        source_todo_id: str | None = None,
    ) -> Alarm:
        trigger = self._normalize_trigger_at(trigger_at)
        item = Alarm(
            id=uuid4().hex,
            title=str(title).strip()[:240] or "六毛闹钟",
            trigger_at=trigger,
            repeat_rule=self._normalize_repeat(repeat_rule),
            enabled=bool(enabled),
            sound_enabled=bool(sound_enabled),
            sound_id=str(sound_id or "system")[:40],
            volume=max(0, min(100, int(volume or 0))),
            max_ring_seconds=max(0, min(300, int(max_ring_seconds or 0))),
            snooze_minutes=max(1, min(120, int(snooze_minutes))),
            linked_todo_id=str(linked_todo_id or "") or None,
            pet_action=str(pet_action or "alarm")[:40],
            allow_during_dnd=bool(allow_during_dnd),
            source_todo_id=str(source_todo_id or "") or None,
            origin="todo" if source_todo_id else "standalone",
            created_at=now_local(self._now).isoformat(),
            schedule_generation=1,
        )
        self._items.append(item)
        self._save()
        return item

    def update(self, alarm_id: str, **changes: Any) -> Alarm:
        item = self.get(alarm_id)
        if item is None:
            raise KeyError(alarm_id)
        if "title" in changes:
            item.title = str(changes["title"] or "六毛闹钟").strip()[:240]
        if "trigger_at" in changes:
            item.trigger_at = self._normalize_trigger_at(changes["trigger_at"])
        if "repeat_rule" in changes:
            item.repeat_rule = self._normalize_repeat(changes["repeat_rule"])
        if "sound_enabled" in changes:
            item.sound_enabled = bool(changes["sound_enabled"])
        if "sound_id" in changes:
            item.sound_id = str(changes["sound_id"] or "system")[:40]
        if "volume" in changes:
            item.volume = max(0, min(100, int(changes["volume"])))
        if "max_ring_seconds" in changes:
            item.max_ring_seconds = max(0, min(300, int(changes["max_ring_seconds"])))
        if "snooze_minutes" in changes:
            item.snooze_minutes = max(1, min(120, int(changes["snooze_minutes"])))
        if "linked_todo_id" in changes:
            item.linked_todo_id = str(changes["linked_todo_id"] or "") or None
        if "allow_during_dnd" in changes:
            item.allow_during_dnd = bool(changes["allow_during_dnd"])
        if "source_todo_id" in changes:
            item.source_todo_id = str(changes["source_todo_id"] or "") or None
        if "enabled" in changes:
            item.enabled = bool(changes["enabled"])
            if item.enabled:
                item.disabled_at = None
                item.disabled_reason = None
            else:
                item.disabled_at = now_local(self._now).isoformat()
                item.disabled_reason = "user"
        item.active = False
        item.snooze_until = None
        item.last_triggered_slot = None
        item.schedule_generation = self._next_generation(item)
        self._save()
        return item

    def sync_todo(self, todo: Any, *, reminder_mode: str) -> None:
        """Mirror an ALARM Todo into the same persisted alarm scheduler.

        The Todo remains the source of truth for title, event time and
        reminder mode. The alarm row only stores runtime state such as an
        active claim or snooze, so editing a Todo cannot create duplicates.
        """

        todo_id = str(getattr(todo, "id", "") or "")
        if not todo_id:
            return
        alarm_id = f"todo:{todo_id}"
        item = self.get(alarm_id)
        mode = str(reminder_mode or "none").strip().lower()
        due = getattr(todo, "remind_at", None) or getattr(todo, "due_at", None)
        should_exist = mode == "alarm" and not bool(getattr(todo, "completed", False)) and bool(due)
        if not should_exist:
            if item is not None:
                item.enabled = False
                item.active = False
                item.snooze_until = None
                item.schedule_generation = self._next_generation(item)
                item.disabled_at = item.disabled_at or now_local(self._now).isoformat()
                item.disabled_reason = (
                    "todo_completed" if bool(getattr(todo, "completed", False))
                    else "todo_reminder_changed"
                )
                self._save()
            return
        if item is None:
            item = Alarm(
                id=alarm_id,
                title=str(getattr(todo, "title", "待办"))[:240] or "待办",
                trigger_at=self._normalize_trigger_at(due),
                sound_enabled=True,
                sound_id=str(getattr(todo, "alarm_sound_id", "system") or "system")[:40],
                volume=max(0, min(100, int(getattr(todo, "alarm_volume", 60) or 0))),
                max_ring_seconds=60,
                snooze_minutes=max(1, min(120, int(getattr(todo, "alarm_snooze_minutes", 10) or 10))),
                linked_todo_id=todo_id,
                source_todo_id=todo_id,
                origin="todo",
                created_at=now_local(self._now).isoformat(),
                schedule_generation=1,
            )
            self._items.append(item)
            self._save()
            return
        was_user_disabled = (not item.enabled and item.disabled_reason == "user")
        due_trigger = self._normalize_trigger_at(due)
        changed = (
            (not item.enabled and not was_user_disabled)
            or
            item.title != str(getattr(todo, "title", "待办"))[:240]
            or item.trigger_at != due_trigger
            or item.sound_id != str(getattr(todo, "alarm_sound_id", "system") or "system")[:40]
            or item.volume != max(0, min(100, int(getattr(todo, "alarm_volume", 60) or 0)))
            or item.snooze_minutes != max(1, min(120, int(getattr(todo, "alarm_snooze_minutes", 10) or 10)))
        )
        item.title = str(getattr(todo, "title", "待办"))[:240] or "待办"
        if not was_user_disabled:
            item.enabled = True
            item.disabled_at = None
            item.disabled_reason = None
        item.trigger_at = due_trigger
        item.sound_enabled = True
        item.sound_id = str(getattr(todo, "alarm_sound_id", "system") or "system")[:40]
        item.volume = max(0, min(100, int(getattr(todo, "alarm_volume", 60) or 0)))
        item.max_ring_seconds = 60
        item.snooze_minutes = max(1, min(120, int(getattr(todo, "alarm_snooze_minutes", 10) or 10)))
        item.linked_todo_id = todo_id
        item.source_todo_id = todo_id
        item.origin = "todo"
        if changed:
            item.active = False
            item.snooze_until = None
            item.last_triggered_slot = None
            item.schedule_generation = self._next_generation(item)
            self._save()

    def get(self, alarm_id: str) -> Alarm | None:
        return next((item for item in self._items if item.id == str(alarm_id)), None)

    def delete(self, alarm_id: str) -> bool:
        before = len(self._items)
        self._items = [item for item in self._items if item.id != str(alarm_id)]
        changed = len(self._items) != before
        if changed:
            self._save()
        return changed

    def active(self) -> list[Alarm]:
        return [item for item in self._items if item.enabled and item.active]

    def set_enabled(self, alarm_id: str, enabled: bool) -> Alarm:
        item = self.get(alarm_id)
        if item is None:
            raise KeyError(alarm_id)
        item.enabled = bool(enabled)
        if not item.enabled:
            item.active = False
            item.snooze_until = None
            item.disabled_at = now_local(self._now).isoformat()
            item.disabled_reason = "user"
        else:
            # Re-enabling is a new schedule.  If today's occurrence already
            # passed, mark only that old occurrence as handled so enabling a
            # 10:00 alarm at 15:00 schedules tomorrow rather than a ghost
            # retry this afternoon.
            current = now_local(self._now)
            item.active = False
            item.snooze_until = None
            item.last_triggered_slot = self._past_or_current_slot(item, current)
            item.disabled_at = None
            item.disabled_reason = None
        item.schedule_generation = self._next_generation(item)
        self._save()
        return item

    def claim_due(
        self,
        *,
        now: datetime | None = None,
        grace_minutes: int = MISSED_ALARM_GRACE_MINUTES,
        allow_during_dnd: bool = True,
    ) -> list[Alarm]:
        """Claim at most one due alarm and keep it active until user action.

        The desktop app has one foreground alarm card, so the scheduler also
        keeps one active claim globally.  This prevents two due rows from
        being persisted as simultaneously ringing and then appearing one
        after another after a restart.

        A scheduled occurrence that is at least ``grace_minutes`` late is
        marked handled without showing a late popup.  This prevents an alarm
        configured for an earlier time from unexpectedly ringing much later
        after a delayed scheduler tick.  An already-open firing card still
        uses the existing retry lane, and an explicit user snooze is honored
        as a separate reminder.
        """

        current = now_local(now or self._now)
        changed = False

        # Repair state written by an older build (or by a process that was
        # terminated while a card was open).  Keep the earliest active alarm
        # and move any additional active rows to the same single retry lane.
        active_items = sorted(
            (item for item in self._items if item.enabled and item.active),
            key=lambda item: (
                self._active_slot(item, current) or current,
                item.created_at or "",
            ),
        )
        active_keeper: Alarm | None = None
        for item in active_items:
            slot = self._active_slot(item, current)
            if slot is not None and self._is_late(slot, current, grace_minutes):
                if self._skip_missed_non_delivery_day(item, current, slot):
                    self._finish_missed_occurrence(item, slot)
                else:
                    self._reschedule_after_missed(item, current, slot)
                changed = True
                continue
            if active_keeper is None:
                active_keeper = item
                continue
            self._reschedule_after_missed(item, current, slot or current)
            changed = True

        # There is already one alarm card waiting for a user action.  Do not
        # claim another alarm in the same timer tick or after a restart.
        if active_keeper is not None:
            if changed:
                self._save()
            return []

        due: list[tuple[datetime, Alarm]] = []
        for item in self._items:
            if not item.enabled or item.active:
                continue
            if not allow_during_dnd and not item.allow_during_dnd:
                continue
            snooze_slot = self._snooze_slot(item, current)
            slot = snooze_slot
            if slot is None:
                slot = self._scheduled_slot(item, current)
            if slot is None or slot <= current and self._was_triggered(item, slot):
                continue
            if slot > current:
                continue
            if self._should_skip_startup_catchup(item, slot):
                self._finish_missed_occurrence(item, slot)
                changed = True
                continue
            if self._is_late(slot, current, grace_minutes):
                if snooze_slot is not None:
                    self._reschedule_after_missed(item, current, slot)
                else:
                    # Never turn a missed scheduled occurrence into a new
                    # surprise alarm.  Recurring alarms will be evaluated at
                    # their next configured occurrence.
                    self._finish_missed_occurrence(item, slot)
                changed = True
                continue

            due.append((slot, item))

        # Stable ordering makes the single-card rule deterministic when old
        # data contains several alarms with the same due time.
        # Python's sort is stable, so equal timestamps retain the persisted
        # insertion order.  This makes the first alarm win deterministically
        # even when two rows were created in the same clock tick.
        due.sort(key=lambda pair: (pair[0], pair[1].created_at or ""))
        claimed: list[Alarm] = []
        if due:
            _slot, item = due[0]
            item.active = True
            item.last_triggered_slot = _slot.isoformat()
            item.snooze_until = None
            claimed.append(item)
            changed = True
        if changed:
            self._save()
        return claimed

    @staticmethod
    def _is_late(slot: datetime, current: datetime, grace_minutes: int) -> bool:
        return slot <= current and (current - slot).total_seconds() >= max(1, int(grace_minutes)) * 60

    def _active_slot(self, item: Alarm, current: datetime) -> datetime | None:
        """Return the timestamp represented by a persisted active claim."""

        for value in (item.last_triggered_slot, item.snooze_until, item.trigger_at):
            if not value:
                continue
            try:
                return parse_datetime(value, self._now)
            except (TypeError, ValueError):
                continue
        return None

    def _was_triggered(self, item: Alarm, slot: datetime) -> bool:
        """Avoid replaying the same recurring occurrence after a retry."""

        if not item.last_triggered_slot:
            return False
        try:
            last = parse_datetime(item.last_triggered_slot, self._now)
        except (TypeError, ValueError):
            return False
        if item.repeat_rule == REPEAT_ONCE:
            return last == slot
        # A snoozed/recovered alarm records the actual retry time.  Once that
        # retry is handled, the original scheduled time on the same calendar
        # day must not fire a second card.
        return last.date() == slot.date() and last >= slot

    @staticmethod
    def _skip_missed_non_delivery_day(
        item: Alarm,
        current: datetime,
        slot: datetime,
    ) -> bool:
        """Do not make a weekday/weekly alarm ring on an excluded day."""

        if item.repeat_rule in {REPEAT_ONCE, REPEAT_DAILY}:
            return False
        if item.repeat_rule == REPEAT_WEEKDAYS:
            allowed = {0, 1, 2, 3, 4}
        elif item.repeat_rule.startswith("weekly:"):
            try:
                allowed = {
                    int(value)
                    for value in item.repeat_rule.split(":", 1)[1].split(",")
                }
            except ValueError:
                allowed = set()
        else:
            allowed = set(range(7))
        return current.weekday() not in allowed and slot.date() != current.date()

    def _finish_missed_occurrence(self, item: Alarm, slot: datetime) -> None:
        """Mark an occurrence as handled without creating a retry."""

        item.active = False
        item.last_triggered_slot = slot.isoformat()
        item.snooze_until = None
        item.schedule_generation = self._next_generation(item)

    def _should_skip_startup_catchup(self, item: Alarm, slot: datetime) -> bool:
        """Return whether *slot* belongs to a period before this process.

        Persisted alarms are not allowed to catch up after the process starts,
        while newly created alarms are exempt from this startup guard.  The
        normal grace-window check still decides whether a newly created alarm
        is close enough to its configured time to show immediately.
        """

        if slot >= self._started_at:
            return False
        try:
            created = parse_datetime(item.created_at, self._now)
        except (TypeError, ValueError):
            # Legacy rows without creation metadata are persisted alarms, not
            # a newly entered reminder in this process.
            return True
        return created < self._started_at

    def _reschedule_after_missed(self, item: Alarm, current: datetime, slot: datetime) -> None:
        """Move a missed claim to one retry without creating another alarm row."""

        item.active = False
        item.last_triggered_slot = slot.isoformat()
        item.snooze_until = (
            current + timedelta(minutes=MISSED_ALARM_RETRY_MINUTES)
        ).isoformat()
        item.schedule_generation = self._next_generation(item)

    def snooze(self, alarm_id: str, minutes: int | None = None) -> Alarm:
        item = self.get(alarm_id)
        if item is None:
            raise KeyError(alarm_id)
        delay = max(1, min(120, int(minutes or item.snooze_minutes)))
        item.active = False
        item.snooze_until = (now_local(self._now) + timedelta(minutes=delay)).isoformat()
        item.schedule_generation = self._next_generation(item)
        self._save()
        return item

    def dismiss(self, alarm_id: str) -> Alarm:
        item = self.get(alarm_id)
        if item is None:
            raise KeyError(alarm_id)
        item.active = False
        item.snooze_until = None
        if item.repeat_rule == REPEAT_ONCE:
            item.enabled = False
            item.disabled_at = now_local(self._now).isoformat()
            item.disabled_reason = "dismissed"
        item.schedule_generation = self._next_generation(item)
        self._save()
        return item

    @staticmethod
    def _next_generation(item: Alarm) -> int:
        """Advance the token used to invalidate stale scheduler callbacks."""

        return max(1, int(item.schedule_generation or 0) + 1)

    def _past_or_current_slot(self, item: Alarm, current: datetime) -> str | None:
        """Return today's already-passed slot when re-enabling an alarm."""

        try:
            base = parse_datetime(item.trigger_at, self._now)
        except (TypeError, ValueError):
            return None
        if item.repeat_rule == REPEAT_ONCE:
            # At the exact configured second the occurrence is still due;
            # only a genuinely past slot should be skipped on re-enable.
            return base.isoformat() if base < current else None
        slot = self._scheduled_slot(item, current)
        return slot.isoformat() if slot is not None and slot < current else None

    def _snooze_slot(self, item: Alarm, current: datetime) -> datetime | None:
        if not item.snooze_until:
            return None
        try:
            return parse_datetime(item.snooze_until, self._now)
        except (TypeError, ValueError):
            item.snooze_until = None
            return None

    def _scheduled_slot(self, item: Alarm, current: datetime) -> datetime | None:
        try:
            base = parse_datetime(item.trigger_at, self._now)
        except (TypeError, ValueError):
            return None
        if item.repeat_rule == REPEAT_ONCE:
            return base
        candidate = current.replace(
            hour=base.hour,
            minute=base.minute,
            second=base.second,
            microsecond=base.microsecond,
        )
        if item.repeat_rule == REPEAT_WEEKDAYS:
            allowed = {0, 1, 2, 3, 4}
        elif item.repeat_rule.startswith("weekly:"):
            try:
                allowed = {int(value) for value in item.repeat_rule.split(":", 1)[1].split(",")}
            except ValueError:
                allowed = set()
            allowed = {value for value in allowed if 0 <= value <= 6}
        else:
            allowed = set(range(7))
        for offset in range(0, 8):
            probe = candidate - timedelta(days=offset)
            if probe.weekday() in allowed:
                return probe
        return None

    @staticmethod
    def _normalize_repeat(value: Any) -> str:
        text = str(value or REPEAT_ONCE).strip().lower()
        if text in {REPEAT_ONCE, REPEAT_DAILY, REPEAT_WEEKDAYS}:
            return text
        if text.startswith("weekly:"):
            try:
                days = sorted({int(part) for part in text.split(":", 1)[1].split(",")})
            except ValueError:
                return REPEAT_ONCE
            if all(0 <= day <= 6 for day in days) and days:
                return "weekly:" + ",".join(str(day) for day in days)
        return REPEAT_ONCE
