"""Single source of truth for local focus state shared by the pet and study room.

The study room never owns a second timer.  It receives a lightweight snapshot
from this manager and may ask the desktop pet to start, pause, or finish the
same local :class:`WorkTimerModel` session.

日度和周度展示使用窗口提供的同一份日历周期投影；计时器仍只负责本地
工作状态，避免自习室、工作报告和桌面提示各自维护一套累计时间。
快照同时提供当前连续工作段，提醒与显示均从同一份快照取值。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from PySide6.QtCore import QObject, Signal

from .focus_analytics import BEIJING_TIMEZONE
from .work_timer import WorkTimerModel


@dataclass(frozen=True)
class FocusSessionSnapshot:
    status: str
    session_seconds: int
    today_seconds: int
    session_started_at: str | None
    room_id: str | None = None
    state: str = "idle"
    pause_reason: str | None = None
    # Reconciled calendar totals. ``None`` means the caller did not provide a
    # shared projection, preserving the lightweight timer-only API for tests
    # and integrations that do not own FocusAnalyticsStore.
    week_seconds: int | None = None
    # Seconds worked in the current uninterrupted episode.  This is kept
    # alongside the cumulative session value so reminders cannot mistake a
    # checkpointed/resumed session for one continuous stretch of work.
    current_continuous_seconds: int = 0

    @property
    def is_running(self) -> bool:
        return self.status == "focus"

    @classmethod
    def from_timer(
        cls,
        timer: WorkTimerModel,
        *,
        room_id: str | None = None,
        now: datetime | None = None,
        resting: bool = False,
    ) -> "FocusSessionSnapshot":
        session_seconds = timer.session_seconds()
        today_seconds = timer.today_seconds()
        current_continuous_seconds = timer.episode_seconds() if timer.is_running else 0
        started_at = None
        if timer.is_running:
            current = now or datetime.now(BEIJING_TIMEZONE)
            if current.tzinfo is None:
                current = current.replace(tzinfo=BEIJING_TIMEZONE)
            else:
                current = current.astimezone(BEIJING_TIMEZONE)
            # ``session_seconds`` includes earlier segments separated by a
            # pause/checkpoint.  The timer keeps the wall-clock start of the
            # current uninterrupted segment separately, so a checkpoint does
            # not collapse the live chart to ``now–now``.  The monotonic
            # fallback is retained for old timer files without this field.
            segment_started = timer.current_segment_started_at()
            if segment_started is None:
                segment_started = current - timedelta(seconds=timer.current_elapsed_seconds())
            started_at = segment_started.astimezone(BEIJING_TIMEZONE).isoformat()
        status = "focus" if timer.is_running else (
            "rest" if timer.has_active_session or resting else "idle"
        )
        return cls(
            status,
            session_seconds,
            today_seconds,
            started_at,
            room_id,
            timer.state,
            timer.pause_reason,
            current_continuous_seconds=current_continuous_seconds,
        )


class FocusSessionManager(QObject):
    """Wrap the existing timer and publish changes to interested windows."""

    changed = Signal(object)

    def __init__(self, timer: WorkTimerModel, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.timer = timer
        self._room_id: str | None = None
        self._resting = bool(timer.has_active_session and not timer.is_running)
        # The timer remains the owner of session state. A caller may provide
        # a reconciled calendar-day projection so every surface displays the
        # same day total without introducing a second timer or mutating it.
        self._today_seconds_provider: Callable[[], int | None] | None = None
        self._period_seconds_provider: Callable[[], Mapping[str, int | None] | None] | None = None

    def set_today_seconds_provider(
        self, provider: Callable[[], int | None] | None
    ) -> None:
        """Use one optional calendar-day projection for all FocusSession views."""

        self._today_seconds_provider = provider
        self.refresh()

    def set_period_seconds_provider(
        self,
        provider: Callable[[], Mapping[str, int | None] | None] | None,
    ) -> None:
        """Provide one reconciled calendar-day/week projection for all views."""

        self._period_seconds_provider = provider
        self.refresh()

    @property
    def room_id(self) -> str | None:
        return self._room_id

    def set_room_id(self, room_id: str | None) -> None:
        clean = str(room_id).strip() if room_id else None
        self._room_id = clean or None
        self.refresh()

    def switch_account(self, account_id: str | None) -> bool:
        """切换计时账号并清空当前账号的房间上下文。"""

        changed = self.timer.switch_account(account_id)
        self._room_id = None
        self._resting = False
        self.refresh()
        return changed

    def snapshot(self, *, include_projection: bool = True) -> FocusSessionSnapshot:
        """Return the current timer snapshot.

        The optional calendar projection is deliberately skipped by the
        one-second GUI tick.  Projection providers may aggregate local
        history and perform file I/O; they are for reports/sync boundaries,
        not for keeping a clock label up to date.
        """
        snapshot = FocusSessionSnapshot.from_timer(
            self.timer,
            room_id=self._room_id,
            resting=self._resting,
        )
        if not include_projection:
            return snapshot
        provider = self._today_seconds_provider
        period_provider = self._period_seconds_provider
        projected_today = None
        projected_week = None
        if period_provider is not None:
            try:
                values = period_provider()
            except (AttributeError, TypeError, ValueError, OverflowError):
                values = None
            if isinstance(values, Mapping):
                projected_today = values.get("today_seconds", values.get("today"))
                projected_week = values.get("week_seconds", values.get("week"))
        if projected_today is None and provider is not None:
            try:
                projected_today = provider()
            except (AttributeError, TypeError, ValueError, OverflowError):
                projected_today = None

        normalized_today = None
        normalized_week = None
        for value_name, value in (("today", projected_today), ("week", projected_week)):
            if value is None:
                continue
            try:
                normalized = max(0, int(value))
            except (TypeError, ValueError, OverflowError):
                continue
            if value_name == "today":
                normalized_today = normalized
            else:
                normalized_week = normalized

        if normalized_today is not None:
            # A recovered timer can contain stale cumulative checkpoints from
            # before the analytics cursor was introduced. It is impossible
            # for the current session to exceed the same calendar day's total;
            # cap that one field while retaining the real session semantics
            # whenever it is smaller than today's total.
            session_seconds = min(snapshot.session_seconds, normalized_today)
            current_continuous_seconds = min(
                max(0, int(snapshot.current_continuous_seconds)),
                normalized_today,
            )
            snapshot = replace(
                snapshot,
                session_seconds=session_seconds,
                today_seconds=normalized_today,
                week_seconds=normalized_week,
                current_continuous_seconds=current_continuous_seconds,
            )
        elif normalized_week is not None:
            snapshot = replace(snapshot, week_seconds=normalized_week)
        return snapshot

    def refresh(self, *, include_projection: bool = True) -> FocusSessionSnapshot:
        snapshot = self.snapshot(include_projection=include_projection)
        self.changed.emit(snapshot)
        return snapshot

    def start(self) -> bool:
        changed = self.timer.start()
        if changed:
            self._resting = False
        self.refresh()
        return changed

    def pause(self, reason: str = "manual") -> bool:
        changed = self.timer.pause(reason)
        if changed:
            self._resting = True
        self.refresh()
        return changed

    def resume(self) -> bool:
        """Resume only after an explicit user action."""

        return self.start()

    def finish(self) -> int:
        total = self.timer.finish()
        self._resting = False
        self.refresh()
        return total
