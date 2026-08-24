"""Local focus continuity, quality and lightweight review data.

Only coarse metrics are stored: duration, round count, away count and
application *categories*.  Window titles, document names, keystrokes and
mouse coordinates never enter this file.  The module is intentionally
transport-free so the desktop pet remains useful when the social backend is
offline.  Period summaries are calculated on demand from the account-scoped
history; no report images or extra server-side report rows are created.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


# A day may legitimately contain a very long work period.  The only hard
# upper bound is an impossible full 24-hour day; older releases incorrectly
# treated anything above eight hours as anomalous.
MAX_ANALYTICS_DAY_SECONDS = 24 * 60 * 60 - 1
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
        # A local raw record is more precise than the profile aggregate.  In
        # particular, never let an old server-side ``greatest`` snapshot
        # overwrite a corrected local day and then feed that value back to the
        # server on the next heartbeat.
        has_local_today = self._has_local_evidence(now, now)
        if remote_date == today_key and not has_local_today:
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
        has_local_week = self._has_local_evidence(
            now - timedelta(days=now.weekday()), now
        )
        if remote_week == current_week_key and not has_local_week:
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

    def merge_remote_history(self, payload: Any) -> bool:
        """Merge server-confirmed daily totals into this account's local cache.

        The server is the source of truth for cross-device day comparisons.
        A local day marked as untrusted is replaced by the server value rather
        than allowed to hide a valid remote record.
        """

        if isinstance(payload, dict):
            entries = payload.get("days")
        else:
            entries = payload
        if not isinstance(entries, list):
            return False

        today = _as_beijing(self._now()).date()
        cutoff = today - timedelta(days=400)
        days = self._state.setdefault("days", {})
        changed = False
        for item in entries:
            if not isinstance(item, dict):
                continue
            try:
                focus_date = date.fromisoformat(str(item.get("focus_date") or "")[:10])
                value = max(0, min(MAX_ANALYTICS_DAY_SECONDS, int(item.get("seconds") or 0)))
            except (TypeError, ValueError, OverflowError):
                continue
            if focus_date < cutoff or focus_date > today:
                continue
            key = focus_date.isoformat()
            day = days.setdefault(key, self._empty_day())
            try:
                local_value = max(0, int(day.get("seconds", 0) or 0))
            except (AttributeError, TypeError, ValueError):
                local_value = 0
            local_untrusted = bool(day.get("seconds_untrusted"))
            has_local_record = self._has_local_records(focus_date, focus_date)
            # Daily rows are a cross-device fallback only when this account
            # has no detailed local record for that date.  Once a local raw
            # record exists, accepting a remote maximum would resurrect a
            # stale/corrupt total in the report.
            merged = (
                local_value
                if has_local_record and not local_untrusted
                else value
            )
            if local_value != merged:
                day["seconds"] = merged
                changed = True
            if local_untrusted and day.get("seconds_untrusted"):
                day["seconds_untrusted"] = False
                changed = True

        if self._trim_days():
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
        record_id: str | None = None,
    ) -> FocusQuality:
        duration = max(0, int(seconds))
        started = _as_beijing(started_at or self._now())
        quality = score_focus_quality(duration, application_switches, away_count)
        clean_record_id = str(record_id or "").strip()[:160]
        if clean_record_id:
            for raw in self._state.get("records", []):
                if isinstance(raw, dict) and str(raw.get("record_id") or "") == clean_record_id:
                    # A crash can occur after the analytics write but before
                    # the timer cursor is persisted.  Replaying the same
                    # segment must be harmless.
                    return quality
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
            "record_id": clean_record_id,
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
            if isinstance(raw, dict) and bool(raw.get("seconds_untrusted")):
                # Legacy releases could write cumulative session checkpoints
                # again after a restart.  The resulting union is useful for
                # diagnostics, but it is not safe for day-to-day comparison.
                return None
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
        if (
            str(account_state.get("focus_date") or "")[:10] == today.isoformat()
            and not self._has_local_evidence(today, today)
        ):
            account_today = max(0, int(account_state.get("focus_today_seconds", 0) or 0))
            today_seconds = max(today_seconds or 0, account_today)
        current_week_key = (today - timedelta(days=today.weekday())).isoformat()
        if (
            str(account_state.get("focus_week_start") or "")[:10] == current_week_key
            and not self._has_local_evidence(
                today - timedelta(days=today.weekday()), today
            )
        ):
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

    def daily_history(self, days: int = 8) -> list[dict[str, Any]]:
        """Return recent trustworthy daily totals for server reconciliation."""

        count = max(1, min(31, int(days)))
        today = _as_beijing(self._now()).date()
        result: list[dict[str, Any]] = []
        stored = self._state.get("days", {})
        for offset in range(count - 1, -1, -1):
            focus_date = today - timedelta(days=offset)
            key = focus_date.isoformat()
            if key not in stored:
                # A new device must not send synthetic zeros for days it has
                # never observed; that would erase valid remote history.
                continue
            raw = stored.get(key, {})
            if not isinstance(raw, dict) or bool(raw.get("seconds_untrusted")):
                continue
            try:
                seconds = max(0, min(MAX_ANALYTICS_DAY_SECONDS, int(raw.get("seconds", 0) or 0)))
            except (TypeError, ValueError, OverflowError):
                continue
            # Include trustworthy zero days as well.  The exact reconciliation
            # RPC needs those rows to clear a previously inflated daily value;
            # omitting them would leave the old server maximum permanently.
            result.append({"focus_date": focus_date.isoformat(), "seconds": seconds})
        return result

    def period_summary(self, period: str = "day", at: datetime | None = None) -> dict[str, Any]:
        """Calculate a day/week/month report from the account's local history.

        This is deliberately a read-only, on-demand projection.  The current
        live timer is supplied by the caller because it is not yet a closed
        analytics record; server-confirmed daily and weekly maxima are merged
        into the same projection when available.
        """

        moment = _as_beijing(at or self._now())
        today = moment.date()
        normalized = str(period or "day").strip().casefold()
        if normalized in {"week", "weekly", "本周", "周"}:
            key = "week"
            start = today - timedelta(days=today.weekday())
        elif normalized in {"month", "monthly", "月度", "月"}:
            key = "month"
            start = today.replace(day=1)
        else:
            key = "day"
            start = today

        if key == "day":
            period_end = today
        elif key == "week":
            period_end = start + timedelta(days=6)
        else:
            next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            period_end = next_month - timedelta(days=1)
        range_start = datetime.combine(start, time.min, tzinfo=BEIJING_TIMEZONE)
        range_end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=BEIJING_TIMEZONE)

        stored = self._state.get("days", {})
        if not isinstance(stored, dict):
            stored = {}
        daily: list[dict[str, Any]] = []
        total_seconds = 0
        completed_rounds = 0
        longest_focus_seconds = 0
        interruptions = 0
        untrusted_days: list[str] = []
        cursor = start
        while cursor <= period_end:
            date_key = cursor.isoformat()
            raw = stored.get(date_key, {})
            raw = raw if isinstance(raw, dict) else {}
            is_future = cursor > today
            untrusted = bool(raw.get("seconds_untrusted"))
            if is_future:
                seconds = None
                rounds = None
                longest = 0
                day_interruptions = 0
            elif untrusted:
                seconds = None
                rounds = None
                longest = 0
                day_interruptions = 0
                untrusted_days.append(date_key)
            else:
                try:
                    seconds = max(0, min(MAX_ANALYTICS_DAY_SECONDS, int(raw.get("seconds", 0) or 0)))
                except (TypeError, ValueError, OverflowError):
                    seconds = 0
            if not is_future and not untrusted:
                try:
                    rounds = max(0, int(raw.get("rounds", 0) or 0))
                    longest = max(0, int(raw.get("longest", 0) or 0))
                    day_interruptions = max(0, int(raw.get("interruptions", 0) or 0))
                except (TypeError, ValueError, OverflowError):
                    rounds = longest = day_interruptions = 0
            total_seconds += int(seconds or 0)
            completed_rounds += int(rounds or 0)
            longest_focus_seconds = max(longest_focus_seconds, longest)
            interruptions += day_interruptions
            weekday = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")[cursor.weekday()]
            daily.append({
                "date": date_key,
                "label": f"{cursor.month}/{cursor.day}",
                "weekday": weekday,
                "display_label": f"{cursor.month}/{cursor.day} {weekday}",
                "seconds": seconds,
                "rounds": rounds,
                "trusted": not untrusted and not is_future,
                "is_today": cursor == today,
                "is_future": is_future,
                "status": "future" if is_future else "untrusted" if untrusted else "observed",
            })
            cursor += timedelta(days=1)

        account_state = self._state.get("account_state", {})
        if not isinstance(account_state, dict):
            account_state = {}
        # A day row can come from a trusted cross-device history sync without
        # having a raw FocusSession record on this computer.  It is still
        # stronger evidence than a server-side weekly ``greatest`` snapshot;
        # otherwise an old aggregate can make Monday show more time than the
        # seven daily rows that produced it.
        local_period_evidence = self._has_local_evidence(start, today)
        if (
            key == "day"
            and not local_period_evidence
            and str(account_state.get("focus_date") or "")[:10] == today.isoformat()
        ):
            total_seconds = max(total_seconds, max(0, int(account_state.get("focus_today_seconds", 0) or 0)))
        if (
            key == "week"
            and not local_period_evidence
            and str(account_state.get("focus_week_start") or "")[:10] == start.isoformat()
        ):
            total_seconds = max(total_seconds, max(0, int(account_state.get("focus_week_seconds", 0) or 0)))
        # A weekly server snapshot can arrive before all of its daily rows are
        # present on a newly opened device.  When the current week is wholly
        # inside the current month, include that same authoritative weekly
        # total in the month projection as well.  This prevents impossible
        # displays such as “本周 53 小时 / 本月 25 小时”.
        current_week_start = today - timedelta(days=today.weekday())
        if (
            key == "month"
            and not local_period_evidence
            and current_week_start >= start
            and str(account_state.get("focus_week_start") or "")[:10]
            == current_week_start.isoformat()
        ):
            total_seconds = max(
                total_seconds,
                max(0, int(account_state.get("focus_week_seconds", 0) or 0)),
            )

        trusted_days = {
            str(item.get("date") or "")
            for item in daily
            if bool(item.get("trusted"))
        }

        def trusted_record_date(value: datetime) -> bool:
            return value.date().isoformat() in trusted_days

        quality_values: list[int] = []
        for raw in self._state.get("records", []):
            if not isinstance(raw, dict):
                continue
            try:
                started = _as_beijing(datetime.fromisoformat(str(raw.get("started_at", ""))))
                value = int(raw.get("quality", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                continue
            if start <= started.date() <= today and trusted_record_date(started) and value > 0:
                quality_values.append(max(0, min(100, value)))

        # Derive report-only metrics from the raw account-scoped records. A
        # paused/resumed work session writes several segments with the same
        # session prefix in record_id, so group those segments before showing
        # completion rate or average session length.
        session_records: dict[str, dict[str, Any]] = {}
        hourly_intervals: list[list[tuple[datetime, datetime]]] = [[] for _ in range(24)]
        focus_intervals: list[dict[str, Any]] = []
        for index, raw in enumerate(self._state.get("records", [])):
            if not isinstance(raw, dict):
                continue
            try:
                started = _as_beijing(datetime.fromisoformat(str(raw.get("started_at", ""))))
                seconds = max(0, int(raw.get("seconds", 0) or 0))
            except (TypeError, ValueError, OverflowError):
                continue
            if not (start <= started.date() <= today) or not trusted_record_date(started):
                continue
            record_id = str(raw.get("record_id") or "").strip()
            session_key = record_id.split(":", 1)[0] if record_id else f"record:{index}"
            ended = started + timedelta(seconds=seconds)
            item = session_records.setdefault(
                session_key,
                {
                    "seconds": 0,
                    "completed": False,
                    "started": started,
                    "ended": ended,
                    "intervals": [],
                },
            )
            item["seconds"] += seconds
            item["completed"] = bool(item["completed"] or raw.get("completed"))
            item["started"] = min(item["started"], started)
            item["ended"] = max(item["ended"], ended)
            item["intervals"].append((started, ended))
            focus_intervals.append(
                {
                    "date": started.date().isoformat(),
                    "started_at": started.isoformat(),
                    "ended_at": ended.isoformat(),
                    "seconds": seconds,
                    "task": str(raw.get("task_title") or raw.get("task") or raw.get("title") or ""),
                }
            )

            clipped_start = max(started, range_start)
            clipped_end = min(ended, range_end)
            cursor = clipped_start
            while cursor < clipped_end:
                next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                part_end = min(clipped_end, next_hour)
                hourly_intervals[cursor.hour].append((cursor, part_end))
                cursor = part_end

        def merge_intervals(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
            merged: list[list[datetime]] = []
            for interval_start, interval_end in sorted(intervals):
                if interval_end <= interval_start:
                    continue
                if not merged or interval_start > merged[-1][1]:
                    merged.append([interval_start, interval_end])
                elif interval_end > merged[-1][1]:
                    merged[-1][1] = interval_end
            return [(item[0], item[1]) for item in merged]

        def union_seconds(intervals: list[tuple[datetime, datetime]]) -> int:
            return sum(
                max(0, int((interval_end - interval_start).total_seconds()))
                for interval_start, interval_end in merge_intervals(intervals)
            )

        def continuous_seconds(intervals: list[tuple[datetime, datetime]]) -> int:
            """Merge short pause gaps while keeping active time as the value."""

            merged = merge_intervals(intervals)
            if not merged:
                return 0
            best = current = max(0, int((merged[0][1] - merged[0][0]).total_seconds()))
            previous_end = merged[0][1]
            for started, ended in merged[1:]:
                duration = max(0, int((ended - started).total_seconds()))
                gap = max(0, int((started - previous_end).total_seconds()))
                if gap <= INTERRUPTION_GRACE_SECONDS:
                    current += duration
                else:
                    best = max(best, current)
                    current = duration
                previous_end = max(previous_end, ended)
            return max(best, current)

        started_rounds = len(session_records)
        completed_sessions = sum(1 for item in session_records.values() if item["completed"])
        session_durations = [
            continuous_seconds(item["intervals"])
            for item in session_records.values()
            if continuous_seconds(item["intervals"]) > 0
        ]
        longest_session_seconds = max(
            session_durations,
            default=0,
        )
        first_started = min((item["started"] for item in session_records.values()), default=None)
        last_ended = max((item["ended"] for item in session_records.values()), default=None)
        completion_rate = round(completed_sessions / started_rounds * 100, 1) if started_rounds else 0.0
        high_quality_seconds = sum(
            continuous_seconds(item["intervals"])
            for item in session_records.values()
            if continuous_seconds(item["intervals"]) >= 25 * 60
        )
        high_quality_seconds = min(high_quality_seconds, total_seconds)
        if first_started is not None:
            first_started_text = first_started.strftime("%H:%M")
            last_ended_text = last_ended.strftime("%H:%M") if last_ended is not None else "--:--"
        else:
            first_started_text = last_ended_text = "暂无记录"

        return {
            "period": key,
            "start": start.isoformat(),
            "end": today.isoformat(),
            "total_seconds": total_seconds,
            "completed_rounds": completed_rounds,
            "longest_focus_seconds": max(longest_focus_seconds, longest_session_seconds),
            "interruptions": interruptions,
            "average_quality": round(sum(quality_values) / len(quality_values)) if quality_values else 0,
            "active_days": sum(1 for item in daily if int(item.get("seconds") or 0) > 0),
            "started_rounds": started_rounds,
            "completion_rate": completion_rate,
            # Average and maximum now use the same session grain.  Previously
            # average used unioned segment time while maximum used continuous
            # session time, which could make the average larger than the max.
            "average_session_seconds": round(sum(session_durations) / len(session_durations))
            if session_durations
            else 0,
            "high_quality_seconds": high_quality_seconds,
            "deep_focus_seconds": high_quality_seconds,
            "first_started_at": first_started_text,
            "last_ended_at": last_ended_text,
            "strongest_window": self._best_window(today, start=start),
            "hourly": [
                {"hour": hour, "label": f"{hour:02d}:00", "seconds": union_seconds(intervals)}
                for hour, intervals in enumerate(hourly_intervals)
            ],
            "focus_intervals": focus_intervals,
            "daily": daily,
            "untrusted_days": untrusted_days,
            "data_quality": {
                "trusted": not bool(untrusted_days),
                "untrusted_days": list(untrusted_days),
                "message": (
                    "本周期包含旧版异常计时记录；异常日期已从报告指标中剔除，避免把重复检查点当成真实工作时间。"
                    if untrusted_days else "本周期数据口径正常。"
                ),
            },
            "local_record_count": sum(
                1 for raw in self._state.get("records", [])
                if isinstance(raw, dict)
                and self._record_date(raw) is not None
                and start <= self._record_date(raw) <= today
            ),
            "local_evidence": local_period_evidence,
        }

    def _best_window(self, today: date, *, start: date | None = None) -> str:
        window_start = start or (today - timedelta(days=6))
        buckets: dict[int, list[int]] = {}
        for raw in self._state.get("records", []):
            try:
                started = _as_beijing(datetime.fromisoformat(str(raw.get("started_at", ""))))
                seconds = max(0, int(raw.get("seconds", 0)))
            except (ValueError, TypeError):
                continue
            if window_start <= started.date() <= today:
                day = self._state.get("days", {}).get(started.date().isoformat(), {})
                if isinstance(day, dict) and bool(day.get("seconds_untrusted")):
                    continue
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
        raw_durations: dict[str, list[int]] = {}
        changed = False
        for raw in self._state.get("records", []):
            if not isinstance(raw, dict):
                continue
            try:
                started = _as_beijing(datetime.fromisoformat(str(raw.get("started_at", ""))))
                duration = max(0, min(int(raw.get("seconds", 0)), MAX_ANALYTICS_DAY_SECONDS))
            except (TypeError, ValueError, OverflowError):
                continue
            if duration <= 0:
                continue
            day_key = started.date().isoformat()
            if raw.get("date") != day_key:
                raw["date"] = day_key
                changed = True
            canonical_started = started.isoformat()
            if raw.get("started_at") != canonical_started:
                raw["started_at"] = canonical_started
                changed = True
            raw_durations.setdefault(day_key, []).append(duration)
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
            raw_total = sum(raw_durations.get(day_key, []))
            # Before the session cursor was persisted, a recovered app could
            # write the cumulative timer total repeatedly.  A large raw/union
            # ratio with several records is a strong signal of that specific
            # corruption.  Keep the raw data for diagnostics, but never use
            # the derived value for a day-vs-day comparison.
            seconds_untrusted = (
                len(raw_durations.get(day_key, [])) >= 3
                and seconds > 0
                and raw_total >= seconds * 1.5
            )
            if int(day.get("seconds", 0) or 0) != seconds:
                day["seconds"] = seconds
                changed = True
            if bool(day.get("seconds_untrusted")) != seconds_untrusted:
                day["seconds_untrusted"] = seconds_untrusted
                changed = True
        return changed

    @staticmethod
    def _record_date(raw: dict[str, Any]) -> date | None:
        try:
            value = raw.get("started_at") or raw.get("date") or ""
            if "T" in str(value):
                return _as_beijing(datetime.fromisoformat(str(value).replace("Z", "+00:00"))).date()
            return date.fromisoformat(str(value)[:10])
        except (TypeError, ValueError, OverflowError):
            return None

    def _has_local_records(self, start: date, end: date) -> bool:
        return any(
            isinstance(raw, dict)
            and (record_date := self._record_date(raw)) is not None
            and start <= record_date <= end
            for raw in self._state.get("records", [])
        )

    def _has_observed_days(self, start: date, end: date) -> bool:
        """Return whether the local cache has a trusted observed day row.

        ``days`` may be populated by the cross-device history sync before a
        raw FocusSession is written on this machine.  Those rows are valid
        report evidence, while future and legacy-untrusted rows are not.
        """

        days = self._state.get("days", {})
        if not isinstance(days, dict):
            return False
        cursor = start
        while cursor <= end:
            raw = days.get(cursor.isoformat())
            if isinstance(raw, dict) and not bool(raw.get("seconds_untrusted")):
                try:
                    seconds = int(raw.get("seconds", 0) or 0)
                except (TypeError, ValueError, OverflowError):
                    seconds = -1
                if 0 <= seconds <= MAX_ANALYTICS_DAY_SECONDS:
                    return True
            cursor += timedelta(days=1)
        return False

    def _has_local_evidence(self, start: date, end: date) -> bool:
        return self._has_local_records(start, end) or self._has_observed_days(start, end)

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

