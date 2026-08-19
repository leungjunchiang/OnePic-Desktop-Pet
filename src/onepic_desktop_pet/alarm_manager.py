"""Persistent local alarms built on top of Lili's reminder clock.

An alarm is intentionally separate from a normal Todo reminder: it stays
active until the user starts work, snoozes it, or closes it.  The manager is
UI-agnostic so Windows and macOS can use the same scheduling rules without
ever activating the pet window.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable
from uuid import uuid4

from .local_data import local_data_path, read_json, write_json_atomic
from .time_service import now_local, parse_datetime


REPEAT_ONCE = "once"
REPEAT_DAILY = "daily"
REPEAT_WEEKDAYS = "weekdays"


@dataclass
class Alarm:
    id: str
    title: str
    trigger_at: str
    repeat_rule: str = REPEAT_ONCE
    enabled: bool = True
    sound_enabled: bool = False
    snooze_minutes: int = 10
    linked_todo_id: str | None = None
    pet_action: str = "alarm"
    allow_during_dnd: bool = False
    active: bool = False
    last_triggered_slot: str | None = None
    snooze_until: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Alarm":
        repeat = str(value.get("repeat_rule") or REPEAT_ONCE)
        if repeat not in {REPEAT_ONCE, REPEAT_DAILY, REPEAT_WEEKDAYS} and not repeat.startswith("weekly:"):
            repeat = REPEAT_ONCE
        try:
            snooze_minutes = max(1, min(120, int(value.get("snooze_minutes", 10) or 10)))
        except (TypeError, ValueError):
            snooze_minutes = 10
        return cls(
            id=str(value.get("id") or uuid4().hex),
            title=str(value.get("title") or "六毛闹钟")[:240],
            trigger_at=str(value.get("trigger_at") or ""),
            repeat_rule=repeat,
            enabled=bool(value.get("enabled", True)),
            sound_enabled=bool(value.get("sound_enabled", False)),
            snooze_minutes=snooze_minutes,
            linked_todo_id=str(value.get("linked_todo_id") or "") or None,
            pet_action=str(value.get("pet_action") or "alarm")[:40],
            allow_during_dnd=bool(value.get("allow_during_dnd", False)),
            active=bool(value.get("active", False)),
            last_triggered_slot=str(value.get("last_triggered_slot") or "") or None,
            snooze_until=str(value.get("snooze_until") or "") or None,
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
        raw = read_json(self.path, [])
        self._items = (
            [Alarm.from_dict(item) for item in raw if isinstance(item, dict)]
            if isinstance(raw, list)
            else []
        )

    @property
    def items(self) -> tuple[Alarm, ...]:
        return tuple(self._items)

    def _save(self) -> None:
        if self.persist:
            write_json_atomic(self.path, [asdict(item) for item in self._items])

    def add(
        self,
        title: str,
        trigger_at: str | datetime,
        *,
        repeat_rule: str = REPEAT_ONCE,
        sound_enabled: bool = False,
        snooze_minutes: int = 10,
        linked_todo_id: str | None = None,
        pet_action: str = "alarm",
        allow_during_dnd: bool = False,
    ) -> Alarm:
        trigger = parse_datetime(trigger_at, self._now).isoformat()
        item = Alarm(
            id=uuid4().hex,
            title=str(title).strip()[:240] or "六毛闹钟",
            trigger_at=trigger,
            repeat_rule=self._normalize_repeat(repeat_rule),
            sound_enabled=bool(sound_enabled),
            snooze_minutes=max(1, min(120, int(snooze_minutes))),
            linked_todo_id=str(linked_todo_id or "") or None,
            pet_action=str(pet_action or "alarm")[:40],
            allow_during_dnd=bool(allow_during_dnd),
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
            item.trigger_at = parse_datetime(changes["trigger_at"], self._now).isoformat()
        if "repeat_rule" in changes:
            item.repeat_rule = self._normalize_repeat(changes["repeat_rule"])
        if "sound_enabled" in changes:
            item.sound_enabled = bool(changes["sound_enabled"])
        if "snooze_minutes" in changes:
            item.snooze_minutes = max(1, min(120, int(changes["snooze_minutes"])))
        if "linked_todo_id" in changes:
            item.linked_todo_id = str(changes["linked_todo_id"] or "") or None
        if "allow_during_dnd" in changes:
            item.allow_during_dnd = bool(changes["allow_during_dnd"])
        if "enabled" in changes:
            item.enabled = bool(changes["enabled"])
        item.active = False
        item.snooze_until = None
        item.last_triggered_slot = None
        self._save()
        return item

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
        self._save()
        return item

    def claim_due(
        self,
        *,
        now: datetime | None = None,
        grace_minutes: int = 30,
        allow_during_dnd: bool = True,
    ) -> list[Alarm]:
        """Claim alarms due now, keeping them active until user action.

        A one-off alarm missed by more than ``grace_minutes`` is disabled
        instead of suddenly shouting hours after the user restarts Lili.
        Repeating alarms follow the same rule for the missed occurrence and
        become eligible at the next scheduled slot.
        """

        current = now_local(now or self._now)
        claimed: list[Alarm] = []
        changed = False
        for item in self._items:
            if not item.enabled or item.active:
                continue
            if not allow_during_dnd and not item.allow_during_dnd:
                continue
            slot = self._snooze_slot(item, current)
            if slot is None:
                slot = self._scheduled_slot(item, current)
            if slot is None or slot <= current and item.last_triggered_slot == slot.isoformat():
                continue
            if slot > current:
                continue
            if (current - slot).total_seconds() > max(1, int(grace_minutes)) * 60:
                if item.repeat_rule == REPEAT_ONCE:
                    item.enabled = False
                item.last_triggered_slot = slot.isoformat()
                item.snooze_until = None
                changed = True
                continue
            item.active = True
            item.last_triggered_slot = slot.isoformat()
            item.snooze_until = None
            claimed.append(item)
            changed = True
        if changed:
            self._save()
        return claimed

    def snooze(self, alarm_id: str, minutes: int | None = None) -> Alarm:
        item = self.get(alarm_id)
        if item is None:
            raise KeyError(alarm_id)
        delay = max(1, min(120, int(minutes or item.snooze_minutes)))
        item.active = False
        item.snooze_until = (now_local(self._now) + timedelta(minutes=delay)).isoformat()
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
        self._save()
        return item

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

