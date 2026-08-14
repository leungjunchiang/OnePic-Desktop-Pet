"""
æœ¬æ¨¡å—éªŒè¯æ¡Œé¢å® ç‰©çª—å£çš„è¿ç»­å¸§æ§åˆ¶ã€è¡¨æƒ…ç¬¦å·ã€è½®å»“é®ç½©ã€DPI æ¸²æŸ“ç¼“å­˜ã€åˆ†åŒºäº’åŠ¨ã€
å–‚é£Ÿã€ç¦»çº¿å¯¹è¯ã€é™ªä¼´åŠ¨ä½œã€å·¥ä½œè®¡æ—¶å’Œè‡ªæ‹æˆç‰‡ã€‚

æµ‹è¯•åœ¨ Qt çš„ç¦»å±å¹³å°ä¸­åˆ›å»ºçœŸå® PetWindowï¼Œä½†ä¸æ˜¾ç¤ºåˆ°ç”¨æˆ·æ¡Œé¢ã€ä¸å†™é…ç½®æ–‡ä»¶ï¼Œ
ä¹Ÿä¸å¯åŠ¨ç³»ç»Ÿæ‰˜ç›˜ã€‚é‡ç‚¹æ£€æŸ¥é€æ˜åŒºåŸŸä¸ä¼šå½¢æˆå®Œæ•´çŸ©å½¢ç‚¹å‡»åŒºã€é‡å¤ç»˜åˆ¶èƒ½å¤Ÿå¤ç”¨ç¼“å­˜ï¼Œ
ä»¥åŠåä¸‹è¿‡æ¸¡å¯æ­£å‘åœåœ¨æœ«å¸§å¹¶åå‘å›åˆ°ç«™ç«‹å¸§ã€‚
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ONEPIC_USE_DEMO_ASSETS", "1")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QDialog, QScrollArea

from onepic_desktop_pet.ai import AIConnectionError, CredentialStore
from onepic_desktop_pet.behavior import PetState, StateDecision
from onepic_desktop_pet.chat_manager import AgentConnectionState
from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.emotion_effects import emotion_effect_name
from onepic_desktop_pet.window import PetWindow
from onepic_desktop_pet.chat import AISettingsDialog, ChatDialog
from onepic_desktop_pet.work_timer import WorkTimerModel


def _create_window() -> tuple[QApplication, PetWindow]:
    """åˆ›å»ºæˆ–å¤ç”¨ç¦»å± Qt åº”ç”¨ï¼Œå¹¶è¿”å›é‡‡ç”¨é»˜è®¤è®¾ç½®çš„å® ç‰©çª—å£ã€‚"""

    app = QApplication.instance() or QApplication([])
    window = PetWindow(PetSettings())
    window.show()
    app.processEvents()
    return app, window


def test_pet_and_ambient_bubbles_never_accept_keyboard_focus() -> None:
    """æ¡Œå® å‘¨æœŸç½®é¡¶æ—¶ä¸å¾—æŠ¢èµ°å¾®ä¿¡ã€Word ç­‰å½“å‰è¾“å…¥çª—å£ã€‚"""

    app, window = _create_window()
    assert window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert window.speech_bubble.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert window.photo_bubble.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    window.close()
    window.deleteLater()
    app.processEvents()


def test_topmost_desktop_mode_switch_preserves_interaction_window(monkeypatch) -> None:
    """åˆ‡æ¢å±‚çº§ä¸å¾—ä¸¢å¤±ä½ç½®ã€åŠ¨ç”»çŠ¶æ€ã€è½®å»“ç©¿é€æˆ–æ— ç„¦ç‚¹æ ‡å¿—ã€‚"""

    app, window = _create_window()
    monkeypatch.setattr("onepic_desktop_pet.window.save_settings", lambda _settings: None)
    window.move(123, 77)
    window.set_state(PetState.WALK)
    window.animation_timer.stop()
    frame = window._frame_index

    window.set_always_on_top(False)
    app.processEvents()

    assert window.settings.always_on_top is False
    assert not window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    # Cocoa's offscreen backend aligns logical coordinates to a display pixel
    # grid (2 px on arm64 runners, 4 px on Intel runners).
    assert (window.pos() - QPoint(123, 77)).manhattanLength() <= 4
    assert window.state is PetState.WALK
    assert window._frame_index == frame
    assert not window.animation_timer.isActive()
    assert not window.mask().isEmpty()

    window.set_always_on_top(True)
    app.processEvents()

    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert (window.pos() - QPoint(123, 77)).manhattanLength() <= 4
    window.close()
    window.deleteLater()
    app.processEvents()


def test_connection_and_companion_settings_scroll_and_include_music_clients() -> None:
    """å°å±å¹•å¯æ»šåŠ¨åˆ°å…¨éƒ¨é™ªä¼´é€‰é¡¹ï¼Œå¹¶èƒ½é€‰æ‹© Apple Music/Spotifyã€‚"""

    app = QApplication.instance() or QApplication([])
    dialog = AISettingsDialog(PetSettings(), CredentialStore())
    assert dialog.findChild(QScrollArea) is not None
    assert dialog.allow_autonomous_walk.isChecked() is False
    services = {
        dialog.music_service.itemData(index)
        for index in range(dialog.music_service.count())
    }
    assert {"auto", "qq", "netease", "kugou", "apple", "spotify"} <= services
    assert dialog.music_service.currentData() == "auto"
    assert dialog.apple_music_path.isEnabled()
    assert dialog.spotify_music_path.isEnabled()
    assert dialog.always_on_top.isChecked()
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_pet_name_can_be_changed_and_updates_chat_ui() -> None:
    app = QApplication.instance() or QApplication([])
    settings = PetSettings()
    settings_dialog = AISettingsDialog(settings, CredentialStore())

    assert settings_dialog.pet_name.text() == "å…­æ¯›"
    settings_dialog.pet_name.setText("å›¢å›¢")
    settings_dialog.apply()

    assert settings.pet_name == "å›¢å›¢"
    chat = ChatDialog(None, settings.pet_name)
    assert chat.windowTitle() == "å’Œå›¢å›¢èŠèŠ"
    assert chat.pet_title.text() == "å›¢å›¢çš„å°çº¸æ¡"
    assert chat.rename_button.text() == "æ”¹åå­—"
    assert chat.rename_button.toolTip() == "ç‚¹å‡»è¿™é‡Œä¿®æ”¹å…­æ¯›çš„åå­—"
    chat.set_pet_name("é˜¿æ¯›")
    assert chat.windowTitle() == "å’Œé˜¿æ¯›èŠèŠ"
    assert chat.input.placeholderText() == "è·Ÿé˜¿æ¯›è¯´ç‚¹ä»€ä¹ˆâ€¦â€¦"

    settings_dialog.close()
    settings_dialog.deleteLater()
    chat.close()
    chat.deleteLater()
    app.processEvents()


def test_autonomous_walk_setting_is_applied_without_disabling_ambient_animation() -> None:
    app, window = _create_window()
    assert window.settings.allow_autonomous_walk is False
    assert window._walk_allowed() is False
    assert window.animation_timer.isActive()

    window.set_allow_autonomous_walk(True, persist=False)
    assert window.settings.allow_autonomous_walk is True
    assert window._walk_allowed() is True

    window.set_allow_autonomous_walk(False, persist=False)
    assert window._walk_allowed() is False
    assert window.animation_timer.isActive()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_chat_rename_button_opens_visible_rename_flow(monkeypatch) -> None:
    app, window = _create_window()
    monkeypatch.setattr("onepic_desktop_pet.window.save_settings", lambda _settings: None)
    monkeypatch.setattr(
        "onepic_desktop_pet.window.QInputDialog.getText",
        lambda *args, **kwargs: ("å›¢å­", True),
    )

    window.prompt_dialogue()
    assert window._chat_dialog is not None
    assert window._chat_dialog.rename_button.isVisible()
    window._chat_dialog.rename_button.click()
    app.processEvents()

    assert window.settings.pet_name == "å›¢å­"
    assert window._chat_dialog.pet_title.text() == "å›¢å­çš„å°çº¸æ¡"
    assert window.windowTitle().endswith("Â· å›¢å­")
    window.close()
    window.deleteLater()
    app.processEvents()


def test_hourly_unlocks_never_override_manual_outfit_selection(monkeypatch) -> None:
    """å°æ—¶æˆé•¿çº¿åªè§£é”å¨ƒè¡£ï¼Œä¸èƒ½æŠŠç”¨æˆ·é€‰å¥½çš„å¤–è§‚å¼ºè¡Œæ¢æ‰ã€‚"""
    app, window = _create_window()
    monkeypatch.setattr("onepic_desktop_pet.window.save_settings", lambda _settings: None)
    window.settings.equipped_outfit = "hour-01"
    window.work_timer._lifetime_seconds = 10 * 3600
    window.work_timer._running_since = None
    window.work_timer._notified_outfit_count = 0

    window._sync_hourly_outfit(announce=False)
    assert window.work_timer.unlocked_outfit_count() == 10
    assert window.settings.equipped_outfit == "hour-01"

    window._sync_hourly_outfit(announce=True)
    assert window.settings.equipped_outfit == "hour-01"
    window.close()
    window.deleteLater()
    app.processEvents()


def test_study_room_menu_restores_minimized_window() -> None:
    """å†æ¬¡ç‚¹å‡»è‡ªä¹ å®¤å…¥å£ä¼šæ¢å¤åŸçª—å£ï¼Œè€Œä¸æ˜¯é‡å¤åˆ›å»ºçª—å£ã€‚"""
    app, window = _create_window()
    window.open_social_hub()
    dialog = window._social_dialog
    assert dialog is not None
    assert dialog.parent() is None
    dialog.showMinimized()
    app.processEvents()

    window.open_social_hub()
    app.processEvents()
    assert window._social_dialog is dialog
    assert not dialog.isMinimized()
    dialog.close()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_background_visit_refresh_does_not_reopen_same_window(monkeypatch) -> None:
    app, window = _create_window()
    calls = []
    monkeypatch.setattr(window._buddy_visit_window, "show_peer", lambda *args, **kwargs: calls.append(args))
    peer = {"id": "visit-1", "nickname": "æ­å­", "today_seconds": 5}
    window._show_buddy_visit(peer)
    window._show_buddy_visit(peer)
    assert len(calls) == 1
    window.close()
    window.deleteLater()
    app.processEvents()


def test_window_uses_character_mask_and_reuses_render_cache() -> None:
    app, window = _create_window()
    initial_render_count = len(window._render_cache)
    initial_mask_count = len(window._mask_cache)

    window._refresh_pixmap()

    assert not window.mask().isEmpty()
    assert window.mask().boundingRect().width() < window.width()
    assert len(window._render_cache) == initial_render_count
    assert len(window._mask_cache) == initial_mask_count
    window.close()
    window.deleteLater()
    app.processEvents()


def test_sit_animation_holds_then_reverses_to_standing_frame() -> None:
    app, window = _create_window()
    window.set_state(PetState.SIT)

    for _ in range(len(window._pixmaps[PetState.SIT])):
        window._animation_tick()

    assert window._frame_index == len(window._pixmaps[PetState.SIT]) - 1
    assert not window.animation_timer.isActive()

    window._reverse_transition_to_idle()
    for _ in range(len(window._pixmaps[PetState.SIT]) - 1):
        window._animation_tick()

    assert window._frame_index == 0
    assert not window.animation_timer.isActive()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_walk_pauses_briefly_when_turning_at_screen_edge() -> None:
    app, window = _create_window()
    window.set_state(PetState.WALK)
    window.direction = -1
    window._frame_index = 3
    window.move(0, 0)
    window._screen_geometry = lambda: QRect(0, 0, 1000, 1000)

    window._movement_tick()

    assert window.direction == 1
    assert window._turn_paused
    assert window.turn_timer.isActive()
    assert not window.animation_timer.isActive()

    window._finish_turn()

    assert not window._turn_paused
    assert window.animation_timer.isActive()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_walk_vertical_offset_follows_footfall_frames() -> None:
    """è¡Œèµ°èµ·ä¼å¿…é¡»è·Ÿéšè¿ç»­å¸§ï¼Œè€Œä¸æ˜¯ç”±ç‹¬ç«‹çš„æ…¢é€Ÿæµ®åŠ¨è®¡æ—¶å™¨é©±åŠ¨ã€‚"""

    app, window = _create_window()
    window.set_state(PetState.WALK)
    offsets = [window.label.y()]

    for _ in range(3):
        window._animation_tick()
        offsets.append(window.label.y())

    assert offsets == [3, 5, 2, 0]
    window.close()
    window.deleteLater()
    app.processEvents()


def test_walk_uses_subpixel_phase_synced_speed(monkeypatch) -> None:
    """æ°´å¹³ç§»åŠ¨åº”äºšåƒç´ ç´¯è®¡ï¼Œè½è„šé˜¶æ®µå‡é€Ÿè€Œä¸å†»ç»“ï¼Œéšåå¹³æ»‘åŠ é€Ÿã€‚"""

    app, window = _create_window()
    window.set_state(PetState.WALK)
    window.direction = 1
    window.move(100, 0)
    window._movement_x = 100.0
    window._last_movement_at = 10.0
    window._screen_geometry = lambda: QRect(0, 0, 1000, 1000)
    current_time = [10.016]
    monkeypatch.setattr(
        "onepic_desktop_pet.window.time.monotonic",
        lambda: current_time[0],
    )

    window._frame_index = 0
    window._movement_tick()
    assert round(window._movement_x, 2) == 100.45
    assert window.x() == 100

    current_time[0] = 10.032
    window._frame_index = 3
    window._movement_tick()
    assert window._movement_speed_pixels_per_second() == 62.5
    assert round(window._movement_x, 2) == 102.1
    assert window.x() == 102
    window.close()
    window.deleteLater()
    app.processEvents()


def test_walk_motion_curve_avoids_freeze_and_balances_both_steps() -> None:
    """ç§»åŠ¨æ›²çº¿ä¸åº”åœé¡¿åçŒ›è·³ï¼Œä¸”å·¦å³ä¸¤ä¸ªåŠæ­¥å¿…é¡»ä½¿ç”¨ç›¸åŒèŠ‚å¥ã€‚"""

    app, window = _create_window()

    assert min(window._walk_motion_factors) > 0.0
    assert max(window._walk_motion_factors) / min(window._walk_motion_factors) < 4
    assert window._walk_motion_factors[:4] == window._walk_motion_factors[4:]
    assert sum(window._walk_motion_factors) / 8 == 1.0

    window.close()
    window.deleteLater()
    app.processEvents()


def test_drag_state_uses_dedicated_suspended_animation() -> None:
    """æ‹–æ‹½çŠ¶æ€åº”åŠ è½½ä¸‰å¸§æ‚¬ç©ºç´ æï¼Œè€Œä¸æ˜¯å›é€€åˆ°å¾…æœºç«™ç«‹ã€‚"""

    app, window = _create_window()
    window.set_state(PetState.DRAG)

    display_state, _pixmap = window._current_source()
    assert display_state is PetState.DRAG
    assert len(window._pixmaps[PetState.DRAG]) == 3
    assert window.animation_timer.isActive()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_interaction_states_have_reusable_emotion_symbols() -> None:
    """äº’åŠ¨è¡¨æƒ…åº”ä½¿ç”¨ç‹¬ç«‹ç¬¦å·å±‚ï¼Œæ¢è§’è‰²ç´ æåä»ç„¶èƒ½å¤Ÿæ˜¾ç¤ºã€‚"""

    expected = {
        PetState.HAPPY: "sparkle",
        PetState.SHY: "heart",
        PetState.SURPRISED: "exclamation",
        PetState.ANNOYED: "anger",
        PetState.SLEEPY: "sleep",
        PetState.CURIOUS: "question",
        PetState.SELFIE: "flash",
        PetState.DRAG: "sweat",
    }
    assert {state: emotion_effect_name(state) for state in expected} == expected
    assert emotion_effect_name(PetState.IDLE) is None


def test_emotion_symbol_timer_follows_current_state() -> None:
    """è¿›å…¥è¡¨æƒ…çŠ¶æ€æ—¶ç¬¦å·åº”åŠ¨ç”»ï¼Œæ¢å¤å¾…æœºåå¿…é¡»åœæ­¢è®¡æ—¶å™¨ã€‚"""

    app, window = _create_window()
    window.set_state(PetState.SURPRISED)
    assert window.effect_timer.isActive()
    assert not window.label.pixmap().isNull()

    window.set_state(PetState.IDLE)
    assert not window.effect_timer.isActive()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_inactivity_progresses_from_sit_to_sleep() -> None:
    """è¶…è¿‡ç¡çœ é˜ˆå€¼åä»åº”å…ˆå®Œæ•´åä¸‹ï¼Œå†æ’­æ”¾åå§¿å…¥ç¡åºåˆ—ã€‚"""

    settings = PetSettings(inactive_sit_ms=10000, inactive_sleep_ms=20000)
    app = QApplication.instance() or QApplication([])
    window = PetWindow(settings)
    window._last_user_interaction = time.monotonic() - 21
    window.set_state(PetState.IDLE)

    window._state_timeout()
    assert window.state is PetState.SIT
    assert window._sleep_after_sit

    window._state_timeout()
    assert window.state is PetState.SLEEP
    assert not window._sleep_after_sit
    window.close()
    window.deleteLater()
    app.processEvents()


def test_pause_disables_running_but_keeps_ambient_state_timer() -> None:
    """æš‚åœè·‘åŠ¨æ—¶åº”è¿›å…¥ç”Ÿæ´×İú¶‰ËkºwµçZ¦¦–»–N’â;*Ûš‰t¹µ•¹Ô ¤(€€€…ÍÍ•ÉĞ™½½‘}µ•¹Ô¥Ì¹½Ğ9½¹”(€€€™½½‘}…Ñ¥½¹Ì€ôm…Ñ¥½¸¹Ñ•áĞ ¤™½È…Ñ¥½¸¥¸™½½‘}µ•¹Ô¹…Ñ¥½¹Ì ¤¥˜¹½Ğ…Ñ¥½¸¹¥ÍM•Á…É…Ñ½È ¥t(€€€…ÍÍ•ÉĞ™½½‘}…Ñ¥½¹Ì€ôôl(€€€€€€€€‹¢.çšzpˆ°(€€€€€€€€‹–Â?¦–ó–æÈˆ°(€€€€€€€€‹·&o––Øˆ°(€€€€€€€€‹–J[–V„ˆ°(€€€€€€€€‹·¢2Øˆ°(€€€€€€€€‹š~—r/–·š¾o–şš’â;¢÷¦<ˆ°(€€€t(€€€…ÍÍ•ÉĞ¹½Ğ…¹ä ‹š&Oš.o–Fğˆ¥¸Ñ•áĞ™½ÈÑ•áĞ¥¸¡…Ñ}…Ñ¥½¹Ì¤(€€€…ÍÍ•ÉĞ€‹¢ş{î·¢Â¢*–ºƒ&§–’Ÿ–Â?Š˜ˆ¥¸ÍåÍÑ•µ}…Ñ¥½¹Ì(€€€İ½É­}…Ñ¥½¸€ô¹•áĞ¡…Ñ¥½¸™½ÈÑ•áĞ°…Ñ¥½¸¥¸™½ÕÍ}…Ñ¥½¹Ì¹¥Ñ•µÌ ¤¥˜Ñ•áĞ¹ÍÑ…ÉÑÍİ¥Ñ  ‹–Ş—’ös¢º‡š^Û¾òhˆ¤¤(€€€İ½É­}±…‰•±Ì€ôm…Ñ¥½¸¹Ñ•áĞ ¤™½È…Ñ¥½¸¥¸İ½É­}…Ñ¥½¸¹µ•¹Ô ¤¹…Ñ¥½¹Ì ¥t(€€€…ÍÍ•ÉĞ€‹š~—r/’î+š^”€ÃŠLàƒ–Â?š^Ûš"C¦Vÿêüˆ¥¸İ½É­}±…‰•±Ì(€€€…ÍÍ•ÉĞ€‹’î+–’§–·š¾o¦f«’öƒ–k’ê’î’æ ˆ¥¸İ½É­}±…‰•±Ì(€€€µ•¹Ô¹±½Í” ¤(€€€İ¥¹‘½Ü¹±½Í” ¤(€€€İ¥¹‘½Ü¹‘•±•Ñ•1…Ñ•È ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤(()‘•˜Ñ•ÍÑ}½µÁ…¹¥½¹}…Ñ¥½¹}Í¡½İÍ}±½Ù¥¹}…¹¥µ…Ñ¥½¹}…¹‘}İ½É‘Ì ¤€´ø9½¹”è(€€€€ˆˆ‹’âï–*£¦'š.§š*Çš*Ç–êS–Š{–*ƒ’êË–¾–ê›¾ò3–æÛšbû’ë"Çš?–*£’ös’â;–º'šÃ¢¾w¢¾·ˆˆˆ((€€€…ÁÀ°İ¥¹‘½Ü€ô}É•…Ñ•}İ¥¹‘½Ü ¤(€€€¥¹¥Ñ¥…±}…™™¥¹¥Ñä€ôİ¥¹‘½Ü¹µ½½¹…™™¥¹¥Ñä((€€€É•Á±ä€ôİ¥¹‘½Ü¹Á•É™½Éµ}½µÁ…¹¥½¹}…Ñ¥½¸ ‰±½Ù”ˆ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤((€€€…ÍÍ•ÉĞÉ•Á±ä¹ÍÑ…Ñ”¥ÌA•ÑMÑ…Ñ”¹M!d(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹ÍÑ…Ñ”¥ÌA•ÑMÑ…Ñ”¹M!d(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹µ½½¹…™™¥¹¥Ñä€ôô¥¹¥Ñ¥…±}…™™¥¹¥Ñä€¬€Ô(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹ÍÁ••¡}‰Õ‰‰±”¹Ñ•áĞ ¤€ôôÉ•Á±ä¹Ñ•áĞ(€€€…ÍÍ•ÉĞ…¹ä¡İ½É¥¸É•Á±ä¹Ñ•áĞ™½Èİ½É¥¸€ ‹š*Çš*Äˆ°€‹¢ÒÓ¢ÒĞˆ¤¤(€€€İ¥¹‘½Ü¹±½Í” ¤(€€€İ¥¹‘½Ü¹‘•±•Ñ•1…Ñ•È ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤(()‘•˜Ñ•ÍÑ}‘¥Ù•ÉÍ•}…Ñ¥½¹}Í•ÅÕ•¹•}…¹‘}‘É¥¹­Í}ÕÍ•}•á¥ÍÑ¥¹}™É…µ•Ì ¤€´ø9½¹”è(€€€€ˆˆ‹’òã–ÆW’â;–Zw¢2Û–êSî–B#–ŞË¦ª3šRÛ*Ûš¾ò3¢3’â7šb¿¦vgš¶‹–rÃ–>«š6‹’â–>—šZ–¶_ˆˆˆ((€€€…ÁÀ°İ¥¹‘½Ü€ô}É•…Ñ•}İ¥¹‘½Ü ¤(€€€É•Á±ä€ôİ¥¹‘½Ü¹Á•É™½Éµ}½µÁ…¹¥½¹}…Ñ¥½¸ ‰ÍÑÉ•Ñ ˆ¤(€€€…ÍÍ•ÉĞÉ•Á±ä¹ÍÑ…Ñ”¥ÌA•ÑMÑ…Ñ”¹]Y(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹ÍÑ…Ñ”¥ÌA•ÑMÑ…Ñ”¹M%P(€€€Ñ•„€ôİ¥¹‘½Ü¹™••‘}Á•Ğ ‰Ñ•„ˆ¤(€€€…ÍÍ•ÉĞ€‹·¢2Øˆ¥¸Ñ•„¹Ñ•áĞ(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹ÍÑ…Ñ”¥ÌA•ÑMÑ…Ñ”¹M%P(€€€İ¥¹‘½Ü¹±½Í” ¤(€€€İ¥¹‘½Ü¹‘•±•Ñ•1…Ñ•È ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤(()‘•˜Ñ•ÍÑ}¡½ÕÉ±å}…¹¹½Õ¹•µ•¹Ñ}…¹}‰•}‘¥Í…‰±•‘}…¹‘}‘•‘ÕÁ±¥…Ñ•Ì ¤€´ø9½¹”è(€€€€ˆˆ‹šVÓ
çš*—š^Û¦îc¢º“’â7¢›–>G¾ò3–ò–B¿–B;–B3’â–Â?š^Û–>«¢›–>G’âš²‡ˆˆˆ((€€€…ÁÀ°İ¥¹‘½Ü€ô}É•…Ñ•}İ¥¹‘½Ü ¤(€€€¹½Ü€ô‘…Ñ•Ñ¥µ” ÈÀÈØ°€à°€ÄÀ°€ÄĞ°€À°€Ô¤(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹}µ…å‰•}…¹¹½Õ¹•}¡½ÕÈ¡¹½Ü¤¥Ì…±Í”(€€€İ¥¹‘½Ü¹Í•ÑÑ¥¹Ì¹¡½ÕÉ±å}…¹¹½Õ¹•µ•¹Ğ€ôQÉÕ”(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹}µ…å‰•}…¹¹½Õ¹•}¡½ÕÈ¡¹½Ü¤¥ÌQÉÕ”(€€€…ÍÍ•ÉĞ€ˆÄĞèÀÀˆ¥¸İ¥¹‘½Ü¹ÍÁ••¡}‰Õ‰‰±”¹Ñ•áĞ ¤(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹}µ…å‰•}…¹¹½Õ¹•}¡½ÕÈ¡¹½Ü¤¥Ì…±Í”(€€€İ¥¹‘½Ü¹±½Í” ¤(€€€İ¥¹‘½Ü¹‘•±•Ñ•1…Ñ•È ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤()‘•˜Ñ•ÍÑ}İ½É­}Ñ¥µ•É}ÍÑ…ÉÑ}ÍÑ…ÑÕÍ}É•µ¥¹‘•É}…¹‘}™¥¹¥Í ¡ÑµÁ}Á…Ñ ¤€´ø9½¹”è(€€€€ˆˆ‹–Ş—’ös¢º‡š^Û–êSšbû’ë’î+š^—Ò¿¢º‡¾ò3–æÛ–r£¢ş{î·–Ş—’ös¢ş’æš^Û–*wR£š"ß’òGš¿ˆˆˆ((€€€¹½Ü€ôm‘…Ñ•Ñ¥µ” ÈÀÈØ°€à°€ÄÀ°€ä°€À°€À¥t(€€€µ½¹½Ñ½¹¥Œ€ôlÄÀÀ¸Át(€€€Ñ¥µ•È€ô]½É­Q¥µ•É5½‘•° (€€€€€€€Á…Ñ õÑµÁ}Á…Ñ €¼€‰İ½É­}Ñ¥µ•È¹©Í½¸ˆ°(€€€€€€€¹½İ}ÁÉ½Ù¥‘•Èõ±…µ‰‘„è¹½İlÁt°(€€€€€€€µ½¹½Ñ½¹¥}ÁÉ½Ù¥‘•Èõ±…µ‰‘„èµ½¹½Ñ½¹¥lÁt°(€€€€¤(€€€…ÁÀ€ôEÁÁ±¥…Ñ¥½¸¹¥¹ÍÑ…¹” ¤½ÈEÁÁ±¥…Ñ¥½¸¡mt¤(€€€İ¥¹‘½Ü€ôA•Ñ]¥¹‘½Ü¡A•ÑM•ÑÑ¥¹Ì ¤°İ½É­}Ñ¥µ•ÈõÑ¥µ•È¤(€€€İ¥¹‘½Ü¹Í¡½Ü ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤((€€€ÍÑ…ÉÑ}É•Á±ä€ôİ¥¹‘½Ü¹ÍÑ…ÉÑ}İ½É­}Ñ¥µ•È ¤(€€€…ÍÍ•ÉĞÍÑ…ÉÑ}É•Á±ä¹ÍÑ…Ñ”¥ÌA•ÑMÑ…Ñ”¹M%P(€€€…ÍÍ•ÉĞÑ¥µ•È¹¥Í}ÉÕ¹¹¥¹œ(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹Á…ÕÍ•((€€€¹½İlÁt€¬ôÑ¥µ•‘•±Ñ„¡µ¥¹ÕÑ•ÌôÔÀ¤(€€€µ½¹½Ñ½¹¥lÁt€¬ô€ÔÀ€¨€ØÀ(€€€İ¥¹‘½Ü¹}İ½É­}Ñ¥µ•É}Ñ¥¬ ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹ÍÑ…Ñ”¥ÌA•ÑMÑ…Ñ”¹M1Ad(€€€…ÍÍ•ÉĞ€‹šÒï–* ˆ¥¸İ¥¹‘½Ü¹ÍÁ••¡}‰Õ‰‰±”¹Ñ•áĞ ¤(€€€…ÍÍ•ÉĞ€ˆÔÃ–"¦J|ˆ¥¸Ñ¥µ•È¹ÍÑ…ÑÕÍ}Ñ•áĞ ¤((€€€™¥¹¥Í¡}É•Á±ä€ôİ¥¹‘½Ü¹™¥¹¥Í¡}İ½É­}Ñ¥µ•È ¤(€€€…ÍÍ•ÉĞ™¥¹¥Í¡}É•Á±ä¹ÍÑ…Ñ”¥ÌA•ÑMÑ…Ñ”¹!AAd(€€€…ÍÍ•ÉĞ¹½ĞÑ¥µ•È¹¥Í}ÉÕ¹¹¥¹œ(€€€…ÍÍ•ÉĞ¹½Ğİ¥¹‘½Ü¹Á…ÕÍ•(€€€…ÍÍ•ÉĞ€ˆÔÃ–"¦J|ˆ¥¸™¥¹¥Í¡}É•Á±ä¹Ñ•áĞ(€€€İ¥¹‘½Ü¹±½Í” ¤(€€€İ¥¹‘½Ü¹‘•±•Ñ•1…Ñ•È ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤(()‘•˜Ñ•ÍÑ}‘¥…±½Õ•}Á…¹•±}Á…ÍÍ•Í}Ñ•áÑ}Ñ½}±½…±}É•Á±ä ¤€´ø9½¹”è(€€€€ˆˆ‹šZÃ&#¢+–’§¦v‹švÿ–>G¦šZ–¶_–B;–êS’ê“îgšïêÿ¢–"g–æÛšbû’ë–n{–’7ˆˆˆ((€€€…ÁÀ°İ¥¹‘½Ü€ô}É•…Ñ•}İ¥¹‘½Ü ¤(€€€İ¥¹‘½Ü¹ÁÉ½µÁÑ}‘¥…±½Õ” ¤(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹}¡…Ñ}‘¥…±½œ¥Ì¹½Ğ9½¹”(€€€Í•ÑÑ¥¹Í}ÍÁä€ôEM¥¹…±MÁä¡İ¥¹‘½Ü¹}¡…Ñ}‘¥…±½œ¹Í•ÑÑ¥¹Í}É•ÅÕ•ÍÑ•¤(€€€İ¥¹‘½Ü¹}¡…Ñ}‘¥…±½œ¹¥¹ÁÕĞ¹Í•ÑQ•áĞ ‹’î+–’§šr'
çÒ¼ˆ¤(€€€İ¥¹‘½Ü¹}¡…Ñ}‘¥…±½œ¹}ÍÕ‰µ¥Ğ ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤((€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹ÍÑ…Ñ”¥ÌA•ÑMÑ…Ñ”¹M1Ad(€€€…ÍÍ•ÉĞ€‹–Zw–>šÂĞˆ¥¸İ¥¹‘½Ü¹ÍÁ••¡}‰Õ‰‰±”¹Ñ•áĞ ¤(€€€…ÍÍ•ÉĞ€‹šïêüˆ¥¸İ¥¹‘½Ü¹}¡…Ñ}‘¥…±½œ¹ÍÑ…ÑÕÍ}±…‰•°¹Ñ•áĞ ¤(€€€…ÍÍ•ÉĞÍ•ÑÑ¥¹Í}ÍÁä¹½Õ¹Ğ ¤€ôô€À(€€€İ¥¹‘½Ü¹±½Í” ¤(€€€İ¥¹‘½Ü¹‘•±•Ñ•1…Ñ•È ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤(()‘•˜Ñ•ÍÑ}…•¹Ñ}¡•­¥¹}¹•Ù•É}½Á•¹Í}Í•ÑÑ¥¹Í}…¹‘}½µÁ±•á}¡…Ñ}Í¡½İÍ}‰ÕÑÑ½¹Ì ¤€´ø9½¹”è(€€€€ˆˆ‰•¹Ğƒ–Âk–r£ššÖ/š^Û¢+–’§–êS®/–6Ïšïêÿ–n{–’7¾ò3–>«šbû’ëš&/–*£š2'¦J»¢3’â7–òç¢ºûö»ˆˆˆ((€€€…ÁÀ°İ¥¹‘½Ü€ô}É•…Ñ•}İ¥¹‘½Ü ¤(€€€İ¥¹‘½Ü¹Í•ÑÑ¥¹Ì¹…¥}ÁÉ½Ù¥‘•È€ô€‰½‘•àˆ(€€€İ¥¹‘½Ü¹ÁÉ½µÁÑ}‘¥…±½Õ” ¤(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹}¡…Ñ}‘¥…±½œ¥Ì¹½Ğ9½¹”(€€€Í•ÑÑ¥¹Í}ÍÁä€ôEM¥¹…±MÁä¡İ¥¹‘½Ü¹}¡…Ñ}‘¥…±½œ¹Í•ÑÑ¥¹Í}É•ÅÕ•ÍÑ•¤((€€€İ¥¹‘½Ü¹}¡…Ñ}‘¥…±½œ¹¥¹ÁÕĞ¹Í•ÑQ•áĞ ‹–â»š"G–g’î‚–æÛ–"šzC¢şg’â«¦†çn¸ˆ¤(€€€İ¥¹‘½Ü¹}¡…Ñ}‘¥…±½œ¹}ÍÕ‰µ¥Ğ ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤((€€€…ÍÍ•ÉĞÍ•ÑÑ¥¹Í}ÍÁä¹½Õ¹Ğ ¤€ôô€À(€€€…ÍÍ•ÉĞ€‹šïêÿš¢‡–ò<ˆ¥¸İ¥¹‘½Ü¹}¡…Ñ}‘¥…±½œ¹ÑÉ…¹ÍÉ¥ÁĞ¹Ñ½A±…¥¹Q•áĞ ¤(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹}¡…Ñ}‘¥…±½œ¹É•½Ù•Éå}…Ñ¥½¹Ì¹¥ÍY¥Í¥‰±” ¤(€€€…ÍÍ•ÉĞ€‹š¶–r£–B;–>ÃššÖ,ˆ¥¸İ¥¹‘½Ü¹}¡…Ñ}‘¥…±½œ¹ÍÑ…ÑÕÍ}±…‰•°¹Ñ•áĞ ¤(€€€İ¥¹‘½Ü¹±½Í” ¤(€€€İ¥¹‘½Ü¹‘•±•Ñ•1…Ñ•È ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤(()‘•˜Ñ•ÍÑ}Í•ÑÑ¥¹Í}½Á•¹}Á…Ñ¡}É•©•ÑÍ}•Ù•Éå}¹½¹}ÕÍ•É}Í½ÕÉ”¡µ½¹­•åÁ…Ñ ¤€´ø9½¹”è(€€€€ˆˆ‰•¹ĞƒššÖ/¦Rg¢¾¿¢Úš^Ûš"[–¦£¢ÂR£–v’â7¢÷–"o–îë¢ºûö»ª_–>ˆˆˆ((€€€É•…Ñ•€ômt((€€€±…ÍÌ…­•M•ÑÑ¥¹Í¥…±½œè(€€€€€€€‘•˜}}¥¹¥Ñ}|¡Í•±˜°€©}…ÉÌ°€¨©}­İ…ÉÌ¤€´ø9½¹”è(€€€€€€€€€€€É•…Ñ•¹…ÁÁ•¹¡QÉÕ”¤((€€€€€€€‘•˜•á•Œ¡Í•±˜¤è(€€€€€€€€€€€É•ÑÕÉ¸E¥…±½œ¹¥…±½½‘”¹I•©•Ñ•((€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ (€€€€€€€€‰½¹•Á¥}‘•Í­Ñ½Á}Á•Ğ¹İ¥¹‘½Ü¹%M•ÑÑ¥¹Í¥…±½œˆ°(€€€€€€€…­•M•ÑÑ¥¹Í¥…±½œ°(€€€€¤(€€€…ÁÀ°İ¥¹‘½Ü€ô}É•…Ñ•}İ¥¹‘½Ü ¤((€€€™½ÈÍ½ÕÉ”¥¸€ (€€€€€€€€‰ÍÑ…ÉÑÕÁ}‘•Ñ•Ñ¥½¸ˆ°(€€€€€€€€‰¡•­¥¹œˆ°(€€€€€€€€‰…•¹Ñ}‘¥Í½¹¹•Ñ•ˆ°(€€€€€€€€‰…•¹Ñ}•ÉÉ½Èˆ°(€€€€€€€€‰…•¹Ñ}Ñ¥µ•½ÕĞˆ°(€€€€€€€€‰±¥}¹½Ñ}™½Õ¹ˆ°(€€€€€€€€‰…¥}…±±}™…¥±•ˆ°(€€€€€€€€‰¥¹Ñ•É¹…°ˆ°(€€€€€€€€ˆˆ°(€€€€¤è(€€€€€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹½Á•¹}Í•ÑÑ¥¹Ì¡Í½ÕÉ”¤¥Ì…±Í”(€€€…ÍÍ•ÉĞÉ•…Ñ•€ôômt((€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹½Á•¹}Í•ÑÑ¥¹Ì ‰ÕÍ•É}…Ñ¥½¸ˆ¤¥ÌQÉÕ”(€€€…ÍÍ•ÉĞÉ•…Ñ•€ôômQÉÕ•t(€€€İ¥¹‘½Ü¹±½Í” ¤(€€€İ¥¹‘½Ü¹‘•±•Ñ•1…Ñ•È ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤(()ÁåÑ•ÍĞ¹µ…É¬¹Á…É…µ•ÑÉ¥é” (€€€€‰Í•¹…É¥¼ˆ°(€€€€ ‰½¹¹•Ñ•ˆ°€‰¡•­¥¹œˆ°€‰‘¥Í½¹¹•Ñ•ˆ°€‰•ÉÉ½Èˆ°€‰Ñ¥µ•½ÕĞˆ¤°(¤)‘•˜Ñ•ÍÑ}Ñ•¹}µ•ÍÍ…•Í}¥¹}•Ù•Éå}…•¹Ñ}ÍÑ…Ñ•}¹•Ù•É}½Á•¹}Í•ÑÑ¥¹Ì (€€€µ½¹­•åÁ…Ñ °(€€€Í•¹…É¥¼èÍÑÈ°(¤€´ø9½¹”è(€€€€ˆˆ‹’êS4•¹Ğƒš–×–B¢ş{î·–>G¦–6šv‡šÚ#š¿¦÷’â7–ú_’êŸR¢ºûö»ª_–>–&¿’ösR£ˆˆˆ((€€€Í•ÑÑ¥¹Í}½Á•¹•€ômt((€€€±…ÍÌ½É‰¥‘‘•¹M•ÑÑ¥¹Í¥…±½œè(€€€€€€€‘•˜}}¥¹¥Ñ}|¡Í•±˜°€©}…ÉÌ°€¨©}­İ…ÉÌ¤€´ø9½¹”è(€€€€€€€€€€€Í•ÑÑ¥¹Í}½Á•¹•¹…ÁÁ•¹¡QÉÕ”¤(€€€€€€€€€€€É…¥Í”ÍÍ•ÉÑ¥½¹ÉÉ½È ‹¢+–’§š"X•¹Ğƒ*Ûš’â7–ú_–"o–îë¢ºûö»ª_–>Œˆ¤((€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ (€€€€€€€€‰½¹•Á¥}‘•Í­Ñ½Á}Á•Ğ¹İ¥¹‘½Ü¹%M•ÑÑ¥¹Í¥…±½œˆ°(€€€€€€€½É‰¥‘‘•¹M•ÑÑ¥¹Í¥…±½œ°(€€€€¤(€€€…ÁÀ°İ¥¹‘½Ü€ô}É•…Ñ•}İ¥¹‘½Ü ¤(€€€İ¥¹‘½Ü¹Í•ÑÑ¥¹Ì¹…¥}ÁÉ½Ù¥‘•È€ô€‰½‘•àˆ(€€€Í•ÉÙ¥•}…±±Ì€ômt((€€€‘•˜ÍÕ•ÍÍ™Õ±}É•Á±ä ©}…ÉÌ°€¨©}­İ…ÉÌ¤€´øÍÑÈè(€€€€€€€Í•ÉÙ¥•}…±±Ì¹…ÁÁ•¹ ‰ÍÕ•ÍÌˆ¤(€€€€€€€É•ÑÕÉ¸€‰$ƒ–·š¾o–r£¢şg¦3ˆ((€€€‘•˜Ñ¥µ•‘}½ÕÑ}É•Á±ä ©}…ÉÌ°€¨©}­İ…ÉÌ¤€´øÍÑÈè(€€€€€€€Í•ÉÙ¥•}…±±Ì¹…ÁÁ•¹ ‰Ñ¥µ•½ÕĞˆ¤(€€€€€€€É…¥Í”%½¹¹•Ñ¥½¹ÉÉ½È ‰½‘•àƒ¢¾ßšÆ¢Úš^Ûˆ¤((€€€¥˜Í•¹…É¥¼€ôô€‰½¹¹•Ñ•ˆè(€€€€€€€İ¥¹‘½Ü¹…•¹Ñ}µ…¹…•È¹µ…É­}ÉÕ¹Ñ¥µ•}ÍÕ•ÍÌ ‰½‘•àˆ¤(€€€€€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡İ¥¹‘½Ü¹¡…Ñ}µ…¹…•È¹Í•ÉÙ¥”°€‰É•Á±äˆ°ÍÕ•ÍÍ™Õ±}É•Á±ä¤(€€€•±¥˜Í•¹…É¥¼€ôô€‰Ñ¥µ•½ÕĞˆè(€€€€€€€İ¥¹‘½Ü¹…•¹Ñ}µ…¹…•È¹µ…É­}ÉÕ¹Ñ¥µ•}ÍÕ•ÍÌ ‰½‘•àˆ¤(€€€€€€€µ½¹­•åÁ…Ñ ¹Í•Ñ…ÑÑÈ¡İ¥¹‘½Ü¹¡…Ñ}µ…¹…•È¹Í•ÉÙ¥”°€‰É•Á±äˆ°Ñ¥µ•‘}½ÕÑ}É•Á±ä¤(€€€•±Í”è(€€€€€€€ÍÑ…Ñ”€ô•¹Ñ½¹¹•Ñ¥½¹MÑ…Ñ”¡Í•¹…É¥¼¤(€€€€€€€İ¥¹‘½Ü¹…•¹Ñ}µ…¹…•È¹}Í•Ñ}ÍÑ…ÑÕÌ ‰½‘•àˆ°ÍÑ…Ñ”°˜‹šÖ/¢¾W*Ûš¾òiíÍ•¹…É¥½ôˆ¤((€€€İ¥¹‘½Ü¹ÁÉ½µÁÑ}‘¥…±½Õ” ¤(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹}¡…Ñ}‘¥…±½œ¥Ì¹½Ğ9½¹”(€€€Í•ÑÑ¥¹Í}Í¥¹…°€ôEM¥¹…±MÁä¡İ¥¹‘½Ü¹}¡…Ñ}‘¥…±½œ¹Í•ÑÑ¥¹Í}É•ÅÕ•ÍÑ•¤((€€€™½È¥¹‘•à¥¸É…¹” ÄÀ¤è(€€€€€€€İ¥¹‘½Ü¹}¡…Ñ}‘¥…±½œ¹¥¹ÁÕĞ¹Í•ÑQ•áĞ¡˜‹²°í¥¹‘•à€¬€Åôƒšv‡šÖ/¢¾WšÚ#š¼ˆ¤(€€€€€€€İ¥¹‘½Ü¹}¡…Ñ}‘¥…±½œ¹}ÍÕ‰µ¥Ğ ¤(€€€€€€€Ñ¡É•…€ôİ¥¹‘½Ü¹¡…Ñ}µ…¹…•È¹}Ñ¡É•…(€€€€€€€¥˜Ñ¡É•…¥Ì¹½Ğ9½¹”è(€€€€€€€€€€€…ÍÍ•ÉĞÑ¡É•…¹İ…¥Ğ ÈÀÀÀ¤(€€€€€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤((€€€…ÍÍ•ÉĞÍ•ÑÑ¥¹Í}½Á•¹•€ôômt(€€€…ÍÍ•ÉĞÍ•ÑÑ¥¹Í}Í¥¹…°¹½Õ¹Ğ ¤€ôô€À(€€€¥˜Í•¹…É¥¼€ôô€‰½¹¹•Ñ•ˆè(€€€€€€€…ÍÍ•ÉĞ±•¸¡Í•ÉÙ¥•}…±±Ì¤€ôô€ÄÀ(€€€•±¥˜Í•¹…É¥¼€ôô€‰Ñ¥µ•½ÕĞˆè(€€€€€€€…ÍÍ•ÉĞÍ•ÉÙ¥•}…±±Ì€ôôl‰Ñ¥µ•½ÕĞ‰t(€€€€€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹…•¹Ñ}µ…¹…•È¹ÍÑ…ÑÕÌ ‰½‘•àˆ¤¹ÍÑ…Ñ”¥Ì•¹Ñ½¹¹•Ñ¥½¹MÑ…Ñ”¹II=H(€€€•±Í”è(€€€€€€€…ÍÍ•ÉĞÍ•ÉÙ¥•}…±±Ì€ôômt(€€€İ¥¹‘½Ü¹±½Í” ¤(€€€İ¥¹‘½Ü¹‘•±•Ñ•1…Ñ•È ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤(()‘•˜Ñ•ÍÑ}¥¹Ñ•É…Ñ¥½¹}é½¹•Í}µ…Á}¡•…‘}™…•}‰½‘å}…¹‘}…µ•É„ ¤€´ø9½¹”è(€€€€ˆˆ‹ª_–>nã–¾ç’ö7ö»–êS¢Ï–ºkšbƒ–Â’âë–no7
ç–ï–2ë–~ˆˆˆ((€€€…ÁÀ°İ¥¹‘½Ü€ô}É•…Ñ•}İ¥¹‘½Ü ¤(€€€•¹Ñ•É}à€ôİ¥¹‘½Ü¹±…‰•°¹à ¤€¬İ¥¹‘½Ü¹±…‰•°¹İ¥‘Ñ  ¤€¼¼€È(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹}¥¹Ñ•É…Ñ¥½¹}é½¹”¡EA½¥¹Ğ¡•¹Ñ•É}à°€ÈÀ¤¤€ôô€‰¡•…ˆ(€€€…ÍÍ•ÉĞ€ (€€€€€€€İ¥¹‘½Ü¹}¥¹Ñ•É…Ñ¥½¹}é½¹” (€€€€€€€€€€€EA½¥¹Ğ¡•¹Ñ•É}à°É½Õ¹¡İ¥¹‘½Ü¹±…‰•°¹¡•¥¡Ğ ¤€¨€À¸ÌĞ¤¤(€€€€€€€€¤(€€€€€€€€ôô€‰™…”ˆ(€€€€¤(€€€…ÍÍ•ÉĞ€ (€€€€€€€İ¥¹‘½Ü¹}¥¹Ñ•É…Ñ¥½¹}é½¹” (€€€€€€€€€€€EA½¥¹Ğ (€€€€€€€€€€€€€€€İ¥¹‘½Ü¹±…‰•°¹à ¤€¬É½Õ¹¡İ¥¹‘½Ü¹±…‰•°¹İ¥‘Ñ  ¤€¨€À¸È¤°(€€€€€€€€€€€€€€€É½Õ¹¡İ¥¹‘½Ü¹±…‰•°¹¡•¥¡Ğ ¤€¨€À¸ØÈ¤°(€€€€€€€€€€€€¤(€€€€€€€€¤(€€€€€€€€ôô€‰…µ•É„ˆ(€€€€¤(€€€…ÍÍ•ÉĞ€ (€€€€€€€İ¥¹‘½Ü¹}¥¹Ñ•É…Ñ¥½¹}é½¹” (€€€€€€€€€€€EA½¥¹Ğ¡•¹Ñ•É}à°É½Õ¹¡İ¥¹‘½Ü¹±…‰•°¹¡•¥¡Ğ ¤€¨€À¸Ü¤¤(€€€€€€€€¤(€€€€€€€€ôô€‰‰½‘äˆ(€€€€¤(€€€İ¥¹‘½Ü¹±½Í” ¤(€€€İ¥¹‘½Ü¹‘•±•Ñ•1…Ñ•È ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤(()‘•˜Ñ•ÍÑ}¡•…‘}±¥­}Ñ¥±ÑÍ}ÕÉ¥½ÕÍ±å}…¹‘}™¥Ù•}‰½‘å}Á½­•Í}…¹¹½ä ¤€´ø9½¹”è(€€€€ˆˆ‹
ç–’Ó–êSš¶«–’Ó––÷––¾ò3~·š^Û¦^Ó¢ş{î·š"Ï’êSš²‡¢ê¯’öOš&7–"š6‹–"Ã¢öï–ú»RšÂSˆˆˆ((€€€…ÁÀ°İ¥¹‘½Ü€ô}É•…Ñ•}İ¥¹‘½Ü ¤(€€€¥¹¥Ñ¥…±}…™™¥¹¥Ñä€ôİ¥¹‘½Ü¹µ½½¹…™™¥¹¥Ñä(€€€¡•…€ôEA½¥¹Ğ¡İ¥¹‘½Ü¹İ¥‘Ñ  ¤€¼¼€È°€ÈÀ¤(€€€‰½‘ä€ôEA½¥¹Ğ¡İ¥¹‘½Ü¹İ¥‘Ñ  ¤€¼¼€È°É½Õ¹¡İ¥¹‘½Ü¹±…‰•°¹¡•¥¡Ğ ¤€¨€À¸Ü¤¤((€€€İ¥¹‘½Ü¹}¡…¹‘±•}±¥¬¡¡•…¤(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹µ½½¹…™™¥¹¥Ñä€ôô¥¹¥Ñ¥…±}…™™¥¹¥Ñä€¬€Ô(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹ÍÑ…Ñ”¥ÌA•ÑMÑ…Ñ”¹UI%=UL((€€€™½È|¥¸É…¹” Ğ¤è(€€€€€€€İ¥¹‘½Ü¹}¡…¹‘±•}±¥¬¡‰½‘ä¤(€€€€€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹ÍÑ…Ñ”¥ÌA•ÑMÑ…Ñ”¹M!d(€€€İ¥¹‘½Ü¹}¡…¹‘±•}±¥¬¡‰½‘ä¤(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹ÍÑ…Ñ”¥ÌA•ÑMÑ…Ñ”¹99=e(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹‘…¥±å}ÍÑ…ÑÌ¹Ñ½Õ¡•Ì€øô€Ø(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹µ½½¹…™™¥¹¥Ñä€ğ¥¹¥Ñ¥…±}…™™¥¹¥Ñä€¬€Ô(€€€İ¥¹‘½Ü¹±½Í” ¤(€€€İ¥¹‘½Ü¹‘•±•Ñ•1…Ñ•È ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤(()‘•˜Ñ•ÍÑ}Í•±™¥•}½µÁ±•Ñ¥½¹}Í¡½İÍ}Á¡½Ñ½}‰Õ‰‰±” ¤€´ø9½¹”è(€€€€ˆˆ‹šÊ‡šr'R£š"ß–:–nûš^Û’â7–ú_R£Rš"C–*£Rïšr¯–âŸ–K–¢«š.7Ÿ&ˆˆˆ((€€€…ÁÀ°İ¥¹‘½Ü€ô}É•…Ñ•}İ¥¹‘½Ü ¤(€€€İ¥¹‘½Ü¹}Í•±™¥•}Á¡½Ñ¼€ôÑåÁ”¡İ¥¹‘½Ü¹}Í•±™¥•}Á¡½Ñ¼¤ ¤(€€€İ¥¹‘½Ü¹Í•Ñ}ÍÑ…Ñ”¡A•ÑMÑ…Ñ”¹M1%¤(€€€İ¥¹‘½Ü¹}™¥¹¥Í¡}¥¹Ñ•É…Ñ¥½¸ ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤((€€€…ÍÍ•ÉĞ¹½Ğİ¥¹‘½Ü¹Á¡½Ñ½}‰Õ‰‰±”¹¥ÍY¥Í¥‰±” ¤(€€€İ¥¹‘½Ü¹Á¡½Ñ½}‰Õ‰‰±”¹¡¥‘” ¤(€€€İ¥¹‘½Ü¹±½Í” ¤(€€€İ¥¹‘½Ü¹‘•±•Ñ•1…Ñ•È ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤(()‘•˜Ñ•ÍÑ}Í•±™¥•}Á¡½Ñ½}ÕÍ•Í}¡¥¡}‘Á¥}‰…­¥¹}Á¥á•±Ì ¤€´ø9½¹”è(€€€€ˆˆˆÈÀÀ”ƒò§šRûš^Ûš¢«®[Ÿ&¦÷–êS’öÿR£¦®c–"¢ú£:–?Òƒ–æÛ¦fC–"Û¦ï¢úG–Âë–¾ãˆˆˆ((€€€…ÁÀ°İ¥¹‘½Ü€ô}É•…Ñ•}İ¥¹‘½Ü ¤(€€€İ¥¹‘½Ü¹}Í•±™¥•}Á¡½Ñ¼€ôİ¥¹‘½Ü¹}Á¥áµ…ÁÍmA•ÑMÑ…Ñ”¹M1%ul´Åt(€€€Á¡½Ñ¼€ôİ¥¹‘½Ü¹}Í…±•‘}Í•±™¥•}Á¡½Ñ¼ È¸À¤((€€€…ÍÍ•ÉĞÁ¡½Ñ¼¹‘•Ù¥•A¥á•±I…Ñ¥¼ ¤€ôô€È¸À(€€€…ÍÍ•ÉĞµ…à¡Á¡½Ñ¼¹İ¥‘Ñ  ¤°Á¡½Ñ¼¹¡•¥¡Ğ ¤¤€øô€ÌÀÀ(€€€…ÍÍ•ÉĞÉ½Õ¹¡Á¡½Ñ¼¹İ¥‘Ñ  ¤€¼Á¡½Ñ¼¹‘•Ù¥•A¥á•±I…Ñ¥¼ ¤¤€ğô€ÄÔÀ(€€€…ÍÍ•ÉĞÉ½Õ¹¡Á¡½Ñ¼¹¡•¥¡Ğ ¤€¼Á¡½Ñ¼¹‘•Ù¥•A¥á•±I…Ñ¥¼ ¤¤€ğô€ÈÄÀ(€€€İ¥¹‘½Ü¹±½Í” ¤(€€€İ¥¹‘½Ü¹‘•±•Ñ•1…Ñ•È ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤(()‘•˜Ñ•ÍÑ}Í•±™¥•}Á¡½Ñ½}¥Í}Á½Í¥Ñ¥½¹•‘}¹•…É}Ù¥Í¥‰±•}¡…É…Ñ•È ¤€´ø9½¹”è(€€€€ˆˆ‹Ÿ&–êS¢ÒÓ¢şG’êë&§’â7¦?šb;¢ö»–îO¾ò3¢3’â7šb¿¢ÒÓv–B¯–’Ÿ–v_Vgf÷jª_–>¢úçòcˆˆˆ((€€€…ÁÀ°İ¥¹‘½Ü€ô}É•…Ñ•}İ¥¹‘½Ü ¤(€€€İ¥¹‘½Ü¹}Í•±™¥•}Á¡½Ñ¼€ôİ¥¹‘½Ü¹}Á¥áµ…ÁÍmA•ÑMÑ…Ñ”¹M1%ul´Åt(€€€İ¥¹‘½Ü¹µ½Ù” ÔÀÀ°€ÌÀÀ¤(€€€İ¥¹‘½Ü¹}ÍÉ••¹}•½µ•ÑÉä€ô±…µ‰‘„èEI•Ğ À°€À°€ÄÈÀÀ°€äÀÀ¤(€€€İ¥¹‘½Ü¹Í•Ñ}ÍÑ…Ñ”¡A•ÑMÑ…Ñ”¹M1%¤(€€€İ¥¹‘½Ü¹}Í¡½İ}Á¡½Ñ½}‰Õ‰‰±” ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤((€€€¡…É…Ñ•É}±•™Ğ€ôİ¥¹‘½Ü¹à ¤€¬İ¥¹‘½Ü¹µ…Í¬ ¤¹‰½Õ¹‘¥¹I•Ğ ¤¹±•™Ğ ¤(€€€Ù¥ÍÕ…±}…À€ô¡…É…Ñ•É}±•™Ğ€´€ (€€€€€€€İ¥¹‘½Ü¹Á¡½Ñ½}‰Õ‰‰±”¹à ¤€¬İ¥¹‘½Ü¹Á¡½Ñ½}‰Õ‰‰±”¹İ¥‘Ñ  ¤(€€€€¤(€€€…ÍÍ•ÉĞÙ¥ÍÕ…±}…À€ôô€à(€€€İ¥¹‘½Ü¹Á¡½Ñ½}‰Õ‰‰±”¹¡¥‘” ¤(€€€İ¥¹‘½Ü¹±½Í” ¤(€€€İ¥¹‘½Ü¹‘•±•Ñ•1…Ñ•È ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤(()‘•˜Ñ•ÍÑ}Í½¹}¥¹ÍÁ¥É…Ñ¥½¹}ÕÍ•Í}¥¹‘•Á•¹‘•¹Ñ}Ñ¥µ•È ¤€´ø9½¹”è(€€€€ˆˆ‹–ò–B¿š¶3¢¾7šÂSšÎ‡–B;–êSšr'.³®/¢º‡š^Û–f£¾ò3’â7–7’úw¢Ö[¦j?šrë&‹¦ªk¢›–>Gˆˆˆ((€€€…ÁÀ°İ¥¹‘½Ü€ô}É•…Ñ•}İ¥¹‘½Ü ¤((€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹Í½¹}Ñ¥µ•È¹¥ÍÑ¥Ù” ¤(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹Í½¹}Ñ¥µ•È¹É•µ…¥¹¥¹Q¥µ” ¤€ø€À(€€€İ¥¹‘½Ü¹±½Í” ¤(€€€İ¥¹‘½Ü¹‘•±•Ñ•1…Ñ•È ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤(()‘•˜Ñ•ÍÑ}‰…‰Õ‘…}™…±±‰…­}¡…¹•Í}ÍåÍÑ•µ}Ù½¥•}Ñ½¹” ¤€´ø9½¹”è(€€€€ˆˆ‹šr«¦'š.§šr³–rÃ¦~Ï¦ŠGš^Û¾ò3¢ş{î·–>3–ï–>Ï¦R»’î7’òk¢:ß–ú_’â7–B3¢¾·šÂSjÎïî¢¾·¦~Ïˆˆˆ((€€€±…ÍÌMÁ••¡I•½É‘•Èè(€€€€€€€‘•˜}}¥¹¥Ñ}|¡Í•±˜¤€´ø9½¹”è(€€€€€€€€€€€Í•±˜¹É…Ñ•Ì€ômt(€€€€€€€€€€€Í•±˜¹Á¥Ñ¡•Ì€ômt(€€€€€€€€€€€Í•±˜¹İ½É‘Ì€ômt((€€€€€€€‘•˜Í•ÑI…Ñ”¡Í•±˜°Ù…±Õ”¤€´ø9½¹”è(€€€€€€€€€€€Í•±˜¹É…Ñ•Ì¹…ÁÁ•¹¡Ù…±Õ”¤((€€€€€€€‘•˜Í•ÑA¥Ñ ¡Í•±˜°Ù…±Õ”¤€´ø9½¹”è(€€€€€€€€€€€Í•±˜¹Á¥Ñ¡•Ì¹…ÁÁ•¹¡Ù…±Õ”¤((€€€€€€€‘•˜Í…ä¡Í•±˜°Ù…±Õ”¤€´ø9½¹”è(€€€€€€€€€€€Í•±˜¹İ½É‘Ì¹…ÁÁ•¹¡Ù…±Õ”¤((€€€…ÁÀ°İ¥¹‘½Ü€ô}É•…Ñ•}İ¥¹‘½Ü ¤(€€€É•½É‘•È€ôMÁ••¡I•½É‘•È ¤(€€€İ¥¹‘½Ü¹}ÍÁ••¡}•¹¥¹”€ôÉ•½É‘•È(€€€İ¥¹‘½Ü¹Í•ÑÑ¥¹Ì¹‰…‰Õ‘…}…Õ‘¥½}Á…Ñ €ô€ˆˆ((€€€İ¥¹‘½Ü¹Á±…å}‰…‰Õ‘…}Ù½¥” ¤(€€€İ¥¹‘½Ü¹Á±…å}‰…‰Õ‘…}Ù½¥” ¤((€€€…ÍÍ•ÉĞÉ•½É‘•È¹İ½É‘Ì€ôôl‹–ŞÓ–â¢úøˆ°€‹–ŞÓ–â¢úø‰t(€€€…ÍÍ•ÉĞÉ•½É‘•È¹É…Ñ•ÍlÁt€„ôÉ•½É‘•È¹É…Ñ•ÍlÅt(€€€…ÍÍ•ÉĞÉ•½É‘•È¹Á¥Ñ¡•ÍlÁt€„ôÉ•½É‘•È¹Á¥Ñ¡•ÍlÅt(€€€İ¥¹‘½Ü¹±½Í” ¤(€€€İ¥¹‘½Ü¹‘•±•Ñ•1…Ñ•È ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤(()‘•˜Ñ•ÍÑ}±½¹}ÁÉ•ÍÍ}ÁÕÑÍ}±¥±¥}Ñ½}Í±••Á}İ¥Ñ¡½ÕÑ}Á±…¥¹}±¥¬ ¤€´ø9½¹”è(€€€€ˆˆ‹¦Vÿš2'–êS–"š6‹v‡¢'–nû&–æÛ¢ºÃ–öW’âš²‡v‡¢'¾ò3’â7–7¢›–>Gšf»¦k
ç–ïˆˆˆ((€€€…ÁÀ°İ¥¹‘½Ü€ô}É•…Ñ•}İ¥¹‘½Ü ¤(€€€İ¥¹‘½Ü¹}ÁÉ•ÍÍ}Á•¹‘¥¹œ€ôQÉÕ”(€€€İ¥¹‘½Ü¹‘É…¥¹œ€ô…±Í”((€€€İ¥¹‘½Ü¹}ÑÉ¥•É}±½¹}ÁÉ•ÍÌ ¤((€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹}±½¹}ÁÉ•ÍÍ}ÑÉ¥•É•(€€€…ÍÍ•ÉĞ¹½Ğİ¥¹‘½Ü¹}ÁÉ•ÍÍ}Á•¹‘¥¹œ(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹}…µ‰¥•¹Ñ}…Ñ¥Ù¥Ñä€ôô€‰Í±••Àˆ(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹‘…¥±å}ÍÑ…ÑÌ¹Í±••ÁÌ€ôô€Ä(€€€İ¥¹‘½Ü¹±½Í” ¤(€€€İ¥¹‘½Ü¹‘•±•Ñ•1…Ñ•È ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤(()‘•˜Ñ•ÍÑ}½µÁ±•Ñ•}Á¥ÑÕÉ•}…Ñ¥½¹Í}É½ÍÍ™…‘•}İ¥Ñ¡½ÕÑ}É•Í¥é¥¹}İ¥¹‘½Ü ¤€´ø9½¹”è(€€€€ˆˆ‹–º3šVÓ–*£’ös–"š6‹–êS~·šj’ê“–>'šŞ‡–2[¾ò3îOšv–B;’şwš2–B3’âª_–>–Âë–¾ãˆˆˆ((€€€…ÁÀ°İ¥¹‘½Ü€ô}É•…Ñ•}İ¥¹‘½Ü ¤(€€€½É¥¥¹…±}Í¥é”€ôİ¥¹‘½Ü¹Í¥é” ¤((€€€İ¥¹‘½Ü¹}Í•Ñ}Ñ•µÁ½É…Éå}…Ñ¥Ù¥Ñä ‰Õ¥Ñ…Èˆ°€ÔÀÀÀ¤((€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹…Ñ¥Ù¥Ñå}ÑÉ…¹Í¥Ñ¥½¹}Ñ¥µ•È¹¥ÍÑ¥Ù” ¤(€€€…ÍÍ•ÉĞ¹½Ğİ¥¹‘½Ü¹}…Ñ¥Ù¥Ñå}ÑÉ…¹Í¥Ñ¥½¹}™É½´¹¥Í9Õ±° ¤(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹Í¥é” ¤€ôô½É¥¥¹…±}Í¥é”((€€€™½È|¥¸É…¹”¡İ¥¹‘½Ü¹}…Ñ¥Ù¥Ñå}ÑÉ…¹Í¥Ñ¥½¹}ÍÑ•ÁÌ¤è(€€€€€€€İ¥¹‘½Ü¹}…Ñ¥Ù¥Ñå}ÑÉ…¹Í¥Ñ¥½¹}Ñ¥¬ ¤((€€€…ÍÍ•ÉĞ¹½Ğİ¥¹‘½Ü¹…Ñ¥Ù¥Ñå}ÑÉ…¹Í¥Ñ¥½¹}Ñ¥µ•È¹¥ÍÑ¥Ù” ¤(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹}…Ñ¥Ù¥Ñå}ÑÉ…¹Í¥Ñ¥½¹}™É½´¹¥Í9Õ±° ¤(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹}…µ‰¥•¹Ñ}…Ñ¥Ù¥Ñä€ôô€‰Õ¥Ñ…Èˆ(€€€…ÍÍ•ÉĞİ¥¹‘½Ü¹Í¥é” ¤€ôô½É¥¥¹…±}Í¥é”(€€€İ¥¹‘½Ü¹±½Í” ¤(€€€İ¥¹‘½Ü¹‘•±•Ñ•1…Ñ•È ¤(€€€…ÁÀ¹ÁÉ½•ÍÍÙ•¹ÑÌ ¤