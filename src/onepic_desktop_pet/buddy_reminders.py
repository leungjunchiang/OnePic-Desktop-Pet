"""持久保存明确的工作状态事件与已展示的搭子提醒，不混入心跳和订阅配置。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .local_data import account_local_data_path, read_json, write_json_atomic


EVENT_TYPES = {"start_work", "finish_work"}
WORK_STATUSES = {"focused", "resting", "off_work"}
NOTIFICATION_LIFETIME_SECONDS = 180
NOTIFICATION_COOLDOWN_SECONDS = 300


def _parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


class BuddyReminderStore:
    """An account-scoped outbox and per-device notification deduplication log."""

    def __init__(self, account_id: str, *, path: Path | None = None, persist: bool = True) -> None:
        self.account_id = str(account_id or "").strip()
        self.path = path or account_local_data_path("buddy_reminder_events.json", self.account_id)
        self.persist = bool(persist)
        raw = read_json(self.path, {}) if self.persist else {}
        raw = raw if isinstance(raw, dict) else {}
        self._pending = [dict(item) for item in raw.get("pending", []) if isinstance(item, dict)]
        self._seen = dict(raw.get("seen", {})) if isinstance(raw.get("seen"), dict) else {}
        self._cooldown = dict(raw.get("cooldown", {})) if isinstance(raw.get("cooldown"), dict) else {}

    def _save(self) -> None:
        if self.persist:
            write_json_atomic(self.path, {
                "pending": self._pending, "seen": self._seen, "cooldown": self._cooldown,
            })

    def queue_transition(
        self, work_status: str, session_id: str, *,
        occurred_at: datetime | None = None, silent: bool = False,
    ) -> dict[str, Any] | None:
        if not self.account_id or work_status not in WORK_STATUSES or not str(session_id).strip():
            return None
        stamp = (occurred_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        item = {
            "p_event_id": str(uuid4()), "p_work_status": work_status,
            "p_session_id": str(session_id)[:80], "p_occurred_at": stamp.isoformat(),
            "p_silent": bool(silent),
        }
        self._pending.append(item)
        self._save()
        return dict(item)

    def pending(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._pending[:20]]

    def acknowledge(self, event_ids: list[str]) -> None:
        accepted = {str(value) for value in event_ids}
        if not accepted:
            return
        self._pending = [item for item in self._pending if str(item.get("p_event_id")) not in accepted]
        self._save()

    def unseen_events(
        self, events: object, *, now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Consume confirmed events once per device without changing subscriptions."""

        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cutoff = current - timedelta(days=2)
        self._seen = {key: value for key, value in self._seen.items()
                      if (stamp := _parse_time(value)) is not None and stamp >= cutoff}
        self._cooldown = {key: value for key, value in self._cooldown.items()
                          if (stamp := _parse_time(value)) is not None and stamp >= cutoff}
        result: list[dict[str, Any]] = []
        for raw in events if isinstance(events, list) else []:
            if not isinstance(raw, dict):
                continue
            event_id = str(raw.get("id") or "")
            event_type = str(raw.get("event_type") or "")
            target_id = str(raw.get("target_user_id") or "")
            occurred = _parse_time(raw.get("occurred_at"))
            if not event_id or event_type not in EVENT_TYPES or not target_id or occurred is None:
                continue
            if event_id in self._seen or occurred > current + timedelta(seconds=30):
                continue
            self._seen[event_id] = current.isoformat()
            if current - occurred > timedelta(seconds=NOTIFICATION_LIFETIME_SECONDS):
                continue
            key = f"{target_id}:{event_type}"
            last = _parse_time(self._cooldown.get(key))
            if last is not None and current - last < timedelta(seconds=NOTIFICATION_COOLDOWN_SECONDS):
                continue
            self._cooldown[key] = current.isoformat()
            result.append(dict(raw))
        self._save()
        return result
