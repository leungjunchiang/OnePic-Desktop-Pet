"""验证专注导航、备注身份、直接监督、撤权与跨账号回调隔离。"""

import os
from datetime import datetime, timedelta
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


def test_plan_save_keeps_mode_and_uses_weekly_goal_workdays_and_optional_finish(tmp_path):
    qt = app()
    store = DisciplineStore("a", path=tmp_path / "a.json")
    store.update_settings({"mode": "officer"})
    panel = DisciplineDialog(store, DisciplineEngine(store), lambda: (3600, 7200))
    panel.weekly_target.setValue(31.5)
    panel.workdays["fri"].setChecked(False)
    panel.usual_start.setTime(QTime(10, 15))
    panel.usual_finish.setTime(QTime(19, 30))
    panel.finish_enabled.setChecked(True)
    panel.mode.setCurrentIndex(panel.mode.findData("normal"))
    panel._save_settings("plan")
    reloaded = DisciplineStore("a", path=store.path)
    assert reloaded.settings.mode == "officer"
    assert reloaded.settings.weekly_target_minutes == 1890
    assert reloaded.settings.workdays == ["mon", "tue", "wed", "thu"]
    assert reloaded.settings.planned_finish_enabled
    assert reloaded.settings.for_weekday(datetime(2026, 9, 30).date()) == 473
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


def test_recent_historical_day_cache_expires_quickly_after_midnight():
    qt = app()
    store = DisciplineStore("a", persist=False)
    engine = DisciplineEngine(store)

    class Analytics:
        def __init__(self):
            self.value = 100
            self.calls = 0

        def account_today_seconds(self, _at):
            self.calls += 1
            return self.value

    class Owner:
        def __init__(self):
            self.focus_analytics = Analytics()
            self._focus_projection_revision = 0

        def engine(self):
            return engine

    owner = Owner()
    panel = DisciplineDialog(
        store, engine, lambda: (0, 0), engine_provider=owner.engine
    )
    yesterday = datetime.now(BEIJING_TIMEZONE).date() - timedelta(days=1)
    first = panel._completed_day_seconds(yesterday, 0)
    assert first == 100 and owner.focus_analytics.calls == 1
    key = (store.account_id, yesterday.isoformat())
    cached_at, value = panel._day_totals_cache[key]
    panel._day_totals_cache[key] = (cached_at - 9, value)
    owner.focus_analytics.value = 120
    assert panel._completed_day_seconds(yesterday, 0) == 120
    assert owner.focus_analytics.calls == 2
    dispose(panel)


def test_current_day_record_uses_live_progress_without_history_cache():
    qt = app()
    store = DisciplineStore("a", persist=False)
    engine = DisciplineEngine(store)

    class Analytics:
        def account_today_seconds(self, _at):
            raise AssertionError("current day must not use historical cache")

    class Owner:
        focus_analytics = Analytics()
        _focus_projection_revision = 0

        def engine(self):
            return engine

    owner = Owner()
    panel = DisciplineDialog(
        store, engine, lambda: (0, 0), engine_provider=owner.engine
    )
    today = datetime.now(BEIJING_TIMEZONE).date()
    assert panel._completed_day_seconds(today, 9 * 3600 + 59 * 60) == 9 * 3600 + 59 * 60
    dispose(panel)

def test_history_includes_previous_weeks_and_pending_explanations():
    qt = app()
    store = DisciplineStore("a", persist=False)
    store.append_event("late_start", datetime(2026, 8, 1, 10, tzinfo=BEIJING_TIMEZONE), requires_explanation=True)
    panel = DisciplineDialog(store, DisciplineEngine(store), lambda: (0, 0))
    panel.records.setCurrentIndex(2)
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
    assert "TA 暂未向你公开工作计划。" in room.peer_plan.text()
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


def test_buddy_supervision_is_direct_and_private_note_updates_title():
    qt = app()
    hub = SocialHubDialog(Client())
    calls = []
    hub.study_rpc = lambda name, body, callback, failure: calls.append((name, body, callback))
    buddy = {"user_id": "b", "nickname": "毛毛冲", "private_note_name": "论文搭子"}
    hub.data["buddies"] = [buddy]
    room = BuddyStudyDialog(hub, buddy)
    room.show(); room.tabs.setCurrentIndex(3)
    room._apply_overview({"peer_permission": {"enabled": True, "eligible": True, "officer": False, "remind": True}})
    assert room.windowTitle() == "论文搭子 · 搭子详情"
    assert "论文搭子\n毛毛冲" in room.status_summary.text()
    assert room.start_normal.isEnabled() and not room.start_officer.isEnabled()
    room.start_normal.click()
    assert calls[-1][:2] == ("lili_start_supervision", {"p_owner_id": "b", "p_mode": "normal"})
    room._apply_overview({"peer_permission": {"enabled": False, "eligible": False}})
    assert not room.start_normal.isEnabled() and all(not button.isEnabled() for button in room.nudges.values())
    assert "尚未开启" in room.relationship.text()
    hub._buddy_study_dialogs[("account-a", "b")] = room
    hub._update_private_note_snapshot("b", "室友")
    assert room.windowTitle().startswith("室友 · ")
    dispose(room, hub)


def test_failed_authority_refresh_clears_protected_cache():
    qt = app()
    hub = SocialHubDialog(Client())
    calls = []
    hub.study_rpc = lambda name, body, callback, failure: calls.append((name, callback, failure))
    room = BuddyStudyDialog(hub, {"user_id": "b"})
    room.show(); room.refresh()
    room._apply_overview({"peer_permission": {"eligible": True}, "peer_plan": {"weekly_target_minutes": 2100}, "can_read_reports": True})
    room.records.addItem("已显示的摘要")
    room.refresh()
    calls[-1][2]("只能查看已确认搭子的自习室")
    assert room.records.count() == 0 and room._overview == {}
    assert not room.start_normal.isEnabled()
    assert "清除缓存" in room.peer_plan.text()
    dispose(room, hub)


def test_authorized_report_open_records_read_receipt_and_discards_revoked_callback():
    qt = app()
    hub = SocialHubDialog(Client())
    calls = []
    hub.study_rpc = lambda name, body, callback, failure: calls.append((name, body, callback))
    room = BuddyStudyDialog(hub, {"user_id": "b"})
    room.show(); room.tabs.setCurrentIndex(4)
    room._apply_overview({"can_read_reports": True})
    room._apply_reports({"reports": [{"event_date": "2026-09-30", "metadata": {"today_seconds": 3600}}]})
    assert calls[-1][:2] == ("lili_mark_discipline_report_read", {"p_owner_id": "b", "p_report_date": "2026-09-30"})
    old_receipt = calls[-1][2]
    room._apply_overview({"can_read_reports": False})
    old_receipt({"read_at": "2026-09-30"})
    assert room.records.count() == 0
    dispose(room, hub)
