"""验证长期订阅、自习室提醒控件、事件重试、去重和非激活提示计时。"""

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QCheckBox, QLineEdit

from onepic_desktop_pet.buddy_study_ui import BuddyStudyDialog
from onepic_desktop_pet.buddy_reminders import BuddyReminderStore
from onepic_desktop_pet.buddy_reminder_toast import BuddyReminderToast, ReminderToastClock
from onepic_desktop_pet.social_ui import BuddyCardWidget, SocialHubDialog, SocialSyncThread


def _event(event_id: str, occurred_at: datetime, event_type: str = "start_work") -> dict:
    return {
        "id": event_id, "target_user_id": "buddy-b", "event_type": event_type,
        "occurred_at": occurred_at.isoformat(), "nickname": "张三",
    }


def test_explicit_transitions_survive_restart_and_ack_without_touching_subscription(tmp_path) -> None:
    path = tmp_path / "reminders.json"
    store = BuddyReminderStore("account-a", path=path)
    started = store.queue_transition("focused", "session-1")
    store.queue_transition("resting", "session-1")
    store.queue_transition("focused", "session-2")
    store.queue_transition("off_work", "session-2")
    assert started is not None
    assert len(BuddyReminderStore("account-a", path=path).pending()) == 4
    store.acknowledge([started["p_event_id"]])
    assert len(BuddyReminderStore("account-a", path=path).pending()) == 3
    assert not store.queue_transition("focused", "")


def test_same_subscription_can_notify_on_separate_days_but_not_repeat_after_reconnect(tmp_path) -> None:
    store = BuddyReminderStore("account-a", path=tmp_path / "reminders.json")
    first_day = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)
    second_day = first_day + timedelta(days=1)
    assert len(store.unseen_events([_event("event-1", first_day)], now=first_day)) == 1
    assert store.unseen_events([_event("event-1", first_day)], now=first_day + timedelta(seconds=30)) == []
    assert len(store.unseen_events([_event("event-2", second_day)], now=second_day)) == 1
    # A second device's duplicate explicit event receives another ID but is
    # still suppressed by the per-target, per-kind cooldown.
    assert store.unseen_events([_event("event-3", second_day + timedelta(seconds=20))],
                               now=second_day + timedelta(seconds=20)) == []


def test_quiet_consumption_and_expired_feed_do_not_replay_later(tmp_path) -> None:
    path = tmp_path / "reminders.json"
    now = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)
    store = BuddyReminderStore("account-a", path=path)
    event = _event("event-quiet", now)
    assert len(store.unseen_events([event], now=now)) == 1
    # The caller may choose not to display it during DND; it remains recorded.
    assert BuddyReminderStore("account-a", path=path).unseen_events([event], now=now) == []
    old = _event("event-old", now - timedelta(minutes=4))
    assert store.unseen_events([old], now=now) == []


def test_toast_full_mini_and_hover_pause() -> None:
    clock = ReminderToastClock()
    assert clock.advance(12_000 - 1) == "full"
    clock.hovered = True
    assert clock.advance(120_000) == "full"
    clock.hovered = False
    assert clock.advance(1) == "mini"
    assert clock.advance(168_000) == "hidden"
    late = ReminderToastClock(remaining_ms=5_000)
    assert late.advance(4_999) == "full"
    assert late.advance(1) == "hidden"


def test_toast_show_and_close_do_not_take_keyboard_focus() -> None:
    app = QApplication.instance() or QApplication([])
    editor = QLineEdit()
    editor.show()
    editor.activateWindow()
    editor.setFocus()
    app.processEvents()
    focus_before = app.focusWidget()
    toast = BuddyReminderToast("张三开始专注", "09:00", mini_title="🟢 张三")
    try:
        toast.show_passive()
        app.processEvents()
        assert toast.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
        assert toast.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        if app.platformName().lower() != "offscreen":
            assert app.focusWidget() is focus_before
        toast.clock.elapsed_ms = 12_000
        toast._tick()
        assert toast.title_label.text() == "🟢 张三"
        assert not toast.detail_label.isVisible()
        toast.close()
        app.processEvents()
        if app.platformName().lower() != "offscreen":
            assert app.focusWidget() is focus_before
    finally:
        toast.close()
        editor.close()


def test_buddy_study_has_independent_persistent_reminder_controls() -> None:
    app = QApplication.instance() or QApplication([])
    class Client:
        signed_in = True
        session = SimpleNamespace(user_id="viewer")
    buddy = {
        "user_id": "buddy-b", "nickname": "张三", "status": "offline",
        "online": False, "on_focus_start": True, "on_focus_end": False,
    }
    card = BuddyCardWidget(buddy)
    hub = SocialHubDialog(Client())
    emitted = []
    hub._set_subscription = lambda _buddy, kind, enabled: emitted.append((kind, enabled))
    room = BuddyStudyDialog(hub, buddy)
    try:
        assert card.findChildren(QCheckBox) == []
        assert "开工提醒已开启" in card.reminder_summary.text()
        controls = room.subscriptions
        assert controls["start_work"].isChecked()
        assert not controls["finish_work"].isChecked()
        controls["finish_work"].click()
        assert emitted == [("finish_work", True)]
        assert controls["start_work"].isChecked()
    finally:
        room.close(); room.deleteLater()
        hub.close(); hub.deleteLater()
        card.close(); card.deleteLater()
        app.processEvents()


def test_server_reminder_snapshot_overrides_stale_device_dashboard_without_presence_notifications() -> None:
    app = QApplication.instance() or QApplication([])
    class Client:
        signed_in = True

    dialog = SocialHubDialog(Client())
    emitted = []
    dialog.buddy_subscription_notice.connect(emitted.append)
    base = {"me": {"user_id": "viewer", "nickname": "我", "visibility": "friends"},
            "buddies": [{"user_id": "buddy-b", "nickname": "张三", "status": "offline",
                          "online": False, "subscribed": False}],
            "room_people": [], "requests": [], "visits": [], "rooms": []}
    try:
        dialog.apply_dashboard({**base, "_reminder_snapshot": {
            "subscriptions": [{"buddy_id": "buddy-b", "on_focus_start": True,
                               "on_focus_end": False, "muted": False}], "events": []}})
        assert dialog.data["buddies"][0]["on_focus_start"] is True
        dialog.apply_dashboard({**base, "buddies": [{**base["buddies"][0],
                                                      "status": "focus", "online": True,
                                                      "working": True}]})
        assert emitted == []  # reconnect/heartbeat cannot manufacture work events
    finally:
        dialog.close()


def test_subscription_click_writes_only_selected_flag_and_preserves_other_flag() -> None:
    app = QApplication.instance() or QApplication([])

    class Client:
        signed_in = True
        session = SimpleNamespace(user_id="viewer")

        def __init__(self) -> None:
            self.calls = []

        def rpc(self, name, body):
            self.calls.append((name, dict(body)))
            return {"buddy_id": "buddy-b", "on_focus_start": True,
                    "on_focus_end": False, "muted": False}

    client = Client()
    dialog = SocialHubDialog(client)
    dialog._initial_refresh_timer.stop()
    dialog.refresh = lambda: None
    dashboard = {
        "me": {"user_id": "viewer", "nickname": "我", "visibility": "friends"},
        "buddies": [{"user_id": "buddy-b", "nickname": "张三", "status": "rest", "online": True}],
        "room_people": [], "requests": [], "visits": [], "rooms": [],
        "_reminder_snapshot": {"subscriptions": [{"buddy_id": "buddy-b",
            "on_focus_start": True, "on_focus_end": True, "muted": False}], "events": []},
    }
    try:
        dialog.apply_dashboard(dashboard)
        dialog._set_subscription(dashboard["buddies"][0], "finish_work", False)
        deadline = time.monotonic() + 3
        while dialog._pending_reminder_updates and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        assert client.calls == [("lili_set_buddy_reminder", {
            "p_buddy_id": "buddy-b", "p_event_type": "finish_work", "p_enabled": False,
        })]
        assert dialog.data["buddies"][0]["on_focus_start"] is True
        assert dialog.data["buddies"][0]["on_focus_end"] is False
    finally:
        dialog.close()


def test_dismiss_reminder_does_not_change_subscription(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = BuddyReminderStore("account-a", path=tmp_path / "reminders.json")
    now = datetime.now(timezone.utc)
    assert len(store.unseen_events([_event("one", now)], now=now)) == 1
    toast = BuddyReminderToast("张三开工", "09:00")
    toast.show_passive()
    toast.close()
    app.processEvents()
    assert BuddyReminderStore("account-a", path=store.path).unseen_events(
        [_event("one", now)], now=now,
    ) == []


def test_background_sync_sends_explicit_event_separately_from_presence() -> None:
    class Client:
        signed_in = True
        session = SimpleNamespace(user_id="viewer")

        def __init__(self) -> None:
            self.calls = []

        def rpc(self, name, body):
            self.calls.append((name, dict(body)))
            if name == "lili_publish_work_transition":
                return {"accepted": True, "work_status": "focused"}
            return {}

        def dashboard(self, **_kwargs):
            return {"me": {"user_id": "viewer"}, "buddies": [], "room_people": []}

        def buddy_reminder_snapshot(self):
            return {"subscriptions": [], "events": []}

    client = Client()
    emitted = []
    event = {"p_event_id": "event-1", "p_work_status": "focused",
             "p_session_id": "session-1", "p_occurred_at": datetime.now(timezone.utc).isoformat(),
             "p_silent": False}
    thread = SocialSyncThread(client, {"user_id": "viewer", "working": True,
                                      "_work_events": [event]})
    thread.completed.connect(emitted.append)
    thread.run()
    assert client.calls[0] == ("lili_publish_work_transition", event)
    assert emitted[-1]["_work_event_acked"] == ["event-1"]
    assert emitted[-1]["_reminder_snapshot"] == {"subscriptions": [], "events": []}
    assert all("subscription" not in str(body) for _, body in client.calls)


def test_server_subscription_mutations_are_field_specific_and_legacy_false_is_enable_only() -> None:
    migration = (Path(__file__).resolve().parents[1] / "supabase" / "migrations"
                 / "20260929180000_lili_durable_buddy_reminders.sql").read_text(encoding="utf-8")
    assert "on_focus_start = case when p_event_type = 'start_work'" in migration
    assert "on_focus_end = case when p_event_type = 'finish_work'" in migration
    assert "muted = p_muted, updated_at = now()" in migration
    assert "on_focus_start = public.lili_buddy_subscriptions.on_focus_start or excluded.on_focus_start" in migration
    assert "muted = public.lili_buddy_subscriptions.muted" in migration
    assert "old_session_id <> p_session_id" in migration
    assert "event_row.created_at > now() - interval '3 minutes'" in migration
    assert "p_buddy_id uuid, p_on_focus_start boolean, p_on_focus_end boolean" in migration
