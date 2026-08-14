"""
本模块实现桌面宠物的透明窗口、连续动画、鼠标交互、快捷控制和情境陪伴。

职责范围：
- 创建无边框、透明、可选始终置顶的 QWidget；
- 使用 Windows/macOS 原生窗口层级补强置顶，同时保持不激活、不占任务栏和轮廓外点击穿透；
- 提供“始终置顶/桌面模式”即时切换并持久化，切换时不破坏动画、拖动和互动状态；
- 播放循环或单次 PNG 序列，并支持拖拽、坐下、坐姿入睡和反向起身；
- 处理左右翻转、边缘转身停顿、亚像素时间驱动移动和同步身体起伏；
- 用窗口遮罩让人物外透明区域穿透鼠标点击；
- 缓存不同 DPI 下的缩放帧，并在窗口跨显示器后按新比例重新栅格化；
- 支持左键拖动、单击调戏、双击快捷口袋、无互动分级休息和连续尺寸滑块；
- 支持给六毛喂食或饮品，并用独立半透明文字气泡反馈状态；
- 支持 Agent 状态缓存、异步 AI、无缝离线降级以及工作、爱意、鼓励和安慰动作；
- 在内存中保留最近三十轮完整聊天，并把更早内容滚动压缩为长期摘要；
- 将连接与陪伴设置收口到唯一入口，只有显式 ``user_action`` 来源才允许创建设置窗口；
- 自动评分并依次尝试本机音乐 Provider，成功后把基础控制锁定到实际播放的平台；
- 支持电脑图层、摸头工作气泡、今日/终身计时、每小时娃衣解锁及健康提醒；
- 根据前台应用粗粒度类别显示电脑、耳机、吉他、鼓、阅读或写字图层；
- 支持头部摸动、脸部/身体/相机分区点击、连续戳击、悬停注视和拖拽后表情；
- 通过与角色素材解耦的矢量图层增强开心、害羞、惊讶、生气、困倦、疑惑、自拍和拖拽反馈；
- 优先从用户私有素材目录显示自拍成片气泡，按当前屏幕 DPI 保持清晰度，并贴近人物真实轮廓定位；
- 标准角色确认后加载本地宠物供现场验收；走路确认仍作为打包门禁；
- 维护亲密度、精力、无聊度与饱食度的会话内状态；
- 使用 QTimer 驱动状态切换及水平移动，并限制窗口不脱离当前屏幕。

Agent 快速定位：
- 窗口初始化和计时器设置位于 PetWindow.__init__()；
- 状态显示入口位于 set_state()，高 DPI 重绘位于 _refresh_pixmap()；
- 自动移动位于 _movement_tick()；
- 鼠标事件位于 mousePressEvent() 等 Qt 事件方法；
- 退出由 quit_requested 信号交给应用生命周期模块处理。

输入为 PetSettings、素材清单和可选的用户自拍照片资源，输出为可交互的 Qt 窗口。
本模块启动后只在后台低频检测 Agent；每条聊天不重复完整检测，普通动画、牢骚和报时均不访问网络。
API 令牌由系统凭据库管理，聊天文本不落盘；位置持久化由 app.py 在退出时完成。
`user_assets/` 默认不进入 Git；只有用户主动放入的自拍图片才会在本机显示。
"""

from __future__ import annotations

import json
import logging
import os
import random
import sys
import time
from collections import OrderedDict, deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QContextMenuEvent,
    QDesktopServices,
    QFont,
    QHideEvent,
    QMouseEvent,
    QMoveEvent,
    QPainter,
    QPixmap,
    QRegion,
    QScreen,
    QShowEvent,
    QTransform,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QInputDialog,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtTextToSpeech import QTextToSpeech
except ImportError:
    QTextToSpeech = None

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
except ImportError:
    QAudioOutput = QMediaPlayer = None

from .ai import AIChatService, CredentialStore, PROVIDER_PRESETS
from .accessories import OUTFITS, draw_activity_overlay, unlocked_outfits
from .activity import active_application_category
from .behavior import (
    BehaviorModel,
    CompanionBehaviorController,
    PetMood,
    PetState,
    StateDecision,
)
from .chat import AISettingsDialog, ChatDialog
from .chat_manager import (
    AgentManager,
    ChatManager,
    ManagedChatReply,
    OfflineDialogueManager,
    should_start_startup_detection,
)
from .chat_memory import ConversationMemory, conversation_memory_path
from .companion import (
    ACTION_BY_KEY,
    APP_DISPLAY_NAME,
    COMPANION_ACTIONS,
    FOOD_OPTIONS,
    CompanionModel,
    CompanionReply,
)
from .config import PET_NAME, PetSettings, clean_owner_nickname, save_settings, social_pet_label
from .controls import QuickControlPanel, SizeControlDialog, WorkControlBubble
from .input_activity import system_idle_seconds
from .emotion_effects import draw_emotion_effect, emotion_effect_name
from .daily_report import render_daily_report
from .diary import DailyCompanionStats, album_directory
from .focus_analytics import FocusAnalyticsStore, FocusQualityTracker
from .focus_session import FocusSessionManager
from .growth import (
    ACTION_GROUPS,
    ACTION_SPRITES,
    COMPLETE_ACTIONS,
    FOCUS_ACTIONS,
    RANDOM_ACTIONS,
    REST_ACTIONS,
    growth_progress_text,
    positive_mood,
    stage_for_seconds,
    time_of_day_activity,
)
from .local_content import find_audio_variants, load_local_lines
from .liumao_worldview import family_music_mode
from .resources import resource_path
from .quiet_mode import detect_quiet_mode
from .social import SocialClient
from .social_ui import BuddyVisitWindow, SocialEventThread, SocialHubDialog, SocialProfileThread, SocialSyncThread
from .music_control import MusicControlResult, MusicController, MusicProviderManager
from .music_playback import SongPlaybackResult
from .wellness import WellnessReminderModel
from .work_timer import WorkTimerModel, format_work_duration
from .workflow import WorkflowError, character_is_approved, load_workflow


LOGGER = logging.getLogger(__name__)
SETTINGS_SOURCE_USER_ACTION = "user_action"


DEFAULT_WALK_MOTION_FACTORS = (0.45, 0.7, 1.2, 1.65, 0.45, 0.7, 1.2, 1.65)


class IdleRecoveryDialog(QDialog):
    """Show one reusable, non-modal decision window for an idle episode.

    A modal ``QMessageBox.exec()`` used to be created every time the system
    idle counter dipped below the threshold.  The dialog now lives for the
    whole pet window and emits one decision, so a single absence cannot spawn
    a stack of windows or block the desktop event loop.
    """

    decision_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("回来啦")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMinimumWidth(390)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowStaysOnTopHint
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("font-size: 15px;")
        layout.addWidget(self.summary_label)
        self.detail_label = QLabel(
            "这里只根据键盘/鼠标的系统输入计时；电脑后台运行、播放音乐或下载文件不算键鼠操作。"
        )
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet("color: #667784; font-size: 12px;")
        layout.addWidget(self.detail_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.rest_button = QPushButton("算作休息")
        self.rest_button.setAutoDefault(False)
        self.rest_button.clicked.connect(lambda: self._request_decision("rest"))
        buttons.addWidget(self.rest_button)
        self.focus_button = QPushButton("计入专注")
        self.focus_button.setAutoDefault(False)
        self.focus_button.clicked.connect(lambda: self._request_decision("focus"))
        buttons.addWidget(self.focus_button)
        layout.addLayout(buttons)

    def set_away_seconds(self, seconds: int) -> None:
        """Update the elapsed absence shown by the reusable dialog."""

        seconds = max(1, int(seconds))
        minutes, remainder = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            duration = f"{hours} 小时 {minutes} 分钟"
        elif minutes:
            duration = f"{minutes} 分 {remainder:02d} 秒"
        else:
            duration = f"{remainder} 秒"
        self.summary_label.setText(
            f"检测到你刚刚离开了约 {duration}。\n这段时间要记作休息，还是计入专注？"
        )

    def _request_decision(self, decision: str) -> None:
        """Hide first, then notify the owner so the window cannot duplicate."""

        self.hide()
        self.decision_requested.emit(decision)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Closing the window dismisses this episode without re-prompting."""

        event.ignore()
        self.hide()
        self.decision_requested.emit("dismiss")


class PetWindow(QWidget):
    """显示并控制单个桌面宠物的透明顶层窗口。"""

    quit_requested = Signal()
    pause_changed = Signal(bool)
    work_timer_changed = Signal(bool)
    always_on_top_changed = Signal(bool)
    pet_name_changed = Signal(str)
    owner_nickname_changed = Signal(str)

    def _pet_name(self) -> str:
        """Return the immutable character identity used by every local UI."""

        return PET_NAME

    def _owner_nickname(self) -> str:
        return clean_owner_nickname(getattr(self.settings, "owner_nickname", ""))

    def _walk_allowed(self) -> bool:
        """返回当前是否允许六毛自主横向跑动。"""

        return bool(getattr(self.settings, "allow_autonomous_walk", False)) and not self.paused

    def __init__(
        self,
        settings: PetSettings,
        work_timer: WorkTimerModel | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.behavior = BehaviorModel(settings)
        self.companion_behavior = CompanionBehaviorController()
        self.mood = PetMood()
        self.companion = CompanionModel(self.mood)
        self.work_timer = work_timer or WorkTimerModel()
        self.focus_session = FocusSessionManager(self.work_timer, self)
        self._rewarded_focus_blocks = self.work_timer.today_seconds() // 600
        self.daily_stats = DailyCompanionStats(
            persist=os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1"
        )
        self.focus_analytics = FocusAnalyticsStore(
            persist=os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1"
        )
        self._focus_quality_tracker = FocusQualityTracker()
        self._last_focus_quality = None
        self.wellness = WellnessReminderModel()
        self.state = PetState.IDLE
        self.direction = -1
        self._movement_x = float(self.x())
        self._last_movement_at = time.monotonic()
        self.paused = False
        self.dragging = False
        self._press_pending = False
        self._press_local = QPoint()
        self._press_global = QPoint()
        self._drag_offset = QPoint()
        self._hover_zone = ""
        self._stroke_points: deque[tuple[float, QPoint]] = deque()
        self._last_stroke_reaction = 0.0
        self._poke_times: deque[float] = deque()
        self._bob_phase = False
        self._effect_phase = 0
        self._frame_index = 0
        self._animation_direction = 1
        self._animation_finished: Callable[[], None] | None = None
        self._turn_paused = False
        self._last_user_interaction = time.monotonic()
        self._auto_paused_for_idle = False
        self._idle_pause_started_at: datetime | None = None
        self._pending_idle_seconds = 0
        self._idle_prompt_pending = False
        self._idle_recovery_resolved = False
        self._idle_above_threshold_samples = 0
        self._idle_recovery_dialog: IdleRecoveryDialog | None = None
        self._sleep_after_sit = False
        self._room_quick_status = ""
        self._room_quick_status_expires_at: datetime | None = None
        self._screen_change_connected = False
        self._connected_screen: QScreen | None = None
        self._pixmaps = self._load_pixmaps()
        self._selfie_photo = self._load_selfie_photo()
        self._render_cache: OrderedDict[tuple[object, ...], QPixmap] = OrderedDict()
        self._mask_cache: OrderedDict[tuple[object, ...], QRegion] = OrderedDict()
        self.credentials = CredentialStore()
        self.ai_service = AIChatService(self.credentials)
        self.social_client = SocialClient(
            persist_tokens=os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1"
        )
        self._social_dialog: SocialHubDialog | None = None
        self._social_thread: SocialSyncThread | None = None
        self._social_event_threads: list[SocialEventThread] = []
        self._social_profile_threads: list[SocialProfileThread] = []
        self._buddy_visit_window = BuddyVisitWindow()
        self._seen_visit_ids: set[str] = set()
        self._shown_active_visit_ids: set[str] = set()
        self._chat_dialog: ChatDialog | None = None
        self._chat_memory = ConversationMemory(
            max_recent_rounds=30,
            persist_path=conversation_memory_path()
            if os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1"
            else None,
        )
        self.agent_manager = AgentManager(self.settings, self.credentials, self)
        self.offline_dialogue_manager = OfflineDialogueManager(
            self.companion,
            self.work_timer.status_text,
            lambda: self.work_timer.today_seconds() // 3600,
        )
        self.chat_manager = ChatManager(
            self.settings,
            self.ai_service,
            self.agent_manager,
            self.offline_dialogue_manager,
            self,
        )
        self.music_provider_manager = MusicProviderManager(self.settings)
        self.music_controller = MusicController(
            self.settings,
            self.music_provider_manager,
            self,
        )
        self.agent_manager.status_changed.connect(self._agent_status_changed)
        self.chat_manager.reply_ready.connect(self._managed_chat_reply)
        self.chat_manager.busy_changed.connect(self._chat_busy_changed)
        self.chat_manager.notice.connect(self._chat_notice)
        self.music_controller.result_ready.connect(self._music_control_result)
        self.focus_session.changed.connect(self._focus_snapshot_changed)
        self._action_sequence_id = 0
        self._last_announced_hour = ""
        self._ambient_activity = "none"
        self._activity_transition_from = QPixmap()
        self._activity_transition_step = 0
        self._activity_transition_steps = 8
        self._manual_activity_until = 0.0
        self._last_app_category = "other"
        self._late_wakeup_shown = False
        self._last_growth_hour = stage_for_seconds(self.work_timer.today_seconds()).hour
        self._long_press_triggered = False
        self._speech_engine = QTextToSpeech(self) if QTextToSpeech is not None else None
        self._babuda_variant_index = 0
        self._pending_context_global = QPoint()
        self._suppress_context_until = 0.0
        self._audio_output = QAudioOutput(self) if QAudioOutput is not None else None
        self._media_player = QMediaPlayer(self) if QMediaPlayer is not None else None
        if self._audio_output is not None and self._media_player is not None:
            self._audio_output.setVolume(0.9)
            self._media_player.setAudioOutput(self._audio_output)

        self.setWindowFlags(self._pet_window_flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle(f"{APP_DISPLAY_NAME} · {self._pet_name()}")
        self.setMouseTracking(True)

        source = self._pixmaps[PetState.IDLE][0]
        width = round(settings.display_height * source.width() / source.height())
        self.setFixedSize(width + 12, settings.display_height + 14)
        self.label = QLabel(self)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setGeometry(6, 0, width, settings.display_height + 8)

        self.photo_bubble = QLabel()
        self.photo_bubble.setWindowFlags(self._ambient_window_flags())
        self.photo_bubble.setAttribute(
            Qt.WidgetAttribute.WA_ShowWithoutActivating,
            True,
        )
        self.photo_bubble.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_bubble.setStyleSheet("background: transparent;")

        self.speech_bubble = QLabel()
        self.speech_bubble.setWindowFlags(self._ambient_window_flags())
        self.speech_bubble.setAttribute(
            Qt.WidgetAttribute.WA_ShowWithoutActivating,
            True,
        )
        self.speech_bubble.setWordWrap(True)
        bubble_font = QFont()
        bubble_font.setFamilies(
            ["PingFang SC", "Microsoft YaHei UI", "Noto Sans CJK SC", "Arial"]
        )
        bubble_font.setPointSize(10)
        self.speech_bubble.setFont(bubble_font)
        self.speech_bubble.setMinimumWidth(180)
        self.speech_bubble.setMaximumWidth(280)
        self.speech_bubble.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.speech_bubble.setStyleSheet(
            "QLabel { background: rgba(239, 245, 248, 175); "
            "color: #27313d; border: 1px solid rgba(75, 96, 112, 120); border-radius: 15px; "
            "padding: 10px 13px; font-size: 14px; }"
        )

        self.work_controls = WorkControlBubble()
        self.work_controls.pause_requested.connect(self.pause_work_timer)
        self.work_controls.finish_requested.connect(self.finish_work_timer)
        self.quick_panel = QuickControlPanel(self._pet_name())
        self.quick_panel.chat_requested.connect(self.prompt_dialogue)
        self.quick_panel.work_requested.connect(self._quick_work_action)
        self.quick_panel.music_control_requested.connect(self.control_music)
        self.quick_panel.music_requested.connect(self.play_random_song)
        self.quick_panel.size_requested.connect(self.open_size_control)
        self.quick_panel.settings_requested.connect(self.open_settings)

        self.movement_timer = QTimer(self)
        self.movement_timer.setInterval(settings.movement_interval_ms)
        self.movement_timer.timeout.connect(self._movement_tick)
        self.movement_timer.start()

        self.state_timer = QTimer(self)
        self.state_timer.setSingleShot(True)
        self.state_timer.timeout.connect(self._state_timeout)

        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(self._animation_tick)

        self.turn_timer = QTimer(self)
        self.turn_timer.setSingleShot(True)
        self.turn_timer.timeout.connect(self._finish_turn)

        self.interaction_timer = QTimer(self)
        self.interaction_timer.setSingleShot(True)
        self.interaction_timer.timeout.connect(self._finish_interaction)

        self.long_press_timer = QTimer(self)
        self.long_press_timer.setSingleShot(True)
        self.long_press_timer.timeou…25486 tokens truncated…t() - self.photo_bubble.height()) // 2)
        self.photo_bubble.move(x, y)
        self.photo_bubble.show()
        self.photo_timer.start(3800)

    def _scaled_selfie_photo(self, ratio: float) -> QPixmap:
        """按设备像素比生成照片缩略图，避免高 DPI 屏幕二次放大导致模糊。"""

        if self._selfie_photo.isNull():
            return QPixmap()
        ratio = max(1.0, ratio)
        photo = self._selfie_photo.scaled(
            max(1, round(150 * ratio)),
            max(1, round(210 * ratio)),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        photo.setDevicePixelRatio(ratio)
        return photo

    def _finish_interaction(self) -> None:
        """结束互动并恢复自主待机。"""

        if not self.dragging:
            if self.state is PetState.SELFIE:
                self._show_photo_bubble()
            self._schedule(self.behavior.initial_idle())

    def _toggle_walk_from_menu(self) -> None:
        """让右键菜单同时覆盖首次开启和日常暂停两种跑动操作。"""

        if not getattr(self.settings, "allow_autonomous_walk", False):
            self.set_allow_autonomous_walk(True)
            return
        self.set_paused(not self.paused)

    def _build_context_menu(self) -> QMenu:
        """构建高频入口直达、低频选项收纳到二级菜单的右键菜单。"""

        menu = QMenu(self)
        rename_action = QAction("修改主人称呼…", self)
        rename_action.triggered.connect(self.rename_pet)
        menu.addAction(rename_action)
        dialogue_action = QAction(f"和{self._pet_name()}聊聊…", self)
        dialogue_action.triggered.connect(self.prompt_dialogue)
        menu.addAction(dialogue_action)
        work_menu = menu.addMenu("工作打卡/工作计时")
        start_work = QAction("开始工作计时" if not self.work_timer.is_running else "工作计时进行中", self)
        start_work.setEnabled(not self.work_timer.is_running)
        start_work.triggered.connect(self.start_work_timer)
        work_menu.addAction(start_work)
        if self.work_timer.is_running:
            pause_work = QAction("暂停/结束工作", self)
            pause_work.triggered.connect(self.show_work_controls)
            work_menu.addAction(pause_work)
        for label, callback in (("查看今日累计", self.show_work_time), ("查看今日成长", self.show_daily_growth), ("查看陪伴报告", self.show_daily_report), (f"打开{self._pet_name()}相册", self.open_daily_album)):
            action = QAction(label, self)
            action.triggered.connect(callback)
            work_menu.addAction(action)
        music_menu = menu.addMenu("音乐")
        random_song = QAction("随机听一首陈楚生", self)
        random_song.triggered.connect(self.play_random_song)
        music_menu.addAction(random_song)
        for label, command in (("播放/暂停", "toggle"), ("下一首", "next"), ("上一首", "previous")):
            action = QAction(label, self)
            action.triggered.connect(lambda _checked=False, value=command: self.control_music(value))
            music_menu.addAction(action)
        music_settings = QAction("音乐播放器设置", self)
        music_settings.triggered.connect(lambda: self.open_settings(SETTINGS_SOURCE_USER_ACTION))
        music_menu.addAction(music_settings)
        study_action = QAction("搭子自习室…", self)
        study_action.triggered.connect(self.open_social_hub)
        menu.addAction(study_action)
        action_menu = menu.addMenu("动作")
        for group_name, entries in ACTION_GROUPS:
            group_menu = action_menu.addMenu(group_name)
            for label, key in entries:
                action = QAction(label, self)
                action.triggered.connect(lambda _checked=False, value=key: self.set_activity(value))
                group_menu.addAction(action)
        selfie = QAction("自拍", self)
        selfie.triggered.connect(self.trigger_selfie)
        action_menu.addAction(selfie)
        pause_label = (
            "开启自动跑动"
            if not getattr(self.settings, "allow_autonomous_walk", False)
            else ("恢复跑动" if self.paused else "暂停跑动")
        )
        pause = QAction(pause_label, self)
        pause.setCheckable(True)
        pause.setChecked(bool(getattr(self.settings, "allow_autonomous_walk", False)) and not self.paused)
        pause.triggered.connect(self._toggle_walk_from_menu)
        action_menu.addAction(pause)
        food_menu = menu.addMenu(f"给{self._pet_name()}喂食")
        for food in FOOD_OPTIONS:
            action = QAction(food.label, self)
            action.triggered.connect(lambda _checked=False, key=food.key: self.feed_pet(key))
            food_menu.addAction(action)
        food_menu.addSeparator()
        mood = QAction("查看心情与能量", self)
        mood.triggered.connect(self.show_companion_status)
        food_menu.addAction(mood)
        outfit_menu = menu.addMenu("换装与外观")
        classic = QAction(f"经典{self._pet_name()}", self)
        classic.setCheckable(True)
        classic.setChecked(not self.settings.equipped_outfit)
        classic.triggered.connect(lambda: self.equip_outfit(""))
        outfit_menu.addAction(classic)
        unlocked = unlocked_outfits(self.work_timer.unlocked_outfit_count())
        for outfit in OUTFITS:
            action = QAction(outfit.name, self)
            available = outfit in unlocked
            action.setEnabled(available)
            action.setCheckable(True)
            action.setChecked(outfit.key == self.settings.equipped_outfit)
            if available:
                action.triggered.connect(lambda _checked=False, key=outfit.key: self.equip_outfit(key))
            outfit_menu.addAction(action)
        menu.addSeparator()
        for label, callback, checked in (("偶尔发牢骚", self.set_automatic_grumbling, self.settings.automatic_grumbling), ("整点报时", self.set_hourly_announcement, self.settings.hourly_announcement), ("始终置顶", self.set_always_on_top, self.settings.always_on_top)):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(checked)
            action.toggled.connect(callback)
            menu.addAction(action)
        system_menu = menu.addMenu("系统与设置")
        ai_action = QAction("AI 与陪伴设置", self)
        ai_action.triggered.connect(lambda: self.open_settings(SETTINGS_SOURCE_USER_ACTION))
        system_menu.addAction(ai_action)
        size_action = QAction("调整桌宠大小", self)
        size_action.triggered.connect(self.open_size_control)
        system_menu.addAction(size_action)
        return_action = QAction("回到主屏幕", self)
        return_action.triggered.connect(self.return_to_primary_screen)
        system_menu.addAction(return_action)
        hide_action = QAction("隐藏", self)
        hide_action.triggered.connect(self.hide)
        menu.addAction(hide_action)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit_requested.emit)
        menu.addAction(quit_action)
        return menu

    def _build_context_menu_legacy(self) -> QMenu:
        """按五个稳定分组构建右键菜单，避免功能平铺和入口层级混乱。"""

        menu = QMenu(self)
        chat_group = menu.addMenu("聊天与陪伴")
        action_group = menu.addMenu("动作与外观")
        music_group = menu.addMenu("音乐与娱乐")
        focus_group = menu.addMenu("专注与自习")
        system_group = menu.addMenu("系统与显示")

        dialogue_action = QAction(f"和{self._pet_name()}聊聊…", self)
        dialogue_action.triggered.connect(self.prompt_dialogue)
        chat_group.addAction(dialogue_action)
        rename_action = QAction("修改主人称呼…", self)
        rename_action.triggered.connect(self.rename_pet)
        chat_group.addAction(rename_action)
        social_action = QAction(f"{self._pet_name()}搭子自习室…", self)
        social_action.triggered.connect(self.open_social_hub)
        chat_group.addAction(social_action)
        action_menu = chat_group.addMenu("陪伴动作")
        for option in COMPANION_ACTIONS:
            action = QAction(option.label, self)
            action.triggered.connect(
                lambda _checked=False, key=option.key: self.perform_companion_action(
                    key
                )
            )
            action_menu.addAction(action)
        food_menu = chat_group.addMenu("喂食、饮品与状态")
        for food in FOOD_OPTIONS:
            food_action = QAction(food.label, self)
            food_action.triggered.connect(
                lambda _checked=False, key=food.key: self.feed_pet(key)
            )
            food_menu.addAction(food_action)
        food_menu.addSeparator()
        mood_action = QAction(f"查看{self._pet_name()}心情与能量", self)
        mood_action.triggered.connect(self.show_companion_status)
        food_menu.addAction(mood_action)

        pause_action = QAction(
            "开启自动跑动"
            if not getattr(self.settings, "allow_autonomous_walk", False)
            else ("恢复跑动" if self.paused else "暂停跑动"),
            self,
        )
        pause_action.triggered.connect(self._toggle_walk_from_menu)
        action_group.addAction(pause_action)
        picture_actions = action_group.addMenu("完整图片动作")
        for group_name, entries in ACTION_GROUPS:
            group_menu = picture_actions.addMenu(group_name)
            for label, key in entries:
                action = QAction(label, self)
                action.triggered.connect(lambda _checked=False, value=key: self.set_activity(value))
                group_menu.addAction(action)
        selfie_action = QAction("自拍一下", self)
        selfie_action.triggered.connect(self.trigger_selfie)
        action_group.addAction(selfie_action)
        outfit_menu = action_group.addMenu("工作时长娃衣")
        classic = QAction(f"经典{self._pet_name()}", self)
        classic.setCheckable(True)
        classic.setChecked(not self.settings.equipped_outfit)
        classic.triggered.connect(lambda: self.equip_outfit(""))
        outfit_menu.addAction(classic)
        unlocked = unlocked_outfits(self.work_timer.unlocked_outfit_count())
        for hour, outfit in enumerate(OUTFITS, start=1):
            available = outfit in unlocked
            label = (
                f"{hour} 小时 · {outfit.name}"
                if available
                else f"🔒 {hour} 小时 · {outfit.name}"
            )
            action = QAction(label, self)
            action.setCheckable(available)
            action.setChecked(outfit.key == self.settings.equipped_outfit)
            action.setEnabled(available)
            if available:
                action.triggered.connect(
                    lambda _checked=False, key=outfit.key: self.equip_outfit(key)
                )
            outfit_menu.addAction(action)

        music_control_menu = music_group.addMenu("控制正在运行的播放器")
        for label, command in (
            ("播放 / 暂停", "toggle"),
            ("上一首", "previous"),
            ("下一首", "next"),
            ("查看正在播放", "status"),
        ):
            control_action = QAction(label, self)
            control_action.triggered.connect(
                lambda _checked=False, value=command: self.control_music(value)
            )
            music_control_menu.addAction(control_action)
        music_search_action = QAction("搜索一首陈楚生", self)
        music_search_action.triggered.connect(self.play_random_song)
        music_group.addAction(music_search_action)
        music_move = music_group.addMenu("音乐动作")
        for label, key in (("戴耳机", "headphones"), ("弹吉他", "guitar"), ("打鼓", "drums")):
            action = QAction(label, self)
            action.triggered.connect(lambda _checked=False, value=key: self.set_activity(value))
            music_move.addAction(action)

        work_menu = focus_group.addMenu(
            f"工作计时：{format_work_duration(self.work_timer.today_seconds())}"
        )
        start_work_action = QAction("开始/继续工作", self)
        start_work_action.setEnabled(not self.work_timer.is_running)
        start_work_action.triggered.connect(self.start_work_timer)
        work_menu.addAction(start_work_action)
        if self.work_timer.is_running:
            controls_action = QAction("摸头或点电脑进行暂停/结束", self)
            controls_action.triggered.connect(self.show_work_controls)
            work_menu.addAction(controls_action)
        show_work_action = QAction("查看今日累计", self)
        show_work_action.triggered.connect(self.show_work_time)
        work_menu.addAction(show_work_action)
        growth_action = QAction("查看今日 0–8 小时成长线", self)
        growth_action.triggered.connect(self.show_daily_growth)
        work_menu.addAction(growth_action)
        report_action = QAction(f"今天{self._pet_name()}陪你做了什么", self)
        report_action.triggered.connect(self.show_daily_report)
        work_menu.addAction(report_action)
        album_action = QAction(f"打开{self._pet_name()}相册", self)
        album_action.triggered.connect(self.open_daily_album)
        work_menu.addAction(album_action)
        focus_social = QAction("打开搭子自习室…", self)
        focus_social.triggered.connect(self.open_social_hub)
        focus_group.addAction(focus_social)

        ai_action = QAction("AI 与陪伴设置…", self)
        ai_action.triggered.connect(
            lambda _checked=False: self.open_settings(SETTINGS_SOURCE_USER_ACTION)
        )
        system_group.addAction(ai_action)
        grumble_action = QAction("偶尔发牢骚", self)
        grumble_action.setCheckable(True)
        grumble_action.setChecked(self.settings.automatic_grumbling)
        grumble_action.toggled.connect(self.set_automatic_grumbling)
        system_group.addAction(grumble_action)
        hourly_action = QAction("整点报时", self)
        hourly_action.setCheckable(True)
        hourly_action.setChecked(self.settings.hourly_announcement)
        hourly_action.toggled.connect(self.set_hourly_announcement)
        system_group.addAction(hourly_action)
        topmost_action = QAction("始终置顶（关闭即桌面模式）", self)
        topmost_action.setCheckable(True)
        topmost_action.setChecked(self.settings.always_on_top)
        topmost_action.toggled.connect(self.set_always_on_top)
        system_group.addAction(topmost_action)
        size_action = QAction("连续调节宠物大小…", self)
        size_action.triggered.connect(self.open_size_control)
        system_group.addAction(size_action)
        system_group.addSeparator()
        return_action = QAction("回到主屏幕", self)
        return_action.triggered.connect(self.return_to_primary_screen)
        system_group.addAction(return_action)
        hide_action = QAction("隐藏", self)
        hide_action.triggered.connect(self.hide)
        system_group.addAction(hide_action)
        system_group.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit_requested.emit)
        system_group.addAction(quit_action)
        return menu

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """稍候显示单次右键菜单，为双击右键的巴布达语音留出判定时间。"""

        self._record_user_interaction()
        if time.monotonic() < self._suppress_context_until:
            event.accept()
            return
        self._pending_context_global = event.globalPos()
        self.context_menu_timer.start(QApplication.doubleClickInterval() + 60)
        event.accept()

    def _show_deferred_context_menu(self) -> None:
        """确认不是双击后，在原鼠标位置打开普通右键菜单。"""

        if time.monotonic() >= self._suppress_context_until:
            self._build_context_menu().exec(self._pending_context_global)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """记录左键按下；只有移动超过系统阈值后才真正进入拖拽。"""

        if event.button() == Qt.MouseButton.LeftButton:
            self._record_user_interaction()
            self._press_pending = True
            self._long_press_triggered = False
            self.long_press_timer.start(850)
            self.dragging = False
            self.state_timer.stop()
            self.interaction_timer.stop()
            self.hover_timer.stop()
            self._press_local = event.position().toPoint()
            self._press_global = event.globalPosition().toPoint()
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """拖动期间根据全局鼠标位置移动并限制窗口。"""

        if event.buttons() & Qt.MouseButton.LeftButton:
            current_global = event.globalPosition().toPoint()
            if (
                self._press_pending
                and (current_global - self._press_global).manhattanLength()
                >= QApplication.startDragDistance()
            ):
                self.long_press_timer.stop()
                self._press_pending = False
                self.dragging = True
                self.mood.receive_drag()
                self.set_state(PetState.DRAG)
            if not self.dragging:
                event.accept()
                return
            target = event.globalPosition().toPoint() - self._drag_offset
            self.move(self._constrained_position(target))
            event.accept()
            return
        self._track_passive_motion(event.position().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """左键释放时结束拖动并恢复待机。"""

        if event.button() == Qt.MouseButton.LeftButton:
            self.long_press_timer.stop()
            if self.dragging:
                self.dragging = False
                self._press_pending = False
                self._show_emotion(PetState.SURPRISED, 1100)
            elif self._long_press_triggered:
                self._long_press_triggered = False
            elif self._press_pending:
                self._press_pending = False
                self._handle_click(self._press_local)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        """鼠标离开宠物时取消尚未触发的悬停和摸头轨迹。"""

        self._hover_zone = ""
        self._stroke_points.clear()
        self.hover_timer.stop()
        self.long_press_timer.stop()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """双击左键打开快捷口袋；双击右键播放一声不同语气的巴布达。"""

        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self._press_pending = False
            self._record_user_interaction()
            self.show_quick_panel()
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.context_menu_timer.stop()
            self._suppress_context_until = time.monotonic() + 0.8
            self._record_user_interaction()
            self.play_babuda_voice()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
