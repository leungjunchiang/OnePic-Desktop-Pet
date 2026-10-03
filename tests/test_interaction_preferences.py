"""验证默认欢迎、跨设备部分更新、消息免打扰与恢复，以及刷新期间的互动设置编辑。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.social import HttpSocialBackend, LegacyDirectSocialClient, SocialError, SocialSession
from onepic_desktop_pet.social_ui import SocialHubDialog


class ProfileClient:
    signed_in = True

    def __init__(self):
        self.session = SimpleNamespace(user_id="account-a")
        self.me = {"user_id": "account-a", "nickname": "搭子", "invite_code": "AB12CD34"}
        self.writes = []
        self.calls = []
        self.fail_mode = False

    def dashboard(self):
        return {"me": deepcopy(self.me), "buddies": [], "requests": [], "visits": [], "rooms": [], "room_people": []}

    def update_profile(self, **kwargs):
        self.writes.append(kwargs)
        self.me.update(kwargs)

    def rpc(self, name, body):
        self.calls.append((name, body))
        if self.fail_mode:
            raise SocialError("网络暂时不可用", kind="network")
        if name == "lili_set_buddy_interaction_mode":
            self.me["buddy_interaction_mode"] = body["p_mode"]


@pytest.fixture(scope="module", autouse=True)
def application():
    app = QApplication.instance() or QApplication([])
    yield app
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def profile_dialog(application):
    client = ProfileClient()
    dialog = SocialHubDialog(client)
    dialog._initial_refresh_timer.stop()
    dialog.refresh = lambda: None
    yield dialog, client
    dialog.close()
    dialog.deleteLater()
    application.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_missing_preferences_default_to_welcome_and_receiving(profile_dialog):
    dialog, client = profile_dialog
    assert dialog.visits_allowed.isChecked()
    assert dialog.interaction_mode.currentData() == "welcome"
    dialog.apply_dashboard(client.dashboard())
    dialog._save_profile()
    assert "allow_visits" not in client.writes[-1]
    assert client.calls == []


def test_stale_computer_saving_owner_name_cannot_disable_remote_interactions(profile_dialog):
    dialog, client = profile_dialog
    client.me.update(allow_visits=False, buddy_interaction_mode="do_not_disturb")
    dialog.apply_dashboard(client.dashboard())  # Computer B's old snapshot.
    client.me.update(allow_visits=True, buddy_interaction_mode="welcome")  # Computer A enables it.
    dialog.owner_name_edit.setText("小梁")
    dialog._save_profile()
    assert "allow_visits" not in client.writes[-1]
    assert client.calls == []
    assert client.me["allow_visits"] is True
    assert client.me["buddy_interaction_mode"] == "welcome"


def test_refresh_preserves_message_dnd_without_disabling_reception(profile_dialog):
    dialog, client = profile_dialog
    client.me.update(allow_visits=True, buddy_interaction_mode="welcome")
    old_snapshot = client.dashboard()
    dialog.apply_dashboard(old_snapshot)
    dialog.interaction_mode.setCurrentIndex(dialog.interaction_mode.findData("do_not_disturb"))
    dialog.apply_dashboard(old_snapshot)
    assert dialog.visits_allowed.isChecked() and not dialog.visits_allowed.isEnabled()
    assert dialog.interaction_mode.currentData() == "do_not_disturb"
    dialog._save_profile()
    assert "allow_visits" not in client.writes[-1]
    assert client.calls == [("lili_set_buddy_interaction_mode", {"p_mode": "do_not_disturb"})]
    dialog.apply_dashboard(old_snapshot)  # An in-flight response must not roll back the save.
    assert dialog.visits_allowed.isChecked() and not dialog.visits_allowed.isEnabled()
    dialog.apply_dashboard(client.dashboard())
    dialog._save_profile()
    assert "allow_visits" not in client.writes[-1]
    assert len(client.calls) == 1


def test_partial_refresh_cannot_reset_an_opt_out(profile_dialog):
    dialog, client = profile_dialog
    client.me.update(allow_visits=False, buddy_interaction_mode="do_not_disturb")
    dialog.apply_dashboard(client.dashboard())
    client.me.pop("allow_visits")
    client.me.pop("buddy_interaction_mode")
    dialog.apply_dashboard(client.dashboard())
    assert dialog.visits_allowed.isChecked() and not dialog.visits_allowed.isEnabled()
    assert dialog.interaction_mode.currentData() == "do_not_disturb"
    assert not dialog._interaction_preferences_dirty
    assert dialog.data["me"]["allow_visits"] is True
    assert dialog.data["me"]["buddy_interaction_mode"] == "do_not_disturb"


def test_single_field_change_does_not_submit_other_interaction_field(profile_dialog):
    dialog, client = profile_dialog
    client.me.update(allow_visits=False, buddy_interaction_mode="focus_priority")
    dialog.apply_dashboard(client.dashboard())
    dialog.interaction_mode.setCurrentIndex(dialog.interaction_mode.findData("focus_priority"))
    dialog._save_profile()
    assert "allow_visits" not in client.writes[-1]
    assert client.calls == [("lili_set_buddy_interaction_mode", {"p_mode": "focus_priority"})]
    assert client.me["buddy_interaction_mode"] == "focus_priority"


def test_failed_mode_save_keeps_edits_and_restores_button_for_retry(profile_dialog):
    dialog, client = profile_dialog
    dialog.apply_dashboard(client.dashboard())
    dialog.interaction_mode.setCurrentIndex(dialog.interaction_mode.findData("focus_priority"))
    client.fail_mode = True
    dialog._save_profile()
    assert dialog.profile_save_button.isEnabled()
    assert "buddy_interaction_mode" in dialog._interaction_preferences_dirty
    assert QApplication.overrideCursor() is None
    client.fail_mode = False
    dialog._save_profile()
    assert not dialog._interaction_preferences_dirty
    assert client.me["buddy_interaction_mode"] == "focus_priority"


def test_account_switch_does_not_submit_previous_accounts_edits(profile_dialog):
    dialog, client = profile_dialog
    dialog.apply_dashboard(client.dashboard())
    client.session.user_id = "account-b"
    client.me = {"user_id": "account-b", "nickname": "另一个搭子"}
    dialog.apply_dashboard(client.dashboard())
    assert dialog.visits_allowed.isChecked()
    assert not dialog._interaction_preferences_dirty
    dialog._save_profile()
    assert "allow_visits" not in client.writes[-1]


def test_restore_interactions_is_explicit_and_restores_both_switches(profile_dialog):
    dialog, client = profile_dialog
    client.me.update(allow_visits=False, buddy_interaction_mode="do_not_disturb")
    dialog.apply_dashboard(client.dashboard())
    dialog._restore_interactions()
    assert dialog.visits_allowed.isChecked()
    assert "allow_visits" not in client.writes[-1]
    assert client.me["buddy_interaction_mode"] == "welcome"
    assert not dialog._interaction_preferences_dirty


@pytest.mark.parametrize("transport", ["direct", "proxy", "legacy"])
def test_transport_omits_unchanged_permission_but_preserves_explicit_false(transport, monkeypatch):
    if transport == "legacy":
        backend = LegacyDirectSocialClient(persist_tokens=False)
    else:
        backend = HttpSocialBackend("https://example.test", persist_tokens=False, transport=transport)
    backend.session = SocialSession("token", "refresh", "user-1", 9999999999)
    sent = []
    monkeypatch.setattr(backend, "_raw", lambda method, path, body=None, **kwargs: sent.append(body))
    common = {"nickname": "搭子", "visibility": "friends", "show_exact_time": True}
    backend.update_profile(**common)
    assert "allow_visits" not in sent[-1]
    backend.update_profile(**common, allow_visits=False)
    assert sent[-1]["allow_visits"] is False
    backend.update_profile(**common, allow_visits=True)
    assert sent[-1]["allow_visits"] is True
