"""动态计划与纪律账本；实际开工复用原始 FocusSession，历史计划快照独立于当前配置。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import uuid4
import time as monotonic_time
import hashlib
import json

from .focus_analytics import BEIJING_TIMEZONE
from .local_data import account_local_data_path, read_json, write_json_atomic


MODES = {"off", "normal", "officer"}
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_DAILY_TARGETS = {day: (360 if index < 5 else 0) for index, day in enumerate(WEEKDAYS)}
LATE_ESCALATION_MINUTES = (10, 30, 60)
BREAK_ESCALATION_MINUTES = (20, 30, 45)


def as_beijing(value: datetime | None = None) -> datetime:
    """将计划和账本时间统一到六毛使用的北京时间。"""

    current = value or datetime.now(BEIJING_TIMEZONE)
    if current.tzinfo is None:
        return current.replace(tzinfo=BEIJING_TIMEZONE)
    return current.astimezone(BEIJING_TIMEZONE)


DISCIPLINE_EVENT_TYPES = frozenset({
    "start_work", "late_start", "long_break", "finish_work", "early_finish",
    "focus_shortfall", "weekly_shortfall", "daily_report", "rest_day", "cancel_rest_day",
})
RESTORE_SOURCES = {"sleep", "lock", "display_off", "restart_safe_seal", "account_switch", "restore", "heartbeat", "startup", "reconnect"}
_SESSION_UNSET = object()


@dataclass(frozen=True)
class FocusStartIndex:
    """一次读取缓存后构建的会话起点；本周/历史无需逐日重读相同事实。"""
    starts: tuple[datetime, ...]
    fingerprint: int = 0


def local_work_time(value: datetime | None = None) -> datetime:
    """实际开工使用电脑本地时区，不改变自然日专注总计的日界线。"""
    return (value or datetime.now().astimezone()).astimezone()


def focus_start_index(sessions, *, tz=None, now=None):
    if sessions is not None:
        if isinstance(sessions, FocusStartIndex): return sessions
        from .focus_segments import FocusSegment, segment_from_record
        firsts = {}
        valid_facts = []
        for index, raw in enumerate(sessions):
            if isinstance(raw, dict) and (raw.get("source") in RESTORE_SOURCES or raw.get("synthetic") or raw.get("restored")):
                continue
            segment = raw if isinstance(raw, FocusSegment) else segment_from_record(raw, index)
            if segment is None or segment.validation_error(now or datetime.now().astimezone()):
                continue
            # Cached presence intervals can show a live chart, but are not evidence of a new start.
            if str(segment.segment_id).startswith(("display-live-device:", "display-live-remote", "presence:", "remote-live:")):
                continue
            stamp = segment.start_at.astimezone(tz) if tz is not None else segment.start_at.astimezone()
            valid_facts.append(segment)
            key = (segment.device_id, segment.session_id or segment.segment_id)
            if key not in firsts or stamp < firsts[key]:
                firsts[key] = stamp
        return FocusStartIndex(tuple(firsts.values()), hash(tuple(valid_facts)))
    return None


def get_actual_work_start(events, day: date, *, tz=None, sessions=None, now=None) -> datetime | None:
    """原始专注事实按设备/会话恢复最初 start，过滤 06:00 前和 checkpoint；兼容旧调用。"""
    if sessions is not None:
        index = focus_start_index(sessions, tz=tz, now=now)
        candidates = [stamp for stamp in index.starts if stamp.date() == day and stamp.hour >= 6]
        # Only new explicitly marked real starts may bridge the first seconds before a sealed fact.
        for row in events:
            meta = row.get("metadata") or {}
            if meta.get("actual_start_source") != "explicit_focus_start":
                continue
            stamp = get_actual_work_start([row], day, tz=tz)
            if stamp is not None:
                candidates.append(stamp)
        return min(candidates) if candidates else None
    candidates = []
    for row in events:
        if row.get("event_type") != "start_work":
            continue
        metadata = row.get("metadata") or {}
        if metadata.get("source") in RESTORE_SOURCES or metadata.get("restored"):
            continue
        try:
            stamp = datetime.fromisoformat(str(row.get("occurred_at", "")).replace("Z", "+00:00"))
            stamp = stamp.astimezone(tz) if tz is not None else stamp.astimezone()
        except (ValueError, TypeError, OverflowError):
            continue
        if stamp.date() == day and stamp.hour >= 6:
            candidates.append(stamp)
    return min(candidates) if candidates else None


def discipline_events(events, day: date | None = None):
    """旧流水账兼容：仅呈现有纪律意义的结果，首次开工每天一条。"""
    rows = [row for row in events if row.get("event_type") in DISCIPLINE_EVENT_TYPES
            and (day is None or row.get("event_date") == day.isoformat())]
    firsts = {}
    final_finishes = {}
    for row in rows:
        if row.get("event_type") == "finish_work":
            key = row.get("event_date")
            if key not in final_finishes or str(row.get("occurred_at")) > str(final_finishes[key].get("occurred_at")):
                final_finishes[key] = row
    for row in sorted(rows, key=lambda r: str(r.get("occurred_at", ""))):
        if row.get("event_type") == "finish_work" and row is not final_finishes.get(row.get("event_date")):
            continue
        if row.get("event_type") == "start_work":
            key = row.get("event_date")
            stamp = get_actual_work_start([row], date.fromisoformat(key)) if key else None
            if stamp is None or key in firsts:
                continue
            firsts[key] = row
        yield row


def _event_fingerprint(row) -> str:
    keys = ("id", "event_type", "event_date", "occurred_at", "metadata", "requires_explanation", "explanation")
    payload = {key: row.get(key) for key in keys}
    payload["explanation"] = payload.get("explanation") or ""
    try:
        from datetime import timezone
        payload["occurred_at"] = datetime.fromisoformat(str(payload["occurred_at"]).replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        pass
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _clock(value: Any, default: str) -> str:
    raw = str(value or default)
    try:
        hour, minute = (int(part) for part in raw.split(":", 1))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    except (TypeError, ValueError):
        pass
    return default


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError, OverflowError):
        return default


@dataclass
class DisciplineSettings:
    """一个账号的工作计划和监督偏好；默认关闭监督功能。"""

    plan_version: int = 2
    mode: str = "off"
    weekly_target_minutes: int = 1800
    workdays: list[str] = field(default_factory=lambda: list(WEEKDAYS[:5]))
    planned_finish_enabled: bool = False
    daily_target_minutes: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_DAILY_TARGETS))
    start_time: str = "09:00"
    finish_time: str = "18:00"
    daily_start_times: dict[str, str] = field(default_factory=dict)
    daily_finish_times: dict[str, str] = field(default_factory=dict)
    recommended_break_minutes: int = 20
    reminder_rules: dict[str, bool] = field(default_factory=dict)
    late_grace_minutes: int = 30
    break_limit_minutes: int = 20
    early_finish_grace_minutes: int = 15
    catchup_strategy: str = "even"
    custom_catchup_minutes: dict[str, int] = field(default_factory=dict)
    carry_across_weeks: bool = False
    supervisor_id: str = ""
    progress_reminders: bool = True
    snooze_until: str = ""

    @classmethod
    def from_dict(cls, raw: object) -> "DisciplineSettings":
        data = raw if isinstance(raw, dict) else {}
        mode = str(data.get("mode") or "off").casefold()
        mode = mode if mode in MODES else "off"
        targets = dict(DEFAULT_DAILY_TARGETS)
        supplied = data.get("daily_target_minutes")
        if isinstance(supplied, dict):
            for day in WEEKDAYS:
                try:
                    targets[day] = max(0, min(1440, int(supplied.get(day, targets[day]))))
                except (TypeError, ValueError):
                    pass
        custom: dict[str, int] = {}
        supplied_custom = data.get("custom_catchup_minutes")
        if isinstance(supplied_custom, dict):
            for day in WEEKDAYS:
                try:
                    custom[day] = max(0, min(1440, int(supplied_custom.get(day, 0))))
                except (TypeError, ValueError):
                    pass
        strategy = str(data.get("catchup_strategy") or "even").casefold()
        if strategy == "custom":
            strategy = "none"
        if strategy not in {"even", "frontload", "none"}:
            strategy = "even"
        return cls(
            mode=mode,
            weekly_target_minutes=_bounded_int(data.get("weekly_target_minutes", 1800), 1800, 0, 10080),
            workdays=[day for day in WEEKDAYS if day in data["workdays"]]
                     if isinstance(data.get("workdays"), list) else [day for day in WEEKDAYS if targets[day] > 0],
            planned_finish_enabled=bool(data.get("planned_finish_enabled", False)),
            daily_target_minutes=targets,
            start_time=_clock(data.get("start_time"), "09:00"),
            finish_time=_clock(data.get("finish_time"), "18:00"),
            daily_start_times={day: _clock(value, _clock(data.get("start_time"), "09:00"))
                               for day, value in (data.get("daily_start_times") or {}).items() if day in WEEKDAYS}
                               if isinstance(data.get("daily_start_times"), dict) else {},
            daily_finish_times={day: _clock(value, _clock(data.get("finish_time"), "18:00"))
                                for day, value in (data.get("daily_finish_times") or {}).items() if day in WEEKDAYS}
                                if isinstance(data.get("daily_finish_times"), dict) else {},
            recommended_break_minutes=_bounded_int(data.get("recommended_break_minutes", 20), 20, 1, 480),
            reminder_rules={key: bool(value) for key, value in (data.get("reminder_rules") or {}).items()
                            if key in {"start", "break", "early", "daily", "weekly"}}
                            if isinstance(data.get("reminder_rules"), dict) else {},
            late_grace_minutes=_bounded_int(data.get("late_grace_minutes", 30), 30, 0, 240),
            break_limit_minutes=_bounded_int(data.get("break_limit_minutes", 20), 20, 1, 480),
            early_finish_grace_minutes=_bounded_int(data.get("early_finish_grace_minutes", 15), 15, 0, 240),
            catchup_strategy=strategy,
            custom_catchup_minutes=custom,
            carry_across_weeks=bool(data.get("carry_across_weeks", False)),
            supervisor_id=str(data.get("supervisor_id") or "").strip()[:80],
            progress_reminders=bool(data.get("progress_reminders", True)),
            snooze_until=str(data.get("snooze_until") or "")[:40],
        )

    def for_weekday(self, day: date) -> int:
        # Legacy daily targets only infer workdays on import; they never form a second total.
        return (self.weekly_target_minutes + len(self.workdays) - 1) // len(self.workdays) if self.is_workday(day) and self.workdays else 0

    def is_workday(self, day: date) -> bool:
        return WEEKDAYS[day.weekday()] in self.workdays

    def start_at(self, day: date) -> datetime:
        hour, minute = (int(part) for part in self.start_time.split(":"))
        return datetime.combine(day, time(hour, minute)).astimezone()

    def finish_at(self, day: date) -> datetime:
        hour, minute = (int(part) for part in self.finish_time.split(":"))
        result = datetime.combine(day, time(hour, minute)).astimezone()
        start = self.start_at(day)
        return result + timedelta(days=1) if result <= start else result


class DisciplineStore:
    """账号隔离的纪律账本；事件只追加，提醒游标幂等保存。"""

    def __init__(self, account_id: str, *, path=None, persist: bool = True) -> None:
        self.account_id = str(account_id or "").strip()
        self.path = path or account_local_data_path("discipline_state.json", self.account_id)
        self.persist = bool(persist)
        raw = read_json(self.path, {}) if self.persist else {}
        raw = raw if isinstance(raw, dict) else {}
        self.settings = DisciplineSettings.from_dict(raw.get("settings"))
        self.settings_updated_at = str(raw.get("settings_updated_at") or "")
        self.events = [dict(row) for row in raw.get("events", []) if isinstance(row, dict)]
        self.fired_rules = {str(item) for item in raw.get("fired_rules", [])}
        self.pending_explanations = [
            dict(row) for row in raw.get("pending_explanations", []) if isinstance(row, dict)
        ]
        self.seen_nudges = set(str(i) for i in raw.get("seen_nudges", []))
        self.rest_days = set(str(i) for i in raw.get("rest_days", []))
        self.synced_events = dict(raw.get("synced_events") or {})
        self.synced_settings_at = str(raw.get("synced_settings_at") or "")
        self.sync_cursor = max(0, int(raw.get("sync_cursor") or 0))

    def _save(self) -> None:
        if self.persist:
            write_json_atomic(self.path, {
                "schema_version": 2,
                "synced_events": self.synced_events,
                "synced_settings_at": self.synced_settings_at,
                "sync_cursor": self.sync_cursor,
                "settings": asdict(self.settings),
                "settings_updated_at": self.settings_updated_at,
                "events": self.events,
                "fired_rules": sorted(self.fired_rules)[-5_000:],
                "pending_explanations": self.pending_explanations[-500:],
                "seen_nudges": sorted(self.seen_nudges)[-500:],
                "rest_days": sorted(self.rest_days),
            })

    def update_settings(self, settings: DisciplineSettings | dict[str, Any]) -> None:
        self.settings = DisciplineSettings.from_dict(
            asdict(settings) if isinstance(settings, DisciplineSettings) else settings
        )
        self.settings_updated_at = as_beijing().isoformat()
        self._save()

    def sync_payload(self, *, include_config: bool = False) -> dict[str, Any]:
        """最多 100 条未确认最终事件；配置仅修改或显式读取时携带。"""
        settings_dirty = bool(self.settings_updated_at and self.settings_updated_at != self.synced_settings_at)
        pending = [row for row in discipline_events(self.events)
                   if self.synced_events.get(str(row.get("id"))) != _event_fingerprint(row)]
        return {
            "p_settings": asdict(self.settings) if settings_dirty else None,
            "p_client_updated_at": self.settings_updated_at if settings_dirty else None,
            "p_events": deepcopy(pending[:100]),
            "p_after_revision": self.sync_cursor,
            "p_include_config": include_config or settings_dirty,
        }

    def acknowledge_sync(self, payload, result) -> None:
        """仅确认服务端明确接受的批次；请求期间新编辑仍留在 outbox。"""
        if not isinstance(result, dict):
            return
        acknowledged = set(str(key) for key in result.get("acknowledged_ids", []))
        for row in payload.get("p_events", []):
            current = next((event for event in self.events if event.get("id") == row.get("id")), None)
            if str(row.get("id")) in acknowledged and current and _event_fingerprint(current) == _event_fingerprint(row):
                self.synced_events[str(row["id"])] = _event_fingerprint(row)
        stamp = payload.get("p_client_updated_at")
        if stamp and result.get("client_updated_at") and self._stamp(result["client_updated_at"]) == self._stamp(stamp):
            self.synced_settings_at = stamp
        # The cursor advances only after merge_remote persisted all returned rows.
        self.sync_cursor = max(self.sync_cursor, int(result.get("next_revision") or 0))
        self._save()

    def merge_remote(self, payload: object) -> None:
        """Merge server settings and append-only events without losing local edits."""

        if not isinstance(payload, dict):
            return
        remote_stamp = str(payload.get("client_updated_at") or "")
        self.rest_days.update(str(day) for day in payload.get("rest_days", []) if isinstance(day, str))
        local_stamp = self.settings_updated_at
        if remote_stamp and (not local_stamp or self._stamp(remote_stamp) > self._stamp(local_stamp)):
            self.settings = DisciplineSettings.from_dict(payload.get("settings"))
            self.settings_updated_at = remote_stamp

        remote_events = payload.get("events")
        if isinstance(remote_events, list):
            by_id = {str(row.get("id") or ""): row for row in self.events if row.get("id")}
            dedupe_ids = {
                str(row.get("metadata", {}).get("rule_key")): str(row.get("id"))
                for row in self.events
                if isinstance(row.get("metadata"), dict) and row.get("metadata", {}).get("rule_key")
            }
            for raw in remote_events:
                if not isinstance(raw, dict):
                    continue
                row = dict(raw)
                row["explanation"] = row.get("explanation") or ""
                if not isinstance(row.get("metadata"), dict):
                    row["metadata"] = {}
                rule_key = str(row.get("metadata", {}).get("rule_key") or "")
                event_id = str(row.get("id") or "")
                if not event_id:
                    continue
                existing_id = dedupe_ids.get(rule_key) if rule_key else None
                existing = by_id.get(existing_id or event_id)
                if existing is not None:
                    earlier_start = row.get("event_type") == "start_work" and self._stamp(str(row.get("occurred_at"))) < self._stamp(str(existing.get("occurred_at")))
                    later_summary = row.get("event_type") in {"daily_report", "finish_work", "early_finish", "focus_shortfall", "weekly_shortfall"} and self._stamp(str(row.get("occurred_at"))) > self._stamp(str(existing.get("occurred_at")))
                    corrected_lateness = row.get("event_type") == "late_start" and row.get("metadata", {}).get("actual_start_source") == "first_real_start_after_0600"
                    if corrected_lateness:
                        existing["metadata"] = deepcopy(row["metadata"])
                        existing["requires_explanation"] = bool(row.get("requires_explanation"))
                    if earlier_start or later_summary:
                        existing.update({key: row[key] for key in ("occurred_at", "metadata", "event_date")})
                    if later_summary:
                        existing["requires_explanation"] = bool(row.get("requires_explanation"))
                    if row.get("explanation") and not existing.get("explanation"):
                        existing["explanation"] = row["explanation"]
                    continue
                by_id[event_id] = row
                self.events.append(row)
                if rule_key:
                    dedupe_ids[rule_key] = event_id
                if row.get("requires_explanation") and not row.get("explanation"):
                    self.pending_explanations.append({
                        "event_id": event_id,
                        "created_at": row.get("occurred_at", ""),
                    })
                if rule_key:
                    self.fired_rules.add(rule_key)
        if isinstance(remote_events, list):
            for remote in remote_events:
                if not isinstance(remote, dict):
                    continue
                existing = next((row for row in self.events if row.get("id") == remote.get("id") or
                    (remote.get("metadata", {}).get("rule_key") and row.get("metadata", {}).get("rule_key") == remote["metadata"]["rule_key"])), None)
                if existing:
                    normalized = {**remote, "id": existing["id"]}
                    if _event_fingerprint(existing) == _event_fingerprint(normalized):
                        self.synced_events[str(existing["id"])] = _event_fingerprint(existing)
        pending_rows = {str(row["id"]): row for row in self.events if row.get("requires_explanation") and not row.get("explanation")}
        self.pending_explanations = [{"event_id": key, "created_at": row["occurred_at"]} for key, row in pending_rows.items()]
        if remote_stamp and remote_stamp == self.settings_updated_at:
            self.synced_settings_at = remote_stamp
        self._save()

    @staticmethod
    def _stamp(value: str) -> datetime:
        try:
            return as_beijing(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except (TypeError, ValueError):
            return datetime.min.replace(tzinfo=BEIJING_TIMEZONE)

    def append_event(
        self, event_type: str, at: datetime | None = None, *, metadata: dict[str, Any] | None = None,
        requires_explanation: bool = False,
    ) -> dict[str, Any]:
        moment = local_work_time(at)
        row = {
            "id": str(uuid4()), "event_type": str(event_type),
            "event_date": moment.date().isoformat(), "occurred_at": moment.isoformat(),
            "metadata": deepcopy(metadata or {}),
            "requires_explanation": bool(requires_explanation), "explanation": "",
        }
        self.events.append(row)
        if requires_explanation:
            self.pending_explanations.append({"event_id": row["id"], "created_at": moment.isoformat()})
        self._save()
        return dict(row)

    def record_work_event(
        self, event_type: str, at: datetime | None = None, *, metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if event_type not in {"start_work", "start_break", "end_break", "finish_work"}:
            return None
        return self.append_event(event_type, at, metadata=metadata)

    def append_rule_once(
        self, rule_key: str, event_type: str, at: datetime, *,
        metadata: dict[str, Any] | None = None, requires_explanation: bool = False,
    ) -> dict[str, Any] | None:
        if rule_key in self.fired_rules:
            if event_type in {"daily_report", "finish_work", "early_finish", "focus_shortfall", "weekly_shortfall"}:
                existing = next((row for row in self.events if row.get("metadata", {}).get("rule_key") == rule_key), None)
                if existing is not None:
                    existing["occurred_at"] = local_work_time(at).isoformat()
                    existing["metadata"] = {"rule_key": rule_key, **deepcopy(metadata or {})}
                    existing["requires_explanation"] = bool(requires_explanation)
                    self.pending_explanations = [row for row in self.pending_explanations if row.get("event_id") != existing["id"]]
                    if requires_explanation and not existing.get("explanation"):
                        self.pending_explanations.append({"event_id": existing["id"], "created_at": existing["occurred_at"]})
                    self._save()
                    return None
            return None
        self.fired_rules.add(rule_key)
        return self.append_event(
            event_type, at, metadata={"rule_key": rule_key, **(metadata or {})},
            requires_explanation=requires_explanation,
        )

    def explain(self, event_id: str, reason: str, note: str = "") -> bool:
        target = next((row for row in self.events if row.get("id") == event_id), None)
        if target is None:
            return False
        target["explanation"] = {"reason": str(reason or "其他")[:40], "note": str(note or "")[:300]}
        target["explained_at"] = as_beijing().isoformat()
        self.pending_explanations = [row for row in self.pending_explanations if row.get("event_id") != event_id]
        self._save()
        return True

    def events_for_day(self, day: date) -> list[dict[str, Any]]:
        key = day.isoformat()
        return [dict(row) for row in self.events if row.get("event_date") == key]

    def events_for_week(self, day: date) -> list[dict[str, Any]]:
        monday = day - timedelta(days=day.weekday())
        sunday = monday + timedelta(days=6)
        return [dict(row) for row in self.events if monday.isoformat() <= str(row.get("event_date")) <= sunday.isoformat()]

    def due_explanations(self) -> list[dict[str, Any]]:
        pending_ids = {str(item.get("event_id")) for item in self.pending_explanations}
        return [dict(row) for row in self.events if str(row.get("id")) in pending_ids and not self.is_exempt(str(row.get("event_date")))]

    def is_exempt(self, day: date | str) -> bool:
        key = day.isoformat() if isinstance(day, date) else day
        return key in self.rest_days or any(row.get("event_type") == "rest_day" and row.get("event_date") == key for row in self.events)

    def exempt_today(self, at: datetime | None = None) -> bool:
        moment = as_beijing(at)
        if not self.settings.is_workday(moment.date()):
            return False
        self.rest_days.add(moment.date().isoformat())
        self.append_rule_once(f"{moment.date()}:rest_day", "rest_day", moment)
        return True


@dataclass(frozen=True)
class PlanProgress:
    date: str
    daily_target_seconds: int
    catchup_target_seconds: int
    today_seconds: int
    daily_gap_seconds: int
    weekly_target_seconds: int
    week_seconds: int
    weekly_remaining_seconds: int
    remaining_workdays: int
    catchup_strategy: str
    caught_up_today_seconds: int


@dataclass(frozen=True)
class DisciplineNotice:
    event_type: str
    title: str
    detail: str
    severity: str
    event_id: str


class DisciplineEngine:
    """把计划和明确的 FocusSession 事件转换为有界提醒与纪律记录。"""

    def __init__(self, store: DisciplineStore, *, focus_sessions_provider=None, now_provider=None) -> None:
        self.store = store
        self.focus_sessions_provider = focus_sessions_provider
        self.now_provider = now_provider
        self.supervision = {}
        self._supervision_revision = -1
        self._remote_mode = "off"
        self._remote_until = 0.0

    def focus_sessions(self):
        return self.focus_sessions_provider() if callable(self.focus_sessions_provider) else None

    def actual_work_start(self, day, *, tz=None, sessions=_SESSION_UNSET):
        return get_actual_work_start(self.store.events, day, tz=tz, sessions=self.focus_sessions() if sessions is _SESSION_UNSET else sessions,
                                     now=self.now_provider() if callable(self.now_provider) else None)

    def work_start_index(self):
        return focus_start_index(self.focus_sessions(), now=self.now_provider() if callable(self.now_provider) else None)

    def apply_supervision(self, payload: object) -> bool:
        """远端监督只影响有效模式，不覆盖本人设置；旧响应不能复活撤销授权。"""
        if not isinstance(payload, dict) or not isinstance(payload.get("policy"), dict):
            return False
        revision = int(payload["policy"].get("revision", 0))
        if revision < self._supervision_revision:
            return False
        if "scope" in payload["policy"]:
            self.supervision = deepcopy(payload)
        else:
            self.supervision["effective_mode"] = payload.get("effective_mode", "off")
            if not payload["policy"].get("enabled"):
                self.supervision.setdefault("policy", {})["enabled"] = False
        self._supervision_revision = revision
        mode = str(payload.get("effective_mode", "off"))
        self._remote_mode = mode if payload["policy"].get("enabled") and mode in MODES else "off"
        self._remote_until = monotonic_time.monotonic() + 120
        return True

    @property
    def mode(self) -> str:
        remote = self._remote_mode if monotonic_time.monotonic() < self._remote_until else "off"
        rank = {"off": 0, "normal": 1, "officer": 2}
        return max((self.store.settings.mode, remote), key=rank.__getitem__)

    @property
    def enabled(self) -> bool:
        return self.mode in {"normal", "officer"}

    def remaining_workdays(self, day: date) -> list[date]:
        monday = day - timedelta(days=day.weekday())
        week = [monday + timedelta(days=index) for index in range(7)]
        return [candidate for candidate in week if candidate >= day and self.store.settings.is_workday(candidate) and not self.store.is_exempt(candidate)]

    def _carried_week_debt_seconds(self, day: date) -> int:
        """Return the last settled weekly shortfall when carry is explicitly enabled."""

        if not self.store.settings.carry_across_weeks:
            return 0
        current_monday = day - timedelta(days=day.weekday())
        candidates = [
            row for row in self.store.events
            if row.get("event_type") == "weekly_shortfall"
            and str(row.get("event_date") or "") < current_monday.isoformat()
        ]
        if not candidates:
            return 0
        latest = max(candidates, key=lambda row: str(row.get("occurred_at") or ""))
        metadata = latest.get("metadata") if isinstance(latest.get("metadata"), dict) else {}
        return max(0, int(metadata.get("remaining_seconds", 0) or 0))

    def progress(self, today_seconds: int, week_seconds: int, at: datetime | None = None) -> PlanProgress:
        moment = local_work_time(at)
        settings = self.store.settings
        week_target = settings.weekly_target_minutes * 60 + self._carried_week_debt_seconds(moment.date())
        remaining = max(0, week_target - max(0, int(week_seconds)))
        days = self.remaining_workdays(moment.date())
        today = max(0, int(today_seconds))
        week = max(0, int(week_seconds))
        # Include today's actual work when distributing the budget at the start of this day.
        budget = max(0, week_target - max(0, week - today))
        day_target = 0
        if moment.date() in days and settings.catchup_strategy != "none":
            day_target = (budget + len(days) - 1) // len(days)
            if settings.catchup_strategy == "frontload":
                base = (week_target + max(1, len(settings.workdays)) - 1) // max(1, len(settings.workdays))
                day_target = max(day_target, budget - base * (len(days) - 1))
        catchup = day_target
        return PlanProgress(
            date=moment.date().isoformat(), daily_target_seconds=day_target,
            catchup_target_seconds=catchup, today_seconds=today,
            daily_gap_seconds=max(0, day_target - today), weekly_target_seconds=week_target,
            week_seconds=week, weekly_remaining_seconds=remaining,
            remaining_workdays=len(days), catchup_strategy=settings.catchup_strategy,
            caught_up_today_seconds=max(0, today - day_target),
        )

    def _notice(
        self, key: str, event_type: str, at: datetime, title: str, detail: str,
        *, severity: str = "info", requires_explanation: bool = False,
    ) -> DisciplineNotice | None:
        rule = {"late_start_warning": "start", "long_break_warning": "break",
                "focus_shortfall": "daily", "behind_schedule": "daily"}.get(event_type)
        if self.mode == "normal" and rule and not self.store.settings.reminder_rules.get(rule, True):
            return None
        if event_type in {"late_start_warning", "long_break_warning", "behind_schedule"}:
            if key in self.store.fired_rules:
                return None
            self.store.fired_rules.add(key)
            self.store._save()
            return DisciplineNotice(event_type, title, detail, severity, key)
        row = self.store.append_rule_once(
            key, event_type, at, metadata={"title": title, "detail": detail, "severity": severity},
            requires_explanation=requires_explanation,
        )
        if row is None:
            return None
        return DisciplineNotice(event_type, title, detail, severity, str(row["id"]))

    def record_work_event(
        self, event_type: str, today_seconds: int, week_seconds: int,
        at: datetime | None = None, *, metadata: dict[str, Any] | None = None,
    ) -> list[DisciplineNotice]:
        moment = local_work_time(at)
        settings = self.store.settings
        details = dict(metadata or {})
        if event_type == "start_work":
            if moment.hour < 6 or details.get("source") in RESTORE_SOURCES or details.get("restored"):
                return []
            if get_actual_work_start(self.store.events, moment.date(), tz=moment.tzinfo) is not None:
                return []
            details["actual_start"] = moment.strftime("%H:%M")
            details["actual_start_source"] = "explicit_focus_start"
            details["planned_start"] = settings.start_time
            details["utc_offset_minutes"] = int(moment.utcoffset().total_seconds() // 60)
            self.store.append_rule_once(f"{moment.date()}:actual_start", "start_work", moment, metadata=details)
        elif event_type == "finish_work":
            self.store.append_rule_once(f"{moment.date()}:actual_finish", "finish_work", moment, metadata=details)
        elif event_type in {"start_break", "end_break"}:
            # 过程细节留在本地，sync_payload 不携带普通 pause / resume。
            self.store.record_work_event(event_type, moment, metadata=details)
        if not self.enabled or moment.hour < 6 or self.store.is_exempt(moment.date()):
            return []
        notices: list[DisciplineNotice] = []
        if event_type == "start_work":
            planned = datetime.combine(moment.date(), time.fromisoformat(settings.start_time), moment.tzinfo)
            late_minutes = max(0, int((moment - planned).total_seconds() // 60))
            if settings.for_weekday(moment.date()) > 0 and late_minutes > 0:
                row = self.store.append_rule_once(
                    f"{moment.date()}:late_start", "late_start", moment,
                    metadata={"minutes_late": late_minutes, "planned_start": planned.isoformat()},
                    requires_explanation=(self.mode == "officer" and late_minutes >= 30),
                )
                if row is None:
                    return notices
                week_lates = sum(1 for row in self.store.events_for_week(moment.date()) if row.get("event_type") == "late_start")
                notices.append(DisciplineNotice(
                    "late_start", "迟到记录", f"今日迟到 {late_minutes} 分钟 · 本周第 {week_lates} 次",
                    "warning", str(row["id"]),
                ))
        elif event_type == "end_break":
            breaks = [row for row in self.store.events_for_day(moment.date()) if row.get("event_type") == "start_break"]
            start = next((row for row in reversed(breaks) if row.get("metadata", {}).get("session_key") == details.get("session_key")), None)
            if start:
                break_start = as_beijing(datetime.fromisoformat(str(start["occurred_at"])))
                elapsed = max(0, int((moment - break_start).total_seconds()))
                over = max(0, elapsed - settings.break_limit_minutes * 60)
                if over > 0:
                    needs_reason = self.mode == "officer" and over >= 10 * 60
                    row = self.store.append_rule_once(
                        f"{moment.date()}:long_break:{details.get('session_key')}", "long_break", moment,
                        metadata={"duration_seconds": elapsed, "overtime_seconds": over,
                                  "limit_minutes": settings.break_limit_minutes},
                        requires_explanation=needs_reason,
                    )
                    if row is None:
                        return notices
                    notices.append(DisciplineNotice(
                        "long_break", "休息超时记录",
                        f"本次休息 {elapsed // 60} 分钟，超过计划 {over // 60} 分钟",
                        "warning" if over < 25 * 60 else "critical", str(row["id"]),
                    ))
        elif event_type == "finish_work":
            progress = self.progress(today_seconds, week_seconds, moment)
            finish_at = settings.finish_at(moment.date())
            early = max(0, int((finish_at - moment).total_seconds() // 60))
            early_problem = settings.planned_finish_enabled and settings.is_workday(moment.date()) and early > settings.early_finish_grace_minutes
            if early_problem or f"{moment.date()}:early_finish" in self.store.fired_rules:
                row = self.store.append_rule_once(
                    f"{moment.date()}:early_finish", "early_finish", moment,
                    metadata={"minutes_early": early if early_problem else 0, "planned_finish": finish_at.isoformat()},
                    requires_explanation=(self.mode == "officer" and early_problem and early >= 60),
                )
                if row is not None:
                    notices.append(DisciplineNotice(
                        "early_finish", "提前下班已记入今日计划",
                        f"比计划提前 {early} 分钟 · 今日 {today_seconds // 60} / {progress.daily_target_seconds // 60} 分钟",
                        "warning", str(row["id"]),
                    ))
            if progress.daily_gap_seconds > 0 or f"{moment.date()}:focus_shortfall" in self.store.fired_rules:
                detail = f"今日缺口 {progress.daily_gap_seconds // 60} 分钟 · 本周还需 {progress.weekly_remaining_seconds // 60} 分钟"
                row = self.store.append_rule_once(
                    f"{moment.date()}:focus_shortfall", "focus_shortfall", moment,
                    metadata={"title": "今日计划小结", "detail": detail, "severity": "info", "gap_seconds": progress.daily_gap_seconds},
                    requires_explanation=(self.mode == "officer" and progress.daily_gap_seconds >= 60 * 60),
                )
                if row and (self.mode == "officer" or settings.reminder_rules.get("daily", True)):
                    notices.append(DisciplineNotice("focus_shortfall", "今日计划小结", detail, "info", str(row["id"])))
            week_end = moment.date() + timedelta(days=6 - moment.weekday())
            remaining_days = [
                moment.date() + timedelta(days=offset)
                for offset in range(1, (week_end - moment.date()).days + 1)
                if settings.is_workday(moment.date() + timedelta(days=offset)) and not self.store.is_exempt(moment.date() + timedelta(days=offset))
            ]
            if not remaining_days:
                weekly_debt = max(0, progress.weekly_target_seconds - max(0, int(week_seconds)))
                row = self.store.append_rule_once(
                    f"{moment.date()}:weekly_settlement", "weekly_shortfall", moment,
                    metadata={"remaining_seconds": weekly_debt,
                              "weekly_target_seconds": progress.weekly_target_seconds,
                              "week_seconds": max(0, int(week_seconds))},
                    requires_explanation=(self.mode == "officer" and weekly_debt >= 60 * 60),
                )
                if row is not None and weekly_debt > 0:
                    notices.append(DisciplineNotice(
                        "weekly_shortfall", "本周结算",
                        f"本周未完成 {weekly_debt // 60} 分钟，已写入周账",
                        "warning", str(row["id"]),
                    ))
            day_events = self.store.events_for_day(moment.date())
            actual_start = self.actual_work_start(moment.date(), tz=moment.tzinfo)
            late_events = [row for row in day_events if row.get("event_type") == "late_start"]
            long_breaks = [row for row in day_events if row.get("event_type") == "long_break"]
            daily_report = self.store.append_rule_once(
                f"{moment.date()}:daily_report", "daily_report", moment,
                metadata={
                    "planned_start": settings.start_at(moment.date()).strftime("%H:%M"),
                    "actual_start": actual_start.strftime("%H:%M") if actual_start else "",
                    "lateness_minutes": max(0, int((actual_start - datetime.combine(moment.date(), time.fromisoformat(settings.start_time), moment.tzinfo)).total_seconds() // 60)) if actual_start else 0,
                    "today_seconds": max(0, int(today_seconds)),
                    "daily_target_seconds": progress.daily_target_seconds,
                    "daily_gap_seconds": progress.daily_gap_seconds,
                    "long_break_count": len(long_breaks),
                    "break_overtime_seconds": sum(
                        int(row.get("metadata", {}).get("overtime_seconds", 0)) for row in long_breaks
                    ),
                    "planned_finish": settings.finish_at(moment.date()).strftime("%H:%M") if settings.planned_finish_enabled else "",
                    "actual_finish": moment.strftime("%H:%M"),
                    "weekly_target_seconds": progress.weekly_target_seconds,
                    "week_seconds": max(0, int(week_seconds)),
                    "weekly_remaining_seconds": progress.weekly_remaining_seconds,
                    "unexplained_count": sum(
                        1 for row in day_events
                        if row.get("requires_explanation") and not row.get("explanation")
                    ),
                    "mode": self.mode,
                },
            )
            if daily_report is not None:
                notices.append(DisciplineNotice(
                    "daily_report", "今日纪律摘要已记录",
                    f"专注 {today_seconds // 60} 分钟 / 目标 {progress.daily_target_seconds // 60} 分钟 · "
                    f"本周剩余 {progress.weekly_remaining_seconds // 60} 分钟",
                    "info", str(daily_report["id"]),
                ))
        if self.mode == "normal":
            kinds = {"late_start": "start", "long_break": "break", "early_finish": "early",
                     "focus_shortfall": "daily", "daily_report": "daily", "weekly_shortfall": "weekly"}
            notices = [notice for notice in notices if settings.reminder_rules.get(kinds.get(notice.event_type, ""), True)]
        return notices

    def evaluate(self, today_seconds: int, week_seconds: int, at: datetime | None = None, *, working: bool = False) -> list[DisciplineNotice]:
        if not self.enabled:
            return []
        moment = local_work_time(at)
        settings = self.store.settings
        if moment.hour < 6 or not settings.is_workday(moment.date()) or self.store.is_exempt(moment.date()):
            return []
        if settings.snooze_until:
            try:
                if moment < as_beijing(datetime.fromisoformat(settings.snooze_until)):
                    return []
            except (TypeError, ValueError):
                settings.snooze_until = ""
        notices: list[DisciplineNotice] = []
        start = settings.start_at(moment.date())
        late_minutes = int((moment - start).total_seconds() // 60)
        started_today = working or self.actual_work_start(moment.date(), tz=moment.tzinfo) is not None
        thresholds = (settings.late_grace_minutes + 1,) if self.mode == "normal" else tuple(settings.late_grace_minutes + delta for delta in (1, 15, 30))
        if not started_today:
            for threshold in thresholds:
                if late_minutes >= threshold:
                    notice = self._notice(
                        f"{moment.date()}:late:{threshold}", "late_start_warning", moment,
                        "今天还没有开工" if threshold == thresholds[0] else ("训导主任点名" if threshold == 30 else "尚未开工"),
                        f"计划开工 {start.strftime('%H:%M')} · 已过计划时间 {late_minutes} 分钟",
                        severity="critical" if threshold >= 60 else "warning",
                    )
                    if notice:
                        notices.append(notice)
        if settings.progress_reminders:
            progress = self.progress(today_seconds, week_seconds, moment)
            finish_at = settings.finish_at(moment.date()) if settings.planned_finish_enabled else start + timedelta(seconds=progress.daily_target_seconds + settings.recommended_break_minutes * 60)
            remaining_time = max(0, int((finish_at - moment).total_seconds()))
            remaining_work = max(0, progress.catchup_target_seconds - progress.today_seconds)
            if remaining_time >= 30 * 60 and remaining_work > 0:
                expected = progress.catchup_target_seconds * (
                    (moment - start).total_seconds() / max(1, (finish_at - start).total_seconds())
                )
                behind = expected - progress.today_seconds
                if behind >= max(30 * 60, progress.catchup_target_seconds * 0.2):
                    severity = "warning" if self.mode == "normal" else "critical"
                    notice = self._notice(
                        f"{moment.date()}:behind:{moment.hour // 3}", "behind_schedule", moment,
                        "今日进度落后" if self.mode == "normal" else "训导主任提醒：进度落后",
                        f"已完成 {today_seconds // 60} 分钟 · 距今日追赶目标还差 {remaining_work // 60} 分钟",
                        severity=severity,
                    )
                    if notice:
                        notices.append(notice)
        return notices

    def break_notices(self, at: datetime | None = None) -> list[DisciplineNotice]:
        if not self.enabled:
            return []
        moment = local_work_time(at)
        if moment.hour < 6 or self.store.is_exempt(moment.date()) or not self.store.settings.is_workday(moment.date()):
            return []
        snooze_until = self.store.settings.snooze_until
        if snooze_until:
            try:
                if moment < as_beijing(datetime.fromisoformat(snooze_until)):
                    return []
            except (TypeError, ValueError):
                self.store.settings.snooze_until = ""
        events = self.store.events_for_day(moment.date())
        last_start = next((row for row in reversed(events) if row.get("event_type") == "start_break"), None)
        if not last_start:
            return []
        session_key = str(last_start.get("metadata", {}).get("session_key") or "")
        if any(row.get("event_type") == "end_break" and str(row.get("metadata", {}).get("session_key") or "") == session_key for row in events):
            return []
        start = as_beijing(datetime.fromisoformat(str(last_start["occurred_at"])))
        elapsed = max(0, int((moment - start).total_seconds() // 60))
        settings = self.store.settings
        if self.mode == "officer":
            thresholds = tuple(dict.fromkeys((
                max(1, settings.break_limit_minutes - 2), settings.break_limit_minutes,
                settings.break_limit_minutes + 10, settings.break_limit_minutes + 25,
            )))
        else:
            thresholds = (settings.break_limit_minutes + 10,)
        notices: list[DisciplineNotice] = []
        for threshold in thresholds:
            if elapsed >= threshold:
                notice = self._notice(
                    f"{moment.date()}:break:{session_key}:{threshold}", "long_break_warning", moment,
                    "训导主任：还有 2 分钟" if self.mode == "officer" and threshold == settings.break_limit_minutes - 2
                    else "训导主任：课间结束" if self.mode == "officer" and threshold == settings.break_limit_minutes
                    else "休息时间超出计划" if self.mode == "normal" else f"训导主任：休息已 {elapsed} 分钟",
                    f"计划休息 {settings.break_limit_minutes} 分钟 · 已超时 {max(0, elapsed - settings.break_limit_minutes)} 分钟",
                    severity="critical" if threshold >= 45 else "warning",
                )
                if notice:
                    notices.append(notice)
        return notices

    def daily_summary(self, day: date, today_seconds: int, week_seconds: int, *, sessions=_SESSION_UNSET) -> dict[str, Any]:
        events = self.store.events_for_day(day)
        progress = self.progress(today_seconds, week_seconds, datetime.combine(day, time(23,59)).astimezone())
        late = [row for row in events if row.get("event_type") == "late_start"]
        breaks = [row for row in events if row.get("event_type") == "long_break"]
        finishes = [row for row in events if row.get("event_type") == "early_finish"]
        if self.store.is_exempt(day):
            late, breaks, finishes = [], [], []
        actual_start = self.actual_work_start(day, sessions=sessions)
        historical = day < local_work_time().date() and callable(self.focus_sessions_provider)
        planned_clock = self.store.settings.start_time if not historical else None
        if historical:
            for row in reversed(events):
                meta = row.get("metadata") or {}
                candidate = str(meta.get("planned_start") or "")
                if row.get("event_type") not in {"daily_report", "late_start", "start_work"}: continue
                if len(candidate) > 5: candidate = candidate[11:16]
                try: time.fromisoformat(candidate)
                except ValueError: continue
                planned_clock = candidate; break
        planned = datetime.combine(day, time.fromisoformat(planned_clock), actual_start.tzinfo if actual_start else None) if planned_clock else None
        late_minutes = max(0, int((actual_start - planned).total_seconds() // 60)) if actual_start and planned and not self.store.is_exempt(day) else 0 if self.store.is_exempt(day) or not historical else None
        display_events = list(discipline_events(events, day))
        if callable(self.focus_sessions_provider):
            # Read model only: unknown legacy actual-start values cannot override interval facts.
            display_events = [dict(row) for row in display_events if row.get("event_type") != "start_work"]
            if actual_start:
                display_events.insert(0, {"id":"derived-start:"+day.isoformat(), "event_type":"start_work", "event_date":day.isoformat(),
                    "occurred_at":actual_start.isoformat(), "metadata":{"actual_start_source":"inferred_from_focus_sessions"}, "requires_explanation":False})
            for row in display_events:
                if row.get("event_type") == "late_start" and late_minutes is not None:
                    row["metadata"] = {**row.get("metadata", {}), "minutes_late":late_minutes}
                    row["requires_explanation"] = bool(row.get("requires_explanation")) and late_minutes >= 30
                    row["derived_resolved"] = late_minutes == 0
            display_events = [row for row in display_events if not row.get("derived_resolved")]

        finish_event = next((row for row in reversed(events) if row.get("event_type") == "finish_work"), None)
        rest_seconds = 0
        break_start = None
        for row in sorted(events, key=lambda item: str(item.get("occurred_at") or "")):
            try:
                stamp = as_beijing(datetime.fromisoformat(str(row.get("occurred_at"))))
            except (ValueError, TypeError):
                continue
            if row.get("event_type") == "start_break" and break_start is None:
                break_start = stamp
            elif row.get("event_type") in {"end_break", "finish_work"} and break_start is not None:
                rest_seconds += max(0, int((stamp - break_start).total_seconds()))
                break_start = None
        if break_start is not None:
            end = min(as_beijing(), datetime.combine(day + timedelta(days=1), time(), BEIJING_TIMEZONE))
            rest_seconds += max(0, int((end - break_start).total_seconds()))
        return {
            "date": day.isoformat(), "mode": self.mode,
            "planned_start": planned_clock or "缺少历史计划",
            "historical_plan_known": bool(planned_clock),
            "actual_start": actual_start.strftime("%H:%M") if actual_start else "",
            "lateness_minutes": late_minutes,
            "today_seconds": max(0, int(today_seconds)),
            "daily_target_seconds": progress.daily_target_seconds,
            "daily_gap_seconds": progress.daily_gap_seconds,
            "long_break_count": len(breaks),
            "rest_seconds": rest_seconds,
            "break_overtime_seconds": sum(int(row.get("metadata", {}).get("overtime_seconds", 0)) for row in breaks),
            "planned_finish": self.store.settings.finish_at(day).strftime("%H:%M") if self.store.settings.planned_finish_enabled else "未启用",
            "actual_finish": self._time_label(finish_event),
            "early_finish_count": len(finishes),
            "week_seconds": max(0, int(week_seconds)),
            "weekly_target_seconds": progress.weekly_target_seconds,
            "weekly_remaining_seconds": progress.weekly_remaining_seconds,
            "remaining_workdays": progress.remaining_workdays,
            "caught_up_today_seconds": progress.caught_up_today_seconds,
            "unexplained_count": 0 if self.store.is_exempt(day) else sum(1 for row in display_events if row.get("requires_explanation") and not row.get("explanation")),
            "exempt": self.store.is_exempt(day),
            "events": display_events,
        }

    @staticmethod
    def _time_label(row: dict[str, Any] | None) -> str:
        if not row:
            return ""
        try:
            return datetime.fromisoformat(str(row["occurred_at"])).astimezone().strftime("%H:%M")
        except (KeyError, TypeError, ValueError):
            return ""
