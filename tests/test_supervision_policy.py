"""验证备注身份、跨账号授权表单、旧回调拒绝及远端军官模式的真实规则。"""

import os
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from onepic_desktop_pet.buddy_identity import buddy_choice, buddy_name, public_name
from onepic_desktop_pet.discipline import DisciplineEngine, DisciplineStore, BEIJING_TIMEZONE
from onepic_desktop_pet.supervision_ui import SupervisionPolicyWidget


def policy(revision=1, enabled=True, mode="officer"):
    return {"policy": {"revision": revision, "enabled": enabled, "scope": "selected",
                      "selected_ids": ["b"], "officer_scope": "selected", "officer_ids": ["b"]},
            "effective_mode": mode, "supervising": []}


def test_private_identity_never_changes_public_identity_and_id_disambiguates():
    a = {"user_id": "user-a", "private_note_name": "论文搭子", "nickname": "毛毛冲", "status": "focus"}
    b = {"user_id": "user-b", "nickname": "毛毛冲"}
    assert buddy_name(a) == "论文搭子"
    assert public_name(a) == "毛毛冲"
    assert "论文搭子（毛毛冲）" in buddy_choice(a) and "🟢 专注中" in buddy_choice(a)
    assert "@user-a" in buddy_choice(a) and "@user-b" in buddy_choice(b)
    assert buddy_name({"user_id": "account-id"}) == "account-id"
    assert a["nickname"] == "毛毛冲"


def test_remote_officer_changes_real_work_rules_without_overwriting_own_preferences(tmp_path):
    store = DisciplineStore("a", path=tmp_path / "a.json")
    engine = DisciplineEngine(store)
    engine.apply_supervision(policy())
    assert engine.mode == "officer" and engine.enabled
    at = datetime(2026, 9, 30, 10, tzinfo=BEIJING_TIMEZONE)
    engine.record_work_event("start_work", 0, 0, at=at)
    assert any(e["requires_explanation"] for e in store.events)
    assert store.settings.mode == "off"
    assert store.sync_payload()["p_settings"] is None  # Consent does not dirty own work settings.
    assert engine.apply_supervision(policy(2, False))
    assert engine.mode == "off" and not engine.enabled
    assert not engine.apply_supervision(policy(1, True))
    assert not engine.enabled
    assert DisciplineEngine(DisciplineStore("a", path=store.path)).mode == "off"
    assert store.events  # Revocation preserves history.


def test_remote_mode_expires_on_disconnect_but_own_mode_is_retained(monkeypatch):
    engine = DisciplineEngine(DisciplineStore("a", persist=False))
    ticks = [1000.0]
    monkeypatch.setattr("onepic_desktop_pet.discipline.monotonic_time.monotonic", lambda: ticks[0])
    engine.apply_supervision(policy())
    assert engine.mode == "officer"
    ticks[0] += 121
    assert engine.mode == "off"
    engine.store.update_settings({"mode": "normal"})
    assert engine.mode == "normal"


def test_supervision_started_during_real_focus_does_not_invent_a_missing_start():
    engine = DisciplineEngine(DisciplineStore("a", persist=False))
    engine.apply_supervision(policy())
    at = datetime(2026, 9, 30, 11, tzinfo=BEIJING_TIMEZONE)
    notices = engine.evaluate(7200, 7200, at, working=True)
    assert all(notice.event_type != "late_start_warning" for notice in notices)
    assert not any(event["event_type"] == "start_work" for event in engine.store.events)


def test_policy_save_uses_server_revision_and_old_account_callback_is_discarded():
    app = QApplication.instance() or QApplication([])
    engines = [DisciplineEngine(DisciplineStore("a", persist=False)), DisciplineEngine(DisciplineStore("c", persist=False))]
    active = [engines[0]]
    calls = []
    panel = SupervisionPolicyWidget(lambda: active[0],
        lambda: [{"user_id": "b", "private_note_name": "论文搭子", "nickname": "毛毛冲"}],
        lambda name, body, callback, failure: calls.append((name, body, callback, failure)), lambda *_: None)
    panel.show(); app.processEvents()
    calls[-1][2](policy(7))
    assert panel.enabled.isChecked() and panel.revision == 7
    assert "论文搭子（毛毛冲）" in panel.selected.item(0).text()
    panel.permissions["view_plan"].setChecked(False)
    panel._save()
    assert calls[-1][1]["p_expected_revision"] == 7
    assert calls[-1][1]["p_policy"]["view_plan"] is False
    old_callback = calls[-1][2]
    active[0] = engines[1]
    old_callback(policy(8))
    assert engines[1].mode == "off"
    panel.refresh()
    assert panel.revision is None and not panel.enabled.isChecked()
    panel.close(); panel.deleteLater(); app.processEvents()


def test_failed_master_switch_does_not_claim_revocation_succeeded():
    app = QApplication.instance() or QApplication([])
    engine = DisciplineEngine(DisciplineStore("a", persist=False))
    calls = []
    panel = SupervisionPolicyWidget(lambda: engine, lambda: [],
        lambda name, body, callback, failure: calls.append((callback, failure)), lambda *_: None)
    panel._apply(policy())
    panel.enabled.setChecked(False); panel._toggle_enabled()
    calls[-1][1]("授权已在另一台电脑修改，请刷新后重试")
    assert panel.enabled.isChecked() and engine.mode == "officer"
    assert "另一台电脑" in panel.status.text()
    panel.close(); panel.deleteLater(); app.processEvents()
