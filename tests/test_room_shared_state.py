"""Regression checks for room-scoped state, interaction feedback and idle pause."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ONEPIC_USE_DEMO_ASSETS", "1")

from PySide6.QtWidgets import QApplication, QLabel

from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.idle_classifier import IdleEvidence
from onepic_desktop_pet.social_ui import BuddyCardWidget, SocialHubDialog
from onepic_desktop_pet.window import PetWindow
from onepic_desktop_pet.work_timer import WorkTimerModel


class RoomClient:
    signed_in = True

    def dashboard(self, room_id=None):
        data = {
            "me": {"nickname": "我", "invite_code": "AB12CD34", "show_exact_time": True},
            "buddies": [], "requests": [], "visits": [],
            "rooms": [{"id": "room-1", "name": "论文冲刺", "members": 2, "invite_code": "ROOM1234"}],
        }
        if room_id:
            data["current_room"] = {
                "room_people": [{"user_id": "peer", "nickname": "搭子", "working": True, "status": "focus", "today_seconds": 120}],
                "room_summary": {
                    "member_count": 2,
                    "focus_count": 2,
                    "today_shared_focus_seconds": 120,
                    "cumulative_shared_focus_seconds": 240,
                    "shared_focus_seconds": 240,
                },
                "room_goal": {"title": "完成第二节", "target_seconds": 1800, "completed_seconds": 240},
                # Supabase commonly returns UTC timestamps; the UI must show
                # the same event as 12:34 Beijing time.
                "room_activity": [{"kind": "cheer", "nickname": "我", "target_nickname": "搭子", "created_at": "2026-08-13T04:34:00+00:00"}],
            }
        return data

    def send_interaction(self, **_kwargs):
        return None


def test_study_room_is_resizable_and_renders_room_scoped_summary():
    app = QApplication.instance() or QApplication([])
    dialog = SocialHubDialog(RoomClient())
    dialog.refresh()
    app.processEvents()
    dialog.rooms.setCurrentRow(0)
    app.processEvents()
    assert dialog.minimumWidth() <= 520
    assert dialog.minimumHeight() <= 480
    assert dialog.isSizeGripEnabled()
    assert "2 人" in dialog.room_summary.text()
    assert "今日共同专注 2分钟" in dialog.room_summary.text()
    assert "累计共同专注 4分钟" in dialog.room_summary.text()
    assert "完成第二节" in dialog.room_goal.text()
    assert "加油" in dialog.room_activity.item(0).text()
    assert dialog.room_activity.item(0).text().startswith("12:34")
    dialog.close(); dialog.deleteLater(); app.processEvents()


def test_first_open_does_not_enter_any_room_implicitly():
    app = QApplication.instance() or QApplication([])

    class MultiRoomClient(RoomClient):
        def dashboard(self, room_id=None):
            data = super().dashboard(room_id)
            data["rooms"] = [
                {"id": "old", "name": "个人房", "members": 1, "invite_code": "OLDROOM1"},
                {"id": "shared", "name": "共同房", "members": 3, "invite_code": "SHARED01"},
            ]
            return data

    dialog = SocialHubDialog(MultiRoomClient())
    dialog.refresh(); app.processEvents()
    assert dialog.current_room_id is None
    assert dialog._room_selection_explicit is False
    dialog.rooms.setCurrentRow(1)
    app.processEvents()
    assert dialog.current_room_id == "shared"
    dialog.close(); dialog.deleteLater(); app.processEvents()


def test_interaction_button_has_a_visible_cooldown():
    app = QApplication.instance() or QApplication([])
    widget = BuddyCardWidget({"user_id": "peer", "nickname": "搭子", "online": True, "working": True})
    button = widget._buttons["visit"]
    button.click()
    app.processEvents()
    assert not button.isEnabled()
    assert "已发送" in button.text()
    widget.deleteLater(); app.processEvents()


def test_social_card_uses_owner_nickname_without_renaming_pet():
    app = QApplication.instance() or QApplication([])
    widget = BuddyCardWidget({"user_id": "peer", "nickname": "小梁", "online": True, "working": True})
    headline = widget.findChildren(QLabel)[0].text()
    assert "小梁家的六毛正在工作" in headline
    assert "小梁家的六毛家的六毛" not in headline
    widget.deleteLater(); app.processEvents()


def test_social_card_prefers_explicit_owner_nickname_and_marks_stale_presence_offline():
    app = QApplication.instance() or QApplication([])
    widget = BuddyCardWidget({
        "user_id": "peer",
        "nickname": "搭子",
        "owner_nickname": "小梁",
        "online": True,
        "working": True,
        "status": "focus",
        "stale_presence": True,
    })
    labels = widget.findChildren(QLabel)
    assert "小梁家的六毛已离线" in labels[0].text()
    assert "离线缓存" in labels[1].text()
    assert "六毛搭子的六毛" not in labels[0].text()
    widget.deleteLater(); app.processEvents()


def test_input_idle_under_threshold_keeps_focus(monkeypatch, tmp_path):
    """Reading, thinking, or switching apps get the full ten-minute grace."""
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    window = PetWindow(PetSettings(idle_pause_seconds=600))
    monkeypatch.setattr(
        "onepic_desktop_pet.window.system_session_state",
        lambda: {"locked": False, "sleeping": False},
    )
    monkeypatch.setattr("onepic_desktop_pet.window.system_idle_seconds", lambda: 599)
    window.start_work_timer()
    for _ in range(5):
        window._check_input_idle()
    assert window.work_timer.is_running
    assert window._auto_paused_for_idle is False
    window.close(); window.deleteLater(); app.processEvents()


def test_verified_sleep_pauses_focus_without_idle_recovery(monkeypatch, tmp_path):
    """Only a verified sleep signal may pause a running session automatically."""
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    window = PetWindow(PetSettings(idle_pause_seconds=600))
    monkeypatch.setattr(
        "onepic_desktop_pet.window.system_session_state",
        lambda: {"locked": False, "sleeping": True},
    )
    window.start_work_timer()
    window._check_input_idle()
    assert not window.work_timer.is_running
    assert window._auto_paused_for_idle is False
    assert "睡眠" in window.speech_bubble.text()
    window.close(); window.deleteLater(); app.processEvents()


def test_room_quick_actions_change_real_focus_state_and_expire_status(tmp_path):
    app = QApplication.instance() or QApplication([])
    timer = WorkTimerModel(path=Path(tmp_path) / "timer.json")
    window = PetWindow(PetSettings(), timer)
    window._room_quick_action("再卷 30 分钟")
    assert timer.is_running
    assert window._room_quick_status == "再卷30分钟"
    assert window._room_quick_status_expires_at is not None
    window._room_quick_action("去喝水")
    assert not timer.is_running
    assert window._room_quick_status == "去喝水"
    window.close(); window.deleteLater(); app.processEvents()
