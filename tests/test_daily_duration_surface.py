"""今日累计胶囊跨专注状态常驻；训导牌有独立定位，仍遵守隐藏与全屏门禁。"""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt

from test_window import _create_window


@pytest.fixture
def pet(monkeypatch):
    monkeypatch.setattr("onepic_desktop_pet.window.detect_quiet_mode",
                        lambda: SimpleNamespace(blocked=False))
    monkeypatch.setattr("onepic_desktop_pet.window.active_window_display_mode",
                        lambda *_args, **_kwargs: "normal")
    app, window = _create_window()
    try:
        yield app, window
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def render(window, status, seconds=3 * 3600 + 21 * 60 + 8):
    window._update_work_duration_bubble(SimpleNamespace(status=status), display_seconds=seconds)


def test_idle_daily_total_is_rendered_without_paused_state(pet):
    _, window = pet
    render(window, "focus")
    render(window, "rest")
    bubble = window.work_duration_bubble
    assert bubble.isVisible() and "已暂停" in bubble.text()
    render(window, "idle")
    assert bubble.isVisible()
    assert bubble.text() == "今日已工作 3:21:08"
    assert not bubble.property("paused")
    assert bubble.toolTip() == "今日累计工作时间"
    assert bubble.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert bubble.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus


def test_finish_real_session_keeps_daily_total(pet):
    _, window = pet
    window.focus_session.start()
    window._update_work_duration_bubble(display_seconds=180)
    window.focus_session.pause()
    window._update_work_duration_bubble(display_seconds=180)
    assert "已暂停" in window.work_duration_bubble.text()
    window.focus_session.finish()
    window._update_work_duration_bubble(display_seconds=180)
    assert window.focus_session.snapshot(include_projection=False).status == "idle"
    assert window.work_duration_bubble.isVisible()
    assert window.work_duration_bubble.text() == "今日已工作 03:00"


def test_reenable_after_paused_hidden_pill_clears_old_paused_style(pet):
    _, window = pet
    render(window, "rest")
    window.settings.show_work_duration = False
    render(window, "rest")
    assert not window.work_duration_bubble.isVisible()
    window.settings.show_work_duration = True
    render(window, "idle", 0)
    assert window.work_duration_bubble.isVisible()
    assert window.work_duration_bubble.text() == "今日已工作 00:00"
    assert not window.work_duration_bubble.property("paused")
    assert window.work_duration_bubble.surface_fill.name() == "#f6fbfb"


@pytest.mark.parametrize("mode", ["manual", "fullscreen"])
def test_idle_daily_total_obeys_suppression_and_restores(pet, monkeypatch, mode):
    app, window = pet
    render(window, "idle")
    assert window.work_duration_bubble.isVisible()
    if mode == "manual":
        window.hide_pet()
    else:
        monkeypatch.setattr("onepic_desktop_pet.window.active_window_display_mode",
                            lambda *_args, **_kwargs: "fullscreen")
        window._sync_fullscreen_visibility()
    render(window, "idle")
    app.processEvents()
    assert not window.work_duration_bubble.isVisible()
    monkeypatch.setattr("onepic_desktop_pet.window.active_window_display_mode",
                        lambda *_args, **_kwargs: "normal")
    if mode == "manual":
        window.show_pet()
    else:
        window._sync_fullscreen_visibility()
    render(window, "idle")
    assert window.work_duration_bubble.isVisible()
    assert "已暂停" not in window.work_duration_bubble.text()


def test_coaching_badge_falls_back_to_pet_and_rejoins_visible_daily_pill(pet, monkeypatch):
    _, window = pet
    engine = SimpleNamespace(store=SimpleNamespace(coaching_cases=[]))
    monkeypatch.setattr(window, "_ensure_discipline_engine", lambda: engine)
    monkeypatch.setattr(window, "_discipline_progress_seconds", lambda: (100, 100))
    monkeypatch.setattr("onepic_desktop_pet.coaching.projection", lambda *_args: {
        "card": None, "badge": {"id": "late", "badge_text": "今天迟到了 · 再认真一会"}})
    render(window, "idle")
    window._refresh_desktop_coaching(engine)
    badge = window._coaching_surfaces["badge"]
    bubble = window.work_duration_bubble
    assert badge.isVisible()
    assert badge.y() == bubble.y()
    assert badge.height() == bubble.height()
    # Turn off only the daily total, then poison the hidden widget's position.
    window.settings.show_work_duration = False
    bubble.move(-5000, -5000)
    render(window, "idle")
    assert not bubble.isVisible() and badge.isVisible()
    area = window._screen_geometry()
    expected_y = min(max(window.y() + window.height() + 5, area.top()),
                     area.bottom() - badge.height() + 1)
    assert badge.y() == expected_y
    assert badge.x() >= area.left()
    window.settings.show_work_duration = True
    render(window, "idle")
    assert bubble.isVisible() and badge.isVisible()
    assert badge.y() == bubble.y()
    assert badge.x() == max(area.left(), bubble.x() - badge.width() - 6)
    window.hide_pet()
    render(window, "idle")
    assert not bubble.isVisible() and not badge.isVisible()
