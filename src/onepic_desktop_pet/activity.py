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
            pid = int(app.processIdentifier())
            screen = NSScreen.mainScreen()
            if screen is None:
                return False
            frame = screen.frame()
            info = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly,
                Quartz.kCGNullWindowID,
            ) or []
            for window in info:
                if int(window.get(Quartz.kCGWindowOwnerPID, -1)) != pid:
                    continue
                bounds = window.get(Quartz.kCGWindowBounds) or {}
                if (
                    abs(float(bounds.get("X", 0)) - float(frame.origin.x)) <= 3
                    and abs(float(bounds.get("Y", 0)) - float(frame.origin.y)) <= 3
                    and abs(float(bounds.get("Width", 0)) - float(frame.size.width)) <= 3
                    and abs(float(bounds.get("Height", 0)) - float(frame.size.height)) <= 3
                ):
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
