"""Single source of truth for local focus state shared by the pet and study room.

The study room never owns a second timer.  It receives a lightweight snapshot
from this manager and may ask the desktop pet to start, pause, or finish the
same local :class:`WorkTimerModel` session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from PySide6.QtCore import QObject, Signal

from .work_timer import WorkTimerModel


@dataclass(frozen=True)
class FocusSessionSnapshot:
    status: str
    session_seconds: int
    today_seconds: int
    session_started_at: str | None
    room_id: str | None = None

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
        started_at = None
        if timer.is_running:
            current = now or datetime.now().astimezone()
            started_at = (current - timedelta(seconds=session_seconds)).isoformat()
        status = "focus" if timer.is_running else ("rest" if resting else "idle")
        return cls(status, session_seconds, today_seconds, started_at, room_id)


class FocusSessionManager(QObject):
    """Wrap the existing timer and publish changes to interested windows."""

    changed = Signal(object)

    def __init__(self, timer: WorkTimerModel, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.timer = timer
        self._room_id: str | None = None
        self._resting = bool(timer.today_seconds() and not timer.is_running)

    @property
    def room_id(self) -> str | None:
        return self._room_id

    def set_room_id(self, room_id: str | None) -> None:
        clean = str(room_id).strip() if room_id else None
        self._room_id = clean or None
        self.refresh()

    def snapshot(self) -> FocusSessionSnapshot:
        return FocusSessionSnapshot.from_timer(
            self.timer,
            room_id=self._room_id,
            resting=self._resting,
        )

    def refresh(self) -> FocusSessionSnapshot:
        snapshot = self.snapshot()
        self.changed.emit(snapshot)
        return snapshot

    def start(self) -> bool:
        changed = self.timer.start()
        if changed:
            self._resting = False
        self.refresh()
        return changed

    def pause(self) -> bool:
        changed = self.timer.pause()
        if changed:
            self._resting = True
        self.refresh()
        return changed

    def finish(self) -> int:
        total = self.timer.finish()
        self._resting = False
        self.refresh()
        return total
