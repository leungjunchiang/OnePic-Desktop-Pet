"""全屏视频、F11、PPT、DPI、通知生命周期与置顶保护的回归测试。"""
from __future__ import annotations

import ctypes
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QLabel

from onepic_desktop_pet import activity
from test_activity import _fake_windows_user32
from test_window import _create_window


@pytest.mark.parametrize("process", ["chrome.exe", "msedge.exe", "firefox.exe", "vlc.exe", "PotPlayer.exe", "POWERPNT.EXE"])
def test_fullscreen_with_retained_caption_uses_monitor_geometry(monkeypatch, process):
    monkeypatch.setattr(activity, "active_application_name", lambda: process)
    # Browsers can retain WS_CAPTION/WS_THICKFRAME during video/F11 fullscreen.
    user32 = _fake_windows_user32(zoomed=True, style=0x00C00000 | 0x00040000,
        work_bounds=(0, 0, 1920, 1040))
    assert activity._windows_foreground_display_mode(user32, 101) == "fullscreen"
    user32 = _fake_windows_user32(zoomed=True, style=0x00C00000 | 0x00040000,
        window_bounds=(0, 0, 1920, 1040), work_bounds=(0, 0, 1920, 1040))
    assert activity._windows_foreground_display_mode(user32, 101) == "maximized"


def test_root_window_dwm_bounds_and_tolerance(monkeypatch):
    monkeypatch.setattr(activity, "active_application_name", lambda: "chrome.exe")
    user32 = _fake_windows_user32(window_bounds=(-8, -8, 1928, 1088))
    visited = []
    user32.GetAncestor = lambda hwnd, flag: 202 if hwnd == 101 and flag == 2 else hwnd
    monkeypatch.setattr(activity, "_windows_visible_frame_bounds",
        lambda api, hwnd: visited.append(hwnd) or (2, -2, 1918, 1082))
    assert activity._windows_foreground_display_mode(user32, 101) == "fullscreen"
    assert visited == [202]
    monkeypatch.setattr(activity, "_windows_visible_frame_bounds", lambda api, hwnd: None)
    assert activity._windows_foreground_display_mode(user32, 101) == "normal"


def test_different_native_monitor_and_scaled_same_monitor(monkeypatch):
    monkeypatch.setattr(activity, "active_application_name", lambda: "chrome.exe")
    user32 = _fake_windows_user32()
    user32.MonitorFromWindow = lambda hwnd, flag: 0x123456781234 if hwnd == 101 else 0x999999999999
    assert activity._windows_foreground_display_mode(user32, 101, reference_hwnd=4242) == "normal"
    user32.MonitorFromWindow = lambda hwnd, flag: 0x123456781234
    assert activity._windows_foreground_display_mode(user32, 101,
        (0, 0, 1536, 864), reference_hwnd=4242) == "fullscreen"


def test_native_binding_preserves_64_bit_hwnd_and_hmonitor():
    wide = 0x123456781234 if ctypes.sizeof(ctypes.c_void_p) == 8 else 0x12345678
    foreground = ctypes.CFUNCTYPE(ctypes.c_void_p)(lambda: wide)
    monitor = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32)(lambda hwnd, flags: hwnd)
    foreground.restype = ctypes.c_int  # ctypes DLL default loses pointer bits.
    user32 = SimpleNamespace(GetForegroundWindow=foreground, MonitorFromWindow=monitor)
    activity._prepare_windows_geometry_api(user32)
    assert foreground() == wide
    assert monitor(wide, 2) == wide


def test_unknown_borderless_fullscreen_does_not_depend_on_process_allowlist(monkeypatch):
    monkeypatch.setattr(activity, "active_application_name", lambda: "custom_fullscreen.exe")
    assert activity._windows_foreground_display_mode(_fake_windows_user32(zoomed=True), 101) == "fullscreen"


@pytest.fixture
def pet(monkeypatch):
    app, window = _create_window()
    window.fullscreen_poll_timer.stop()
    monkeypatch.setattr("onepic_desktop_pet.window.detect_quiet_mode", lambda: SimpleNamespace(blocked=False))
    yield app, window
    window.close()
    window.deleteLater()
    app.processEvents()


def test_poll_debounces_both_transitions_and_preserves_position(pet, monkeypatch):
    app, window = pet
    state = {"mode": "fullscreen"}
    monkeypatch.setattr(window, "_foreground_display_mode", lambda: state["mode"])
    anchor = window.pos()
    window._poll_fullscreen_visibility()
    assert window.isVisible()
    window._poll_fullscreen_visibility()
    assert window._fullscreen_hidden and not window.isVisible()
    state["mode"] = "normal"
    window._poll_fullscreen_visibility()
    assert not window.isVisible()
    state["mode"] = "fullscreen"
    window._poll_fullscreen_visibility()
    assert not window.isVisible()
    state["mode"] = "normal"
    window._poll_fullscreen_visibility()
    window._poll_fullscreen_visibility()
    window._finish_fullscreen_restore()
    assert window.isVisible() and window.pos() == anchor


def test_hidden_walking_pet_keeps_its_restore_position(pet, monkeypatch):
    from onepic_desktop_pet.behavior import PetState
    app, window = pet
    window.move(100, 100)
    window.state = PetState.WALK
    window.direction = 1
    anchor = window.pos()
    monkeypatch.setattr(window, "_foreground_display_mode", lambda: "fullscreen")
    window._sync_fullscreen_visibility()
    window._last_movement_at -= 1.0
    window._movement_tick()
    assert window.pos() == anchor
    monkeypatch.setattr(window, "_foreground_display_mode", lambda: "normal")
    window._sync_fullscreen_visibility()
    window._finish_fullscreen_restore()
    assert window.isVisible() and window.pos() == anchor


def test_fullscreen_blocks_watchdog_refresh_coaching_and_new_notifications(pet, monkeypatch):
    app, window = pet
    badge = QLabel("补时中", window, window.speech_bubble.windowFlags())
    window._coaching_surfaces = {"badge": badge}
    badge.show()
    monkeypatch.setattr(window, "_foreground_display_mode", lambda: "fullscreen")
    window._sync_fullscreen_visibility()
    assert not badge.isVisible()
    native_calls = []
    monkeypatch.setattr("onepic_desktop_pet.window.apply_native_window_policy", lambda *args, **kwargs: native_calls.append(args) or {})
    window._topmost_watchdog_tick()
    window._ensure_on_top(event="ApplicationDeactivateSettled")
    window._show_nonactivating(badge)
    window._update_work_duration_bubble()
    window.show_speech("加油", 4000)
    assert not native_calls
    assert not window.isVisible() and not badge.isVisible() and not window.speech_bubble.isVisible()
    manager = window.notification_manager
    assert not manager.notify("new-cheer", "加油", "搭子给你加油", lambda: None)
    assert "new-cheer" in manager.shown_event_ids
    assert manager.current is None and not manager.pending


def test_direct_coaching_show_uses_owner_fullscreen_gate(pet, monkeypatch):
    from onepic_desktop_pet.coaching_ui import DesktopCoachingSurface
    app, window = pet
    badge = DesktopCoachingSurface(window, lambda: None, compact=True)
    window._coaching_surfaces = {"badge": badge}
    monkeypatch.setattr(window, "_foreground_display_mode", lambda: "fullscreen")
    window._sync_fullscreen_visibility()
    badge.label.setText("补时中 · 还差18分钟")
    badge.passive_show()
    assert not badge.isVisible()


def test_fullscreen_drops_transient_notice_without_resetting_baseline(pet, monkeypatch):
    app, window = pet
    manager = window.notification_manager
    manager.baselines["interaction"] = "existing-baseline"
    manager.notify("existing", "加油", "搭子给你加油", lambda: None)
    manager.flush()
    assert manager.current is not None
    window.show_speech("该回来了", 4000)
    monkeypatch.setattr(window, "_foreground_display_mode", lambda: "fullscreen")
    window._sync_fullscreen_visibility()
    assert manager.current is None and not manager.timer.isActive()
    assert manager.baselines["interaction"] == "existing-baseline"
    assert "existing" in manager.shown_event_ids
    # A transient received during fullscreen must not be replayed on exit.
    window.show_speech("嘲讽", 4000)
    window.speech_timer.stop()  # its four-second lifetime has ended
    monkeypatch.setattr(window, "_foreground_display_mode", lambda: "normal")
    window._sync_fullscreen_visibility()
    window._finish_fullscreen_restore()
    assert window.isVisible() and not window.speech_bubble.isVisible()
    assert manager.current is None
    assert not manager.notify("existing", "加油", "搭子给你加油", lambda: None)


def test_delayed_restore_keeps_native_monitor_reference(pet, monkeypatch):
    app, window = pet
    monkeypatch.setattr(window, "_pet_native_window_handle", lambda: 4242)
    references = []
    state = {"mode": "fullscreen"}
    monkeypatch.setattr("onepic_desktop_pet.window.active_window_display_mode",
        lambda bounds, *, reference_hwnd=None: references.append(reference_hwnd) or state["mode"])
    window._sync_fullscreen_visibility()
    state["mode"] = "normal"
    window._sync_fullscreen_visibility()
    # Foreground returns to fullscreen during the delayed native repair.
    state["mode"] = "fullscreen"
    window._finish_fullscreen_restore()
    assert not window.isVisible() and window._fullscreen_hidden
    assert references and set(references) == {4242}
