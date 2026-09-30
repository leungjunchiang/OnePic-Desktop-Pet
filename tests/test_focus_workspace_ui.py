"""验证专注导航、计划保存、搭子授权撤销与跨账号回调隔离。"""

import os
from datetime import datetime
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTime
from PySide6.QtWidgets import QApplication, QLabel

from onepic_desktop_pet.buddy_study_ui import BuddyStudyDialog
from onepic_desktop_pet.discipline import BEIJING_TIMEZONE, DisciplineEngine, DisciplineStore
from onepic_desktop_pet.discipline_ui import DisciplineDialog
from onepic_desktop_pet.social_ui import SocialHubDialog


class Client:
    signed_in = False
    session = SimpleNamespace(user_id="account-a")
    backend_name = "Supabase Direct"
    backend_endpoint = "https://example.supabase.co"


def app():
    return QApplication.instance() or QApplication([])


def dispose(*widgets):
    for widget in widgets:
        widget.close()
        widget.deleteLater()
    app().processEvents()


def test_focus_navigation_and_network_diagnostics_have_stable_homes():
    qt = app()
    hub = SocialHubDialog(Client())
    assert [hub.focus_navigation.tabText(i) for i in range(4)] == ["今日", "工作计划", "训导主任", "记录"]
    assert [hub.focus_workspace.records.tabText(i) for i in range(3)] == ["今日", "本周", "历史"]
    home_labels = [label.text() for label in hub.tabs.widget(0).findChildren(QLabel)]
    mine_labels = [label.text() for label in hub.tabs.widget(3).findChildren(QLabel)]
    assert all("supabase.co" not in text for text in home_labels)
    assert any("example.supabase.co" in text for text in mine_labels)
    hub.open_focus_section(2)
    assert hub.tabs.currentIndex() == 2 and hub.focus_navigation.currentIndex() == 2
    assert "FocusSession" not in " ".join(label.text() for label in hub.findChildren(QLabel))
    dispose(hub)


def test_plan_save_keeps_mode_and_accepts_individual_day_schedules(tmp_path):
    qt = app()
    store = DisciplineStore("a", path=tmp_path / "a.json")
    store.update_settings({"mode": "officer"})
    panel = DisciplineDialog(store, DisciplineEngine(store), lambda: (3600, 7200))
    panel.weekly_target.setValue(31.5)
    panel.daily_targets["wed"].setValue(5.5)
    panel.daily_starts["wed"].setTime(QTime(10, 15))
    panel.daily_finishes["wed"].setTime(QTime(19, 30))
    panel.mode.setCurrentIndex(panel.mode.findData("normal"))
    panel._save_settings("plan")
    reloaded = DisciplineStore("a", path=store.path)
    assert reloaded.settings.mode == "officer"
    assert reloaded.settings.weekly_target_minutes == 1890
    assert reloaded.settings.daily_target_minutes["wed"] == 330
    day = datetime(2026, 9, 30, tzinfo=BEIJING_TIMEZONE).date()
    assert reloaded.settings.start_at(day).strftime("%H:%M") == "10:15"
    assert reloaded.settings.finish_at(day).strftime("%H:%M") == "19:30"
    panel._save_settings("mode")
    assert store.settings.mode == "normal"
    assert store.settings.weekly_target_minutes == 1890
    dispose(panel)


def test_account_switch_cannot_save_previous_accounts_form():
    qt = app()
    engines = [DisciplineEngine(DisciplineStore("a", persist=False)), DisciplineEngine(DisciplineStore("b", persist=False))]
    active = [engines[0]]
    panel = DisciplineDialog(active[0].store, active[0], lambda: (0, 0), engine_provider=lambda: active[0])
    panel.weekly_target.setValue(99)
    active[0] = engines[1]
    panel._save_settings("plan")
    assert engines[0].store.settings.weekly_target_minutes == 1800
    assert engines[1].store.settings.weekly_target_minutes == 1800
    assert panel.store.account_id == "b"
    dispose(panel)


def test_history_includes_previous_weeks_and_pending_explanations():
    qt = app()
    store = DisciplineStore("a", persist=False)
    store.append_event("late_start", datetime(2026, 8, 1, 10, tzinfo=BEIJING_TIMEZONE), requires_explanation=True)
    panel = DisciplineDialog(store, DisciplineEngine(store), lambda: (0, 0))
    assert panel.ledger.rowCount() == 1
    assert panel.ledger.item(0, 0).text() == "2026-08-01"
    assert "1 项" in panel.duty_status.text()
    dispose(panel)


def test_buddy_room_refresh_clears_revoked_plans_and_reports():
    qt = app()
    hub = SocialHubDialog(Client())
    callbacks = []
    hub.study_rpc = lambda name, body, callback, failure: callbacks.append((name, callback))
    buddy = {"user_id": "buddy-a", "nickname": "测试搭子", "on_focus_start": True, "today_seconds": 3600, "week_seconds": 7200}
    room = BuddyStudyDialog(hub, buddy)
    room.show(); room.tabs.setCurrentIndex(4); room.refresh()
    room._apply_overview({"peer_plan": {"weekly_target_minutes": 2100}, "can_read_reports": True})
    report_callback = callbacks[-1][1]
    assert "35小时" in room.peer_plan.text()
    room._apply_overview({"peer_plan": None, "can_read_reports": False})
    report_callback({"reports": [{"event_date": "2026-09-30", "metadata": {"today_seconds": 999}}]})
    assert room.records.count() == 0
    assert "未授权" in room.peer_plan.text()
    assert room.subscriptions["start_work"].isChecked()
    dispose(room, hub)


def test_buddy_room_discards_callbacks_from_previous_account():
    qt = app()
    client = Client(); client.session = SimpleNamespace(user_id="account-a")
    hub = SocialHubDialog(client)
    callbacks = []
    hub.study_rpc = lambda name, body, callback, failure: callbacks.append(callback)
    room = BuddyStudyDialog(hub, {"user_id": "buddy-a"})
    room.show(); room.refresh()
    client.session = SimpleNamespace(user_id="account-b")
    callbacks[0]({"peer_plan": {"weekly_target_minutes": 2100}, "can_read_reports": True})
    assert room._overview == {}
    hub._buddy_study_dialogs[("account-a", "buddy-a")] = room
    hub._update_account_state()
    assert not room.isVisible()
    dispose(room, hub)


def test_card_study_entry_and_repeated_deep_link_reuse_room():
    qt = app()
    hub = SocialHubDialog(Client())
    hub.study_rpc = lambda *_args: None
    buddy = {"user_id": "buddy-a", "nickname": "测试搭子"}
    hub.open_buddy_study(buddy)
    hub.open_buddy_study(buddy, 3)
    assert len(hub._buddy_study_dialogs) == 1
    room = next(iter(hub._buddy_study_dialogs.values()))
    assert room.tabs.currentIndex() == 3
    dispose(room, hub)
