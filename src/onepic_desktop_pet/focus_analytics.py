"""Local focus continuity, quality and lightweight review data.

Only coarse metrics are stored: duration, round count, away count and
application *categories*.  Window titles, document names, keystrokes and
mouse coordinates never enter this file.  The module is intentionally
transport-free so the desktop pet remains useful when the social backend is
offline.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


MAX_ANALYTICS_DAY_SECONDS = 24 * 60 * 60
INTERRUPTION_GRACE_SECONDS = 10 * 60
BEIJING_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")


def _as_beijing(value: datetime) -> datetime:
    """Normalize an event without making machine timezone part of the metric."""

    if value.tzinfo is None:
        # Naive values are legacy/test values.  They already represent the
        # caller's stated clock time, so attach Beijing rather than silently
        # shifting them by the developer machine's timezone.
        value = value.replace(tzinfo=BEIJING_TIMEZONE)
    return value.astimezone(BEIJING_TIMEZONE)


def focus_analytics_path(account_id: str | None = None) -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".desktop_pet"
    value = str(account_id or "").strip().casefold()
    key = re.sub(r"[^a-z0-9._-]", "_", value)[:80] or "anonymous"
    return root / "Lili" / "accounts" / key / "focus_analytics.json"


@dataclass(frozen=True)
class FocusQuality:
    score: int
    label: str


@dataclass(frozen=True)
class FocusAnalyticsSummary:
    date: str
    today_rounds: int
    current_streak_days: int
    longest_streak_days: int
    weekly_total_seconds: int
    yesterday_seconds: int | None
    difference_vs_yesterday_seconds: int | None
    average_quality: int
    quality_label: str
    high_efficiency_window: str
    late_night_average_seconds: int
    today_interruptions: int = 0
    current_interruptions: int = 0
    longest_continuous_seconds: int = 0
    current_continuous_seconds: int = 0
    # The server-confirmed daily value is kept separately from the local
    # record history so a new computer can render the same account totals.
    today_seconds: int | None = None


class FocusQualityTracker:
    """Collect session-local quality signals without collecting private text."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.started_at = 0.0
        self.application_switches = 0
        self.away_count = 0
        self._last_category = ""

    def start(self, category: str = "other") -> None:
        self.reset()
        self.started_at = datetime.now().timestamp()
        self._last_category = str(category or "other")

    def note_application_switch(self, category: str) -> None:
        clean = str(category or "other")
        if self.started_at and self._last_category and clean != self._last_category:
            self.application_switches += 1
        self._last_category = clean

    def note_away(self) -> None:
        if self.started_at:
            self.away_count += 1

    def snapshot(self) -> dict[str, int]:
        return {
            "application_switches": max(0, int(self.application_switches)),
            "away_count": max(0, int(self.away_count)),
        }


def score_focus_quality(seconds: int, application_switches: int = 0, away_count: int = 0) -> FocusQuality:
    """Return a stable, explainable quality score for one focus round."""

    duration = max(0, int(seconds))
    score = 48 + min(32, duration // 100) + min(20, duration // 600)
    score -= max(0, int(application_switches)) * 4
    score -= max(0, int(away_count)) * 9
    score = max(0, min(100, score))
    if score >= 82 and duration >= 25 * 60:
        label = "很深的一轮"
    elif score >= 62:
        label = "这一轮比较稳"
    else:
        label = "切换有点多"
    return FocusQuality(score, label)


class FocusAnalyticsStore:
    """Persist a bounded local history and derive continuity summaries."""

    def __init__(
        self,
        path: Path | None = None,
        now_provider: Callable[[], datetime] | None = None,
        persist: bool = True,
    ) -> None:
        self._uses_explicit_path = path is not None
        self.path = path or focus_analytics_path()
        self._now = now_provider or (lambda: datetime.now(BEIJING_TIMEZONE))
        self._persist = bool(persist)
        self._state: dict[str, Any] = {"days": {}, "records": [], "reviews": {}, "current_task": None, "account_state": {}}
        self._live: dict[str, Any] = {
            "session_active": False,
            "running": False,
            "paused_at": None,
            "continuous_started_at": None,
            "current_interruptions": 0,
            "current_continuous_seconds": 0,
        }
        if self._persist:
            self._load()
        # ``days.seconds`` is derived data.  Older releases added cumulative
        # timer checkpoints as if they were independent sessions, which could
        # produce impossible values such as 38 hours in one calendar day.
        # Rebuild from the raw records on load while keeping those records for
        # diagnostics and future migrations.
        if self._rebuild_days_from_records() or self._trim_days():
            self._save()

    def switch_account(self, account_id: str | None) -> bool:
        """切换本地专注分析命名空间，避免账号之间复用历史记录。"""

        if not self._persist or self._uses_explicit_path:
            return False
        target = focus_analytics_path(account_id)
        if self.path == target:
            return False
        self._save()
        self.path = target
        self._state = {
            "days": {},
            "records": [],
            "reviews": {},
            "current_task": None,
            "account_state": {},
        }
        self._live = {
            "session_active": False,
            "running": False,
            "paused_at": None,
            "continuous_started_at": None,
            "current_interruptions": 0,
            "current_continuous_seconds": 0,
        }
        self._load()
        if self._rebuild_days_from_records() or self._trim_days():
            self._save()
        return True

    def merge_remote_state(
        self,
        *,
        focus_date: str | None = None,
        today_seconds: int = 0,
        lifetime_seconds: int = 0,
        week_start: str | None = None,
        week_seconds: int = 0,
    ) -> bool:
        """Merge account totals received from Supabase without double counting.

        The detailed history remains local, but the totals that identify the
        account (today, this week, and lifetime) are server-authoritative
        maxima.  This lets a second computer immediately show the same totals
        while keeping an offline session usable until the next sync.
        """

        now = _as_beijing(self._now()).date()
        today_key = now.isoformat()
        current_week_key = (now - timedelta(days=now.weekday())).isoformat()
        state = self._state.setdefault("account_state", {})
        if not isinstance(state, dict):
            state = {}
            self._state["account_state"] = state
        changed = False

        remote_date = str(focus_date or "")[:10]
        if remote_date == today_key:
            value = max(0, min(MAX_ANALYTICS_DAY_SECONDS, int(today_seconds or 0)))
            if value > max(0, int(state.get("focus_today_seconds", 0) or 0)):
                state["focus_today_seconds"] = value
                changed = True
            if state.get("focus_date") != today_key:
                state["focus_date"] = today_key
                changed = True

        lifetime = max(0, int(lifetime_seconds or 0))
        if lifetime > max(0, int(state.get("focus_lifetime_seconds", 0) or 0)):
            state["focus_lifetime_seconds"] = lifetime
            changed = True

        remote_week = str(week_start or "")[:10]
        if remote_week == current_week_key:
            value = max(0, min(7 * MAX_ANALYTICS_DAY_SECONDS, int(week_seconds or 0)))
            if value > max(0, int(state.get("focus_week_seconds", 0) or 0)):
                state["focus_week_seconds"] = value
                changed = True
            if state.get("focus_week_start") != current_week_key:
                state["focus_week_start"] = current_week_key
                changed = True

        if changed:
            self._save()
        return changed

    def record_session(
        self,
        seconds: int,
        *,
        started_at: datetime | None = None,
        completed: bool = False,
        application_switches: int = 0,
        away_count: int = 0,
        task: str = "",
        interruptions: int = 0,
    ) -> FocusQuality:
        duration = max(0, int(seconds))
        started = _as_beijing(started_at or self._now())
        quality = score_focus_quality(duration, application_switches, away_count)
        day_key = started.date().isoformat()
        days = self._state.setdefault("days", {})
        day = days.setdefault(day_key, self._empty_day())
        day["seconds"] = max(0, int(day.get("seconds", 0))) + duration
        day["rounds"] = max(0, int(day.get("rounds", 0))) + (1 if completed else 0)
        day["longest"] = max(max(0, int(day.get("longest", 0))), duration)
        day["quality"] = [*list(day.get("quality", []))[-49:], quality.score]
        day["switches"] = max(0, int(day.get("switches", 0))) + max(0, int(application_switches))
        day["away"] = max(0, int(day.get("away", 0))) + max(0, int(away_count))
        day["interruptions"] = max(0, int(day.get("interruptions", 0))) + max(0, int(interruptions))
        day["longest_continuous"] = max(
            max(0, int(day.get("longest_continuous", 0))), duration
        )
        records = self._state.setdefault("records", [])
        records.append({
            "date": day_key,
            "started_at": started.isoformat(),
            "seconds": duration,
            "completed": bool(completed),
            "application_switches": max(0, int(application_switches)),
            "away_count": max(0, int(away_count)),
            "quality": quality.score,
            "task": str(task)[:120],
            "interruptions": max(0, int(interruptions)),
        })
        self._state["records"] = records[-500:]
        self._rebuild_days_from_records()
        self._trim_days()
        self._save()
        return quality

    @staticmethod
    def _empty_day() -> dict[str, Any]:
        return {
            "seconds": 0, "rounds": 0, "longest": 0, "quality": [],
            "switches": 0, "away": 0, "interruptions": 0,
            "longest_continuous": 0,
        }

    def begin_focus_session(self, at: datetime | None = None) -> None:
        """Start/resume the live continuity tracker, without a second timer."""

        now = _as_beijing(at or self._now())
        live = self._live
        if not live.get("session_active"):
            live.update({
                "session_active": True,
                "current_interruptions": 0,
                "current_continuous_seconds": 0,
            })
        paused_at = live.get("paused_at")
        if paused_at:
            try:
                paused = _as_beijing(datetime.fromisoformat(str(paused_at)))
                if (now - paused).total_seconds() > INTERRUPTION_GRACE_SECONDS:
                    live["current_interruptions"] = max(0, int(live.get("current_interruptions", 0))) + 1
                    day = self._state.setdefault("days", {}).setdefault(now.date().isoformat(), self._empty_day())
                    day["interruptions"] = max(0, int(day.get("interruptions", 0))) + 1
            except (TypeError, ValueError, OverflowError):
                pass
        live["running"] = True
        live["paused_at"] = None
        live["continuous_started_at"] = now.isoformat()
        self._save()

    def pause_focus_session(self, at: datetime | None = None) -> None:
        """Mark a pause; only a pause longer than ten minutes is an interruption."""

        if not self._live.get("session_active"):
            return
        now = _as_beijing(at or self._now())
        self._update_live_continuous(now)
        self._live["running"] = False
        self._live["paused_at"] = now.isoformat()
        self._save()

    def finish_focus_session(self, *, completed: bool = True, at: datetime | None = None) -> None:
        """Close the live tracker after the timer has recorded its final segment."""

        if self._live.get("session_active"):
            self._update_live_continuous(_as_beijing(at or self._now()))
        self._live.update({
            "session_active": False,
            "running": False,
            "paused_at": None,
            "continuous_started_at": None,
            "current_continuous_seconds": 0,
            "current_interruptions": 0,
        })
        self._save()

    def _update_live_continuous(self, now: datetime) -> None:
        started = self._live.get("continuous_started_at")
        if not started:
            return
        try:
            value = max(0, int((now - _as_beijing(datetime.fromisoformat(str(started)))).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return
        self._live["current_continuous_seconds"] = max(
            max(0, int(self._live.get("current_continuous_seconds", 0))), value
        )
        day = self._state.setdefault("days", {}).setdefault(now.date().isoformat(), self._empty_day())
        day["longest_continuous"] = max(max(0, int(day.get("longest_continuous", 0))), value)

    def set_current_task(self, title: str, *, due_at: str | None = None, target_seconds: int = 0) -> None:
        clean = str(title).strip()[:120]
        self._state["current_task"] = {
            "title": clean,
            "due_at": str(due_at or ""),
            "target_seconds": max(0, int(target_seconds)),
            "progress_seconds": 0,
        } if clean else None
        self._save()

    def update_current_task_progress(self, seconds: int) -> None:
        task = self._state.get("current_task")
        if isinstance(task, dict):
            task["progress_seconds"] = max(0, int(task.get("progress_seconds", 0))) + max(0, int(seconds))
            self._save()

    def current_task(self) -> dict[str, Any] | None:
        task = self._state.get("current_task")
        return dict(task) if isinstance(task, dict) else None

    def set_tomorrow_task(self, title: str) -> None:
        tomorrow = (_as_beijing(self._now()).date() + timedelta(days=1)).isoformat()
        clean = str(title).strip()[:160]
        if clean:
            self._state.setdefault("reviews", {})[tomorrow] = clean
        else:
            self._state.setdefault("reviews", {}).pop(tomorrow, None)
        self._save()

    def tomorrow_task(self) -> str:
        tomorrow = (_as_beijing(self._now()).date() + timedelta(days=1)).isoformat()
        return str(self._state.setdefault("reviews", {}).get(tomorrow, ""))

    def today_first_task(self) -> str:
        today = _as_beijing(self._now()).date().isoformat()
        return str(self._state.setdefault("reviews", {}).get(today, ""))

    def summary(self, at: datetime | None = None) -> FocusAnalyticsSummary:
        moment = _as_beijing(at or self._now())
        today = moment.date()
        days = self._state.get("days", {})

        def day_value(day: date, key: str) -> int:
            raw = days.get(day.isoformat(), {})
            try:
                return max(0, int(raw.get(key, 0)))
            except (AttributeError, TypeError, ValueError):
                return 0

        def day_seconds(day: date) -> int | None:
            raw = days.get(day.isoformat(), {})
            try:
                value = max(0, int(raw.get("seconds", 0)))
            except (AttributeError, TypeError, ValueError):
                return None
            return value if value <= MAX_ANALYTICS_DAY_SECONDS else None

        weekly_total = sum(
            seconds or 0
            for i in range(7)
            for seconds in (day_seconds(today - timedelta(days=i)),)
        )
        yesterday = day_seconds(today - timedelta(days=1))
        today_seconds = day_seconds(today)
        account_state = self._state.get("account_state", {})
        if not isinstance(account_state, dict):
            account_state = {}
        if str(account_state.get("focus_date") or "")[:10] == today.isoformat():
            account_today = max(0, int(account_state.get("focus_today_seconds", 0) or 0))
            today_seconds = max(today_seconds or 0, account_today)
        current_week_key = (today - timedelta(days=today.weekday())).isoformat()
        if str(account_state.get("focus_week_start") or "")[:10] == current_week_key:
            weekly_total = max(
                weekly_total,
                max(0, int(account_state.get("focus_week_seconds", 0) or 0)),
            )
        streak_reference = today if (today_seconds or 0) > 0 else today - timedelta(days=1)
        streak = 0
        while (day_seconds(streak_reference - timedelta(days=streak)) or 0) > 0:
            streak += 1
        longest = 0
        run = 0
        for offset in range(366, -1, -1):
            if (day_seconds(today - timedelta(days=offset)) or 0) > 0:
                run += 1
                longest = max(longest, run)
            else:
                run = 0
        quality_values: list[int] = []
        for raw in self._state.get("records", []):
            if raw.get("date") == today.isoformat():
                try:
                    quality_values.append(int(raw.get("quality", 0)))
                except (TypeError, ValueError):
                    pass
        average_quality = round(sum(quality_values) / len(quality_values)) if quality_values else 0
        quality_label = score_focus_quality(45 * 60, max(0, 24 - average_quality), 0).label if average_quality else "尚无本日质量数据"
        window = self._best_window(today)
        late_records = []
        for raw in self._state.get("records", []):
            try:
                started = _as_beijing(datetime.fromisoformat(str(raw.get("started_at", ""))))
            except ValueError:
                continue
            if today - timedelta(days=6) <= started.date() <= today and started.hour >= 23:
                late_records.append(max(0, int(raw.get("seconds", 0))))
        late_average = round(sum(late_records) / len(late_records)) if late_records else 0
        return FocusAnalyticsSummary(
            today.isoformat(), day_value(today, "rounds"), streak, longest, weekly_total,
            yesterday, (today_seconds - yesterday) if today_seconds is not None and yesterday is not None else None,
            average_quality,
            quality_label, window, late_average,
            day_value(today, "interruptions"),
            max(0, int(self._live.get("current_interruptions", 0))),
            day_value(today, "longest_continuous"),
            max(0, int(self._live.get("current_continuous_seconds", 0))),
            today_seconds,
        )

    def snapshot(self) -> dict[str, Any]:
        if self._live.get("running"):
            self._update_live_continuous(_as_beijing(self._now()))
        summary = self.summary()
        return {
            **summary.__dict__,
            "current_task": self.current_task(),
            "tomorrow_task": self.tomorrow_task(),
            "first_task_today": self.today_first_task(),
        }

    def _best_window(self, today: date) -> str:
        buckets: dict[int, list[int]] = {}
        for raw in self._state.get("records", []):
            try:
                started = _as_beijing(datetime.fromisoformat(str(raw.get("started_at", ""))))
                seconds = max(0, int(raw.get("seconds", 0)))
            except (ValueError, TypeError):
                continue
            if today - timedelta(days=6) <= started.date() <= today:
                buckets.setdefault(started.hour, []).append(seconds)
        if not buckets:
            return "暂无足够数据"
        hour = max(buckets, key=lambda key: (sum(buckets[key]) / len(buckets[key]), len(buckets[key])))
        return f"{hour:02d}:00–{(hour + 1) % 24:02d}:00"

    def _rebuild_days_from_records(self) -> bool:
        """Recompute daily duration as a union of raw focus intervals.

        Raw records are intentionally retained.  Only the derived daily
        ``seconds`` field is repaired, so a future migration can still inspect
        the original checkpoints that caused an over-count.
        """

        intervals: dict[str, list[tuple[datetime, datetime]]] = {}
        for raw in self._state.get("records", []):
            if not isinstance(raw, dict):
                continue
            try:
                started = datetime.fromisoformat(str(raw.get("started_at", "")))
                duration = max(0, min(int(raw.get("seconds", 0)), MAX_ANALYTICS_DAY_SECONDS))
            except (TypeError, ValueError, OverflowError):
                continue
            if duration <= 0:
                continue
            end = started + timedelta(seconds=duration)
            cursor = started
            while cursor.date() < end.date():
                boundary = datetime.combine(
                    cursor.date() + timedelta(days=1), time.min, tzinfo=cursor.tzinfo
                )
                intervals.setdefault(cursor.date().isoformat(), []).append((cursor, boundary))
                cursor = boundary
            intervals.setdefault(cursor.date().isoformat(), []).append((cursor, end))

        days = self._state.setdefault("days", {})
        changed = False
        for day_key, pieces in intervals.items():
            pieces.sort(key=lambda item: item[0])
            merged: list[list[datetime]] = []
            for start, end in pieces:
                if not merged or start > merged[-1][1]:
                    merged.append([start, end])
                elif end > merged[-1][1]:
                    merged[-1][1] = end
            seconds = min(
                MAX_ANALYTICS_DAY_SECONDS,
                sum(max(0, int((end - start).total_seconds())) for start, end in merged),
            )
            day = days.setdefault(day_key, self._empty_day())
            if int(day.get("seconds", 0) or 0) != seconds:
                day["seconds"] = seconds
                changed = True
        return changed

    def _trim_days(self) -> bool:
        days = self._state.setdefault("days", {})
        cutoff = _as_beijing(self._now()).date() - timedelta(days=400)
        trimmed = {key: value for key, value in days.items() if key >= cutoff.isoformat()}
        changed = len(trimmed) != len(days)
        self._state["days"] = trimmed
        return changed

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._state.update(raw)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return

    def _save(self) -> None:
        if not self._persist:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)
