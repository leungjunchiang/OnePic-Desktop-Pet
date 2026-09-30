"""验证周目标唯一、免战日、旧设备兼容、对象边界与等宽导航。"""

import os
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QLabel, QPushButton
from onepic_desktop_pet.discipline import BEIJING_TIMEZONE, DisciplineEngine, DisciplineStore
from onepic_desktop_pet.buddy_study_ui import BuddyStudyDialog
from onepic_desktop_pet.social_ui import SocialHubDialog, BuddyCardWidget, _presence_status


def moment(day=29, hour=12):
    return datetime(2026, 9, day, hour, tzinfo=BEIJING_TIMEZONE)


def test_week_goal_redistributes_under_and_over_completion_without_daily_authority():
    store = DisciplineStore("a", persist=False)
    store.update_settings({"weekly_target_minutes": 1800, "daily_target_minutes": {"mon": 500, "tue": 500}})
    engine = DisciplineEngine(store)
    # Tuesday, after Monday: 30h - 7h spread across four working days.
    assert engine.progress(0, 7 * 3600, moment()).daily_target_seconds == 23 * 3600 // 4
    assert engine.progress(0, 4 * 3600, moment()).daily_target_seconds == 26 * 3600 // 4
    # Work done today cannot make its original reference shrink a second time.
    assert engine.progress(2 * 3600, 6 * 3600, moment()).daily_target_seconds == 26 * 3600 // 4
    assert engine.progress(3600, 31 * 3600, moment()).daily_target_seconds == 0
    store.settings.catchup_strategy = "none"
    assert engine.progress(0, 4 * 3600, moment()).daily_target_seconds == 0
    assert engine.progress(0, 4 * 3600, moment()).weekly_remaining_seconds == 26 * 3600


def test_exemption_suppresses_all_rules_and_pending_reasons_but_keeps_week_goal(tmp_path):
    store = DisciplineStore("a", path=tmp_path / "discipline.json")
    store.update_settings({"mode": "officer", "planned_finish_enabled": True})
    engine = DisciplineEngine(store)
    store.append_event("late_start", moment(), requires_explanation=True)
    assert store.due_explanations()
    assert store.exempt_today(moment())
    assert not store.due_explanations()
    assert not engine.evaluate(0, 12 * 3600, moment())
    assert not engine.record_work_event("start_work", 0, 12 * 3600, moment())
    assert not engine.record_work_event("finish_work", 0, 12 * 3600, moment())
    assert not engine.break_notices(moment())
    progress = engine.progress(0, 12 * 3600, moment())
    assert progress.daily_target_seconds == 0
    assert progress.weekly_remaining_seconds == 18 * 3600
    assert progress.remaining_workdays == 3
    next_day = engine.progress(0, 12 * 3600, moment(30))
    assert next_day.daily_target_seconds == 6 * 3600
    assert not store.is_exempt(moment(30).date())
    # Newer preferences from another device cannot erase append-only rest days.
    store.merge_remote({"client_updated_at": "2030-01-01T00:00:00Z", "settings": {"mode": "officer"}, "events": []})
    reloaded = DisciplineStore("a", path=store.path)
    assert reloaded.is_exempt(moment().date()) and not reloaded.due_explanations()
    assert not reloaded.exempt_today(moment(26))  # fixed Saturday rest day


def test_early_finish_is_opt_in_and_grace_applies_to_strict_mode():
    store = DisciplineStore("a", persist=False)
    store.update_settings({"mode": "officer"})
    engine = DisciplineEngine(store)
    assert store.settings.late_grace_minutes == 30
    assert not engine.evaluate(0, 0, moment(30, 9).replace(minute=30))
    engine.record_work_event("finish_work", 3600, 3600, moment(30, 12))
    assert not any(event["event_type"] == "early_finish" for event in store.events)
    assert next(event for event in store.events if event["event_type"] == "daily_report")["metadata"]["planned_finish"] == ""
    store.settings.planned_finish_enabled = True
    assert any(notice.event_type == "early_finish" for notice in engine.record_work_event("finish_work", 3600, 3600, moment(30, 13)))


class Client:
    signed_in = False
    session = SimpleNamespace(user_id="a")
    backend_name = "Supabase Direct"
    backend_endpoint = "https://example.supabase.co"


def test_buddy_boundary_pair_pause_dynamic_actions_and_no_sensitive_default():
    app = QApplication.instance() or QApplication([])
    hub = SocialHubDialog(Client())
    calls = []
    hub.study_rpc = lambda name, body, cb, failed: calls.append((name, body))
    room = BuddyStudyDialog(hub, {"user_id": "b", "private_note_name": "论文搭子", "nickname": "hjy"})
    room.show()
    assert room.windowTitle() == "论文搭子 · 搭子详情"
    assert [room.tabs.tabText(i) for i in range(5)] == ["状态", "一起专注", "TA的计划", "监督关系", "纪律记录"]
    text = " ".join(label.text() for label in room.findChildren(QLabel)) + " ".join(button.text() for button in room.findChildren(QPushButton))
    assert all(word not in text for word in ("我的计划", "编辑我的", "设置谁可以", "军官"))
    room._apply_overview({"peer_permission": {"eligible": True, "officer": True}, "own_permission": {"eligible": True, "officer": True}, "peer_active_mode": "officer", "actions": ["cheer", "take_break"], "can_read_reports": False})
    assert room.nudges["cheer"].isEnabled() and not room.nudges["start"].isEnabled()
    assert "严格训导" in room.inverse_relationship.text()
    room.stop_peer.click()
    assert calls[-1] == ("lili_pause_peer_supervision", {"p_supervisor_id": "b", "p_paused": True})
    room._apply_overview({"peer_permission": {"eligible": True, "officer": True}, "exempt": True, "actions": ["cheer", "start"]})
    assert not room.start_normal.isEnabled() and not room.start_officer.isEnabled()
    assert all(not button.isEnabled() for button in room.nudges.values())
    assert "暂停训导" in room.rest_hint.text()
    assert "TA 暂未向你公开工作计划。" in room.peer_plan.text()
    room.close(); hub.close(); app.processEvents()


def test_focus_tabs_remain_equal_on_resize_and_rest_is_not_offline():
    app = QApplication.instance() or QApplication([])
    hub = SocialHubDialog(Client()); hub.show(); hub.open_focus_section(1)
    for width in (1100, 820):
        hub.resize(width, 750); app.processEvents()
        bar = hub.focus_navigation.tabBar()
        widths = [bar.tabRect(i).width() for i in range(4)]
        assert max(widths) - min(widths) <= 1
        assert abs(sum(widths) - hub.focus_navigation.width()) <= 4
        assert bar.height() in range(54, 61)
    today = datetime.now(BEIJING_TIMEZONE).date()
    record = {"user_id": "b", "nickname": "hjy", "online": False, "rest_day_date": today.isoformat()}
    assert _presence_status(record) == "offline"
    record["online"] = True
    assert _presence_status(record) == "exempt"
    card = BuddyCardWidget(record)
    assert "高挂免战牌" in card._headline_label.text()
    record["rest_day_date"] = (today - timedelta(days=1)).isoformat()
    assert _presence_status(record) != "exempt"
    card.close(); hub.close(); app.processEvents()


def test_study_deployment_preserves_latest_projection_without_replaying_backfill():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/deploy-supabase-focus-history.yml").read_text(encoding="utf-8")
    script = (root / "scripts/apply_supabase_discipline.ps1").read_text(encoding="utf-8")
    assert '"supabase/migrations/**"' not in workflow
    assert '"supabase/migrations/*focus*.sql"' in workflow
    assert "projectionSource.Substring($projectionStart)" in script
    assert 'begin;`n$projectionSql' in script
