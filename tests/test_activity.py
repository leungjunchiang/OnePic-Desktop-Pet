from __future__ import annotations

import sys
from types import SimpleNamespace

from onepic_desktop_pet import activity


def test_macos_finder_desktop_is_not_treated_as_fullscreen(monkeypatch) -> None:
    """Clicking the wallpaper must not hide the desktop pet as "fullscreen"."""

    class Workspace:
        @staticmethod
        def sharedWorkspace():
            return Workspace()

        def frontmostApplication(self):
            return SimpleNamespace(
                localizedName=lambda: "Finder",
                processIdentifier=lambda: 42,
            )

    monkeypatch.setattr(activity.sys, "platform", "darwin")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setitem(sys.modules, "AppKit", SimpleNamespace(NSWorkspace=Workspace))

    assert activity.active_window_is_fullscreen() is False


def test_macos_desktop_shell_name_matching_is_normalized() -> None:
    assert activity._is_macos_desktop_shell("  Finder  ") is True
    assert activity._is_macos_desktop_shell("Control   Center") is True
    assert activity._is_macos_desktop_shell("Safari") is False


def test_windows_desktop_shell_class_matching_is_normalized() -> None:
    assert activity._is_windows_desktop_shell_class(" Progman ") is True
    assert activity._is_windows_desktop_shell_class("WorkerW") is True
    assert activity._is_windows_desktop_shell_class("Chrome_WidgetWin_1") is False


def _fake_windows_user32(*, zoomed: bool = False, style: int = 0):
    class User32:
        @staticmethod
        def GetForegroundWindow():
            return 101

        @staticmethod
        def GetClassNameW(_hwnd, buffer, _length):
            buffer.value = "Chrome_WidgetWin_1"
            return len(buffer.value)

        @staticmethod
        def IsZoomed(_hwnd):
            return int(zoomed)

        @staticmethod
        def GetWindowLongW(_hwnd, _index):
            return style

        @staticmethod
        def GetWindowRect(_hwnd, rect):
            rect = getattr(rect, "_obj", rect)
            rect.left = 0
            rect.top = 0
            rect.right = 1920
            rect.bottom = 1080
            return 1

        @staticmethod
        def MonitorFromWindow(_hwnd, _flags):
            return 202

        @staticmethod
        def GetMonitorInfoW(_monitor, info):
            info = getattr(info, "_obj", info)
            info.rcMonitor.left = 0
            info.rcMonitor.top = 0
            info.rcMonitor.right = 1920
            info.rcMonitor.bottom = 1080
            return 1

    return User32()


def test_windows_maximised_chatgpt_is_not_treated_as_fullscreen(monkeypatch) -> None:
    """Maximising a normal app must not hide the desktop pet."""

    monkeypatch.setattr(activity.os, "name", "nt")
    monkeypatch.setattr(activity.sys, "platform", "win32")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setattr(
        activity.ctypes,
        "windll",
        SimpleNamespace(user32=_fake_windows_user32(zoomed=True)),
        raising=False,
    )

    assert activity.active_window_is_fullscreen() is False


def test_windows_browser_video_fullscreen_is_treated_as_fullscreen(monkeypatch) -> None:
    """A borderless browser video surface must hide the desktop pet."""

    monkeypatch.setattr(activity.os, "name", "nt")
    monkeypatch.setattr(activity.sys, "platform", "win32")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setattr(activity, "active_application_name", lambda: "msedge.exe")
    monkeypatch.setattr(
        activity.ctypes,
        "windll",
        SimpleNamespace(user32=_fake_windows_user32(zoomed=True)),
        raising=False,
    )

    assert activity.active_window_is_fullscreen() is True
    assert activity.active_fullscreen_video() is True


def test_windows_maximised_browser_is_not_treated_as_video_fullscreen(monkeypatch) -> None:
    """A normal maximised browser window must remain visible like Word/ChatGPT."""

    monkeypatch.setattr(activity.os, "name", "nt")
    monkeypatch.setattr(activity.sys, "platform", "win32")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setattr(activity, "active_application_name", lambda: "chrome.exe")
    monkeypatch.setattr(
        activity.ctypes,
        "windll",
        SimpleNamespace(
            user32=_fake_windows_user32(
                zoomed=True,
                style=0x00C00000 | 0x00040000,
            )
        ),
        raising=False,
    )

    assert activity.active_window_is_fullscreen() is False
    assert activity.active_fullscreen_video() is False


def test_windows_borderless_monitor_window_is_treated_as_fullscreen(monkeypatch) -> None:
    """A borderless monitor-sized surface still yields to the desktop pet policy."""

    monkeypatch.setattr(activity.os, "name", "nt")
    monkeypatch.setattr(activity.sys, "platform", "win32")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setattr(
        activity.ctypes,
        "windll",
        SimpleNamespace(user32=_fake_windows_user32()),
        raising=False,
    )

    assert activity.active_window_is_fullscreen() is True


def test_windows_fullscreen_game_with_window_style_is_treated_as_fullscreen(monkeypatch) -> None:
    """Dota2-style engines may keep a caption/frame style while filling the monitor."""

    monkeypatch.setattr(activity.os, "name", "nt")
    monkeypatch.setattr(activity.sys, "platform", "win32")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setattr(activity, "active_application_name", lambda: "dota2.exe")
    monkeypatch.setattr(
        activity.ctypes,
        "windll",
        SimpleNamespace(
            user32=_fake_windows_user32(
                zoomed=True,
                style=0x00C00000 | 0x00040000,
            )
        ),
        raising=False,
    )

    assert activity.active_window_is_fullscreen() is True
    assert activity.active_fullscreen_game() is True


def test_macos_fullscreen_yield_is_limited_to_media_and_games(monkeypatch) -> None:
    """Ordinary macOS apps must not make the pet yield merely when maximised."""

    import onepic_desktop_pet.activity as activity

    monkeypatch.setattr(activity.sys, "platform", "darwin")
    monkeypatch.setattr(activity, "active_window_is_fullscreen", lambda: True)
    monkeypatch.setattr(activity, "active_application_name", lambda: "PotPlayer")
    assert activity.active_fullscreen_video() is True

    monkeypatch.setattr(activity, "active_application_name", lambda: "Microsoft Word")
    assert activity.active_fullscreen_video() is False

    monkeypatch.setattr(activity, "active_application_name", lambda: "Minecraft")
    assert activity.active_fullscreen_game() is True
