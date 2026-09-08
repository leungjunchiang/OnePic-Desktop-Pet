"""Local OS activity guard for the canonical focus timer.

The guard is deliberately independent from analytics and networking.  It
turns trusted local observations into one-shot pause decisions; the caller
still owns the normal ``FocusSessionManager -> WorkTimerModel`` pause path.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .input_activity import InputIdleSnapshot, get_input_idle_snapshot, system_session_state

LOGGER = logging.getLogger(__name__)

AUTO_PAUSE_IDLE = "idle_10m"
AUTO_PAUSE_LOCK = "lock"
AUTO_PAUSE_DISPLAY_OFF = "display_off"
AUTO_PAUSE_SLEEP = "sleep"

AUTO_PAUSE_REASONS = frozenset(
    {
        AUTO_PAUSE_IDLE,
        AUTO_PAUSE_LOCK,
        AUTO_PAUSE_DISPLAY_OFF,
        AUTO_PAUSE_SLEEP,
    }
)


@dataclass(frozen=True)
class AutoPauseDecision:
    reason: str
    effective_at: datetime
    source: str


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_idle(value: InputIdleSnapshot | float | int | None) -> InputIdleSnapshot:
    if isinstance(value, InputIdleSnapshot):
        return value
    if value is None:
        return InputIdleSnapshot(None, False, "unknown", "provider returned None")
    try:
        return InputIdleSnapshot(max(0.0, float(value)), True, "legacy-provider")
    except (TypeError, ValueError, OverflowError):
        return InputIdleSnapshot(None, False, "unknown", "invalid provider value")


class FocusActivityGuard:
    """Convert local activity observations to at-most-once pause decisions."""

    def __init__(
        self,
        *,
        idle_provider: Callable[[], InputIdleSnapshot | float | int | None] = get_input_idle_snapshot,
        session_provider: Callable[[], dict[str, object]] = system_session_state,
        now_provider: Callable[[], datetime] | None = None,
        monotonic_provider: Callable[[], float] | None = None,
        sleep_gap_seconds: float = 30.0,
    ) -> None:
        self._idle_provider = idle_provider
        self._session_provider = session_provider
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic_provider or time.monotonic
        self._sleep_gap_seconds = max(5.0, float(sleep_gap_seconds))
        self._last_wall: datetime | None = None
        self._last_monotonic: float | None = None
        self._last_snapshot = InputIdleSnapshot(None, False, "not-probed")
        self._last_session: dict[str, object] = {}
        self._last_decision: AutoPauseDecision | None = None
        self._probe_count = 0
        self._resume_gap_effective_at: datetime | None = None
        self._last_probe_status: tuple[bool, str, str | None] | None = None

    def reset_for_new_session(self) -> None:
        """Forget a previous pause latch when the user explicitly resumes."""

        self._last_decision = None
        self._last_wall = None
        self._last_monotonic = None
        self._resume_gap_effective_at = None

    @property
    def last_snapshot(self) -> InputIdleSnapshot:
        return self._last_snapshot

    @property
    def last_session(self) -> dict[str, object]:
        return dict(self._last_session)

    @property
    def probe_count(self) -> int:
        return self._probe_count

    def diagnostics(self) -> dict[str, object]:
        return {
            "idle_seconds": self._last_snapshot.idle_seconds,
            "idle_available": self._last_snapshot.available,
            "input_provider": self._last_snapshot.provider,
            "input_error": self._last_snapshot.error,
            "locked": bool(self._last_session.get("locked")),
            "sleeping": bool(self._last_session.get("sleeping")),
            "display_state": str(self._last_session.get("display_state") or "unknown"),
            "probe_count": self._probe_count,
            "last_decision": self._last_decision.reason if self._last_decision else None,
        }

    def _record_probe(self, now: datetime, monotonic_now: float) -> tuple[datetime, float, float]:
        current_wall = _aware(now)
        current_mono = float(monotonic_now)
        wall_gap = 0.0
        if self._last_wall is not None and self._last_monotonic is not None:
            wall_gap = max(0.0, (current_wall - self._last_wall).total_seconds())
            mono_gap = max(0.0, current_mono - self._last_monotonic)
            # A Qt event-loop gap around a suspend is evidence that the timer
            # must not bridge the gap, even if the first post-wake input makes
            # GetLastInputInfo report zero idle seconds.
            if wall_gap - mono_gap >= self._sleep_gap_seconds:
                self._resume_gap_effective_at = self._last_wall
                self._last_wall = current_wall
                self._last_monotonic = current_mono
                # Keep the pre-gap wall time available to the caller through
                # ``_last_decision``'s effective timestamp; the next probe
                # must not repeatedly classify the same resume gap.
                return current_wall, current_mono, wall_gap
        self._last_wall = current_wall
        self._last_monotonic = current_mono
        return current_wall, current_mono, wall_gap

    def poll(
        self,
        *,
        working: bool,
        auto_pause_on_idle: bool,
        idle_threshold_seconds: int,
    ) -> AutoPauseDecision | None:
        now = self._now()
        monotonic_now = self._monotonic()
        current_wall, current_mono, wall_gap = self._record_probe(now, monotonic_now)
        self._probe_count += 1
        try:
            session = dict(self._session_provider() or {})
        except Exception as exc:
            session = {}
            LOGGER.warning("[focus-activity] session probe unavailable: %s", exc)
        try:
            snapshot = _normalize_idle(self._idle_provider())
        except Exception as exc:
            snapshot = InputIdleSnapshot(None, False, "provider-error", f"{type(exc).__name__}: {exc}")
        self._last_session = session
        self._last_snapshot = snapshot
        probe_status = (snapshot.available, snapshot.provider, snapshot.error)
        if probe_status != self._last_probe_status:
            if snapshot.available:
                LOGGER.info(
                    "[focus-activity] idle provider available provider=%s idle_seconds=%.3f",
                    snapshot.provider,
                    float(snapshot.idle_seconds or 0.0),
                )
            else:
                LOGGER.warning(
                    "[focus-activity] idle provider unavailable provider=%s error=%s",
                    snapshot.provider,
                    snapshot.error,
                )
            self._last_probe_status = probe_status
        if not working:
            self._last_decision = None
            return None

        if wall_gap >= self._sleep_gap_seconds:
            decision = AutoPauseDecision(
                AUTO_PAUSE_SLEEP,
                self._resume_gap_effective_at or current_wall,
                "resume_gap",
            )
            self._resume_gap_effective_at = None
            self._last_decision = decision
            return decision

        if bool(session.get("sleeping")):
            decision = AutoPauseDecision(AUTO_PAUSE_SLEEP, current_wall, "session_probe")
            self._last_decision = decision
            return decision
        if bool(session.get("locked")):
            decision = AutoPauseDecision(AUTO_PAUSE_LOCK, current_wall, "session_probe")
            self._last_decision = decision
            return decision
        if str(session.get("display_state") or "").casefold() == "off":
            decision = AutoPauseDecision(AUTO_PAUSE_DISPLAY_OFF, current_wall, "session_probe")
            self._last_decision = decision
            return decision

        threshold = max(600, int(idle_threshold_seconds))
        if auto_pause_on_idle and snapshot.available and snapshot.idle_seconds is not None:
            if snapshot.idle_seconds >= threshold:
                effective = current_wall - timedelta(
                    seconds=max(0.0, float(snapshot.idle_seconds) - threshold)
                )
                decision = AutoPauseDecision(AUTO_PAUSE_IDLE, effective, snapshot.provider)
                self._last_decision = decision
                return decision
        return None

    def handle_event(
        self,
        kind: str,
        *,
        working: bool,
        at: datetime | None = None,
    ) -> AutoPauseDecision | None:
        """Handle an immediate native event; resume events never resume work."""

        normalized = str(kind or "").strip().casefold()
        if normalized in {"resume", "unlock", "display_on", "display_dimmed"}:
            return None
        reason = {
            "lock": AUTO_PAUSE_LOCK,
            "display_off": AUTO_PAUSE_DISPLAY_OFF,
            "suspend": AUTO_PAUSE_SLEEP,
            "sleep": AUTO_PAUSE_SLEEP,
        }.get(normalized)
        if not working or reason is None:
            return None
        decision = AutoPauseDecision(
            reason,
            _aware(at or self._now()),
            f"native:{normalized}",
        )
        self._last_decision = decision
        return decision


try:
    from PySide6.QtCore import QAbstractNativeEventFilter
except ImportError:  # pragma: no cover - only used in minimal non-Qt tooling
    QAbstractNativeEventFilter = object  # type: ignore[misc,assignment]


class WindowsFocusActivityBridge(QAbstractNativeEventFilter):
    """Best-effort Windows message bridge for lock, power and display events."""

    WM_WTSSESSION_CHANGE = 0x02B1
    WM_POWERBROADCAST = 0x0218
    WTS_SESSION_LOCK = 0x7
    WTS_SESSION_UNLOCK = 0x8
    PBT_APMSUSPEND = 0x4
    PBT_APMRESUMEAUTOMATIC = 0x12
    PBT_APMRESUMESUSPEND = 0x7
    PBT_POWERSETTINGCHANGE = 0x8013
    NOTIFY_FOR_THIS_SESSION = 0
    DEVICE_NOTIFY_WINDOW_HANDLE = 0
    DISPLAY_STATE_GUID = "6fe69556-704a-47a0-8f24-c28d936fdebf"

    def __init__(self, callback: Callable[[str, datetime], None]) -> None:
        super().__init__()
        self._callback = callback
        self._hwnd: int | None = None
        self._wts_registered = False
        self._power_notification = None
        self._display_guid = None
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def start(self, widget: Any) -> bool:
        if sys.platform != "win32":
            return False
        try:
            self.stop()
            hwnd = int(widget.winId())
            self._hwnd = hwnd
            from ctypes import wintypes

            wts = ctypes.windll.wtsapi32
            wts.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
            wts.WTSRegisterSessionNotification.restype = wintypes.BOOL
            self._wts_registered = bool(
                wts.WTSRegisterSessionNotification(hwnd, self.NOTIFY_FOR_THIS_SESSION)
            )

            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", ctypes.c_uint32),
                    ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            parsed = uuid.UUID(self.DISPLAY_STATE_GUID)
            self._display_guid = GUID(
                parsed.time_low,
                parsed.time_mid,
                parsed.time_hi_version,
                (ctypes.c_ubyte * 8).from_buffer_copy(parsed.bytes[8:]),
            )
            user32 = ctypes.windll.user32
            user32.RegisterPowerSettingNotification.argtypes = [
                wintypes.HWND,
                ctypes.POINTER(GUID),
                wintypes.DWORD,
            ]
            user32.RegisterPowerSettingNotification.restype = wintypes.HANDLE
            self._power_notification = user32.RegisterPowerSettingNotification(
                hwnd,
                ctypes.byref(self._display_guid),
                self.DEVICE_NOTIFY_WINDOW_HANDLE,
            )
            self._active = bool(self._wts_registered or self._power_notification)
            return self._active
        except Exception as exc:
            LOGGER.warning("[focus-activity] native bridge start unavailable: %s", exc)
            self.stop()
            return False

    def stop(self) -> None:
        if sys.platform != "win32":
            self._active = False
            return
        try:
            if self._power_notification:
                ctypes.windll.user32.UnregisterPowerSettingNotification(
                    self._power_notification
                )
        except Exception:
            LOGGER.debug("[focus-activity] power notification cleanup failed", exc_info=True)
        try:
            if self._wts_registered and self._hwnd:
                ctypes.windll.wtsapi32.WTSUnRegisterSessionNotification(self._hwnd)
        except Exception:
            LOGGER.debug("[focus-activity] WTS notification cleanup failed", exc_info=True)
        self._power_notification = None
        self._wts_registered = False
        self._hwnd = None
        self._active = False

    def _emit(self, kind: str) -> None:
        try:
            self._callback(kind, datetime.now(timezone.utc))
        except Exception:
            LOGGER.exception("[focus-activity] native event callback failed kind=%s", kind)

    def nativeEventFilter(self, event_type: Any, message: Any) -> tuple[bool, int]:
        if sys.platform != "win32" or not self._active:
            return False, 0
        try:
            address = int(message)
            if not address:
                return False, 0
            from ctypes import wintypes

            msg = wintypes.MSG.from_address(address)
            if msg.message == self.WM_WTSSESSION_CHANGE:
                if int(msg.wParam) == self.WTS_SESSION_LOCK:
                    self._emit("lock")
                elif int(msg.wParam) == self.WTS_SESSION_UNLOCK:
                    self._emit("unlock")
            elif msg.message == self.WM_POWERBROADCAST:
                event = int(msg.wParam)
                if event == self.PBT_APMSUSPEND:
                    self._emit("suspend")
                elif event in {self.PBT_APMRESUMEAUTOMATIC, self.PBT_APMRESUMESUSPEND}:
                    self._emit("resume")
                elif event == self.PBT_POWERSETTINGCHANGE and msg.lParam and self._display_guid is not None:
                    class POWERBROADCAST_SETTING(ctypes.Structure):
                        _fields_ = [
                            ("PowerSetting", type(self._display_guid)),
                            ("DataLength", ctypes.c_uint32),
                            ("Data", ctypes.c_ubyte * 1),
                        ]

                    setting = POWERBROADCAST_SETTING.from_address(int(msg.lParam))
                    if bytes(setting.PowerSetting) == bytes(self._display_guid):
                        state = int(setting.Data[0]) if int(setting.DataLength) else -1
                        if state == 0:
                            self._emit("display_off")
                        elif state == 1:
                            self._emit("display_on")
                        elif state == 2:
                            self._emit("display_dimmed")
        except Exception:
            LOGGER.debug("[focus-activity] native event decode failed", exc_info=True)
        return False, 0


class MacOSFocusActivityBridge:
    """Optional PyObjC notification bridge for sleep/wake/display changes."""

    def __init__(self, callback: Callable[[str, datetime], None]) -> None:
        self._callback = callback
        self._center = None
        self._tokens: list[object] = []
        self._blocks: list[Callable[[object], None]] = []

    @property
    def active(self) -> bool:
        return bool(self._tokens)

    def start(self, _widget: Any = None) -> bool:
        if sys.platform != "darwin" or self._tokens:
            return self.active
        try:
            import AppKit
            from AppKit import NSWorkspace

            workspace = NSWorkspace.sharedWorkspace()
            center = workspace.notificationCenter()
            notifications = (
                ("NSWorkspaceWillSleepNotification", "suspend"),
                ("NSWorkspaceDidWakeNotification", "resume"),
                ("NSWorkspaceScreensDidSleepNotification", "display_off"),
                ("NSWorkspaceScreensDidWakeNotification", "display_on"),
                ("NSWorkspaceSessionDidResignActiveNotification", "lock"),
                ("NSWorkspaceSessionDidBecomeActiveNotification", "unlock"),
            )
            for constant_name, kind in notifications:
                # PyObjC exposes the string notification names as module
                # constants on most supported macOS releases.  Falling back
                # to the literal keeps the bridge compatible with versions
                # where one of the newer constants is absent.
                token_name = getattr(AppKit, constant_name, constant_name)

                def block(_notification: object, event_kind: str = kind) -> None:
                    self._emit(event_kind)

                token = center.addObserverForName_object_queue_usingBlock_(
                    token_name,
                    None,
                    None,
                    block,
                )
                if token is not None:
                    self._tokens.append(token)
                    self._blocks.append(block)
            self._center = center
            return self.active
        except Exception as exc:
            LOGGER.warning("[focus-activity] macOS notification bridge unavailable: %s", exc)
            self.stop()
            return False

    def stop(self) -> None:
        center = self._center
        if center is not None:
            for token in self._tokens:
                try:
                    center.removeObserver_(token)
                except Exception:
                    LOGGER.debug(
                        "[focus-activity] macOS notification cleanup failed",
                        exc_info=True,
                    )
        self._tokens.clear()
        self._blocks.clear()
        self._center = None

    def _emit(self, kind: str) -> None:
        try:
            self._callback(kind, datetime.now(timezone.utc))
        except Exception:
            LOGGER.exception("[focus-activity] macOS event callback failed kind=%s", kind)


__all__ = [
    "AUTO_PAUSE_DISPLAY_OFF",
    "AUTO_PAUSE_IDLE",
    "AUTO_PAUSE_LOCK",
    "AUTO_PAUSE_REASONS",
    "AUTO_PAUSE_SLEEP",
    "AutoPauseDecision",
    "FocusActivityGuard",
    "MacOSFocusActivityBridge",
    "WindowsFocusActivityBridge",
]
