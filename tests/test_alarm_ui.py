"""Qt behavior tests for the one-time foreground alarm card."""

from __future__ import annotations

import os
import sys
import wave

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if (
    sys.platform == "darwin"
    and os.environ.get("QT_QPA_PLATFORM", "").casefold() in {"offscreen", "minimal"}
):
    pytest.skip(
        "macOS headless Qt cannot exercise native alarm windows safely",
        allow_module_level=True,
    )

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from onepic_desktop_pet.alarm_manager import Alarm
from onepic_desktop_pet.alarm_sounds import AlarmSoundLibrary
from onepic_desktop_pet.alarm_ui import (
    AlarmCard,
    AlarmPopupState,
    AlarmSoundSelector,
    AwayRecoveryCard,
    _WindowsAlarmAudio,
)
from onepic_desktop_pet import alarm_ui


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_alarm_card_changes_native_z_order_without_mutating_qt_flags() -> None:
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
    initial_flags = card.windowFlags()

    card.show_alarm_foreground()
    app.processEvents()
    assert card.popup_state is AlarmPopupState.UNSEEN
    assert card.windowFlags() == initial_flags

    # A click anywhere is represented by the same acknowledgement path used
    # by the event filter and by every action button.
    card._acknowledge_alarm()
    assert card.popup_state is AlarmPopupState.ACKNOWLEDGED
    # Acknowledgement changes the native z-order directly on Windows; it must
    # not mutate Qt window flags on a visible top-level dialog.
    assert card.windowFlags() == initial_flags

    card.close_from_app()
    app.processEvents()
    assert card.popup_state is AlarmPopupState.DISMISSED


def test_alarm_card_action_is_idempotent_and_cancels_delayed_audio() -> None:
    app = _app()
    card = AlarmCard(
        Alarm(
            id="alarm-action-1",
            title="点击测试",
            trigger_at="2026-08-26T09:00:00",
            sound_enabled=True,
        )
    )
    card.show_alarm_foreground()
    assert card._audio_start_timer.isActive()

    emitted: list[str] = []
    card.dismiss_requested.connect(emitted.append)
    card._request_dismiss()
    card._request_dismiss()
    app.processEvents()

    assert emitted == ["alarm-action-1"]
    assert card.popup_state is AlarmPopupState.DISMISSED
    assert not card._audio_start_timer.isActive()
    card.close_from_app()
    app.processEvents()


def test_alarm_card_real_button_click_returns_before_action_signal() -> None:
    app = _app()
    card = AlarmCard(
        Alarm(
            id="alarm-click-1",
            title="真实按钮点击测试",
            trigger_at="2026-08-26T09:00:00",
            sound_enabled=True,
        )
    )
    started: list[str] = []
    card.start_requested.connect(started.append)
    card.show_alarm_foreground()
    app.processEvents()

    button = next(
        item for item in card.findChildren(QPushButton)
        if item.text() == "开始30分钟"
    )
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    app.processEvents()

    assert started == ["alarm-click-1"]
    assert card.popup_state is AlarmPopupState.DISMISSED
    assert not card._audio_start_timer.isActive()
    card.close_from_app()
    app.processEvents()


def test_custom_audio_button_queues_player_stop_before_card_cleanup(tmp_path) -> None:
    app = _app()
    source = tmp_path / "test-tone.wav"
    with wave.open(str(source), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8_000)
        output.writeframes(b"\x00\x00" * 1_600)
    library = AlarmSoundLibrary(tmp_path)
    sound = library.import_file(source, display_name="测试音频")
    card = AlarmCard(
        Alarm(
            id="alarm-custom-click-1",
            title="自定义音频点击测试",
            trigger_at="2026-08-26T09:00:00",
            sound_enabled=True,
            sound_id=sound.sound_id,
            max_ring_seconds=2,
        ),
        sound_library=library,
    )
    card.show_alarm_foreground()
    QTest.qWait(220)
    app.processEvents()

    button = next(
        item for item in card.findChildren(QPushButton)
        if item.text() == "关闭"
    )
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)

    # The click handler must return while native media cleanup is still
    # queued.  Calling QMediaPlayer.stop() inline here was the freeze path.
    assert card._media_stop_timer.isActive() or card._media_stop_completed
    app.processEvents()

    assert card.popup_state is AlarmPopupState.DISMISSED
    assert not card._audio_start_timer.isActive()
    assert card._audio_closing is True
    assert card._media_stop_completed is True
    card.close_from_app()
    # The custom backend is intentionally drained instead of synchronously
    # stopped.  The short fixture should finish and release the card without
    # ever blocking the GUI thread.
    for _ in range(20):
        app.processEvents()
        if card.audio_cleanup_ready:
            break
        QTest.qWait(50)
    assert card.audio_cleanup_ready is True


def test_windows_alarm_audio_always_closes_native_alias_after_stop_request() -> None:
    finished: list[bool] = []
    backend = _WindowsAlarmAudio(
        "test-tone.mp3",
        volume=60,
        on_finished=lambda: finished.append(True),
        on_error=lambda _error: None,
    )
    backend.alias = "lili_alarm_test"
    commands: list[str] = []
    backend._send = lambda command: commands.append(command) or 0
    # Simulate the start/stop race where the Python-side flag was not set,
    # while the native alias may still exist.
    backend._opened = False
    backend._stop_worker()

    assert commands == [
        "stop lili_alarm_test wait",
        "close lili_alarm_test wait",
    ]
    assert finished == [True]


def test_windows_custom_audio_preview_uses_async_mci_not_qt_player(tmp_path, monkeypatch) -> None:
    """The selector's automatic preview timeout must not call QMediaPlayer.stop on Windows."""

    _app()
    source = tmp_path / "preview-tone.wav"
    with wave.open(str(source), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8_000)
        output.writeframes(b"\x00\x00" * 800)
    library = AlarmSoundLibrary(tmp_path)
    sound = library.import_file(source, display_name="试听音频")
    calls: list[str] = []

    class FakeWindowsPreviewAudio:
        def __init__(self, *_args, **_kwargs) -> None:
            self.available = True

        def start(self) -> None:
            calls.append("start")

        def request_stop(self) -> None:
            calls.append("stop")

    monkeypatch.setattr(alarm_ui.sys, "platform", "win32")
    monkeypatch.setattr(alarm_ui, "_WindowsAlarmAudio", FakeWindowsPreviewAudio)
    selector = AlarmSoundSelector(library, sound.sound_id)

    assert selector._preview_player is None
    selector.preview()
    assert calls == ["start"]
    assert selector._preview_stop_timer.isActive()

    # Exercise the same callback used by the 15-second automatic stop,
    # without waiting for real time in the test.
    selector._preview_stop_timer.timeout.emit()
    assert calls == ["start", "stop"]
    assert not selector._preview_stop_timer.isActive()
    selector.close()


def test_away_recovery_card_has_no_drop_shadow() -> None:
    card = AwayRecoveryCard("idle_10m", 600)
    assert card.graphicsEffect() is None
    card.close_from_app()
