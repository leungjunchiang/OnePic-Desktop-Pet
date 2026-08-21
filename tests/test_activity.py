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
