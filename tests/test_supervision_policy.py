"""验证备注身份、跨账号授权表单、旧回调拒绝及远端军官模式的真实规则。"""

import os
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
import pytest

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
    panel._save()
    calls[-1][1]("授权已在另一台电脑修改，请刷新后重试")
    assert not panel.enabled.isChecked() and panel.dirty and engine.mode == "officer"
    assert "另一台电脑" in panel.status.text()
    panel.close(); panel.deleteLater(); app.processEvents()


@pytest.fixture
def editable_panel():
    app = QApplication.instance() or QApplication([])
    engine = DisciplineEngine(DisciplineStore("a", persist=False))
    calls = []
    panel = SupervisionPolicyWidget(lambda: engine, lambda: [{"user_id": "b"}],
        lambda name, body, done, fail: calls.append((name, body, done, fail)), lambda *_: None)
    panel._apply(policy(7))
    panel.show(); app.processEvents()
    yield panel, calls
    panel.close(); panel.deleteLater(); app.processEvents()


def test_background_sync_keeps_form_editable_and_preserves_new_edits(editable_panel):
    panel, calls = editable_panel
    assert panel.pending
    assert panel.enabled.isEnabled() and panel.scope.isEnabled()
    assert not panel.scope.isEditable()  # Choose-only is enabled, not disabled.
    assert all(check.isEnabled() for check in panel.permissions.values())
    panel.permissions["view_plan"].click()
    calls[-1][2](policy(8))
    assert not panel.permissions["view_plan"].isChecked()
    assert panel.dirty and panel.revision == 8 and not panel.pending
    panel._save()
    assert calls[-1][1]["p_expected_revision"] == 8
    assert calls[-1][1]["p_policy"]["view_plan"] is False


@pytest.mark.parametrize("failure", ["payload", "network", "callback", "executor", "timeout"])
@pytest.mark.parametrize("operation", ["read", "save"])
def test_all_request_exit_paths_release_controls(editable_panel, failure, operation):
    panel, calls = editable_panel
    calls[-1][2](policy(7))
    panel.request_timeout_ms = 10
    if failure == "executor":
        def broken(*_):
            raise RuntimeError("executor failed")
        panel.rpc_executor = broken
    if failure == "callback":
        def broken_apply(_):
            raise RuntimeError("callback failed")
        panel._apply = broken_apply
    if operation == "save":
        panel.permissions["view_plan"].click()
    panel._reload() if operation == "read" else panel._save()
    if failure == "payload":
        calls[-1][2]({"policy": None})
    elif failure == "network":
        calls[-1][3]("network failed")
    elif failure == "callback":
        calls[-1][2](policy(8))
    elif failure == "timeout":
        callback = calls[-1][2]
        # AppKit/offscreen can defer a 10 ms timer; await the state instead of
        # assuming it fired after one short event-loop sleep.
        for _ in range(100):
            if not panel.pending:
                break
            QTest.qWait(10)
        assert not panel.pending
        callback(policy(99, False))  # Timed-out replies cannot resurrect state.
        assert panel.revision == 7
    assert not panel.pending and panel.save.isEnabled() == panel.dirty
    assert panel.enabled.isEnabled() and panel.scope.isEnabled()
    assert "重试" in panel.status.text()


def test_master_off_alone_disables_dependents_and_save_preserves_later_edits(editable_panel):
    panel, calls = editable_panel
    calls[-1][2](policy(7))
    count = len(calls)
    panel.enabled.click()
    assert panel.enabled.isEnabled() and not panel.scope.isEnabled()
    assert panel.save.isEnabled() and len(calls) == count
    panel._save()
    assert not panel.save.isEnabled() and panel.save.text() == "正在保存…"
    panel.enabled.click()  # A new draft is editable while the earlier save is running.
    assert panel.scope.isEnabled()
    calls[-1][2](policy(8, False))
    assert panel.enabled.isChecked() and panel.scope.isEnabled() and panel.dirty
    assert not panel.pending and len(calls) == count + 1
    panel._save()
    assert calls[-1][1]["p_policy"]["enabled"] is True
    assert calls[-1][1]["p_expected_revision"] == 8
    calls[-1][2](policy(9))
    assert not panel.pending and not panel.dirty and not panel.save.isEnabled()


def test_stale_and_duplicate_callbacks_cannot_change_current_form(editable_panel):
    panel, calls = editable_panel
    old = calls[-1][2]
    old(policy(8))
    panel._reload()
    old(policy(99, False))
    assert panel.pending and panel.enabled.isChecked()
    calls[-1][2](policy(7, False))
    assert panel.revision == 8 and panel.enabled.isChecked()


def test_manual_refresh_updates_authority_without_discarding_existing_dirty_form(editable_panel):
    panel, calls = editable_panel
    calls[-1][2](policy(7))
    panel.permissions["view_progress"].click()
    panel._reload()
    assert panel.pending and panel.save.isEnabled()
    calls[-1][2](policy(8))
    assert panel.revision == 8 and panel.dirty
    assert not panel.permissions["view_progress"].isChecked()


def test_late_response_is_rejected_even_before_delayed_timer_delivery(editable_panel, monkeypatch):
    panel, calls = editable_panel
    calls[-1][2](policy(7))
    ticks = [1000.0]
    monkeypatch.setattr("onepic_desktop_pet.supervision_ui.monotonic", lambda: ticks[0])
    panel._reload()
    ticks[0] += 31
    calls[-1][2](policy(99, False))
    assert panel.revision == 7 and panel.enabled.isChecked()
    assert not panel.pending and panel.save.isEnabled() == panel.dirty
    assert "超时" in panel.status.text()


def test_dirty_form_only_updates_changed_fields_after_another_device_saved(editable_panel):
    panel, calls = editable_panel
    calls[-1][2](policy(7))
    panel.permissions["view_plan"].click()
    panel._reload()
    remote = policy(8, False)
    remote["policy"].update({"scope": "all", "view_reports": False})
    calls[-1][2](remote)
    panel._save()
    body = calls[-1][1]
    assert body["p_expected_revision"] == 8
    assert body["p_policy"]["view_plan"] is False  # My actual edit.
    assert body["p_policy"]["view_reports"] is False  # Other device edit survives.
    assert body["p_policy"]["scope"] == "all"
    assert body["p_policy"]["enabled"] is False  # An old form cannot undo revocation.


def test_configuration_reads_only_on_entry_or_explicit_refresh(editable_panel):
    panel, calls = editable_panel
    calls[-1][2](policy(7))
    count = len(calls)
    for _ in range(50):
        panel.refresh()
    assert len(calls) == count
    panel._save()
    assert len(calls) == count and not panel.save.isEnabled()
    panel.hide(); panel.show()
    assert len(calls) == count + 1
    calls[-1][2](policy(8))
    panel._reload()
    assert len(calls) == count + 2


def test_reverting_draft_does_not_write_and_success_message_clears(editable_panel):
    panel, calls = editable_panel
    calls[-1][2](policy(7))
    count = len(calls)
    check = panel.permissions["view_plan"]
    check.click(); check.click()
    assert not panel.dirty and not panel.save.isEnabled()
    panel._save()
    assert len(calls) == count
    check.click(); panel._save()
    saved = policy(8); saved["policy"]["view_plan"] = False
    calls[-1][2](saved)
    assert not panel.dirty and panel.save.text() == "✓ 已保存"
    QTest.qWait(2100)
    assert panel.save.text() == "保存设置" and panel.status.isHidden()


def test_revert_during_save_remains_a_new_unsaved_change(editable_panel):
    panel, calls = editable_panel
    calls[-1][2](policy(7))
    panel.permissions["view_plan"].click()
    panel._save()
    panel.permissions["view_plan"].click()
    saved = policy(8); saved["policy"]["view_plan"] = False
    calls[-1][2](saved)
    assert panel.dirty and panel.save.isEnabled()
    panel._save()
    assert calls[-1][1]["p_policy"]["view_plan"] is True


def test_work_stats_refresh_does_not_read_policy(editable_panel):
    from onepic_desktop_pet.discipline_ui import DisciplineDialog
    panel, calls = editable_panel
    calls[-1][2](policy(7))
    engine = panel.engine_provider()
    workspace = DisciplineDialog(engine.store, engine, lambda: (10, 100))
    workspace.policy_panel = panel
    count = len(calls)
    for _ in range(20):
        workspace.refresh()
    assert len(calls) == count
    workspace.close(); workspace.deleteLater()
