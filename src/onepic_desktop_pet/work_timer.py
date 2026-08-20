"""
��ģ���ṩ Lili �ı��ع�����ʱ���º���Ϣ���ѣ����������ڻ�������硣

ְ��Χ��
- ��¼����������ۼƹ����������������ڱ仯ʱ�Զ���ʼ��һ�죻
- ֧�ֿ�ʼ����ͣ����ɡ�״̬��ʽ���������ж������̣�
- ֻ�ڱ���Ӧ������Ŀ¼�����������ۼ��������������������ƻ��������ݣ�
- ��������������ʱ������ 25 ���ӹ�����50 ������Ϣ�͸���ʱ��Ȱο���ѡ�

��ʱʹ�õ���ʱ�ӱ���ϵͳʱ��΢��������䣻��ʼ���Զ����㶼�ᱣ�桰���ڹ������ı�ǡ�
�쳣�˳����´������ָ������һ���ѱ���ļ�ʱ�㣬�������Ӧ�ùر��ڼ������ʱ������Ϊ����ʱ�䡣
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path


# Persisted values are intentionally plain strings so older installations can
# read the file without importing a new enum.  The UI may present a simpler
# "���ڹ��� / ����ͣ", while these values keep the reason auditable.
WORK_STATE_IDLE = "idle"
WORK_STATE_WORKING = "working"
WORK_STATE_PAUSED_MANUAL = "paused_manual"
WORK_STATE_PAUSED_IDLE = "paused_idle"
WORK_STATE_PAUSED_LOCK = "paused_lock"
WORK_STATE_PAUSED_SLEEP = "paused_sleep"
WORK_STATE_PAUSED_VIDEO = "paused_video"

_PAUSE_STATE_BY_REASON = {
    "manual": WORK_STATE_PAUSED_MANUAL,
    "idle": WORK_STATE_PAUSED_IDLE,
    "idle_10m": WORK_STATE_PAUSED_IDLE,
    "lock": WORK_STATE_PAUSED_LOCK,
    "sleep": WORK_STATE_PAUSED_SLEEP,
    "fullscreen_video": WORK_STATE_PAUSED_VIDEO,
    "video": WORK_STATE_PAUSED_VIDEO,
}


def work_timer_path() -> Path:
    """���ص�ǰ�û��ı��ع�����ʱ�ļ�·����"""

    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".desktop_pet"
    current = root / "Lili" / "work_timer.json"
    legacy = root / "SixHairWorkmate" / "work_timer.json"
    if not current.exists() and legacy.exists():
        try:
            current.parent.mkdir(parents=True, exist_ok=True)
            current.write_bytes(legacy.read_bytes())
        except OSError:
            return legacy
    return current


def format_work_duration(seconds: int) -> str:
    """��������ʽ��Ϊ�ʺϲ˵���������ʾ������ʱ����"""

    safe_seconds = max(0, int(seconds))
    if safe_seconds < 60:
        return "����1����" if safe_seconds else "0����"
    total_minutes = safe_seconds // 60
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}Сʱ{minutes}����"
    if hours:
        return f"{hours}Сʱ"
    return f"{minutes}����"


def format_elapsed_clock(seconds: int) -> str:
    """Format a live session duration as mm:ss or h:mm:ss."""

    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


class WorkTimerModel:
    """ά�������û��Ľ����ۼ�ʱ���͵�ǰ��������ʱ�Ρ�"""

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
        self.path = (path or work_timer_path()) if self.persist else None
        self._now = now_provider or datetime.now
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

    @property
    def is_running(self) -> bool:
        """���ص�ǰ�Ƿ����ڼ�ʱ��"""

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
        """���ر������ڼ���"""

        return self._now().date().isoformat()

    def _load(self) -> None:
        """��ȡ�ۼ�������������ָ�������������״̬��"""

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
        """���ڱ仯ʱ��������ۼƣ�����������״̬�ӵ�ǰʱ�����¼�ʱ��"""

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
        """���ص�ǰδ���̹����ε�����������"""

        if self._running_since is None:
            return 0
        return max(0, int(self._monotonic() - self._running_since))

    def today_seconds(self) -> int:
        """���ص����ۼƹ���������������ǰ���жΡ�"""

        self._rollover_if_needed()
        return self._accumulated_seconds + self._current_elapsed()

    def session_seconds(self) -> int:
        """���ر��ֹ��� Session ��������ͣ/�����ڼ䱣���ۼơ�"""

        self._rollover_if_needed()
        return self._session_accumulated_seconds + self._current_elapsed()

    def episode_seconds(self) -> int:
        """Return uninterrupted WORKING seconds since the last pause/resume."""

        self._rollover_if_needed()
        return self._episode_accumulated_seconds + self._current_elapsed()

    def lifetime_seconds(self) -> int:
        """�����ۼƹ���������������ǰ��δ���̵�һ�Ρ�"""

        return self._lifetime_seconds + self._current_elapsed()

    def unlocked_outfit_count(self) -> int:
        """ÿ�ۼ�һСʱ����һ�����£����ʮ���ס�"""

        return min(12, self.lifetime_seconds() // 3600)

    def take_new_outfit_unlock(self) -> int | None:
        """�״ο����Сʱ�ż�ʱ���ش�һ��ʼ�Ľ�����š�"""

        count = self.unlocked_outfit_count()
        if count <= self._notified_outfit_count:
            return None
        self._notified_outfit_count = count
        self._save()
        return count

    def start(self) -> bool:
        """��ʼ������µĹ����Σ�������ʱ���� False��"""

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
        """��ɵ�ǰ�����β����ؽ����ۼ�������"""

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
        """�����а���С���������ȣ�����Ƶ��д�̡�"""

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
        """���ش�����״̬�Ľ��չ���ʱ����"""

        suffix = " �� ���ڼ�ʱ" if self.is_running else " �� ����ͣ"
        return f"���չ��� {format_work_duration(self.today_seconds())}{suffix}"

    def take_due_reminder(self) -> str | None:
        """�����������ﵽ������ֵʱ����һ���������͡�"""

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
        """ԭ�ӱ������ں��ۼ���������д���κι������ݡ�"""

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

