"""验证用户主动浮层与被动通知的显示权限分流。

这些回归覆盖 v0.23.313 将快捷口袋、悬停提示和本地特效误接入
quiet-mode 被动门禁的问题。测试只使用本地 Qt/offscreen 状态，不发网络请求。
"""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ONEPIC_USE_DEMO_ASSETS", "1")

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from test_window import _create_window


@pytest.fixture
def pet(monkeypatch):
    app, window = _create_window()
    for timer in window.findChildren(
        __import__("PySide6.QtCore", fromlist=["QTimer"]).QTimer
    ):
        timer.stop()
    monkeypatch.setattr(window, "_foreground_display_mode", lambda: "normal")
    monkeypatch.setattr(
        "onepic_desktop_pet.window.detect_quiet_mode",
        lambda: SimpleNamespace(blocked=True, reason="游戏中"),
    )
    yield app, window
    window.close()
    window.deleteLater()
    app.processEvents()


def test_quiet_mode_allows_explicit_quick_panel_but_blocks_passive_toast(
    pet, monkeypatch
):
    app, window = pet
    events = []
    monkeypatch.setattr(
        "onepic_desktop_pet.window.lifecycle_log",
        lambda event, *args, **kwargs: events.append((event, dict(kwargs))),
    )

    window.show_quick_panel()
    app.processEvents()

    assert window.quick_panel.isVisible()
    assert window.quick_panel.hide_timer.isActive()
    assert any(
        event == "quick_panel.show.request"
        and data.get("source") == "user_action"
        and data.get("quiet_mode") is True
        and data.get("decision") == "allow_explicit"
        for event, data in events
    )

    window.quick_panel.hide()
    assert not window.notification_manager.notify(
        "buddy:quiet",
        "搭子提醒",
        "后台被动提醒",
        lambda: None,
    )
    window.notification_manager.flush()
    assert window.notification_manager.current is None


def test_quick_panel_hover_and_work_report_remain_user_actions(pet, monkeypatch):
    app, window = pet
    monkeypatch.setattr(window, "_schedule_social_tick", lambda **kwargs: None)

    window.show_quick_panel()
    panel = window.quick_panel
    panel._button_at_global_pos = lambda _position: panel.work_button
    panel._set_hover_button(panel.work_button)
    panel._show_hint(panel.work_button)
    app.processEvents()

    assert panel.isVisible()
    assert panel.report_button.isVisible()
    assert panel.hover_hint.isVisible()

    panel.report_button.click()
    app.processEvents()

    assert window._work_report_dialog is not None
    assert window._work_report_dialog.isVisible()
    assert not panel.isVisible()
    window._work_report_dialog.close()


def test_work_controls_and_explicit_speech_ignore_quiet_mode(pet):
    app, window = pet

    window.show_work_controls()
    app.processEvents()
    assert window.work_controls.isVisible()

    window.work_controls.hide()
    window.show_speech("后台自动话语", 2000)
    app.processEvents()
    assert not window.speech_bubble.isVisible()

    window.show_speech("用户刚刚点了六毛", 2000, source="user_action")
    app.processEvents()
    assert window.speech_bubble.isVisible()


def test_quick_food_actions_still_dispatch_while_quiet(pet, monkeypatch):
    _app, window = pet
    calls = []
    supplies = []
    monkeypatch.setattr(
        window,
        "_start_food_scene",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(
        window,
        "show_food_scene_dialog",
        lambda: supplies.append("cake"),
    )

    window._quick_food_action("coffee")
    window._quick_food_action("milk_tea")
    window._quick_food_action("cake")

    assert [call[0][0] for call in calls] == ["coffee", "milk_tea"]
    assert calls[0][1]["source"] == "quick_food"
    assert calls[1][0][1] == 10
    assert supplies == ["cake"]


def _right_double_click(window):
    return QMouseEvent(
        QEvent.Type.MouseButtonDblClick,
        QPointF(window.width() / 2.0, window.height() / 2.0),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier,
    )


def test_color_mist_user_action_has_visible_renderer_and_clean_toggle(pet):
    app, window = pet
    event = _right_double_click(window)

    window.mouseDoubleClickEvent(event)
    app.processEvents()

    assert window._local_effect_manager.color_mist_world_active is True
    assert window._local_burst_effect.isVisible()

    window.mouseDoubleClickEvent(event)
    app.processEvents()

    assert window._local_effect_manager.color_mist_world_active is False
    assert not window._local_burst_effect.active
    assert not window._local_burst_effect.isVisible()


def test_true_fullscreen_still_blocks_explicit_surface_and_color_mist(
    pet, monkeypatch
):
    app, window = pet
    monkeypatch.setattr(window, "_foreground_display_mode", lambda: "fullscreen")

    window.show_quick_panel()
    app.processEvents()
    assert not window.quick_panel.isVisible()

    window.mouseDoubleClickEvent(_right_double_click(window))
    app.processEvents()
    assert window._local_effect_manager.color_mist_world_active is False
    assert not window._local_burst_effect.isVisible()
