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
    assert activity.active_window_display_mode() == activity.DISPLAY_MODE_NORMAL


def test_macos_desktop_shell_name_matching_is_normalized() -> None:
    assert activity._is_macos_desktop_shell("  Finder  ") is True
    assert activity._is_macos_desktop_shell("Control   Center") is True
    assert activity._is_macos_desktop_shell("Safari") is False


def test_windows_desktop_shell_class_matching_is_normalized() -> None:
    assert activity._is_windows_desktop_shell_class(" Progman ") is True
    assert activity._is_windows_desktop_shell_class("WorkerW") is True
    assert activity._is_windows_desktop_shell_class("Chrome_WidgetWin_1") is False


def _fake_windows_user32(
    *,
    zoomed: bool = False,
    visible: bool = True,
    iconic: bool = False,
    style: int = 0,
    window_bounds: tuple[int, int, int, int] = (0, 0, 1920, 1080),
    monitor_bounds: tuple[int, int, int, int] = (0, 0, 1920, 1080),
    work_bounds: tuple[int, int, int, int] = (0, 0, 1920, 1080),
):
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
        def IsWindow(_hwnd):
            return 1

        @staticmethod
        def IsWindowVisible(_hwnd):
            return int(visible)

        @staticmethod
        def IsIconic(_hwnd):
            return int(iconic)

        @staticmethod
        def GetWindowLongW(_hwnd, _index):
            return style

        @staticmethod
        def GetWindowRect(_hwnd, rect):
            rect = getattr(rect, "_obj", rect)
            rect.left, rect.top, rect.right, rect.bottom = window_bounds
            return 1

        @staticmethod
        def MonitorFromWindow(_hwnd, _flags):
            return 202

        @staticmethod
        def GetMonitorInfoW(_monitor, info):
            info = getattr(info, "_obj", info)
            info.rcMonitor.left, info.rcMonitor.top, info.rcMonitor.right, info.rcMonitor.bottom = monitor_bounds
            info.rcWork.left, info.rcWork.top, info.rcWork.right, info.rcWork.bottom = work_bounds
            return 1

    return User32()


def test_windows_minimized_foreground_window_does_not_suppress_pet(monkeypatch) -> None:
    """A minimize transition must fail open instead of hiding the pet."""

    monkeypatch.setattr(activity.os, "name", "nt")
    monkeypatch.setattr(activity.sys, "platform", "win32")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setattr(
        activity.ctypes,
        "windll",
        SimpleNamespace(
            user32=_fake_windows_user32(
                zoomed=True,
                iconic=True,
                window_bounds=(0, 0, 1920, 1080),
            )
        ),
        raising=False,
    )

    assert activity.active_window_display_mode() == activity.DISPLAY_MODE_NORMAL


def test_windows_hidden_foreground_window_does_not_suppress_pet(monkeypatch) -> None:
    """A stale hidden HWND is not evidence of a display takeover."""

    monkeypatch.setattr(activity.os, "name", "nt")
    monkeypatch.setattr(activity.sys, "platform", "win32")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setattr(
        activity.ctypes,
        "windll",
        SimpleNamespace(user32=_fake_windows_user32(zoomed=True, visible=False)),
        raising=False,
    )

    assert activity.active_window_display_mode() == activity.DISPLAY_MODE_NORMAL


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
    assert activity.active_window_display_mode() == activity.DISPLAY_MODE_MAXIMIZED


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
    assert activity.active_window_display_mode() == activity.DISPLAY_MODE_MAXIMIZED


def test_windows_large_nonmaximized_window_is_not_suppressed(monkeypatch) -> None:
    """A manually resized monitor-sized window is not system maximised."""

    monkeypatch.setattr(activity.os, "name", "nt")
    monkeypatch.setattr(activity.sys, "platform", "win32")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setattr(activity, "active_application_name", lambda: "notepad.exe")
    monkeypatch.setattr(
        activity.ctypes,
        "windll",
        SimpleNamespace(
            user32=_fake_windows_user32(
                zoomed=False,
                style=0x00C00000 | 0x00040000,
            )
        ),
        raising=False,
    )

    assert activity.active_window_display_mode() == activity.DISPLAY_MODE_NORMAL


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
    assert activity.active_window_display_mode() == activity.DISPLAY_MODE_FULLSCREEN


def test_windows_powerpoint_work_area_slideshow_is_treated_as_fullscreen(monkeypatch) -> None:
    """A borderless PPT slideshow may end at the work area above the taskbar."""

    monkeypatch.setattr(activity.os, "name", "nt")
    monkeypatch.setattr(activity.sys, "platform", "win32")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setattr(activity, "active_application_name", lambda: "POWERPNT.EXE")
    monkeypatch.setattr(
        activity.ctypes,
        "windll",
        SimpleNamespace(
            user32=_fake_windows_user32(
                window_bounds=(0, 0, 1920, 1040),
                monitor_bounds=(0, 0, 1920, 1080),
                work_bounds=(0, 0, 1920, 1040),
            )
        ),
        raising=False,
    )

    assert activity.active_window_display_mode((0, 0, 1920, 1080)) == activity.DISPLAY_MODE_FULLSCREEN
    assert activity.active_fullscreen_presentation((0, 0, 1920, 1080)) is True

    # The logical Qt geometry may be smaller than the physical Win32 monitor
    # rectangle at 125/150% scaling; a native monitor reference still keeps
    # this same-display slideshow classified as fullscreen.
    assert (
        activity.active_window_display_mode(
            (0, 0, 1536, 864),
            reference_hwnd=4242,
        )
        == activity.DISPLAY_MODE_FULLSCREEN
    )

    # A framed PowerPoint editing window occupying the work area is not the
    # slideshow surface and must remain visible.
    monkeypatch.setattr(
        activity.ctypes,
        "windll",
        SimpleNamespace(
            user32=_fake_windows_user32(
                style=0x00C00000 | 0x00040000,
                window_bounds=(0, 0, 1920, 1040),
                monitor_bounds=(0, 0, 1920, 1080),
                work_bounds=(0, 0, 1920, 1040),
            )
        ),
        raising=False,
    )
    assert activity.active_window_display_mode((0, 0, 1920, 1080)) == activity.DISPLAY_MODE_NORMAL


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


def test_windows_maximized_window_on_another_monitor_does_not_suppress_pet(monkeypatch) -> None:
    """Only the foreground window's own monitor may suppress a pet."""

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

    assert activity.active_window_display_mode((1920, 0, 3840, 1080)) == activity.DISPLAY_MODE_NORMAL


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


def test_presentation_fullscreen_yields_only_for_known_slideshow(monkeypatch) -> None:
    """Only a real PowerPoint/Keynote slideshow may hide the pet."""

    monkeypatch.setattr(activity, "active_application_name", lambda: "Microsoft PowerPoint")
    monkeypatch.setattr(activity, "active_window_is_fullscreen", lambda: True)
    assert activity.active_fullscreen_presentation() is True

    monkeypatch.setattr(activity, "active_application_name", lambda: "Microsoft Word")
    assert activity.active_fullscreen_presentation() is False
