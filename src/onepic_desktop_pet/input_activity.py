"""Cross-platform system input-idle detection for automatic focus pausing.

The desktop pet never records the actual keys, pointer coordinates, or the
active application.  It only asks the operating system for the elapsed time
since the last user input event, which is enough to pause a focus session
after a configurable idle period.
"""

from __future__ import annotations

import sys
import time


def system_session_state() -> dict[str, bool]:
    """Return coarse lock/sleep hints without reading user content.

    Windows exposes the currently interactive desktop through
    ``OpenInputDesktop``.  A missing desktop or foreground window generally
    means the secure lock screen is active.  Other platforms return an
    intentionally conservative unknown state; the idle duration and
    application evidence still provide the normal fallback classification.
    """

    state = {"locked": False, "sleeping": False}
    if sys.platform != "win32":
        return state
    try:
        import ctypes

        user32 = ctypes.windll.user32
        desktop = user32.OpenInputDesktop(0, False, 0x0100)
        foreground = user32.GetForegroundWindow()
        if not desktop or not foreground:
            state["locked"] = True
        if desktop:
            user32.CloseDesktop(desktop)
    except Exception:
        # A native probe failure must never turn into a false rest record.
        return state
    return state


def _windows_elapsed_ms(current_tick: int, last_input_tick: int) -> int:
    """Calculate a 32-bit Windows input age, including tick-counter wrap."""

    return (int(current_tick) - int(last_input_tick)) & 0xFFFFFFFF


def system_idle_seconds() -> float:
    """Return seconds since the last keyboard/mouse input when available.

    Windows exposes this through ``GetLastInputInfo``.  macOS exposes an
    equivalent aggregate event clock through Quartz.  Other platforms (and
    locked-down environments where the native API is unavailable) return
    ``0`` so the feature never pauses a session based on an untrusted guess.
    """

    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

            info = LASTINPUTINFO()
            info.cbSize = ctypes.sizeof(LASTINPUTINFO)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
                # ``dwTime`` is a 32-bit tick count; compare it modulo 2^32
                # with the 64-bit counter to handle the Windows tick wrap.
                tick = int(ctypes.windll.kernel32.GetTickCount64())
                elapsed_ms = _windows_elapsed_ms(tick, int(info.dwTime))
                return max(0.0, float(elapsed_ms) / 1000.0)
        except Exception:
            return 0.0
        return 0.0

    if sys.platform == "darwin":
        try:
            import Quartz  # type: ignore

            state = Quartz.kCGEventSourceStateCombinedSessionState
            event_type = Quartz.kCGAnyInputEventType
            return max(0.0, float(Quartz.CGEventSourceSecondsSinceLastEventType(state, event_type)))
        except Exception:
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
                return max(0.0, float(fn(0, 0xFFFFFFFF)))
            except Exception:
                return 0.0

    return 0.0

