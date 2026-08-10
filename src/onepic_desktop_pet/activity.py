"""检测当前前台应用并归类为音乐、办公、编程、阅读或普通场景。

本模块只读取前台进程名称并立即转成粗粒度类别，不记录窗口标题、不保存历史，也不联网。
Windows 使用系统 API，macOS 在可用时使用 Cocoa；平台能力缺失时安全返回 ``other``。
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


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
