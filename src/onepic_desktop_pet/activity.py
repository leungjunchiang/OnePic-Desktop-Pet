"""检测当前前台应用并归类为音乐、办公、编程、阅读或普通场景。

本模块只读取前台进程名称、原生窗口几何和显示器元数据并立即转成粗粒度类别，不记录窗口标题、不保存历史，也不联网。
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

# Browsers do not expose the video page as a separate process.  When a user
# enters the browser's real video fullscreen, the foreground process is still
# Chrome/Edge/Firefox/etc.  Keep this list limited to browser executables so a
# normal maximised document window is not treated as a video surface.
KNOWN_BROWSER_VIDEO_TOKENS = (
    "chrome",
    "msedge",
    "firefox",
    "brave",
    "opera",
    "vivaldi",
    "arc",
    "qqbrowser",
    "sogouexplorer",
)

# Full-screen games may keep a caption/thick-frame style even after taking
# over the monitor. Keep this list to actual game executables rather than
# launchers such as Steam, so a maximised game library window remains visible.
KNOWN_GAME_PROCESS_TOKENS = (
    "dota2",
    "csgo",
    "cs2",
    "valorant",
    "leagueoflegends",
    "overwatch",
    "fortnite",
    "r5apex",
    "apexlegends",
    "pubg",
    "genshinimpact",
    "yuanshen",
    "starrail",
    "eldenring",
    "monsterhunter",
    "rainbowsix",
    "rocketleague",
    "warframe",
    "minecraft",
    "robloxplayer",
    "ffxiv",
    "ff14",
    "helldivers",
    "palworld",
    "terraria",
)

# A real PowerPoint/Keynote slideshow is a fullscreen surface, while their
# ordinary editing windows must remain below the pet's floating level.
KNOWN_PRESENTATION_PROCESS_TOKENS = (
    "powerpnt",
    "microsoft powerpoint",
    "keynote",
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

# Keep display-mode detection separate from the older video/game fullscreen
# predicates.  Window visibility may yield to an ordinary maximised app, but
# focus auto-pause must continue to use only the latter predicates.
DISPLAY_MODE_NORMAL = "normal"
DISPLAY_MODE_MAXIMIZED = "maximized"
DISPLAY_MODE_FULLSCREEN = "fullscreen"


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


def _windows_foreground_is_normal_window(
    user32,
    hwnd,
    *,
    allow_media_fullscreen: bool = False,
    allow_game_fullscreen: bool = False,
    allow_presentation_fullscreen: bool = False,
) -> bool:
    """Return whether a full-monitor HWND is still a normal window.

    A maximised application can have the same outer rectangle as its monitor
    without being a real exclusive/borderless fullscreen surface.  In
    particular, ChatGPT and other ordinary desktop apps may be maximised this
    way.  Treating those windows as fullscreen would hide the desktop pet even
    though the user has only maximised a normal application window.
    """

    try:
        # WS_CAPTION includes the title bar and WS_THICKFRAME identifies a
        # resizable window.  Borderless fullscreen surfaces normally have
        # neither style, while maximised ChatGPT/browser/document windows do.
        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        style = int(get_style(hwnd, -16))
        # Some borderless/full-screen game engines retain the normal window
        # style while resizing to the monitor. Geometry is still required by
        # the display-mode detector below.
        if style & (0x00C00000 | 0x00040000) and not (
            allow_game_fullscreen or allow_presentation_fullscreen
        ):
            return True

        # Some Chromium builds keep the maximised bit while switching to
        # borderless video fullscreen.  For non-browser apps, IsZoomed remains
        # a useful conservative guard (and protects normal maximised apps
        # whose style query is unavailable).  A known player/browser is
        # allowed through only after the style check above has confirmed it is
        # borderless; geometry is still checked by the display-mode detector.
        is_zoomed = getattr(user32, "IsZoomed", None)
        if is_zoomed is not None and bool(is_zoomed(hwnd)):
            return not (
                allow_media_fullscreen
                or allow_game_fullscreen
                or allow_presentation_fullscreen
            )
        return False

    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _is_known_media_process(name: str) -> bool:
    """Return whether a process can own a browser/player video fullscreen."""

    normalized = str(name or "").casefold().strip()
    return bool(normalized) and any(
        token in normalized
        for token in (*KNOWN_VIDEO_PLAYER_TOKENS, *KNOWN_BROWSER_VIDEO_TOKENS)
    )


def _is_known_game_process(name: str) -> bool:
    """Return whether a foreground process is a known game executable."""

    normalized = str(name or "").casefold().strip()
    return bool(normalized) and any(token in normalized for token in KNOWN_GAME_PROCESS_TOKENS)


def _is_known_presentation_process(name: str) -> bool:
    """Return whether a foreground process can own a slideshow fullscreen."""

    normalized = str(name or "").casefold().strip()
    return bool(normalized) and any(
        token in normalized
        for token in KNOWN_PRESENTATION_PROCESS_TOKENS
    )


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


def _rect_close(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
    tolerance: int = 2,
) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(left, right))


def _windows_foreground_rectangles(user32, hwnd):
    """Return foreground, monitor, and work-area rectangles without titles."""

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", ctypes.c_ulong),
        ]

    window_rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(window_rect)):
        return None
    monitor = user32.MonitorFromWindow(hwnd, 2)
    if not monitor:
        return None
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None

    def values(rect: RECT) -> tuple[int, int, int, int]:
        return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))

    return values(window_rect), values(info.rcMonitor), values(info.rcWork)


def _windows_foreground_display_mode(
    user32,
    hwnd,
    screen_bounds: tuple[int, int, int, int] | None = None,
) -> str:
    if _windows_foreground_is_desktop_shell(user32, hwnd):
        return DISPLAY_MODE_NORMAL

    foreground_name = active_application_name()
    allow_media_fullscreen = _is_known_media_process(foreground_name)
    allow_game_fullscreen = _is_known_game_process(foreground_name)
    allow_presentation_fullscreen = _is_known_presentation_process(foreground_name)
    is_zoomed = False
    try:
        is_zoomed_fn = getattr(user32, "IsZoomed", None)
        is_zoomed = bool(is_zoomed_fn(hwnd)) if is_zoomed_fn is not None else False
    except (AttributeError, OSError, TypeError, ValueError):
        is_zoomed = False

    rectangles = _windows_foreground_rectangles(user32, hwnd)
    if rectangles is None:
        return DISPLAY_MODE_MAXIMIZED if is_zoomed else DISPLAY_MODE_NORMAL
    _window_bounds, monitor_bounds, _work_bounds = rectangles
    if screen_bounds is not None and not _rect_close(screen_bounds, monitor_bounds):
        # The foreground window belongs to another monitor; it must not hide
        # a pet living on the current monitor.
        return DISPLAY_MODE_NORMAL

    normal_window = _windows_foreground_is_normal_window(
        user32,
        hwnd,
        allow_media_fullscreen=allow_media_fullscreen,
        allow_game_fullscreen=allow_game_fullscreen,
        allow_presentation_fullscreen=allow_presentation_fullscreen,
    )
    if normal_window:
        return DISPLAY_MODE_MAXIMIZED if is_zoomed else DISPLAY_MODE_NORMAL
    if _rect_close(_window_bounds, monitor_bounds):
        return DISPLAY_MODE_FULLSCREEN
    return DISPLAY_MODE_MAXIMIZED if is_zoomed else DISPLAY_MODE_NORMAL


def _macos_rect_values(rect) -> tuple[int, int, int, int]:
    origin = rect.origin
    size = rect.size
    x = round(float(origin.x))
    y = round(float(origin.y))
    width = round(float(size.width))
    height = round(float(size.height))
    return x, y, x + width, y + height


def _macos_screen_rectangles(screens) -> list[tuple[tuple[int, int, int, int], tuple[int, int, int, int]]]:
    """Return AppKit frame/visibleFrame in Quartz's top-left coordinates."""

    frames = [_macos_rect_values(screen.frame()) for screen in screens]
    desktop_bottom = max((bottom for _left, _top, _right, bottom in frames), default=0)
    result = []
    for screen, frame in zip(screens, frames):
        left, top, right, bottom = frame
        frame_q = (left, desktop_bottom - bottom, right, desktop_bottom - top)
        visible = _macos_rect_values(screen.visibleFrame())
        visible_q = (
            visible[0],
            desktop_bottom - visible[3],
            visible[2],
            desktop_bottom - visible[1],
        )
        result.append((frame_q, visible_q))
    return result


def _macos_window_display_mode(
    screen_bounds: tuple[int, int, int, int] | None = None,
) -> str:
    try:
        from AppKit import NSScreen, NSWorkspace
        import Quartz  # type: ignore

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None or _is_macos_desktop_shell(str(app.localizedName() or "")):
            return DISPLAY_MODE_NORMAL
        pid = int(app.processIdentifier())
        screens = list(NSScreen.screens() or [])
        screen_rectangles = _macos_screen_rectangles(screens)
        if not screen_rectangles:
            return DISPLAY_MODE_NORMAL

        requested_indices = list(range(len(screen_rectangles)))
        if screen_bounds is not None:
            requested_indices = [
                index
                for index, (frame, visible) in enumerate(screen_rectangles)
                if _rect_close(screen_bounds, frame) or _rect_close(screen_bounds, visible)
            ]
            if not requested_indices:
                requested_size = (
                    screen_bounds[2] - screen_bounds[0],
                    screen_bounds[3] - screen_bounds[1],
                )
                requested_indices = [
                    index
                    for index, (frame, visible) in enumerate(screen_rectangles)
                    if requested_size
                    in {
                        (frame[2] - frame[0], frame[3] - frame[1]),
                        (visible[2] - visible[0], visible[3] - visible[1]),
                    }
                ]
            # If two displays have the same geometry and Qt/AppKit did not
            # expose a common origin, fail closed instead of hiding the wrong
            # monitor's pet.
            if len(requested_indices) != 1:
                return DISPLAY_MODE_NORMAL

        info = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly,
            Quartz.kCGNullWindowID,
        ) or []
        for native_window in info:
            if int(native_window.get(Quartz.kCGWindowOwnerPID, -1)) != pid:
                continue
            bounds = native_window.get(Quartz.kCGWindowBounds) or {}
            window_bounds = (
                round(float(bounds.get("X", 0))),
                round(float(bounds.get("Y", 0))),
                round(float(bounds.get("X", 0)) + float(bounds.get("Width", 0))),
                round(float(bounds.get("Y", 0)) + float(bounds.get("Height", 0))),
            )
            candidate_indices = requested_indices
            if screen_bounds is None:
                center = (
                    (window_bounds[0] + window_bounds[2]) / 2,
                    (window_bounds[1] + window_bounds[3]) / 2,
                )
                candidate_indices = [
                    index
                    for index, (frame, _visible) in enumerate(screen_rectangles)
                    if frame[0] <= center[0] <= frame[2]
                    and frame[1] <= center[1] <= frame[3]
                ]
            for index in candidate_indices:
                frame, visible = screen_rectangles[index]
                if _rect_close(window_bounds, frame):
                    return DISPLAY_MODE_FULLSCREEN
                if _rect_close(window_bounds, visible):
                    return DISPLAY_MODE_MAXIMIZED
        return DISPLAY_MODE_NORMAL
    except Exception:
        return DISPLAY_MODE_NORMAL


def active_window_display_mode(
    screen_bounds: tuple[int, int, int, int] | None = None,
) -> str:
    """Return ``normal``, ``maximized`` or ``fullscreen`` for the front window.

    Only process/application identity, native window geometry and monitor
    metadata are used.  ``screen_bounds`` is the pet's current monitor in
    ``left, top, right, bottom`` form; passing it prevents a maximised window
    on another display from suppressing this pet.
    """

    # Qt's offscreen platform has no real frontmost window or display.  Fail
    # closed so tests and headless diagnostics never suppress the pet.
    if os.environ.get("QT_QPA_PLATFORM", "").casefold() in {
        "offscreen",
        "minimal",
        "minimalegl",
    }:
        return DISPLAY_MODE_NORMAL
    if sys.platform == "darwin":
        return _macos_window_display_mode(screen_bounds)
    if os.name != "nt":
        return DISPLAY_MODE_NORMAL
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return DISPLAY_MODE_NORMAL
        return _windows_foreground_display_mode(user32, hwnd, screen_bounds)
    except (AttributeError, OSError, TypeError, ValueError):
        return DISPLAY_MODE_NORMAL


def active_window_is_maximized_or_fullscreen(
    screen_bounds: tuple[int, int, int, int] | None = None,
) -> bool:
    return active_window_display_mode(screen_bounds) in {
        DISPLAY_MODE_MAXIMIZED,
        DISPLAY_MODE_FULLSCREEN,
    }


def active_window_is_fullscreen() -> bool:
    """Return whether the foreground window is a true borderless fullscreen surface."""

    return active_window_display_mode() == DISPLAY_MODE_FULLSCREEN


def active_fullscreen_video() -> bool:
    """Return true for a known player or browser in real fullscreen.

    A maximised Word/PDF/browser/IDE window is intentionally not enough
    evidence.  Browser video fullscreen is identified by the browser process
    plus a borderless monitor-sized foreground window; ordinary maximised
    browser/document windows retain their caption or resize frame and remain
    visible.  This helper reads only the process name and coarse window
    geometry, never page content or pixels.
    """

    name = active_application_name().casefold().strip()
    if not _is_known_media_process(name):
        return False
    return active_window_is_fullscreen()


def active_fullscreen_game() -> bool:
    """Return true for a known game whose foreground window fills its monitor."""

    name = active_application_name().casefold().strip()
    if not _is_known_game_process(name):
        return False
    return active_window_is_fullscreen()


def active_fullscreen_presentation() -> bool:
    """Return true only for a known PowerPoint/Keynote fullscreen surface."""

    name = active_application_name().casefold().strip()
    if not _is_known_presentation_process(name):
        return False
    return active_window_is_fullscreen()
