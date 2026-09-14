"""
本模块提供 Lili 的本地工作计时与温和休息提醒，不创建窗口或访问网络。

职责范围：
- 记录当天和终身累计工作秒数；日、周、月、年只是统计投影，不切断逻辑 FocusSession；
- 支持开始、暂停、完成、状态格式化和运行中定期落盘；
- 在 session 身份变化前保留 durable checkpoint，避免午夜、重启或账号切换丢失事实；
- 只在本机应用数据目录保存日期与累计秒数，不保存任务名称或聊天内容；
- 按单次连续工作时长产生 25 分钟鼓励、50 分钟休息和更长时段劝慰提醒。

计时使用单调时钟避免系统时间微调造成跳变；提醒阈值按当前连续工作段计算。应用异常退出后不会自动恢复计时，而是恢复为待安全封存状态，交由 FocusSession/AccountFocusStore 先落盘后等待用户明确继续。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .local_data import account_local_data_path, adopt_legacy_account_file


# Persisted values are intentionally plain strings so older installations can
# read the file without importing a new enum.  The UI may present a simpler
# "正在工作 / 已暂停", while these values keep the reason auditable.
WORK_STATE_IDLE = "idle"
WORK_STATE_WORKING = "working"
WORK_STATE_PAUSED_MANUAL = "paused_manual"
WORK_STATE_PAUSED_IDLE = "paused_idle"
WORK_STATE_PAUSED_LOCK = "paused_lock"
WORK_STATE_PAUSED_DISPLAY_OFF = "paused_display_off"
WORK_STATE_PAUSED_SLEEP = "paused_sleep"
WORK_STATE_PAUSED_VIDEO = "paused_video"
WORK_STATE_PAUSED_RESTART = "paused_restart"

# Focus statistics use one calendar everywhere.  A fixed offset is deliberate:
# Beijing has no daylight-saving transition, and the social backend uses the
# same Asia/Shanghai boundary for its daily and weekly ledgers.
BEIJING_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")

_PAUSE_STATE_BY_REASON = {
    "manual": WORK_STATE_PAUSED_MANUAL,
    "idle": WORK_STATE_PAUSED_IDLE,
    "idle_10m": WORK_STATE_PAUSED_IDLE,
    "lock": WORK_STATE_PAUSED_LOCK,
    "display_off": WORK_STATE_PAUSED_DISPLAY_OFF,
    "sleep": WORK_STATE_PAUSED_SLEEP,
    "fullscreen_video": WORK_STATE_PAUSED_VIDEO,
    "video": WORK_STATE_PAUSED_VIDEO,
    "account_switch": WORK_STATE_PAUSED_MANUAL,
    "restart_safe_seal": WORK_STATE_PAUSED_RESTART,
}

LOGGER = logging.getLogger(__name__)


def work_timer_path(account_id: str | None = None) -> Path:
    """返回按账号隔离的本地工作计时文件路径。

    旧版本把所有账号写进 ``Lili/work_timer.json``。该文件不再自动
    迁移或读取，避免登录另一个账号时把旧账号的累计时长再次上传。
    """

    return account_local_data_path("work_timer.json", account_id)


def format_work_duration(seconds: int) -> str:
    """把秒数格式化为适合菜单和气泡显示的中文时长。"""

    safe_seconds = max(0, int(seconds))
    if safe_seconds < 60:
        return "不足1分钟" if safe_seconds else "0分钟"
    total_minutes = safe_seconds // 60
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}小时{minutes}分钟"
    if hours:
        return f"{hours}小时"
    return f"{minutes}分钟"


def format_elapsed_clock(seconds: int) -> str:
    """Format a live session duration as mm:ss or h:mm:ss."""

    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


class SmoothDurationDisplay:
    """Project an authoritative duration onto a monotonic live display.

    The authoritative value may be a cached account projection, so it can
    remain unchanged while the local focus session is still running.  Anchor
    the display to monotonic time instead of allowing a stale value to freeze
    it.  A delayed GUI callback therefore catches up in one update, while a
    newer authoritative value is applied immediately.  Running displays are
    monotonic; pause/account/day transitions deliberately snap to the value
    supplied by the owner.
    """

    def __init__(
        self,
        monotonic_provider: Callable[[], float] | None = None,
    ) -> None:
        self._monotonic = monotonic_provider or time.monotonic
        self._identity = ""
        self._value: int | None = None
        self._active = False
        self._anchor_seconds = 0
        self._anchor_monotonic = self._monotonic()

    def reset(self) -> None:
        self._identity = ""
        self._value = None
        self._active = False
        self._anchor_seconds = 0
        self._anchor_monotonic = self._monotonic()

    def project(
        self,
        authoritative_seconds: int,
        *,
        active: bool,
        identity: str,
    ) -> int:
        authoritative = max(0, int(authoritative_seconds or 0))
        now = self._monotonic()
        clean_identity = str(identity or "")
        must_snap = (
            self._value is None
            or clean_identity != self._identity
            or not active
            or not self._active
            or now < self._anchor_monotonic
        )
        if must_snap:
            self._identity = clean_identity
            self._value = authoritative
            self._active = bool(active)
            self._anchor_seconds = authoritative
            self._anchor_monotonic = now
            return authoritative

        self._active = True
        local_projection = self._anchor_seconds + max(
            0,
            int(now - self._anchor_monotonic),
        )
        if authoritative > local_projection:
            # A server/account projection may include another device's newly
            # sealed work.  Calibrate immediately; making it chase at 1s/s
            # would leave the display permanently behind a clock that is also
            # advancing at 1s/s.
            self._anchor_seconds = authoritative
            self._anchor_monotonic = now
            local_projection = authoritative
        self._value = max(int(self._value or 0), local_projection)
        return self._value


class WorkTimerModel:
    """维护单个用户的今日累计时长和当前连续工作时段。"""

    def __init__(
        self,
        path: Path | None = None,
        now_provider: Callable[[], datetime] | None = None,
        monotonic_provider: Callable[[], float] | None = None,
        *,
        persist: bool = True,
        device_id: str | None = None,
    ) -> None:
        # Demo/offscreen windows must not read or mutate the user's real
        # session.  Production callers keep the historical persistent default.
        self.persist = bool(persist)
        self._uses_account_storage = path is None and self.persist
        self._account_id = "" if self._uses_account_storage else None
        self._device_id = str(device_id or "").strip()[:120]
        self.path = (path or work_timer_path()) if self.persist else None
        if self._uses_account_storage and self.path is not None:
            adopt_legacy_account_file(
                "work_timer.json",
                None,
                destination=self.path,
            )
        self._now = now_provider or (lambda: datetime.now(BEIJING_TIMEZONE))
        self._monotonic = monotonic_provider or time.monotonic
        self._date_key = self._today_key()
        self._accumulated_seconds = 0
        self._lifetime_seconds = 0
        self._notified_outfit_count = 0
        self._session_accumulated_seconds = 0
        self._episode_accumulated_seconds = 0
        self._session_id = ""
        self._analytics_recorded_session_seconds = 0
        self._session_active = False
        self._running_since: float | None = None
        # Wall-clock start of the current uninterrupted segment.  The
        # monotonic timestamp above is intentionally reset at checkpoints for
        # crash-safe persistence, but this value must remain stable so reports
        # can draw the real interval instead of ``now–now``.
        self._running_started_at: datetime | None = None
        self._state = WORK_STATE_IDLE
        self._pause_reason: str | None = None
        self._last_update_reason = "init"
        self._last_checkpoint = self._monotonic()
        self._last_reminder_key: str | None = None
        self._recovered_active_session = False
        self._last_trusted_checkpoint_at: datetime | None = None
        self._recovery_pending = False
        # Kept as a compatibility no-op for older integrations.  Calendar
        # boundaries must never own FocusSession lifecycle anymore.
        self._day_rollover_handler: Callable[[int, str, datetime | None], None] | None = None
        self._load()

    def set_day_rollover_handler(
        self,
        handler: Callable[[int, str, datetime | None], None] | None,
    ) -> None:
        """Retain the old API without allowing midnight to split a session.

        Older callers registered a callback that wrote a synthetic segment at
        midnight.  That ordering made the next presence heartbeat expose a
        new session before the old fact was guaranteed to exist.  The callback
        is intentionally ignored; only a real pause/finish/shutdown seals a
        FocusSegment now.
        """

        self._day_rollover_handler = handler

    def switch_account(self, account_id: str | None) -> bool:
        """切换本地计时命名空间，绝不把一个账号的计时带给另一个账号。"""

        if not self._uses_account_storage or self.path is None:
            return False
        target_id = str(account_id or "").strip()
        target = work_timer_path(target_id)
        if self.path == target:
            self._account_id = target_id
            return False
        if self.is_running:
            # The timer cannot write a FocusSegment by itself.  Refuse the
            # namespace switch so the window owner can run the shared
            # seal-before-transition pipeline first.
            LOGGER.error("refusing account switch while FocusSession is running")
            return False
        adopt_legacy_account_file(
            "work_timer.json",
            target_id,
            destination=target,
        )
        self.path = target
        self._account_id = target_id
        self._device_id = ""
        self._reset_in_memory_state()
        self._load()
        return True

    def set_device_id(self, device_id: str | None) -> None:
        """Persist the device provenance used by the active checkpoint."""

        clean = str(device_id or "").strip()[:120]
        if clean == self._device_id:
            return
        self._device_id = clean
        self._save()

    def _reset_in_memory_state(self) -> None:
        self._date_key = self._today_key()
        self._accumulated_seconds = 0
        self._lifetime_seconds = 0
        self._notified_outfit_count = 0
        self._session_accumulated_seconds = 0
        self._episode_accumulated_seconds = 0
        self._session_id = ""
        self._analytics_recorded_session_seconds = 0
        self._session_active = False
        self._running_since = None
        self._running_started_at = None
        self._state = WORK_STATE_IDLE
        self._pause_reason = None
        self._last_update_reason = "account_switch"
        self._last_checkpoint = self._monotonic()
        self._last_reminder_key = None
        self._recovered_active_session = False
        self._last_trusted_checkpoint_at = None
        self._recovery_pending = False

    @property
    def is_running(self) -> bool:
        """返回当前是否正在计时。"""

        return self._running_since is not None

    @property
    def recovered_active_session(self) -> bool:
        """Whether the last saved state was running and has just been resumed."""

        return self._recovered_active_session

    @property
    def has_active_session(self) -> bool:
        """Whether a started work session is paused or currently running."""

        return self._session_active

    @property
    def state(self) -> str:
        """Return the durable work state, including why a pause happened."""

        return self._state

    @property
    def pause_reason(self) -> str | None:
        """Return the normalized reason for the current pause, if any."""

        return self._pause_reason

    @property
    def is_paused(self) -> bool:
        return self._session_active and not self.is_running

    def _today_key(self) -> str:
        """Return the Beijing calendar date used by all focus ledgers."""

        current = self._now()
        if current.tzinfo is None:
            # Test providers and legacy callers often supply a naive datetime;
            # preserve that explicit value while production uses the aware
            # Beijing provider above.
            return current.date().isoformat()
        return current.astimezone(BEIJING_TIMEZONE).date().isoformat()

    def _load(self) -> None:
        """读取累计秒数，并把异常退出的 running 状态转为安全待恢复。"""

        if self.path is None:
            return
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._lifetime_seconds = max(0, int(data.get("lifetime_seconds", 0)))
            self._notified_outfit_count = max(0, int(data.get("notified_outfit_count", 0)))
            same_date = data.get("date") == self._date_key
            # Daily counters are projections and may reset at a calendar
            # boundary.  Session identity and its durable checkpoint are not
            # daily data and must survive midnight/restart unchanged.
            seconds = int(data.get("accumulated_seconds", 0)) if same_date else 0
            session_seconds = max(0, int(data.get("session_accumulated_seconds", 0)))
            analytics_cursor_present = "analytics_recorded_session_seconds" in data
            analytics_recorded_seconds = max(
                0, int(data.get("analytics_recorded_session_seconds", 0))
            )
            episode_seconds = max(0, int(data.get("episode_accumulated_seconds", 0)))
            saved_running = bool(data.get("running", False))
            saved_session_active = bool(
                data.get("session_active", saved_running or session_seconds > 0)
            )
            saved_state = str(data.get("state") or "").strip()
            if saved_running:
                saved_state = WORK_STATE_WORKING
            elif saved_session_active and saved_state not in {
                WORK_STATE_PAUSED_MANUAL,
                WORK_STATE_PAUSED_IDLE,
                WORK_STATE_PAUSED_LOCK,
                WORK_STATE_PAUSED_DISPLAY_OFF,
                WORK_STATE_PAUSED_SLEEP,
                WORK_STATE_PAUSED_VIDEO,
                WORK_STATE_PAUSED_RESTART,
            }:
                # Old files only had session_active/running.  Treat a paused
                # old session as a manual pause rather than auto-resuming it.
                saved_state = WORK_STATE_PAUSED_MANUAL
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return
        self._accumulated_seconds = max(0, seconds)
        self._session_accumulated_seconds = session_seconds
        self._episode_accumulated_seconds = episode_seconds
        self._session_id = str(data.get("session_id") or "")
        self._session_active = saved_session_active
        if self._session_active and not self._session_id:
            self._session_id = uuid.uuid4().hex
        # Old releases did not persist the analytics cursor.  Their active
        # session may already have been written before a restart; treating
        # the saved session total as accounted prevents the next pause from
        # writing that cumulative total a second time.
        self._analytics_recorded_session_seconds = min(
            session_seconds,
            analytics_recorded_seconds if analytics_cursor_present else session_seconds,
        )
        self._state = saved_state if self._session_active else WORK_STATE_IDLE
        self._pause_reason = (
            str(data.get("pause_reason") or "manual")
            if self._state != WORK_STATE_WORKING and self._session_active
            else None
        )
        self._last_update_reason = str(
            data.get("last_update_reason") or self._pause_reason or self._state or "load"
        )
        saved_device_id = str(data.get("device_id") or "").strip()[:120]
        if saved_device_id:
            self._device_id = saved_device_id
        raw_started = data.get("running_started_at")
        try:
            if raw_started:
                recovered_at = datetime.fromisoformat(str(raw_started).replace("Z", "+00:00"))
                if recovered_at.tzinfo is None:
                    recovered_at = recovered_at.replace(tzinfo=BEIJING_TIMEZONE)
                self._running_started_at = recovered_at.astimezone(BEIJING_TIMEZONE)
        except (TypeError, ValueError, OverflowError):
            self._running_started_at = None
        raw_checkpoint = data.get("last_trusted_checkpoint_at")
        try:
            if raw_checkpoint:
                checkpoint = datetime.fromisoformat(str(raw_checkpoint).replace("Z", "+00:00"))
                if checkpoint.tzinfo is None:
                    checkpoint = checkpoint.replace(tzinfo=BEIJING_TIMEZONE)
                self._last_trusted_checkpoint_at = checkpoint.astimezone(BEIJING_TIMEZONE)
        except (TypeError, ValueError, OverflowError):
            self._last_trusted_checkpoint_at = None
        if saved_running:
            # Never auto-resume after a process restart.  The persisted
            # checkpoint remains available for the owner to seal as a raw
            # FocusSegment before the user can start a new session.
            self._running_since = None
            self._last_checkpoint = self._monotonic()
            self._state = WORK_STATE_PAUSED_RESTART
            self._pause_reason = "restart_safe_seal"
            self._last_update_reason = "restart_safe_seal"
            self._recovered_active_session = True
            self._recovery_pending = True
        elif saved_session_active:
            self._recovery_pending = False

    @property
    def recovery_pending(self) -> bool:
        """Whether a durable local interval still needs a canonical seal."""

        return bool(
            self._recovery_pending
            or (
                self._session_active
                and not self.is_running
                and bool(self._session_id)
                and self._last_trusted_checkpoint_at is not None
                and self._session_accumulated_seconds
                > self._analytics_recorded_session_seconds
            )
        )

    def pending_recovery_seal(self) -> tuple[int, str, datetime | None] | None:
        """Return an exact durable interval awaiting the canonical store.

        Besides abnormal-restart checkpoints, this covers a paused timer from
        a release where the timer transition succeeded but the analytics/WAL
        write did not.  The latter is reconstructed only from the persisted
        session cursor and pause checkpoint; aggregate day/week totals and log
        prose are never used.
        """

        if not self.recovery_pending:
            return None
        if not self._recovery_pending:
            total = max(0, int(self._session_accumulated_seconds))
            recorded = min(
                total,
                max(0, int(self._analytics_recorded_session_seconds)),
            )
            missing = total - recorded
            ended_at = self._last_trusted_checkpoint_at
            if missing <= 0 or ended_at is None:
                return None
            return (
                total,
                str(self._session_id or ""),
                ended_at - timedelta(seconds=missing),
            )
        return (
            max(0, int(self._session_accumulated_seconds)),
            str(self._session_id or ""),
            self._running_started_at or self._last_trusted_checkpoint_at,
        )

    def complete_recovery_seal(self) -> bool:
        """Mark a restart checkpoint sealed without auto-resuming work."""

        if not self._recovery_pending:
            # A paused-gap repair is complete when mark_analytics_recorded()
            # durably advances the cursor; no timer lifecycle flag changes.
            return not self.recovery_pending
        self._recovery_pending = False
        self._recovered_active_session = False
        self._running_started_at = None
        self._state = WORK_STATE_PAUSED_RESTART
        self._pause_reason = "restart_safe_seal"
        self._last_update_reason = "restart_safe_seal"
        self._last_trusted_checkpoint_at = self._last_trusted_checkpoint_at or self._now()
        self._save()
        return True

    def _elapsed_at(self, reference_at: datetime | None = None) -> int:
        """Return observed running seconds at ``reference_at`` if supplied."""

        if not self.is_running:
            return 0
        if reference_at is None:
            return self._current_elapsed()
        reference = reference_at
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=BEIJING_TIMEZONE)
        else:
            reference = reference.astimezone(BEIJING_TIMEZONE)
        current = self._now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=BEIJING_TIMEZONE)
        else:
            current = current.astimezone(BEIJING_TIMEZONE)
        reference = min(reference, current)
        anchor = self._last_trusted_checkpoint_at or self._running_started_at
        if anchor is None:
            return self._current_elapsed()
        elapsed = max(0, int((reference - anchor).total_seconds()))
        return min(elapsed, self._current_elapsed())

    def _rollover_if_needed(
        self,
        reference_at: datetime | None = None,
        *,
        persist: bool = True,
    ) -> None:
        """Refresh the daily projection without changing FocusSession identity.

        Beijing midnight is an analytics boundary only.  The active logical
        session and its wall-clock start remain unchanged, so a later pause
        can persist one valid cross-midnight FocusSegment.
        """

        today = self._today_key()
        if reference_at is not None:
            reference = reference_at
            if reference.tzinfo is None:
                reference = reference.replace(tzinfo=BEIJING_TIMEZONE)
            else:
                reference = reference.astimezone(BEIJING_TIMEZONE)
            # A delayed pause can be discovered after midnight but have an
            # effective cutoff before midnight.  Do not advance the daily
            # projection before that cutoff is applied.
            if reference.date().isoformat() == self._date_key:
                today = self._date_key
        if today == self._date_key:
            return
        was_running = self.is_running
        had_active_session = self._session_active
        current = self._now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=BEIJING_TIMEZONE)
        else:
            current = current.astimezone(BEIJING_TIMEZONE)
        boundary = datetime.combine(current.date(), datetime.min.time(), tzinfo=BEIJING_TIMEZONE)
        elapsed = self._elapsed_at(reference_at) if was_running else 0
        # ``_running_since`` is reset at checkpoints.  The monotonic delta is
        # authoritative for the observed work; the wall clock only tells the
        # daily projection how much of that delta belongs after midnight.
        post_midnight_seconds = (
            min(elapsed, max(0, int((current - boundary).total_seconds())))
            if was_running
            else 0
        )
        self._date_key = today
        if was_running:
            self._lifetime_seconds += elapsed
            self._session_accumulated_seconds += elapsed
            self._episode_accumulated_seconds += elapsed
        self._accumulated_seconds = post_midnight_seconds
        self._session_active = had_active_session
        self._running_since = self._monotonic() if was_running else None
        if was_running:
            self._state = WORK_STATE_WORKING
            self._pause_reason = None
            self._last_trusted_checkpoint_at = current
        elif not had_active_session:
            self._accumulated_seconds = 0
            self._state = WORK_STATE_IDLE
            self._pause_reason = None
        self._last_checkpoint = self._monotonic()
        self._last_reminder_key = None
        if persist:
            self._save()

    def _current_elapsed(self) -> int:
        """返回当前未落盘工作段的完整秒数。"""

        if self._running_since is None:
            return 0
        return max(0, int(self._monotonic() - self._running_since))

    def _mutable_state_snapshot(self) -> dict[str, object]:
        """Capture timer state so a failed durable transition can roll back."""

        return {
            "_date_key": self._date_key,
            "_accumulated_seconds": self._accumulated_seconds,
            "_lifetime_seconds": self._lifetime_seconds,
            "_session_accumulated_seconds": self._session_accumulated_seconds,
            "_episode_accumulated_seconds": self._episode_accumulated_seconds,
            "_session_id": self._session_id,
            "_session_active": self._session_active,
            "_running_since": self._running_since,
            "_running_started_at": self._running_started_at,
            "_last_trusted_checkpoint_at": self._last_trusted_checkpoint_at,
            "_analytics_recorded_session_seconds": self._analytics_recorded_session_seconds,
            "_state": self._state,
            "_pause_reason": self._pause_reason,
            "_last_update_reason": self._last_update_reason,
            "_last_checkpoint": self._last_checkpoint,
            "_last_reminder_key": self._last_reminder_key,
            "_recovered_active_session": self._recovered_active_session,
            "_recovery_pending": self._recovery_pending,
        }

    def _restore_mutable_state(self, snapshot: dict[str, object]) -> None:
        """Restore a pre-transition state after local persistence fails."""

        for name, value in snapshot.items():
            setattr(self, name, value)

    def today_seconds(self) -> int:
        """返回当天累计工作秒数，包括当前运行段。"""

        self._rollover_if_needed()
        return self._accumulated_seconds + self._current_elapsed()

    def session_seconds(self) -> int:
        """返回本轮工作 Session 秒数，暂停/继续期间保持累计。"""

        self._rollover_if_needed()
        return self._session_accumulated_seconds + self._current_elapsed()

    def session_seconds_at(self, end_at: datetime | None = None) -> int:
        """Return the current logical session duration at a trusted cutoff.

        Delayed idle/power callbacks must seal at the observed boundary, not
        at the later GUI callback time.  This read-only helper lets the owner
        persist the raw fact before calling :meth:`pause`.
        """

        self._rollover_if_needed(end_at, persist=end_at is None)
        if not self.is_running or end_at is None or self._running_started_at is None:
            return self.session_seconds()
        cutoff = end_at
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=BEIJING_TIMEZONE)
        else:
            cutoff = cutoff.astimezone(BEIJING_TIMEZONE)
        current = self._now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=BEIJING_TIMEZONE)
        else:
            current = current.astimezone(BEIJING_TIMEZONE)
        cutoff = min(cutoff, current)
        current_total = self._session_accumulated_seconds + self._current_elapsed()
        return min(current_total, self._session_accumulated_seconds + self._elapsed_at(cutoff))

    def current_elapsed_seconds(self) -> int:
        """Return only the currently running segment.

        A FocusSession can contain several paused/checkpointed segments.  A
        report or presence heartbeat must not mistake that cumulative session
        total for the interval that is currently running.
        """

        self._rollover_if_needed()
        return self._current_elapsed()

    def current_segment_started_at(self) -> datetime | None:
        """Return the wall-clock start of the currently running segment."""

        self._rollover_if_needed()
        if not self.is_running:
            return None
        return self._running_started_at

    @property
    def focus_session_id(self) -> str:
        """Stable identifier for the current session across app restarts."""

        if self._session_id:
            return self._session_id
        if self._session_active:
            self._session_id = uuid.uuid4().hex
            self._save()
        return self._session_id

    def analytics_recorded_session_seconds(self) -> int:
        """Return the cumulative session seconds already written to analytics."""

        return min(
            max(0, int(self.session_seconds())),
            max(0, int(self._analytics_recorded_session_seconds)),
        )

    def mark_analytics_recorded(self, seconds: int) -> None:
        """Persist the analytics cursor after a segment is committed."""

        total = max(0, int(seconds))
        self._analytics_recorded_session_seconds = max(
            self._analytics_recorded_session_seconds,
            total,
        )
        self._save()

    def episode_seconds(self) -> int:
        """Return uninterrupted WORKING seconds since the last pause/resume."""

        self._rollover_if_needed()
        return self._episode_accumulated_seconds + self._current_elapsed()

    def lifetime_seconds(self) -> int:
        """返回累计工作秒数，包括当前尚未落盘的一段。"""

        return self._lifetime_seconds + self._current_elapsed()

    def merge_remote_state(
        self,
        *,
        today_seconds: int = 0,
        lifetime_seconds: int = 0,
        date_key: str | None = None,
    ) -> bool:
        """Merge a server snapshot without counting the same work twice.

        The same account may be open on more than one computer.  The server
        stores the greatest confirmed totals, while this model keeps the
        currently running monotonic segment separate.  Therefore a remote
        snapshot can raise local totals, but can never reset them or add the
        live segment a second time.
        """

        self._rollover_if_needed()
        # A server snapshot can legitimately straddle midnight (and older
        # deployments used UTC for the profile date).  A date mismatch means
        # the daily bucket must not be merged into today's counter, but the
        # account-wide lifetime total is still authoritative and is needed to
        # calculate cross-device wardrobe unlocks.
        date_matches = not date_key or str(date_key)[:10] == self._date_key
        remote_today = max(0, int(today_seconds or 0))
        remote_lifetime = max(0, int(lifetime_seconds or 0))
        elapsed = self._current_elapsed()
        local_today = self._accumulated_seconds + elapsed
        local_lifetime = self._lifetime_seconds + elapsed
        target_lifetime = max(local_lifetime, remote_lifetime)
        changed = False
        if date_matches:
            target_today = max(local_today, remote_today)
            if target_today > local_today:
                self._accumulated_seconds = max(0, target_today - elapsed)
                changed = True
        if target_lifetime > local_lifetime:
            self._lifetime_seconds = max(0, target_lifetime - elapsed)
            changed = True
        if changed:
            self._save()
        return changed

    def reconcile_today_seconds(self, recorded_seconds: int) -> bool:
        """Repair a stale daily aggregate from the local raw focus ledger.

        Older builds could persist a server-side maximum in the timer file.
        When the account has a trustworthy local analytics record, the raw
        ledger is the only safe baseline; the currently running monotonic
        segment remains separate and is still added by ``today_seconds()``.
        """

        self._rollover_if_needed()
        target = max(0, min(24 * 60 * 60, int(recorded_seconds or 0)))
        elapsed = self._current_elapsed()
        desired_accumulated = max(0, target - elapsed) if self.is_running else target
        if desired_accumulated == self._accumulated_seconds:
            return False
        self._accumulated_seconds = desired_accumulated
        self._save()
        return True

    def unlocked_outfit_count(self) -> int:
        """每累计一小时解锁一套娃衣，最多十二套。"""

        return min(12, self.lifetime_seconds() // 3600)

    def take_new_outfit_unlock(self) -> int | None:
        """首次跨过整小时门槛时返回从一开始的解锁序号。"""

        count = self.unlocked_outfit_count()
        if count <= self._notified_outfit_count:
            return None
        self._notified_outfit_count = count
        self._save()
        return count

    def start(self) -> bool:
        """开始或继续新的工作段；已运行时返回 False。"""

        self._rollover_if_needed()
        if self.is_running:
            return False
        if self._recovery_pending:
            LOGGER.error("refusing start until restart recovery is durably sealed")
            return False
        snapshot = self._mutable_state_snapshot()
        try:
            now = self._monotonic()
            # Every explicit resume starts a new logical FocusSession.  A paused
            # session has already gone through the durable seal pipeline; keeping
            # its id would make later segments look like one mutable fact.
            resuming = self._session_active
            self._session_accumulated_seconds = 0
            self._session_id = uuid.uuid4().hex
            self._analytics_recorded_session_seconds = 0
            self._last_reminder_key = None
            self._session_active = True
            self._state = WORK_STATE_WORKING
            self._pause_reason = None
            self._last_update_reason = "manual_resume" if resuming else "new_focus"
            # A resume starts a new uninterrupted work episode.  A newly created
            # session also starts at zero; crash recovery leaves the saved episode
            # intact because this method is not called during __init__.
            self._episode_accumulated_seconds = 0
            self._running_since = now
            current = self._now()
            if current.tzinfo is None:
                current = current.replace(tzinfo=BEIJING_TIMEZONE)
            self._running_started_at = current.astimezone(BEIJING_TIMEZONE)
            self._last_trusted_checkpoint_at = self._running_started_at
            self._last_checkpoint = now
            self._last_reminder_key = None
            self._recovered_active_session = False
            self._save()
            return True
        except Exception:
            self._restore_mutable_state(snapshot)
            raise

    def pause(
        self,
        reason: str = "manual",
        *,
        effective_end_at: datetime | None = None,
    ) -> bool:
        """Pause the current episode at the real observed cutoff.

        ``effective_end_at`` is used by delayed idle polling and native power
        events.  It prevents the GUI callback's discovery time from adding
        seconds after the user actually became idle or the system suspended.
        """

        snapshot = self._mutable_state_snapshot()
        try:
            self._rollover_if_needed(effective_end_at, persist=False)
            if not self.is_running:
                self._save()
                return False
            elapsed = self._current_elapsed()
            if effective_end_at is not None and self._running_started_at is not None:
                current = self._now()
                if current.tzinfo is None:
                    current = current.replace(tzinfo=BEIJING_TIMEZONE)
                else:
                    current = current.astimezone(BEIJING_TIMEZONE)
                cutoff = effective_end_at
                if cutoff.tzinfo is None:
                    cutoff = cutoff.replace(tzinfo=BEIJING_TIMEZONE)
                else:
                    cutoff = cutoff.astimezone(BEIJING_TIMEZONE)
                cutoff = min(cutoff, current)
                episode_total_at_cutoff = self._episode_accumulated_seconds + self._elapsed_at(cutoff)
                current_episode_total = self._episode_accumulated_seconds + elapsed
                episode_total_at_cutoff = min(episode_total_at_cutoff, current_episode_total)
                elapsed = max(
                    0,
                    episode_total_at_cutoff - self._episode_accumulated_seconds,
                )
            self._accumulated_seconds += elapsed
            self._lifetime_seconds += elapsed
            self._session_accumulated_seconds += elapsed
            self._episode_accumulated_seconds += elapsed
            self._session_active = True
            self._running_since = None
            self._running_started_at = None
            clean_reason = str(reason or "manual").strip().casefold()
            self._pause_reason = clean_reason or "manual"
            self._last_update_reason = self._pause_reason
            self._state = _PAUSE_STATE_BY_REASON.get(
                self._pause_reason, WORK_STATE_PAUSED_MANUAL
            )
            current_checkpoint = effective_end_at or self._now()
            if current_checkpoint.tzinfo is None:
                current_checkpoint = current_checkpoint.replace(tzinfo=BEIJING_TIMEZONE)
            self._last_trusted_checkpoint_at = current_checkpoint.astimezone(BEIJING_TIMEZONE)
            self._last_reminder_key = None
            self._recovered_active_session = False
            self._save()
            return True
        except Exception:
            self._restore_mutable_state(snapshot)
            raise

    def finish(self) -> int:
        """完成当前工作段并返回今天累计秒数。"""

        if self.is_running:
            self.pause()
        total = self.today_seconds()
        self._session_active = False
        self._session_accumulated_seconds = 0
        self._episode_accumulated_seconds = 0
        self._session_id = ""
        self._analytics_recorded_session_seconds = 0
        self._running_started_at = None
        self._last_trusted_checkpoint_at = None
        self._recovery_pending = False
        self._state = WORK_STATE_IDLE
        self._pause_reason = None
        self._last_update_reason = "finish"
        self._last_reminder_key = None
        self._save()
        return total

    def checkpoint(self, minimum_interval_seconds: int = 60) -> bool:
        """运行中按最小间隔保存进度，避免频繁写盘。"""

        self._rollover_if_needed()
        if not self.is_running:
            return False
        now = self._monotonic()
        if now - self._last_checkpoint < max(1, minimum_interval_seconds):
            return False
        elapsed = self._current_elapsed()
        self._accumulated_seconds += elapsed
        self._lifetime_seconds += elapsed
        self._session_accumulated_seconds += elapsed
        self._episode_accumulated_seconds += elapsed
        self._running_since = now
        self._last_checkpoint = now
        checkpoint_at = self._now()
        if checkpoint_at.tzinfo is None:
            checkpoint_at = checkpoint_at.replace(tzinfo=BEIJING_TIMEZONE)
        self._last_trusted_checkpoint_at = checkpoint_at.astimezone(BEIJING_TIMEZONE)
        self._last_update_reason = "checkpoint"
        self._save()
        return True

    def status_text(self) -> str:
        """返回带运行状态的今日工作时长。"""

        suffix = " · 正在计时" if self.is_running else " · 已暂停"
        return f"今日工作 {format_work_duration(self.today_seconds())}{suffix}"

    def take_due_reminder(self, seconds: int | None = None) -> str | None:
        """在连续工作达到提醒阈值时返回一次提醒类型。

        Callers that already have the reconciled FocusSession snapshot may
        provide its canonical continuous value.  This prevents a stale timer
        checkpoint from firing a two-hour reminder after the shared day total
        has been corrected to a smaller value.
        """

        if not self.is_running:
            return None
        # Reminder thresholds describe one uninterrupted stretch, not the
        # cumulative value carried across pause/resume or checkpoint writes.
        measured = self.episode_seconds() if seconds is None else max(0, int(seconds))
        minutes = measured // 60
        if minutes >= 90:
            reminder_key = f"long-{(minutes - 90) // 45}"
            reminder_kind = "long_break"
        elif minutes >= 50:
            reminder_key = "break-50"
            reminder_kind = "break"
        elif minutes >= 25:
            reminder_key = "focus-25"
            reminder_kind = "focus"
        else:
            return None
        if reminder_key == self._last_reminder_key:
            return None
        self._last_reminder_key = reminder_key
        return reminder_kind

    def _save(self) -> None:
        """原子保存日期和累计秒数，不写入任何工作内容。"""

        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        data = {
            "account_id": self._account_id or "",
            "date": self._date_key,
            "accumulated_seconds": max(0, int(self._accumulated_seconds)),
            "lifetime_seconds": max(0, int(self._lifetime_seconds)),
            "notified_outfit_count": max(0, int(self._notified_outfit_count)),
            "running": self.is_running,
            "session_active": self._session_active,
            "session_accumulated_seconds": max(0, int(self._session_accumulated_seconds)),
            "episode_accumulated_seconds": max(0, int(self._episode_accumulated_seconds)),
            "session_id": self._session_id,
            "analytics_recorded_session_seconds": max(
                0, int(self._analytics_recorded_session_seconds)
            ),
            "running_started_at": self._running_started_at.isoformat()
            if self._running_started_at is not None else None,
            "last_trusted_checkpoint_at": self._last_trusted_checkpoint_at.isoformat()
            if self._last_trusted_checkpoint_at is not None else None,
            "active_session_schema": 3,
            "device_id": self._device_id,
            "state": self._state,
            "pause_reason": self._pause_reason,
            "last_update_reason": self._last_update_reason,
            "last_saved_at": self._now().isoformat(),
            "updated_at": self._now().isoformat(),
        }
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
