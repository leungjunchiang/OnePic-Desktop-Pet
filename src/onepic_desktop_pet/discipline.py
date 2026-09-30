"""以周目标为总账的动态工作计划、免战日账本与独立的搭子监督授权。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import uuid4
import time as monotonic_time

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
        hour, minute = (int(part) for part in self.daily_start_times.get(WEEKDAYS[day.weekday()], self.start_time).split(":"))
        return datetime.combine(day, time(hour, minute), BEIJING_TIMEZONE)

    def finish_at(self, day: date) -> datetime:
        hour, minute = (int(part) for part in self.daily_finish_times.get(WEEKDAYS[day.weekday()], self.finish_time).split(":"))
        result = datetime.combine(day, time(hour, minute), BEIJING_TIMEZONE)
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

    def _save(self) -> None:
        if self.persist:
            write_json_atomic(self.path, {
                "schema_version": 1,
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

    def sync_payload(self) -> dict[str, Any]:
        """Build a bounded account sync payload separate from presence state."""

        return {
            "p_settings": asdict(self.settings) if self.settings_updated_at else None,
            "p_client_updated_at": self.settings_updated_at or None,
            "p_events": self.events[-2000:],
        }

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
                row.setdefault("metadata", {})
                rule_key = str(row.get("metadata", {}).get("rule_key") or "")
                event_id = str(row.get("id") or "")
                if not event_id:
                    continue
                existing_id = dedupe_ids.get(rule_key) if rule_key else None
                existing = by_id.get(existing_id or event_id)
                if existing is not None:
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
        moment = as_beijing(at)
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

    def __init__(self, store: DisciplineStore) -> None:
        self.store = store
        self.supervision = {}
        self._supervision_revision = -1
        self._remote_mode = "off"
        self._remote_until = 0.0

    def apply_supervision(self, payload: object) -> bool:
        """远端监督只影响有效模式，不覆盖本人设置；旧响应不能复活撤销授权。"""
        if not isinstance(payload, dict) or not isinstance(payload.get("policy"), dict):
            return False
        revision = int(payload["policy"].get("revision", 0))
        if revision < self._supervision_revision:
            return False
        self.supervision = deepcopy(payload)
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
        moment = as_beijing(at)
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
        if not self.enabled:
            return []
        moment = as_beijing(at)
        settings = self.store.settings
        if self.store.is_exempt(moment.date()):
            return []
        details = dict(metadata or {})
        self.store.record_work_event(event_type, moment, metadata=details)
        notices: list[DisciplineNotice] = []
        if event_type == "start_work":
            start_events = [row for row in self.store.events_for_day(moment.date()) if row.get("event_type") == "start_work"]
            planned = settings.start_at(moment.date())
            late_minutes = max(0, int((moment - planned).total_seconds() // 60))
            if settings.for_weekday(moment.date()) > 0 and len(start_events) == 1 and late_minutes > settings.late_grace_minutes:
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
                    row = self.store.append_event(
                        "long_break", moment,
                        metadata={"duration_seconds": elapsed, "overtime_seconds": over,
                                  "limit_minutes": settings.break_limit_minutes},
                        requires_explanation=needs_reason,
                    )
                    notices.append(DisciplineNotice(
                        "long_break", "休息超时记录",
                        f"本次休息 {elapsed // 60} 分钟，超过计划 {over // 60} 分钟",
                        "warning" if over < 25 * 60 else "critical", str(row["id"]),
                    ))
        elif event_type == "finish_work":
            progress = self.progress(today_seconds, week_seconds, moment)
            finish_at = settings.finish_at(moment.date())
            early = max(0, int((finish_at - moment).total_seconds() // 60))
            if settings.planned_finish_enabled and settings.is_workday(moment.date()) and early > settings.early_finish_grace_minutes:
                row = self.store.append_rule_once(
                    f"{moment.date()}:early_finish", "early_finish", moment,
                    metadata={"minutes_early": early, "planned_finish": finish_at.isoformat()},
                    requires_explanation=(self.mode == "officer" and early >= 60),
                )
                if row is not None:
                    notices.append(DisciplineNotice(
                        "early_finish", "提前下班已记入今日计划",
                        f"比计划提前 {early} 分钟 · 今日 {today_seconds // 60} / {progress.daily_target_seconds // 60} 分钟",
                        "warning", str(row["id"]),
                    ))
            if progress.daily_gap_seconds > 0:
                notice = self._notice(
                    f"{moment.date()}:focus_shortfall", "focus_shortfall", moment,
                    "今日计划小结",
                    f"今日缺口 {progress.daily_gap_seconds // 60} 分钟 · 本周还需 {progress.weekly_remaining_seconds // 60} 分钟",
                    severity="info",
                    requires_explanation=(self.mode == "officer" and progress.daily_gap_seconds >= 60 * 60),
                )
                if notice:
                    notices.append(notice)
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
            first_start = next((row for row in day_events if row.get("event_type") == "start_work"), None)
            late_events = [row for row in day_events if row.get("event_type") == "late_start"]
            long_breaks = [row for row in day_events if row.get("event_type") == "long_break"]
            daily_report = self.store.append_rule_once(
                f"{moment.date()}:daily_report", "daily_report", moment,
                metadata={
                    "planned_start": settings.start_at(moment.date()).strftime("%H:%M"),
                    "actual_start": self._time_label(first_start),
                    "lateness_minutes": int(late_events[0].get("metadata", {}).get("minutes_late", 0)) if late_events else 0,
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
        moment = as_beijing(at)
        settings = self.store.settings
        if not settings.is_workday(moment.date()) or self.store.is_exempt(moment.date()):
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
        started_today = working or any(
            row.get("event_type") == "start_work" for row in self.store.events_for_day(moment.date())
        )
        thresholds = (settings.late_grace_minutes + 1,) if self.mode == "normal" else tuple(settings.late_grace_minutes + delta for delta in (1, 15, 30))
        if not started_today:
            for threshold in thresholds:
                if late_minutes >= threshold:
                    notice = self._notice(
                        f"{moment.date()}:late:{threshold}", "late_start_warning", moment,
                        "今天还没有开工" if threshold == thresholds[0] else ("训导主任点名" if threshold == 30 else "迟到已记账"),
                        f"计划开工 {start.strftime('%H:%M')} · 已迟到 {late_minutes} 分钟",
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
        moment = as_beijing(at)
        if self.store.is_exempt(moment.date()) or not self.store.settings.is_workday(moment.date()):
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

    def daily_summary(self, day: date, today_seconds: int, week_seconds: int) -> dict[str, Any]:
        events = self.store.events_for_day(day)
        progress = self.progress(today_seconds, week_seconds, as_beijing(datetime.combine(day, time(23, 59))))
        late = [row for row in events if row.get("event_type") == "late_start"]
        breaks = [row for row in events if row.get("event_type") == "long_break"]
        finishes = [row for row in events if row.get("event_type") == "early_finish"]
        if self.store.is_exempt(day):
            late, breaks, finishes = [], [], []
        start_event = next((row for row in events if row.get("event_type") == "start_work"), None)
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
            "planned_start": self.store.settings.start_at(day).strftime("%H:%M"),
            "actual_start": self._time_label(start_event),
            "lateness_minutes": int(late[0].get("metadata", {}).get("minutes_late", 0)) if late else 0,
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
            "unexplained_count": 0 if self.store.is_exempt(day) else sum(1 for row in events if row.get("requires_explanation") and not row.get("explanation")),
            "exempt": self.store.is_exempt(day),
            "events": events,
        }

    @staticmethod
    def _time_label(row: dict[str, Any] | None) -> str:
        if not row:
            return ""
        try:
            return as_beijing(datetime.fromisoformat(str(row["occurred_at"]))).strftime("%H:%M")
        except (KeyError, TypeError, ValueError):
            return ""
