"""
本模块验证桌面宠物窗口的连续帧控制、表情符号、轮廓遮罩、DPI 渲染缓存、分区互动、
喂食、离线对话、陪伴动作、工作计时和自拍成片。

测试在 Qt 的离屏平台中创建真实 PetWindow，但不显示到用户桌面、不写配置文件，
也不启动系统托盘。重点检查透明区域不会形成完整矩形点击区、重复绘制能够复用缓存，
以及坐下过渡可正向停在末帧并反向回到站立帧。
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ONEPIC_USE_DEMO_ASSETS", "1")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QScrollArea

from onepic_desktop_pet.behavior import PetState, StateDecision
from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.emotion_effects import emotion_effect_name
from onepic_desktop_pet.window import PetWindow
from onepic_desktop_pet.chat import AISettingsDialog
from onepic_desktop_pet.ai import CredentialStore
from onepic_desktop_pet.work_timer import WorkTimerModel


def _create_window() -> tuple[QApplication, PetWindow]:
    """创建或复用离屏 Qt 应用，并返回采用默认设置的宠物窗口。"""

    app = QApplication.instance() or QApplication([])
    window = PetWindow(PetSettings())
    window.show()
    app.processEvents()
    return app, window


def test_pet_and_ambient_bubbles_never_accept_keyboard_focus() -> None:
    """桌宠周期置顶时不得抢走微信、Word 等当前输入窗口。"""

    app, window = _create_window()
    assert window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert window.speech_bubble.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert window.photo_bubble.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    window.close()
    window.deleteLater()
    app.processEvents()


def test_topmost_desktop_mode_switch_preserves_interaction_window(monkeypatch) -> None:
    """切换层级不得丢失位置、动画状态、轮廓穿透或无焦点标志。"""

    app, window = _create_window()
    monkeypatch.setattr("onepic_desktop_pet.window.save_settings", lambda _settings: None)
    window.move(123, 77)
    window.set_state(PetState.WALK)
    frame = window._frame_index

    window.set_always_on_top(False)
    app.processEvents()

    assert window.settings.always_on_top is False
    assert not window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert window.pos() == QPoint(123, 77)
    assert window.state is PetState.WALK
    assert window._frame_index == frame
    assert not window.mask().isEmpty()

    window.set_always_on_top(True)
    app.processEvents()

    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert window.pos() == QPoint(123, 77)
    window.close()
    window.deleteLater()
    app.processEvents()


def test_connection_and_companion_settings_scroll_and_include_music_clients() -> None:
    """小屏幕可滚动到全部陪伴选项，并能选择 Apple Music/Spotify。"""

    app = QApplication.instance() or QApplication([])
    dialog = AISettingsDialog(PetSettings(), CredentialStore())
    assert dialog.findChild(QScrollArea) is not None
    services = {
        dialog.music_service.itemData(index)
        for index in range(dialog.music_service.count())
    }
    assert {"qq", "netease", "kugou", "apple", "spotify"} <= services
    assert dialog.apple_music_path.isEnabled()
    assert dialog.spotify_music_path.isEnabled()
    assert dialog.always_on_top.isChecked()
    dialog.close()
    dialog.deleteLater()
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
    """行走起伏必须跟随连续帧，而不是由独立的慢速浮动计时器驱动。"""

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
    """水平移动应亚像素累计，落脚阶段减速而不冻结，随后平滑加速。"""

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
    """移动曲线不应停顿后猛跳，且左右两个半步必须使用相同节奏。"""

    app, window = _create_window()

    assert min(window._walk_motion_factors) > 0.0
    assert max(window._walk_motion_factors) / min(window._walk_motion_factors) < 4
    assert window._walk_motion_factors[:4] == window._walk_motion_factors[4:]
    assert sum(window._walk_motion_factors) / 8 == 1.0

    window.close()
    window.deleteLater()
    app.processEvents()


def test_drag_state_uses_dedicated_suspended_animation() -> None:
    """拖拽状态应加载三帧悬空素材，而不是回退到待机站立。"""

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
    """互动表情应使用独立符号层，换角色素材后仍然能够显示。"""

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
    """进入表情状态时符号应动画，恢复待机后必须停止计时器。"""

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
    """超过睡眠阈值后仍应先完整坐下，再播放坐姿入睡序列。"""

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
    """暂停跑动时应进入生活状态并继续计时，而不是冻结在站立帧。"""

    app, window = _create_window()
    window.set_state(PetState.WALK)
    window.behavior.next_autonomous_state = (
        lambda _current, allow_walk: StateDecision(PetState.SIT, 2000)
    )

    window.set_paused(True)

    assert window.paused
    assert window.state is PetState.SIT
    assert window.state_timer.isActive()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_display_size_preset_updates_geometry_and_settings() -> None:
    """右键尺寸预设应立即改变窗口和标签尺寸，并写回设置对象。"""

    app, window = _create_window()
    window.set_display_height(280)

    assert window.settings.display_height == 280
    assert window.height() == 294
    assert window.label.height() == 288
    assert not window.mask().isEmpty()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_default_workmate_size_is_smaller_than_previous_standard() -> None:
    """六毛工作搭子的首次启动高度应从旧版进一步缩小到 160。"""

    app, window = _create_window()

    assert window.settings.display_height == 160
    assert window.height() == 174
    window.close()
    window.deleteLater()
    app.processEvents()


def test_quick_panel_double_click_behavior_toggles_and_auto_hides() -> None:
    """快捷口袋只在主动打开时显示，再次调用会立即收起。"""

    app, window = _create_window()
    window.show_quick_panel()
    app.processEvents()
    assert window.quick_panel.isVisible()
    assert window.quick_panel.hide_timer.isActive()
    window.show_quick_panel()
    assert not window.quick_panel.isVisible()
    window.close(); window.deleteLater(); app.processEvents()


def test_feeding_updates_fullness_and_shows_speech_bubble() -> None:
    """从菜单喂苹果应更新状态，并在人物附近显示文字反馈。"""

    app, window = _create_window()
    initial_fullness = window.mood.fullness

    reply = window.feed_pet("apple")
    app.processEvents()

    assert window.mood.fullness == initial_fullness + 18
    assert reply.state is PetState.HAPPY
    assert window.state is PetState.HAPPY
    assert window.speech_bubble.isVisible()
    assert "苹果" in window.speech_bubble.text()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_dialogue_is_handled_locally_and_shows_reply() -> None:
    """输入工作话题后应直接生成本地回复，不依赖外部服务。"""

    app, window = _create_window()

    reply = window.talk_to_pet("今天工作很多")
    app.processEvents()

    assert reply.state is PetState.HAPPY
    assert "十分钟" in reply.text
    assert window.speech_bubble.text() == reply.text
    assert window.speech_bubble.isVisible()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_context_menu_exposes_dialogue_food_mood_ai_and_no_greeting() -> None:
    """菜单保留对话、饮品、有效心情信息和设置，删除打招呼入口。"""

    app, window = _create_window()
    menu = window._build_context_menu()
    actions = {action.text(): action for action in menu.actions()}

    assert "和六毛聊聊…" in actions
    assert "六毛陪伴动作" in actions
    assert "给六毛喂食/饮品" in actions
    assert "AI 与陪伴设置…" in actions
    assert actions["偶尔发牢骚"].isChecked()
    assert not actions["整点报时"].isChecked()
    food_menu = actions["给六毛喂食/饮品"].menu()
    assert food_menu is not None
    food_actions = [action.text() for action in food_menu.actions() if not action.isSeparator()]
    assert food_actions == [
        "苹果",
        "小饼干",
        "热牛奶",
        "咖啡",
        "热茶",
        "查看六毛心情与能量",
    ]
    assert not any(text.startswith("查看状态：") for text in actions)
    assert not any("打招呼" in text for text in actions)
    assert any(text.startswith("工作计时：") for text in actions)
    assert "连续调节宠物大小…" in actions
    assert "工作时长娃衣（自动换装）" in actions
    assert "46 个透明图片动作" in actions
    work_action = next(action for text, action in actions.items() if text.startswith("工作计时："))
    work_labels = [action.text() for action in work_action.menu().actions()]
    assert "查看今日 0–8 小时成长线" in work_labels
    assert "今天六毛陪你做了什么" in work_labels
    menu.close()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_companion_action_shows_loving_animation_and_words() -> None:
    """主动选择抱抱应增加亲密度，并显示爱意动作与安慰话语。"""

    app, window = _create_window()
    initial_affinity = window.mood.affinity

    reply = window.perform_companion_action("love")
    app.processEvents()

    assert reply.state is PetState.SHY
    assert window.state is PetState.SHY
    assert window.mood.affinity == initial_affinity + 5
    assert window.speech_bubble.text() == reply.text
    assert any(word in reply.text for word in ("抱抱", "贴贴"))
    window.close()
    window.deleteLater()
    app.processEvents()


def test_diverse_action_sequence_and_drinks_use_existing_frames() -> None:
    """伸展与喝茶应组合已验收状态，而不是静止地只换一句文字。"""

    app, window = _create_window()
    reply = window.perform_companion_action("stretch")
    assert reply.state is PetState.WAVE
    assert window.state is PetState.SIT
    tea = window.feed_pet("tea")
    assert "热茶" in tea.text
    assert window.state is PetState.SIT
    window.close()
    window.deleteLater()
    app.processEvents()


def test_hourly_announcement_can_be_disabled_and_deduplicates() -> None:
    """整点报时默认不触发，开启后同一小时只触发一次。"""

    app, window = _create_window()
    now = datetime(2026, 8, 10, 14, 0, 5)
    assert window._maybe_announce_hour(now) is False
    window.settings.hourly_announcement = True
    assert window._maybe_announce_hour(now) is True
    assert "14:00" in window.speech_bubble.text()
    assert window._maybe_announce_hour(now) is False
    window.close()
    window.deleteLater()
    app.processEvents()

def test_work_timer_start_status_reminder_and_finish(tmp_path) -> None:
    """工作计时应显示今日累计，并在连续工作过久时劝用户休息。"""

    now = [datetime(2026, 8, 10, 9, 0, 0)]
    monotonic = [100.0]
    timer = WorkTimerModel(
        path=tmp_path / "work_timer.json",
        now_provider=lambda: now[0],
        monotonic_provider=lambda: monotonic[0],
    )
    app = QApplication.instance() or QApplication([])
    window = PetWindow(PetSettings(), work_timer=timer)
    window.show()
    app.processEvents()

    start_reply = window.start_work_timer()
    assert start_reply.state is PetState.SIT
    assert timer.is_running
    assert window.paused

    now[0] += timedelta(minutes=50)
    monotonic[0] += 50 * 60
    window._work_timer_tick()
    app.processEvents()
    assert window.state is PetState.SLEEPY
    assert "活动" in window.speech_bubble.text()
    assert "50分钟" in timer.status_text()

    finish_reply = window.finish_work_timer()
    assert finish_reply.state is PetState.HAPPY
    assert not timer.is_running
    assert not window.paused
    assert "50分钟" in finish_reply.text
    window.close()
    window.deleteLater()
    app.processEvents()


def test_dialogue_panel_passes_text_to_local_reply() -> None:
    """新版聊天面板发送文字后应交给离线规则并显示回复。"""

    app, window = _create_window()
    window.prompt_dialogue()
    assert window._chat_dialog is not None
    settings_spy = QSignalSpy(window._chat_dialog.settings_requested)
    window._chat_dialog.input.setText("今天有点累")
    window._chat_dialog._submit()
    app.processEvents()

    assert window.state is PetState.SLEEPY
    assert "喝口水" in window.speech_bubble.text()
    assert "离线" in window._chat_dialog.status_label.text()
    assert settings_spy.count() == 0
    window.close()
    window.deleteLater()
    app.processEvents()


def test_agent_checking_never_opens_settings_and_complex_chat_shows_buttons() -> None:
    """Agent 尚在检测时聊天应立即离线回复，只显示手动按钮而不弹设置。"""

    app, window = _create_window()
    window.settings.ai_provider = "codex"
    window.prompt_dialogue()
    assert window._chat_dialog is not None
    settings_spy = QSignalSpy(window._chat_dialog.settings_requested)

    window._chat_dialog.input.setText("帮我写代码并分析这个项目")
    window._chat_dialog._submit()
    app.processEvents()

    assert settings_spy.count() == 0
    assert "离线模式" in window._chat_dialog.transcript.toPlainText()
    assert window._chat_dialog.recovery_actions.isVisible()
    assert "正在后台检测" in window._chat_dialog.status_label.text()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_interaction_zones_map_head_face_body_and_camera() -> None:
    """窗口相对位置应稳定映射为四种点击区域。"""

    app, window = _create_window()
    center_x = window.label.x() + window.label.width() // 2
    assert window._interaction_zone(QPoint(center_x, 20)) == "head"
    assert (
        window._interaction_zone(
            QPoint(center_x, round(window.label.height() * 0.34))
        )
        == "face"
    )
    assert (
        window._interaction_zone(
            QPoint(
                window.label.x() + round(window.label.width() * 0.2),
                round(window.label.height() * 0.62),
            )
        )
        == "camera"
    )
    assert (
        window._interaction_zone(
            QPoint(center_x, round(window.label.height() * 0.7))
        )
        == "body"
    )
    window.close()
    window.deleteLater()
    app.processEvents()


def test_head_click_tilts_curiously_and_five_body_pokes_annoy() -> None:
    """点头应歪头好奇，短时间连续戳五次身体才切换到轻微生气。"""

    app, window = _create_window()
    initial_affinity = window.mood.affinity
    head = QPoint(window.width() // 2, 20)
    body = QPoint(window.width() // 2, round(window.label.height() * 0.7))

    window._handle_click(head)
    assert window.mood.affinity == initial_affinity + 5
    assert window.state is PetState.CURIOUS

    for _ in range(4):
        window._handle_click(body)
        assert window.state is PetState.SHY
    window._handle_click(body)
    assert window.state is PetState.ANNOYED
    assert window.daily_stats.touches >= 6
    assert window.mood.affinity < initial_affinity + 5
    window.close()
    window.deleteLater()
    app.processEvents()


def test_selfie_completion_shows_photo_bubble() -> None:
    """没有用户原图时不得用生成动画末帧冒充自拍照片。"""

    app, window = _create_window()
    window._selfie_photo = type(window._selfie_photo)()
    window.set_state(PetState.SELFIE)
    window._finish_interaction()
    app.processEvents()

    assert not window.photo_bubble.isVisible()
    window.photo_bubble.hide()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_selfie_photo_uses_high_dpi_backing_pixels() -> None:
    """200% 缩放时横竖照片都应使用高分辨率像素并限制逻辑尺寸。"""

    app, window = _create_window()
    window._selfie_photo = window._pixmaps[PetState.SELFIE][-1]
    photo = window._scaled_selfie_photo(2.0)

    assert photo.devicePixelRatio() == 2.0
    assert max(photo.width(), photo.height()) >= 300
    assert round(photo.width() / photo.devicePixelRatio()) <= 150
    assert round(photo.height() / photo.devicePixelRatio()) <= 210
    window.close()
    window.deleteLater()
    app.processEvents()


def test_selfie_photo_is_positioned_near_visible_character() -> None:
    """照片应贴近人物不透明轮廓，而不是贴着含大块留白的窗口边缘。"""

    app, window = _create_window()
    window._selfie_photo = window._pixmaps[PetState.SELFIE][-1]
    window.move(500, 300)
    window._screen_geometry = lambda: QRect(0, 0, 1200, 900)
    window.set_state(PetState.SELFIE)
    window._show_photo_bubble()
    app.processEvents()

    character_left = window.x() + window.mask().boundingRect().left()
    visual_gap = character_left - (
        window.photo_bubble.x() + window.photo_bubble.width()
    )
    assert visual_gap == 8
    window.photo_bubble.hide()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_song_inspiration_uses_independent_timer() -> None:
    """开启歌词气泡后应有独立计时器，不再依赖随机牢骚触发。"""

    app, window = _create_window()

    assert window.song_timer.isActive()
    assert window.song_timer.remainingTime() > 0
    window.close()
    window.deleteLater()
    app.processEvents()


def test_babuda_fallback_changes_system_voice_tone() -> None:
    """未选择本地音频时，连续双击右键仍会获得不同语气的系统语音。"""

    class SpeechRecorder:
        def __init__(self) -> None:
            self.rates = []
            self.pitches = []
            self.words = []

        def setRate(self, value) -> None:
            self.rates.append(value)

        def setPitch(self, value) -> None:
            self.pitches.append(value)

        def say(self, value) -> None:
            self.words.append(value)

    app, window = _create_window()
    recorder = SpeechRecorder()
    window._speech_engine = recorder
    window.settings.babuda_audio_path = ""

    window.play_babuda_voice()
    window.play_babuda_voice()

    assert recorder.words == ["巴布达", "巴布达"]
    assert recorder.rates[0] != recorder.rates[1]
    assert recorder.pitches[0] != recorder.pitches[1]
    window.close()
    window.deleteLater()
    app.processEvents()


def test_long_press_puts_lili_to_sleep_without_plain_click() -> None:
    """长按应切换睡觉图片并记录一次睡觉，不再触发普通点击。"""

    app, window = _create_window()
    window._press_pending = True
    window.dragging = False

    window._trigger_long_press()

    assert window._long_press_triggered
    assert not window._press_pending
    assert window._ambient_activity == "sleep"
    assert window.daily_stats.sleeps == 1
    window.close()
    window.deleteLater()
    app.processEvents()


def test_complete_picture_actions_crossfade_without_resizing_window() -> None:
    """完整动作切换应短暂交叉淡化，结束后保持同一窗口尺寸。"""

    app, window = _create_window()
    original_size = window.size()

    window._set_temporary_activity("guitar", 5000)

    assert window.activity_transition_timer.isActive()
    assert not window._activity_transition_from.isNull()
    assert window.size() == original_size

    for _ in range(window._activity_transition_steps):
        window._activity_transition_tick()

    assert not window.activity_transition_timer.isActive()
    assert window._activity_transition_from.isNull()
    assert window._ambient_activity == "guitar"
    assert window.size() == original_size
    window.close()
    window.deleteLater()
    app.processEvents()
