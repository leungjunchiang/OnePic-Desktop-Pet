"""游戏期间的被动展示、线程分发、单窗上限、历史静默与闹钟音频隔离回归。"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from datetime import datetime, timedelta
from threading import Thread
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QThread, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget, QApplication
from onepic_desktop_pet.notification_manager import NotificationManager
from onepic_desktop_pet.time_service import BEIJING_TIMEZONE
from test_window import _create_window

@pytest.fixture
def pet(monkeypatch):
    app, window = _create_window()
    for timer in window.findChildren(__import__('PySide6.QtCore', fromlist=['QTimer']).QTimer):
        timer.stop()
    monkeypatch.setattr('onepic_desktop_pet.window.detect_quiet_mode', lambda: SimpleNamespace(blocked=False))
    monkeypatch.setattr(window, '_foreground_display_mode', lambda: 'normal')
    yield app, window
    window.close(); window.deleteLater(); app.processEvents()

@pytest.mark.parametrize('mode', ['fullscreen', 'game_fullscreen', 'presentation_fullscreen'])
def test_preflight_blocks_before_debounced_visibility_and_constructor(pet, monkeypatch, mode):
    app, window = pet
    monkeypatch.setattr(window, '_foreground_display_mode', lambda: mode)
    assert not window._fullscreen_hidden  # 750ms scheduler 尚未执行。
    def unexpected(*a, **kw): raise AssertionError('must not construct a toast')
    monkeypatch.setattr('onepic_desktop_pet.notification_manager.BuddyReminderToast', unexpected)
    assert not window._notify_instant_interaction('nudge:new', 'return', '回来', '正文')
    assert not window.notification_manager.notify('work:new', '开工', '正文', lambda: None)
    window.notification_manager.flush()
    assert window.notification_manager.current is None
    assert not window._interaction_hint_text


def test_game_takes_foreground_between_receipt_and_flush(pet, monkeypatch):
    app, window = pet
    assert window.notification_manager.notify('buddy:request', '申请', '正文', lambda: None)
    monkeypatch.setattr(window, '_foreground_display_mode', lambda: 'fullscreen')
    window.notification_manager.flush()
    assert window.notification_manager.current is None
    monkeypatch.setattr(window, '_foreground_display_mode', lambda: 'normal')
    window.notification_manager.flush()
    assert window.notification_manager.current is None
    assert not window.notification_manager.notify('buddy:request', '申请', '正文', lambda: None)


def test_live_status_countdown_cannot_reshow_hidden_accessory(pet, monkeypatch):
    app, window = pet
    monkeypatch.setattr(window, '_foreground_display_mode', lambda: 'fullscreen')
    window.visit_status_bubble.set_taunter('搭子', remaining_seconds=1200)
    assert not window.visit_status_bubble.isVisible()
    window.visit_status_bubble.set_taunter('搭子', remaining_seconds=1199)
    assert not window.visit_status_bubble.isVisible()
    window._show_buddy_visit({'id': 'new', 'nickname': '搭子'})
    assert not window.visit_status_bubble.isVisible()


def test_instant_interaction_is_painted_without_another_native_window(pet):
    app, window = pet
    native = window.winId()
    before = set(app.topLevelWidgets())
    children = set(window.findChildren(QWidget))
    assert window._notify_instant_interaction('nudge:one', 'return', '回来', '正文')
    assert set(window.findChildren(QWidget)) == children
    assert window._interaction_hint_text
    assert window.winId() == native and set(app.topLevelWidgets()) == before
    assert window.mask().contains(window._interaction_hint_rect.center())
    window._hide_interaction_hint()
    assert not window._interaction_hint_text and not window.interaction_hint_timer.isActive()


def test_windows_style_prepared_before_first_show(pet, monkeypatch):
    app, window = pet
    from onepic_desktop_pet.coaching_ui import DesktopCoachingSurface
    surface = DesktopCoachingSurface(window, lambda: None)
    events = []
    monkeypatch.setattr('onepic_desktop_pet.window.sys.platform', 'win32')
    monkeypatch.setattr(window, '_apply_native_window_policy_for_widget', lambda widget, **kw: events.append(('prepare', widget.isVisible())))
    original = surface.show
    monkeypatch.setattr(surface, 'show', lambda: (events.append(('show', surface.isVisible())), original()))
    window._show_nonactivating(surface)
    assert events == [('prepare', False), ('show', False)]
    surface.close(); surface.deleteLater()


def test_topmost_watchdog_only_reasserts_pet(pet, monkeypatch):
    app, window = pet
    window.show_speech('文字')
    calls = []
    monkeypatch.setattr(window, '_apply_native_window_policy_for_widget', lambda widget, **kw: calls.append(widget))
    window._ensure_on_top(event='TopmostWatchdog')
    assert calls == [window]


def test_worker_notification_dispatches_display_on_owner_thread(pet):
    app, window = pet
    threads = []
    worker = Thread(target=lambda: window.notification_manager.notify('nudge:worker', '标题', '正文', lambda: None,
                    display=lambda: threads.append(QThread.currentThread())))
    worker.start(); worker.join()
    assert not threads
    app.processEvents()
    assert threads == [window.thread()]


def test_restart_baseline_and_reconnect_expired_events_are_silent(pet, monkeypatch):
    app, window = pet
    import onepic_desktop_pet.notification_manager as module
    now = datetime(2026, 10, 2, 12, tzinfo=BEIJING_TIMEZONE)
    monkeypatch.setattr(module, 'now_beijing', lambda: now)
    manager = window.notification_manager
    shown = []
    def incoming(row): manager.notify('room:'+row['id'], '标题', '正文', lambda: None, display=lambda: shown.append(row['id']))
    manager.observe('room', [{'id':'startup','created_at':(now-timedelta(seconds=1)).isoformat()}], notify=incoming)
    now += timedelta(minutes=10)
    manager.observe('room', [{'id':'missed','created_at':(now-timedelta(minutes=5)).isoformat()},
                            {'id':'fresh','created_at':(now-timedelta(seconds=30)).isoformat()}], notify=incoming)
    assert shown == ['fresh']
    manager.observe('room', [{'id':'fresh','created_at':(now-timedelta(seconds=30)).isoformat()}], notify=incoming)
    assert shown == ['fresh']


def test_single_toast_for_multiple_channels_and_replacement(pet):
    app, window = pet
    manager = window.notification_manager
    for kind in ('buddy', 'work', 'nudge'):
        assert manager.notify(kind+':one', '标题', '正文', lambda: None)
    manager.flush()
    first = manager.current
    assert first.isVisible() and '3' in first.title_label.text()
    manager.notify('buddy:two', '新标题', '新正文', lambda: None)
    manager.flush()
    assert not first.isVisible() and manager.current.isVisible()
    assert len([w for w in app.topLevelWidgets() if w.__class__.__name__=='BuddyReminderToast' and w.isVisible()]) == 1


def test_fullscreen_alarm_audio_has_no_native_window_and_no_replay(pet, monkeypatch):
    app, window = pet
    from onepic_desktop_pet.alarm_ui import AlarmCard
    from onepic_desktop_pet.alarm_manager import Alarm
    audio = []
    monkeypatch.setattr(AlarmCard, '_start_alarm_audio', lambda card: audio.append(card.alarm.id))
    monkeypatch.setattr(window, '_foreground_display_mode', lambda: 'fullscreen')
    window._show_alarm_card(Alarm(id='game-alarm', title='开工', trigger_at='2026-10-02T12:00:00', sound_enabled=False))
    card = window._alarm_card
    assert audio == ['game-alarm'] and card._ui_deferred
    assert not card.isVisible() and not card.testAttribute(Qt.WidgetAttribute.WA_WState_Created)
    monkeypatch.setattr(window, '_foreground_display_mode', lambda: 'normal')
    window._sync_fullscreen_visibility()
    assert not card.isVisible()  # 由用户主动打开闹钟中心才展开。


def test_game_snapshot_consumed_without_replay_after_exit(pet, monkeypatch):
    app, window = pet
    import onepic_desktop_pet.notification_manager as module
    now = datetime(2026, 10, 2, 12, tzinfo=BEIJING_TIMEZONE)
    monkeypatch.setattr(module, 'now_beijing', lambda: now)
    window._social_dashboard_received({'_encouragement_state': {'active':False}})
    now += timedelta(seconds=2)
    state = {'id':'game-cheer','active':True,'sender_id':'peer','created_at':(now-timedelta(seconds=1)).isoformat()}
    monkeypatch.setattr('onepic_desktop_pet.window.detect_quiet_mode', lambda: SimpleNamespace(blocked=True))
    window._social_dashboard_received({'_encouragement_state':state})
    assert 'cheer:game-cheer' in window.notification_manager.shown_event_ids
    assert not window._interaction_hint_text
    monkeypatch.setattr('onepic_desktop_pet.window.detect_quiet_mode', lambda: SimpleNamespace(blocked=False))
    window._social_dashboard_received({'_encouragement_state':state})
    assert not window._interaction_hint_text


def test_discipline_timer_is_local_and_periodic_read_has_one_coordinator():
    import ast, inspect, textwrap
    from onepic_desktop_pet.window import PetWindow
    def calls(method):
        tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
        return [node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
    assert '_sync_discipline_state' not in calls(PetWindow._discipline_tick)
    assert calls(PetWindow._social_tick_impl).count('_sync_discipline_state') == 1


@pytest.mark.parametrize('reason,allowed',[('游戏中',True),('全屏工作中',True),('会议中',False)])
def test_game_suppression_does_not_mute_alarm_sound(pet, monkeypatch, reason, allowed):
    app, window = pet
    received = []
    monkeypatch.setattr(window.time_memory.alarms, 'claim_due', lambda **kw: received.append(kw['allow_during_dnd']) or [])
    window._check_local_alarms(SimpleNamespace(blocked=True,reason=reason))
    assert received == [allowed]


def test_hover_hint_uses_owner_preflight_and_never_raises(pet, monkeypatch):
    app, window = pet
    monkeypatch.setattr(window, '_foreground_display_mode', lambda: 'fullscreen')
    monkeypatch.setattr(window.quick_panel.hover_hint, 'raise_', lambda: (_ for _ in ()).throw(AssertionError('raise')))
    window.quick_panel._show_hint(window.quick_panel.work_button)
    assert not window.quick_panel.hover_hint.isVisible()


def test_idle_hint_uses_owner_preflight(pet, monkeypatch):
    from onepic_desktop_pet.window import IdleRecoveryDialog
    app, window = pet
    monkeypatch.setattr(window, '_foreground_display_mode', lambda: 'fullscreen')
    hint = IdleRecoveryDialog(window)
    hint.set_away_seconds(120)
    hint.show_hint(window)
    assert not hint.isVisible()
    hint.deleteLater()


def test_speech_and_status_geometry_is_complete_before_show(pet, monkeypatch):
    app, window = pet
    window.move(500, 300)
    shown = []
    for widget in (window.speech_bubble, window.visit_status_bubble):
        original = widget.show
        monkeypatch.setattr(widget, 'show', lambda w=widget, f=original: (shown.append((w.pos(), w.size())), f()))
    window.show_speech('正文已经布局')
    window.visit_status_bubble.set_taunter('搭子', remaining_seconds=1200)
    assert len(shown)==2
    assert all(point.x()!=0 or point.y()!=0 for point,size in shown)
    assert all(size.width()>100 and size.height()>10 for point,size in shown)
