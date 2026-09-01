"""验证六毛原生窗口层级修复只改层级、不激活窗口。"""

from __future__ import annotations

import ctypes
import sys
from types import SimpleNamespace

from onepic_desktop_pet import native_window_policy


def test_macos_policy_uses_safe_pyobjc_window_bridge(monkeypatch) -> None:
    class FakeWindow:
        def __init__(self) -> None:
            self._level = 0
            self._behavior = 0
            self._style = 0
            self.hides_on_deactivate = None
            self.becomes_key_only = None

        def level(self):
            return self._level

        def setLevel_(self, value):
            self._level = int(value)

        def collectionBehavior(self):
            return self._behavior

        def setCollectionBehavior_(self, value):
            self._behavior = int(value)

        def styleMask(self):
            return self._style

        def setStyleMask_(self, value):
            self._style = int(value)

        def setHidesOnDeactivate_(self, value):
            self.hides_on_deactivate = bool(value)

        def setBecomesKeyOnlyIfNeeded_(self, value):
            self.becomes_key_only = bool(value)

    native = FakeWindow()
    fake_objc = SimpleNamespace(
        objc_object=lambda **_kwargs: SimpleNamespace(window=lambda: native)
    )
    fake_appkit = SimpleNamespace(
        NSFloatingWindowLevel=3,
        NSNormalWindowLevel=0,
        NSWindowCollectionBehaviorFullScreenNone=128,
        NSWindowStyleMaskNonactivatingPanel=128,
    )
    monkeypatch.setattr(native_window_policy.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "objc", fake_objc)
    monkeypatch.setitem(sys.modules, "AppKit", fake_appkit)

    result = native_window_policy.apply_macos_window_policy(
        SimpleNamespace(winId=lambda: 123),
        topmost=True,
        qt_stays_on_top=True,
    )

    assert result["action"] == "restore_topmost"
    assert result["native_level"] == 3
    assert result["native_topmost"] is True
    assert native._behavior == 128
    assert native.hides_on_deactivate is False
    assert native.becomes_key_only is True


def test_windows_policy_restores_topmost_without_activation(monkeypatch) -> None:
    class FakeUser32:
        def __init__(self) -> None:
            self.style = 0x00000080 | 0x08000000
            self.calls: list[tuple[int, int, tuple[int, ...]]] = []

        def GetWindowLongPtrW(self, _hwnd, _index):
            return self.style

        def GetWindowLongW(self, _hwnd, _index):
            return self.style

        def SetWindowLongPtrW(self, _hwnd, _index, value):
            self.style = int(value)
            return self.style

        def SetWindowLongW(self, _hwnd, _index, value):
            self.style = int(value)
            return self.style

        def SetWindowPos(self, hwnd, insert_after, x, y, width, height, flags):
            self.calls.append((int(hwnd), int(insert_after), (x, y, width, height, flags)))
            if int(insert_after) == -1:
                self.style |= 0x00000008
            else:
                self.style &= ~0x00000008
            return 1

    user32 = FakeUser32()
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(user32=user32),
        raising=False,
    )

    result = native_window_policy.apply_windows_window_policy(
        SimpleNamespace(winId=lambda: 456),
        topmost=True,
        qt_stays_on_top=True,
    )

    assert result["action"] == "restore_topmost"
    assert result["native_id"] == 456
    assert result["native_topmost"] is True
    assert user32.calls[0][1] == -1
    assert user32.calls[0][2][-1] & 0x0010  # SWP_NOACTIVATE
