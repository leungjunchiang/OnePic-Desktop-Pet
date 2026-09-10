"""Cross-platform system input-idle detection for automatic focus pausing.

The desktop pet never records the actual keys, pointer coordinates, or the
active application.  It only asks the operating system for the elapsed time
since the last user input event, which is enough to pause a focus session
after a configurable idle period.
"""

from __future__ import annotations

from dataclasses import dataclass
import sys


@dataclass(frozen=True)
class InputIdleSnapshot:
    """Privacy-preserving result of the OS-wide input idle probe.

    ``idle_seconds=0`` is only valid when ``available`` is true.  Native API
    failures deliberately use ``None`` so callers cannot mistake an unknown
    probe for proof that the user just interacted with the computer.
    """

    idle_seconds: float | None
    available: bool
    provider: str
    error: str | None = None


def system_session_state() -> dict[str, object]:
    """Return coarse lock/sleep hints without reading user content.

    Windows exposes the currently interactive desktop through
    ``OpenInputDesktop``.  A failed probe or a missing foreground window is
    *not* enough evidence to call the machine locked: both can happen during
    normal desktop transitions and when another process has restricted API
    access.  We only report a lock when the desktop name is explicitly the
    secure ``Winlogon``/screen-saver desktop.  Other platforms return an
    intentionally conservative unknown state.
    """

    state: dict[str, object] = {
        "locked": False,
        # ``sleeping`` is only set by the native power-event bridge.  A
        # polling probe cannot observe the interval while the process is
        # suspended, so callers must not treat False as proof that no sleep
        # occurred.
        "sleeping": False,
        "display_state": "unknown",
    }
    if sys.platform == "darwin":
        try:
            import Quartz  # type: ignore

            session = Quartz.CGSessionCopyCurrentDictionary() or {}
            # Different macOS releases expose one of these names.  The
            # explicit locked flag is preferred; leaving the console is a
            # conservative fallback for the secure lock screen/fast switch.
            for key in (
                "CGSSessionScreenIsLocked",
                "CGSSessionScreenIsLockedKey",
                "kCGSessionScreenIsLocked",
            ):
                if bool(session.get(key)):
                    state["locked"] = True
                    return state
            # ``OnConsole`` is false during fast-user switching and a few
            # normal display/session transitions; it is not a lock signal.
            # Do not infer a lock from that ambiguous value.
        except Exception:
            # If the optional native probe is unavailable, never guess.
            pass
        return state
    if sys.platform != "win32":
        return state
    try:
        import ctypes

        from ctypes import wintypes

        user32 = ctypes.windll.user32
        # ``OpenInputDesktop`` can fail transiently or under restricted
        # permissions.  Treat that as unknown/false rather than a lock.
        desktop = user32.OpenInputDesktop(0, False, 0x0100)
        if not desktop:
            return state
        try:
            # UOI_NAME = 2.  The secure desktop has a stable name on supported
            # Windows versions; ordinary desktops (including a missing
            # foreground window) are never classified as locked.
            name = ctypes.create_unicode_buffer(128)
            returned = wintypes.DWORD()
            get_name = getattr(user32, "GetUserObjectInformationW", None)
            if get_name is None:
                return state
            ok = get_name(
                desktop,
                2,
                ctypes.byref(name),
                ctypes.sizeof(name),
                ctypes.byref(returned),
            )
            if ok:
                desktop_name = name.value.casefold().replace(" ", "-")
                state["locked"] = desktop_name in {
                    "winlogon",
                    "screen-saver",
                    "screensaver",
                }
        finally:
            user32.CloseDesktop(desktop)
    except Exception:
        # A native probe failure must never turn into a false rest record.
        return state
    return state


def _windows_elapsed_ms(current_tick: int, last_input_tick: int) -> int:
    """Calculate a 32-bit Windows input age, including tick-counter wrap."""

    return (int(current_tick) - int(last_input_tick)) & 0xFFFFFFFF


def get_input_idle_snapshot() -> InputIdleSnapshot:
    """Return a typed, privacy-preserving system idle observation."""

    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

            info = LASTINPUTINFO()
            info.cbSize = ctypes.sizeof(LASTINPUTINFO)
            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
                return InputIdleSnapshot(
                    None,
                    False,
                    "windows:GetLastInputInfo",
                    "GetLastInputInfo returned false",
                )
            # ``dwTime`` is a 32-bit tick count; compare it modulo 2^32 with
            # the 64-bit counter to handle the Windows tick wrap.
            tick = int(ctypes.windll.kernel32.GetTickCount64())
            elapsed_ms = _windows_elapsed_ms(tick, int(info.dwTime))
            return InputIdleSnapshot(
                max(0.0, float(elapsed_ms) / 1000.0),
                True,
                "windows:GetLastInputInfo",
            )
        except Exception as exc:
            return InputIdleSnapshot(
                None,
                False,
                "windows:GetLastInputInfo",
                f"{type(exc).__name__}: {exc}",
            )

    if sys.platform == "darwin":
        try:
            import Quartz  # type: ignore

            state = Quartz.kCGEventSourceStateCombinedSessionState
            event_type = Quartz.kCGAnyInputEventType
            return InputIdleSnapshot(
                max(
                    0.0,
                    float(
                        Quartz.CGEventSourceSecondsSinceLastEventType(
                            state,
                            event_type,
                        )
                    ),
                ),
                True,
                "macos:Quartz",
            )
        except Exception as quartz_exc:
            # PyObjC is optional in the packaged app.  Call the same
            # CoreGraphics symbol directly when it is not installed.
            try:
                import ctypes

                core_graphics = ctypes.CDLL(
                    "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
                )
                fn = core_graphics.CGEventSourceSecondsSinceLastEventType
                fn.argtypes = [ctypes.c_int, ctypes.c_uint32]
                fn.restype = ctypes.c_double
                return InputIdleSnapshot(
                    max(0.0, float(fn(0, 0xFFFFFFFF))),
                    True,
                    "macos:CoreGraphics",
                )
            except Exception as exc:
                return InputIdleSnapshot(
                    None,
                    False,
                    "macos:CoreGraphics",
                    f"{type(exc).__name__}: {exc}; Quartz: {quartz_exc}",
                )

    return InputIdleSnapshot(None, False, f"unsupported:{sys.platform}", "platform unsupported")


def system_idle_seconds() -> float | None:
    """Return seconds since the last keyboard/mouse input when available.

    Windows exposes this through ``GetLastInputInfo``.  macOS exposes an
    equivalent aggregate event clock through Quartz.  A native failure is
    represented by ``None`` rather than ``0`` so automatic pause cannot be
    disabled silently by a broken probe.
    """

    snapshot = get_input_idle_snapshot()
    return snapshot.idle_seconds if snapshot.available else None


def activity_diagnostics() -> dict[str, object]:
    """Return a small developer-facing snapshot without collecting input data."""

    idle = get_input_idle_snapshot()
    session = system_session_state()
    return {
        "idle_seconds": idle.idle_seconds,
        "idle_available": idle.available,
        "input_provider": idle.provider,
        "input_error": idle.error,
        "locked": bool(session.get("locked")),
        "sleeping": bool(session.get("sleeping")),
        "display_state": str(session.get("display_state") or "unknown"),
    }
