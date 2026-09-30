"""验证卡片核心操作、真实反应状态、共享冷却与窄窗口提醒标签布局。"""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from onepic_desktop_pet.social_ui import BuddyCardWidget, BUDDY_FEED_ITEMS


@pytest.fixture
def card():
    app = QApplication.instance() or QApplication([])
    widget = BuddyCardWidget({"user_id": "b", "private_note_name": "论文搭子", "nickname": "毛毛冲",
                              "online": True, "working": True, "status": "focus",
                              "on_focus_start": True, "on_focus_end": True,
                              "today_seconds": 17820, "week_seconds": 71340})
    yield widget, app
    widget.close(); widget.deleteLater(); app.processEvents()


@pytest.mark.parametrize("width", [420, 520, 760])
def test_five_actions_fit_and_badge_is_above_footer(card, width):
    widget, app = card
    widget.resize(width, 230); widget.show(); app.processEvents()
    buttons = [widget.study_button, *widget._buttons.values(), widget.feed_button]
    assert widget.width() == width
    assert all(b.isVisible() and b.height() >= 32 and b.width() >= 57 for b in buttons)
    assert 0.25 <= widget.study_button.width() / (width - 16) <= 0.30
    assert buttons[-1].geometry().right() < width
    assert widget.reminder_summary.isVisible()
    assert widget.reminder_summary.width() >= widget.reminder_summary.sizeHint().width()
    assert widget.reminder_summary.height() >= widget.reminder_summary.sizeHint().height()
    assert widget.reminder_summary.y() < widget._focus_label.y() < widget.study_button.y()
    assert "#60451e" in widget.reminder_summary.styleSheet()
    assert "4小时57分钟" in widget._focus_label.text()


def test_reactions_reuse_cheer_handler_and_share_cooldown_across_status_updates(card, monkeypatch):
    widget, _ = card
    monkeypatch.setattr("onepic_desktop_pet.social_ui._taunt_window_open", lambda: True)
    ticks = [1000.0]
    monkeypatch.setattr("onepic_desktop_pet.social_ui.time.monotonic", lambda: ticks[0])
    sent = []
    widget.interaction_requested.connect(lambda buddy, kind: sent.append(kind))
    assert widget._buttons["cheer"].isEnabled() and not widget._buttons["taunt"].isEnabled()
    widget._buttons["cheer"].click()
    widget.update_buddy({**widget.buddy, "status": "rest", "working": False})
    assert not widget._buttons["taunt"].isEnabled()
    widget._buttons["taunt"].click()
    assert sent == ["cheer"]
    ticks[0] += 16
    widget._restore_button("cheer")
    assert widget._buttons["taunt"].isEnabled() and not widget._buttons["cheer"].isEnabled()
    widget._buttons["taunt"].click()
    assert sent == ["cheer", "cheer"]


def test_feed_config_and_reminder_updates_keep_existing_events(card):
    widget, _ = card
    sent = []
    widget.food_interaction_requested.connect(lambda buddy, kind: sent.append(kind))
    for kind, _ in BUDDY_FEED_ITEMS:
        widget._food_buttons[kind].trigger()
    assert sent == [kind for kind, _ in BUDDY_FEED_ITEMS]
    widget.update_buddy({**widget.buddy, "on_focus_end": False})
    assert widget.reminder_summary.text() == "🔔 开工提醒已开启"
    widget.update_buddy({**widget.buddy, "on_focus_start": False})
    assert widget.reminder_summary.isHidden()
    widget.update_buddy({**widget.buddy, "is_self": True})
    assert not any(b.isEnabled() for b in [widget.study_button, *widget._buttons.values(), widget.feed_button])
