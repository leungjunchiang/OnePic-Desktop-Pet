"""
本模块提供 Lili 的本地工作计时与温和休息提醒，不创建窗口或访问网络。

职责范围：
- 记录当天和终身累计工作秒数，并在日期变化时自动开始新一天；
- 支持开始、暂停、完成、状态格式化和运行中定期落盘；
- 只在本机应用数据目录保存日期与累计秒数，不保存任务名称或聊天内容；
- 按单次连续工作时长产生 25 分钟鼓励、50 分钟休息和更长时段劝慰提醒。

计时使用单调时钟避免系统时间微调造成跳变；开始和自动检查点都会保存“仍在工作”的标记。
异常退出后下次启动恢复到最近一次已保存的计时点，但不会把应用关闭期间的离线时间误算为工作时间。
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path


# Persisted values are intentionally plain strings so older installations can
# read the file without importing a new enum.  The UI may present a simpler
# "正在工作 / 已暂停", while these values keep the reason auditable.
WORK_STATE_IDLE = "idle"
WORK_STATE_WORKING = "working"
WORK_STATE_PAUSED_MANUAL = "paused_manual"
WORK_STATE_PAUSED_IDLE = "paused_idle"
WORK_STATE_PAUSED_LOCK = "paused_lock"
WORK_STATE_PAUSED_SLEEP = "paused_sleep"
WORK_STATE_PAUSED_VIDEO = "paused_video"

# Focus statistics use one calendar everywhere.  A fixed offset is deliberate:
# Beijing has no daylight-saving transition, and the social backend uses the
# same Asia/Shanghai boundary for its daily and weekly ledgers.
BEIJING_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")

_PAUSE_STATE_BY_REASON = {
    "manual": WORK_STATE_PAUSED_MANUAL,
    "idle": WORK_STATE_PAUSED_IDLE,
    "idle_10m": WORK_STATE_PAUSED_IDLE,
    "lock": WORK_STATE_PAUSED_LOCK,
    "sleep": WORK_STATE_PAUSED_SLEEP,
    "fullscreen_video": WORK_STATE_PAUSED_VIDEO,
    "video": WORK_STATE_PAUSED_VIDEO,
    "account_switch": WORK_STATE_PAUSED_MANUAL,
}


def _local_data_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) if base else Path.home() / ".desktop_pet"


def _account_storage_key(account_id: str | None) -> str:
    """将 Supabase user id 转成安全、稳定的本地目录名。"""

    value = str(account_id or "").strip().casefold()
    if not value:
        return "anonymous"
    value = re.sub(r"[^a-z0-9._-]", "_", value)
    return value[:80] or "anonymous"


def work_timer_path(account_id: str | None = None) -> Path:
    """返回按账号隔离的本地工作计时文件路径。

    旧版本把所有账号写进 ``Lili/work_timer.json``。该文件不再自动
    迁移或读取，避免登录另一个账号时把旧账号的累计时长再次上传。
    """

    return (
        _local_data_root()
        / "Lili"
        / "accounts"
        / _account_storage_key(account_id)
        / "work_timer.json"
    )


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


class WorkTimerModel:
    """维护单个用户的今日累计时长和当前连续工作时段。"""

    def __init__(
        self,
        path: Path | None = None,
        now_provider: Callable[[], datetime] | None = None,
        monotonic_provider: Callable[[], float] | None = None,
        *,
        persist: bool = True,
    ) -> None:
        # Demo/offscreen windows must not read or mutate the user's real
        # session.  Production callers keep the historical persistent default.
        self.persist = bool(persist)
        self._uses_account_storage = path is None and self.persist
        self._account_id = "" if self._uses_account_storage else None
        self.path = (path or work_timer_path()) if self.persist else None
        self._now = now_provider or (lambda: datetime.now(BEIJING_TIMEZONE))
        self._monotonic = monotonic_provider or time.monotonic
        self._date_key = self._today_key()
        self._accumulated_seconds = 0
        self._lifetime_seconds = 0
        self._notified_outfit_count = 0
        self._session_accumulated_seconds = 0
        self._episode_accumulated_seconds = 0
        self._session_active = False
        self._running_since: float | None = None
        self._state = WORK_STATE_IDLE
        self._pause_reason: str | None = None
        self._last_checkpoint = self._monotonic()
        self._last_reminder_key: str | None = None
        self._recovered_active_session = False
        self._load()

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
            self.pause("account_switch")
        self.path = target
        self._account_id = target_id
        self._reset_in_memory_state()
        self._load()
        return True

    def _reset_in_memory_state(self) -> None:
        self._date_key = self._today_key()
        self._accumulated_seconds = 0
        self._lifetime_seconds = 0
        self._notified_outfit_count = 0
        self._session_accumulated_seconds = 0
        self._episode_accumulated_seconds = 0
        self._session_active = False
        self._running_since = None
        self._state = WORK_STATE_IDLE
        self._pause_reason = None
        self._last_checkpoint = self._monotonic()
        self._last_reminder_key = None
        self._recovered_active_session = False

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
        """读取累计秒数；崩溃后恢复最近保存的运行状态。"""

        if self.path is None:
            return
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._lifetime_seconds = max(0, int(data.get("lifetime_seconds", 0)))
            self._notified_outfit_count = max(0, int(data.get("notified_outfit_count", 0)))
            same_date = data.get("date") == self._date_key
            seconds = int(data.get("accumulated_seconds", 0)) if same_date else 0
            session_seconds = (
                max(0, int(data.get("session_accumulated_seconds", 0)))
                if same_date else 0
            )
            episode_seconds = (
                max(0, int(data.get("episode_accumulated_seconds", 0)))
                if same_date else 0
            )
            saved_running = bool(data.get("running", False)) and same_date
            saved_session_active = bool(
                data.get("session_active", saved_running or session_seconds > 0)
            ) and same_date
            saved_state = str(data.get("state") or "").strip()
            if saved_running:
                saved_state = WORK_STATE_WORKING
            elif saved_session_active and saved_state not in {
                WORK_STATE_PAUSED_MANUAL,
                WORK_STATE_PAUSED_IDLE,
                WORK_STATE_PAUSED_LOCK,
                WORK_STATE_PAUSED_SLEEP,
                WORK_STATE_PAUSED_VIDEO,
            }:
                # Old files only had session_active/running.  Treat a paused
                # old session as a manual pause rather than auto-resuming it.
                saved_state = WORK_STATE_PAUSED_MANUAL
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return
        self._accumulated_seconds = max(0, seconds)
        self._session_accumulated_seconds = session_seconds
        self._episode_accumulated_seconds = episode_seconds
        self._session_active = saved_session_active
        self._state = saved_state if same_date else WORK_STATE_IDLE
        self._pause_reason = (
            str(data.get("pause_reason") or "manual")
            if self._state != WORK_STATE_WORKING and self._session_active
            else None
        )
        if saved_running:
            # Monotonic clocks are process-local. Resume from the last
            # checkpoint instead of counting the period while the app was down.
            now = self._monotonic()
            self._running_since = now
            self._last_checkpoint = now
            self._recovered_active_session = True

    def _rollover_if_needed(self) -> None:
        """日期变化时清空昨日累计，并保持运行状态从当前时刻重新计时。"""

        today = self._today_key()
        if today == self._date_key:
            return
        was_running = self.is_running
        self._date_key = today
        self._accumulated_seconds = 0
        self._session_accumulated_seconds = 0
        self._session_active = False
        self._running_since = self._monotonic() if was_running else None
        self._episode_accumulated_seconds = 0
        self._state = WORK_STATE_IDLE
        self._pause_reason = None
        self._last_checkpoint = self._monotonic()
        self._last_reminder_key = None
        self._recovered_active_session = False
        self._save()

    def _current_elapsed(self) -> int:
        """返回当前未落盘工作段的完整秒数。"""

        if self._running_since is None:
            return 0
        return max(0, int(self._monotonic() - self._running_since))

    def today_seconds(self) -> int:
        """返回当天累计工作秒数，包括当前运行段。"""

        self._rollover_if_needed()
        return self._accumulated_seconds + self._current_elapsed()

    def session_seconds(self) -> int:
        """返回本轮工作 Session 秒数，暂停/继续期间保持累计。"""

        self._rollover_if_needed()
        return self._session_accumulated_seconds + self._current_elapsed()

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
        if date_key and str(date_key)[:10] != self._date_key:
            return False
        remote_today = max(0, int(today_seconds or 0))
        remote_lifetime = max(0, int(lifetime_seconds or 0))
        elapsed = self._current_elapsed()
        local_today = self._accumulated_seconds + elapsed
        local_lifetime = self._lifetime_seconds + elapsed
        target_today = max(local_today, remote_today)
        target_lifetime = max(local_lifetime, remote_lifetime)
        changed = False
        if target_today > local_today:
            self._accumulated_seconds = max(0, target_today - elapsed)
            changed = True
        if target_lifetime > local_lifetime:
            self._lifetime_seconds = max(0, target_lifetime - elapsed)
            changed = True
        if changed:
            self._save()
        return changed

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
        now = self._monotonic()
        if not self._session_active:
            self._session_accumulated_seconds = 0
            self._last_reminder_key = None
        self._session_active = True
        self._state = WORK_STATE_WORKING
        self._pause_reason = None
        # A resume starts a new uninterrupted work episode.  A newly created
        # session also starts at zero; crash recovery leaves the saved episode
        # intact because this method is not called during __init__.
        self._episode_accumulated_seconds = 0
        self._running_since = now
        self._last_checkpoint = now
        self._last_reminder_key = None
        self._recovered_active_session = False
        self._save()
        return True

    def pause(self, reason: str = "manual") -> bool:
        """Pause the current episode and persist the explicit pause reason."""

        self._rollover_if_needed()
        if not self.is_running:
            return False
        elapsed = self._current_elapsed()
        self._accumulated_seconds += elapsed
        self._lifetime_seconds += elapsed
        self._session_accumulated_seconds += elapsed
        self._episode_accumulated_seconds += elapsed
        self._session_active = True
        self._running_since = None
        clean_reason = str(reason or "manual").strip().casefold()
        self._pause_reason = clean_reason or "manual"
        self._state = _PAUSE_STATE_BY_REASON.get(
            self._pause_reason, WORK_STATE_PAUSED_MANUAL
        )
        self._last_reminder_key = None
        self._recovered_active_session = False
        self._save()
        return True

    def finish(self) -> int:
        """完成当前工作段并返回今天累计秒数。"""

        if self.is_running:
            self.pause()
        total = self.today_seconds()
        self._session_active = False
        self._session_accumulated_seconds = 0
        self._episode_accumulated_seconds = 0
        self._state = WORK_STATE_IDLE
        self._pause_reason = None
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
        self._save()
        return True

    def status_text(self) -> str:
        """返回带运行状态的今日工作时长。"""

        suffix = " · 正在计时" if self.is_running else " · 已暂停"
        return f"今日工作 {format_work_duration(self.today_seconds())}{suffix}"

    def take_due_reminder(self) -> str | None:
        """在连续工作达到提醒阈值时返回一次提醒类型。"""

        if not self.is_running:
            return None
        minutes = self.session_seconds() // 60
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
            "date": self._date_key,
            "accumulated_seconds": max(0, int(self._accumulated_seconds)),
            "lifetime_seconds": max(0, int(self._lifetime_seconds)),
            "notified_outfit_count": max(0, int(self._notified_outfit_count)),
            "running": self.is_running,
            "session_active": self._session_active,
            "session_accumulated_seconds": max(0, int(self._session_accumulated_seconds)),
            "episode_accumulated_seconds": max(0, int(self._episode_accumulated_seconds)),
            "state": self._state,
            "pause_reason": self._pause_reason,
        }
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
