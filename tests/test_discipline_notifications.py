"""验证启动静默恢复、实时训导单窗合并、持久去重、无正文拦截与退出销毁。"""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ONEPIC_USE_DEMO_ASSETS", "1")

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.discipline import DisciplineNotice, DisciplineStore
from onepic_desktop_pet.buddy_reminder_toast import BuddyReminderToast
from onepic_desktop_pet.window import PetWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def pet(app, monkeypatch):
    monkeypatch.setattr("onepic_desktop_pet.window.save_settings", lambda *_: None)
    monkeypatch.setattr("onepic_desktop_pet.window.detect_quiet_mode", lambda: SimpleNamespace(blocked=False))
    settings = PetSettings(); settings.auto_pause_on_idle = False
    window = PetWindow(settings)
    window._active_focus_account_id = "account-a"
    window._ensure_discipline_engine()
    monkeypatch.setattr(window, "_buddy_display_record", lambda _: {"private_note_name": "论文搭子"})
    yield window
    window.close(); window.deleteLater()
    app.processEvents()


def row(identifier, *, at=None, **extra):
    return {"id": identifier, "supervisor_id": "peer", "kind": "explain",
            "created_at": (at or datetime.now().astimezone()-timedelta(seconds=1)).isoformat(), **extra}


def sync(pet, rows, *, enabled=True):
    pet._discipline_sync_completed({"nudges": rows,
        "supervision": {"policy": {"revision": 1, "enabled": enabled, "scope": "all"}, "effective_mode": "normal"}},
        "account-a", SimpleNamespace(sync_payload={}))


def establish(pet):
    sync(pet, [])
    pet._discipline_notice_baseline_at = datetime.now().astimezone()-timedelta(seconds=5)


def test_initial_three_messages_only_restore_inbox(pet):
    sync(pet, [row("old-1"), row("old-2"), row("old-3")])
    pet._flush_discipline_notices()
    assert not pet._buddy_reminder_toasts
    assert not pet._discipline_pending_notices
    assert pet._discipline_store.seen_nudges == {"old-1", "old-2", "old-3"}
    assert len(pet._discipline_store.coach_messages) == 3


def test_first_response_is_silent_even_for_recent_event(pet):
    sync(pet, [row("just-arrived")])
    assert pet._discipline_notice_baseline_at is not None
    assert not pet._discipline_pending_notices


@pytest.mark.parametrize("payload", [{}, {"nudges": [], "_sync_offline": True}, {"nudges": [], "data_source": "local_cache"}])
def test_missing_inbox_does_not_establish_baseline(pet, payload):
    pet._discipline_sync_completed(payload, "account-a", SimpleNamespace(sync_payload={}))
    assert pet._discipline_notice_baseline_at is None


def test_three_new_messages_coalesce_and_later_update_same_window(pet):
    establish(pet)
    sync(pet, [row("new-1"), row("new-2"), row("new-3")])
    assert not pet._buddy_reminder_toasts  # burst buffering, no immediate shells
    pet._flush_discipline_notices()
    toast = pet._discipline_toast
    assert len(pet._buddy_reminder_toasts) == 1
    assert "3 条新事项" in toast.title_label.text()
    assert "论文搭子" in toast.detail_label.text()
    assert toast.detail_label.text().strip()
    shot = toast.grab(); pixels = shot.toImage(); scale = shot.devicePixelRatio()
    assert pixels.pixelColor(round(5*scale), round(40*scale)).name() == "#173229"
    assert pixels.pixelColor(round(5*scale), round(40*scale)).alpha() == 255
    assert toast.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert toast.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert pet._social_dialog is None
    toast.clock.elapsed_ms = 12_000; toast._tick()
    assert toast.title_label.text() == "📋 3 条训导事项"
    sync(pet, [row("new-4")]); pet._flush_discipline_notices()
    assert pet._discipline_toast is toast
    assert len(pet._buddy_reminder_toasts) == 1
    assert "4 条新事项" in toast.title_label.text()
    assert toast.detail_label.isVisible()


def test_refresh_reconnect_and_delayed_history_do_not_replay(pet):
    establish(pet)
    fresh = row("new")
    sync(pet, [fresh]); pet._flush_discipline_notices()
    toast = pet._discipline_toast
    toast.close()
    sync(pet, [fresh, row("late-page", at=datetime.now().astimezone()-timedelta(days=1))])
    pet._flush_discipline_notices()
    assert not pet._buddy_reminder_toasts
    assert "late-page" in pet._discipline_store.seen_nudges


@pytest.mark.parametrize("extra", [{"created_at": ""}, {"created_at": "2026-10-01T10:00:00"}, {"message": "   "},
                                   {"message": None}, {"created_at": (datetime.now().astimezone()+timedelta(days=1)).isoformat()}])
def test_invalid_time_or_empty_body_cannot_create_toast(pet, extra):
    establish(pet)
    sync(pet, [row("invalid", **extra)]); pet._flush_discipline_notices()
    assert not pet._buddy_reminder_toasts


def test_local_officer_thresholds_are_not_desktop_events(pet):
    engine = pet._ensure_discipline_engine()
    engine.store.settings.mode = "officer"
    engine.store.settings.workdays = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    engine.store.settings.progress_reminders = False
    engine.focus_sessions_provider = lambda: []
    notices = engine.evaluate(0, 0, datetime(2026, 10, 1, 17).astimezone())
    assert len(notices) == 3  # reproduces the original single-tick burst
    establish(pet)
    for notice in notices: pet._show_discipline_notice(notice)
    pet._flush_discipline_notices()
    assert not pet._buddy_reminder_toasts
    assert len(engine.store.fired_rules) == 3


def test_quiet_and_exempt_messages_stay_inbox_without_later_replay(pet, monkeypatch):
    establish(pet)
    monkeypatch.setattr("onepic_desktop_pet.window.detect_quiet_mode", lambda: SimpleNamespace(blocked=True))
    sync(pet, [row("quiet")]); pet._flush_discipline_notices()
    monkeypatch.setattr("onepic_desktop_pet.window.detect_quiet_mode", lambda: SimpleNamespace(blocked=False))
    sync(pet, [row("quiet")]); pet._flush_discipline_notices()
    assert not pet._buddy_reminder_toasts
    pet._discipline_store.rest_days.add(datetime.now().astimezone().date().isoformat())
    sync(pet, [row("exempt")]); pet._flush_discipline_notices()
    assert len(pet._discipline_store.coach_messages) == 2
    assert not pet._buddy_reminder_toasts


def test_close_only_dismisses_window_and_exit_cancels_pending(pet, app):
    establish(pet)
    sync(pet, [row("one")]); pet._flush_discipline_notices()
    toast = pet._discipline_toast; toast.close()
    assert not pet._buddy_reminder_toasts
    assert pet._discipline_toast is None
    assert pet._discipline_store.coach_messages[0]["id"] == "one"
    assert pet._discipline_engine.supervision["policy"]["enabled"]
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(toast)
    sync(pet, [row("two")]); pet.close()
    assert not pet._discipline_notice_timer.isActive()
    assert not pet._discipline_pending_notices
    pet._flush_discipline_notices()
    assert not pet._buddy_reminder_toasts


def test_exit_destroys_active_toast(pet):
    establish(pet); sync(pet, [row("active")]); pet._flush_discipline_notices()
    toast = pet._discipline_toast
    pet.close()
    assert not pet._buddy_reminder_toasts
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(toast)


def test_account_switch_resets_baseline_and_rejects_old_callback(pet):
    establish(pet); sync(pet, [row("account-a-only")]); pet._flush_discipline_notices()
    pet._active_focus_account_id = "account-b"
    new = pet._ensure_discipline_engine()
    assert pet._discipline_notice_baseline_at is None
    assert not pet._buddy_reminder_toasts
    sync(pet, [row("late-old-account")])
    assert not new.store.coach_messages


def test_inbox_and_seen_persist_but_are_not_cloud_events(tmp_path):
    path = tmp_path / "discipline.json"
    store = DisciplineStore("account-a", path=path)
    store.seen_nudges.add("old")
    now = datetime.now().astimezone()
    store.remember_coach_message("old", "要求说明", "请查看昨天的记录", now)
    store._save()
    loaded = DisciplineStore("account-a", path=path)
    assert loaded.seen_nudges == {"old"}
    assert loaded.coach_messages_for_day(now.date())[0]["detail"] == "请查看昨天的记录"
    assert loaded.sync_payload()["p_events"] == []


@pytest.mark.parametrize("defer_pages", [False, True])
def test_hydrated_messages_are_visible_in_today_and_records(pet, app, defer_pages):
    sync(pet, [row("old-visible", message="请说明昨天的缺口")])
    from onepic_desktop_pet.social_ui import SocialHubDialog
    engine = pet._discipline_engine
    client = SimpleNamespace(signed_in=False, session=SimpleNamespace(user_id="account-a"),
                             backend_name="Supabase Direct", backend_endpoint="https://example.supabase.co")
    dialog = SocialHubDialog(client, defer_pages=defer_pages)
    dialog.configure_discipline(lambda: engine, lambda: (0, 0), lambda: None, lambda *_: None)
    try:
        dialog.show(); dialog.open_focus_section(0); app.processEvents()
        assert dialog.focus_coach_card.isVisible(), (dialog.tabs.currentIndex(), dialog.focus_navigation.currentIndex(),
            dialog.focus_coach_card.isHidden(), engine.store.coach_messages, dialog.focus_coach_summary.text())
        assert "请说明昨天的缺口" in dialog.focus_coach_summary.text()
        dialog.open_focus_section(3)
        assert "请说明昨天的缺口" in dialog.focus_workspace.today_events.text()
        assert not pet._buddy_reminder_toasts
    finally:
        dialog.close(); dialog.deleteLater(); app.processEvents()


def test_empty_toast_never_becomes_visible(app):
    toast = BuddyReminderToast("📋 训导主任：", "   ")
    toast.show_passive()
    assert not toast.isVisible()
    assert not toast._timer.isActive()
    toast.close(); toast.deleteLater()
