"""检测当前前台应用并归类为音乐、办公、编程、阅读或普通场景。

本模块只读取前台进程名称并立即转成粗粒度类别，不记录窗口标题、不保存历史，也不联网。
Windows 使用系统 API，macOS 在可用时使用 Cocoa；平台能力缺失时安全返回 ``other``。
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


KNOWN_VIDEO_PLAYER_TOKENS = (
    "vlc",
    "iina",
    "mpv",
    "potplayer",
    "gom player",
    "gomplayer",
    "quicktime player",
    "quicktimeplayer",
    "windows media player",
    "wmplayer",
)

# Finder's desktop is exposed by Quartz as a screen-sized window.  Treating
# that window as fullscreen makes a click on an empty desktop area hide Lili;
# opening a normal browser window then appears to "restore" it.  These are
# desktop/compositor shells, not user content that should receive fullscreen
# priority.  Keep the list narrow and compare normalized localized names.
MACOS_DESKTOP_SHELL_NAMES = frozenset(
    {
        "finder",
        "访达",
        "dock",
        "程序坞",
        "systemuiserver",
        "control center",
        "控制中心",
        "notification center",
        "通知中心",
        "windowserver",
    }
)

# Windows exposes the wallpaper and desktop icon host as a full-monitor
# foreground window too.  These class names identify Explorer's shell rather
# than a video, presentation, or document window.
WINDOWS_DESKTOP_SHELL_CLASSES = frozenset(
    {
        "progman",
        "workerw",
        "shell_traywnd",
        "shell_secondarytraywnd",
    }
)


def _is_macos_desktop_shell(name: str) -> bool:
    """Return whether *name* is macOS's desktop/compositor, not a document app."""

    normalized = " ".join(str(name or "").casefold().split())
    return normalized in MACOS_DESKTOP_SHELL_NAMES


def _is_windows_desktop_shell_class(name: str) -> bool:
    """Return whether a Win32 class belongs to Explorer's desktop shell."""

    return str(name or "").casefold().strip() in WINDOWS_DESKTOP_SHELL_CLASSES


def _windows_foreground_is_desktop_shell(user32, hwnd) -> bool:
    """Check the foreground HWND class without reading window text/content."""

    try:
        buffer = ctypes.create_unicode_buffer(256)
        length = int(user32.GetClassNameW(hwnd, buffer, len(buffer)))
        return length > 0 and _is_windows_desktop_shell_class(buffer.value)
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _windows_foreground_is_normal_window(user32, hwnd) -> bool:
    """Return whether a full-monitor HWND is still a normal window.

    A maximised application can have the same outer rectangle as its monitor
    without being a real exclusive/borderless fullscreen surface.  In
    particular, ChatGPT and other ordinary desktop apps may be maximised this
    way.  Treating those windows as fullscreen would hide the desktop pet even
    though the user has only maximised a normal application window.
    """

    try:
        is_zoomed = getattr(user32, "IsZoomed", None)
        if is_zoomed is not None and bool(is_zoomed(hwnd)):
            return True

        # WS_CAPTION includes the title bar and WS_THICKFRAME identifies a
        # resizable window.  Borderless fullscreen surfaces normally have
        # neither style, while maximised ChatGPT/browser/document windows do.
        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        style = int(get_style(hwnd, -16))
        return bool(style & (0x00C00000 | 0x00040000))
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def classify_application(name: str) -> str:
    """把进程或应用名称归类，供六毛选择不打扰的陪伴动作。"""

    value = name.casefold()
    if any(key in value for key in ("cloudmusic", "qqmusic", "netease", "spotify", "music")):
        return "music"
    if any(key in value for key in ("winword", "excel", "powerpnt", "wps", "pages", "numbers", "keynote")):
        return "office"
    if any(key in value for key in ("code", "codex", "claude", "pycharm", "terminal", "iterm")):
        return "coding"
    if any(key in value for key in ("reader", "kindle", "calibre", "preview", "acrobat")):
        return "reading"
    return "other"


def _windows_foreground_process() -> str:
    """读取 Windows 前台进程文件名；失败时返回空字符串。"""

    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        process_id = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        handle = kernel32.OpenProcess(0x1000, False, process_id.value)
        if not handle:
            return ""
        try:
            size = ctypes.c_ulong(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return Path(buffer.value).name
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, ValueError):
        return ""
    return ""


def active_application_name() -> str:
    """返回当前前台应用名称，不包含窗口内容。"""

    if os.name == "nt":
        return _windows_foreground_process()
    if sys.platform == "darwin":
        try:
            from AppKit import NSWorkspace

            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            return str(app.localizedName() or "")
        except (ImportError, AttributeError):
            return ""
    return ""


def active_application_category() -> str:
    """检测并返回当前前台应用的粗粒度类别。"""

    return classify_application(active_application_name())


def active_window_is_fullscreen() -> bool:
    """Return whether the foreground window fills its monitor.

    This intentionally compares window geometry only.  It does not inspect
    a title, document, pixel content or input stream.  Unsupported platforms
    safely report ``False``.
    """

    # Qt's offscreen platform has no real frontmost window or display.  On
    # macOS CI it can nevertheless expose a synthetic window whose bounds
    # happen to match the synthetic screen, which would make the privacy
    # guards incorrectly treat every test as fullscreen.  Fail closed here:
    # real desktop builds never use the offscreen platform, and the input-idle
    # policy remains the correct fallback when geometry is unavailable.
    if os.environ.get("QT_QPA_PLATFORM", "").casefold() == "offscreen":
        return False

    if sys.platform == "darwin":
        # Use only coarse native window geometry.  If Quartz/AppKit is not
        # available, fail closed: browser/PDF fullscreen must not be treated
        # as video and the 10-minute input-idle guard remains the fallback.
        try:
            from AppKit import NSScreen, NSWorkspace
            import Quartz  # type: ignore

            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return False
            # Finder owns a screen-sized desktop window.  It is the normal
            # foreground shell after the user clicks the wallpaper, not a
            # presentation/video fullscreen surface.  Exclude it before the
            # geometry check so desktop-mode pets remain visible and still
            # yield to genuine fullscreen content.
            app_name = str(app.localizedName() or "")
            if _is_macos_desktop_shell(app_name):
                return False
            pid = int(app.processIdentifier())
            info = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly,
                Quartz.kCGNullWindowID,
            ) or []
            screens = list(NSScreen.screens() or [])
            screen_sizes: set[tuple[int, int]] = set()
            for screen in screens:
                frame = screen.frame()
                logical_size = (
                    round(float(frame.size.width)),
                    round(float(frame.size.height)),
                )
                screen_sizes.add(logical_size)
                # Quartz window bounds are normally reported in points, but
                # a few macOS/video paths expose backing-pixel dimensions on
                # Retina displays.  Accept both representations without
                # inspecting window titles or pixels.
                try:
                    scale = float(screen.backingScaleFactor())
                except (AttributeError, TypeError, ValueError):
                    scale = 1.0
                if scale > 1.0:
                    screen_sizes.add(
                        (
                            round(logical_size[0] * scale),
                            round(logical_size[1] * scale),
                        )
                    )
            for window in info:
                if int(window.get(Quartz.kCGWindowOwnerPID, -1)) != pid:
                    continue
                bounds = window.get(Quartz.kCGWindowBounds) or {}
                width = round(float(bounds.get("Width", 0)))
                height = round(float(bounds.get("Height", 0)))
                # Quartz and AppKit use different global-origin conventions
                # on some multi-monitor layouts.  Full-screen playback and
                # PowerPoint still have an unambiguous display-sized frame,
                # so compare dimensions first and avoid missing fullscreen
                # merely because the monitor origin was transformed.
                if (width, height) in screen_sizes:
                    return True
        except Exception:
            return False
        return False
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        # Clicking wallpaper makes Explorer's Progman/WorkerW window the
        # foreground HWND. It fills the monitor, but it is not real fullscreen
        # content and must not hide the desktop pet.
        if _windows_foreground_is_desktop_shell(user32, hwnd):
            return False
        if _windows_foreground_is_normal_window(user32, hwnd):
            return False

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

        window_rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(window_rect)):
            return False
        monitor = user32.MonitorFromWindow(hwnd, 2)
        if not monitor:
            return False
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False
        bounds = info.rcMonitor
        return (
            abs(window_rect.left - bounds.left) <= 2
            and abs(window_rect.top - bounds.top) <= 2
            and abs(window_rect.right - bounds.right) <= 2
            and abs(window_rect.bottom - bounds.bottom) <= 2
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def active_fullscreen_video() -> bool:
    """Return true only for a known video player in real fullscreen.

    A maximised Word/PDF/browser/IDE window is intentionally not enough
    evidence.  This helper is privacy-preserving: it reads only the process
    name and coarse window geometry, never page content or pixels.
    """

    name = active_application_name().casefold().strip()
    if not name or not any(token in name for token in KNOWN_VIDEO_PLAYER_TOKENS):
        return False
    return active_window_is_fullscreen()
