"""闹钟处理入口独立于消息与宠物隐藏；全屏延后恢复只展示仍待处理的同一张卡。"""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QWidget

from test_passive_notifications import pet


class Card(QWidget):
    audio_cleanup_finished = Signal()
    start_requested = Signal(str)
    snooze_requested = Signal(str, int)
    dismiss_requested = Signal(str)

    def __init__(self, alarm, **kwargs):
        super().__init__()
        self.alarm = alarm
        self._action_requested = self._close_requested = self._ui_deferred = self._presented_once = False
        self.audio_cleanup_ready = True
        self.presentations = self.centers = self.suppressed = 0

    def center_on_current_screen(self):
        self.centers += 1

    def show_alarm_foreground(self):
        self.presentations += 1
        self._presented_once = True
        self._ui_deferred = False
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.show()

    def start_alarm_suppressed(self, *, reason):
        self.suppressed += 1
        self._ui_deferred = True

    def defer_alarm_ui(self, *, reason):
        self._ui_deferred = True
        self.hide()

    def close_from_app(self):
        self._close_requested = True
        self.close()


@pytest.fixture
def alarm_factory(monkeypatch):
    monkeypatch.setattr('onepic_desktop_pet.window.AlarmCard', Card)
    return SimpleNamespace(id='alarm-a')


@pytest.mark.parametrize('context', ['quiet', 'pet_hidden', 'normal'])
def test_desktop_alarm_shows_controls_independently_of_pet_and_message_policy(pet, monkeypatch, alarm_factory, context):
    app, window = pet
    if context == 'quiet':
        monkeypatch.setattr('onepic_desktop_pet.window.detect_quiet_mode',
                            lambda: SimpleNamespace(blocked=True, reason='游戏中'))
    elif context == 'pet_hidden':
        window.hide_pet()
    window._show_alarm_card(alarm_factory)
    card = window._alarm_card
    assert card.isVisible() and card.presentations == 1
    assert card.suppressed == 0 and card.centers == 1
    window.hide_pet()
    assert card.isVisible()  # 隐藏宠物不关闭闹钟处理入口。


def test_fullscreen_before_pet_debounce_recovers_same_alarm_once(pet, monkeypatch, alarm_factory):
    _, window = pet
    monkeypatch.setattr(window, '_foreground_display_mode', lambda: 'fullscreen')
    window._show_alarm_card(alarm_factory)
    card = window._alarm_card
    assert not card.isVisible() and not window._fullscreen_hidden
    assert card.suppressed == 1
    # Fullscreen ended before the pet was withdrawn; no pet transition is
    # needed, but the stable normal samples must recover the alarm controls.
    monkeypatch.setattr(window, '_foreground_display_mode', lambda: 'normal')
    for _ in range(4):
        window._poll_fullscreen_visibility()
    assert window._alarm_card is card and card.isVisible()
    assert card.presentations == 1 and card.centers == 1 and card.suppressed == 1


def test_visible_alarm_temporarily_defers_without_recentering_after_fullscreen(pet, alarm_factory):
    _, window = pet
    window._show_alarm_card(alarm_factory)
    card = window._alarm_card
    card.move(123, 234)
    window._sync_alarm_card_visibility(mode='fullscreen')
    assert not card.isVisible() and card._ui_deferred
    window._sync_alarm_card_visibility(mode='normal')
    assert card.isVisible() and card.pos().x() == 123 and card.pos().y() == 234
    assert card.centers == 1 and card.presentations == 2
    # User-minimized controls stay minimized, rather than being forced open.
    card.showMinimized()
    window._sync_alarm_card_visibility(mode='fullscreen')
    window._sync_alarm_card_visibility(mode='normal')
    assert card.isMinimized() and card.presentations == 2


def test_alarm_entry_opens_pending_controls_instead_of_covering_with_editor(pet, monkeypatch, alarm_factory):
    _, window = pet
    window._show_alarm_card(alarm_factory)
    card = window._alarm_card
    def editor(*args, **kwargs):
        raise AssertionError('must handle pending alarm first')
    monkeypatch.setattr('onepic_desktop_pet.window.AlarmCenterDialog', editor)
    window.show_alarm_center()
    assert window._alarm_card is card and card.isVisible()
    assert window._alarm_center_dialog is None


def test_dismissed_deferred_alarm_is_not_recreated_and_shutdown_cannot_ring(pet, alarm_factory):
    _, window = pet
    window._show_alarm_card(alarm_factory)
    window._sync_alarm_card_visibility(mode='fullscreen')
    window._close_alarm_card()
    window._sync_alarm_card_visibility(mode='normal')
    assert window._alarm_card is None
    window._close_in_progress = True
    window._show_alarm_card(alarm_factory)
    assert window._alarm_card is None


def test_mac_alarm_uses_existing_nonactivating_floating_bridge(monkeypatch):
    from onepic_desktop_pet import alarm_ui, native_window_policy
    calls = []
    monkeypatch.setattr(alarm_ui, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(native_window_policy, 'apply_macos_window_policy',
                        lambda widget, **kw: calls.append(kw))
    holder = SimpleNamespace(windowFlags=lambda: Qt.WindowType.Window)
    alarm_ui.AlarmCard._set_temporary_topmost(holder, True)
    alarm_ui.AlarmCard._set_temporary_topmost(holder, False)
    assert calls == [dict(topmost=True, qt_stays_on_top=False, force_topmost=True),
                     dict(topmost=False, qt_stays_on_top=False, force_topmost=False)]
