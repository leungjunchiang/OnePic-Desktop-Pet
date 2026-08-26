"""Qt behavior tests for the one-time foreground alarm card."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.alarm_manager import Alarm
from onepic_desktop_pet.alarm_ui import AlarmCard, AlarmPopupState, AwayRecoveryCard


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_alarm_card_is_foreground_once_then_releases_topmost() -> None:
    app = _app()
    card = AlarmCard(
        Alarm(
            id="alarm-1",
            title="叫我开工",
            trigger_at="2026-08-26T09:00:00",
            sound_enabled=False,
        )
    )
    assert card.graphicsEffect() is None

    card.show_alarm_foreground()
    app.processEvents()
    assert card.popup_state is AlarmPopupState.UNSEEN
    assert card.windowFlags() & Qt.WindowType.WindowStaysOnTopHint

    # A click anywhere is represented by the same acknowledgement path used
    # by the event filter and by every action button.
    card._acknowledge_alarm()
    assert card.popup_state is AlarmPopupState.ACKNOWLEDGED
    assert not card.windowFlags() & Qt.WindowType.WindowStaysOnTopHint

    card.close_from_app()
    app.processEvents()
    assert card.popup_state is AlarmPopupState.DISMISSED


def test_away_recovery_card_has_no_drop_shadow() -> None:
    card = AwayRecoveryCard("idle_10m", 600)
    assert card.graphicsEffect() is None
    card.close_from_app()
