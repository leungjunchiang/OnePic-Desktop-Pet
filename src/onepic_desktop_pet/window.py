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
from .time_memory import TimeMemory
from .today_note import TimeMemoryWindow, TodayNoteWindow
from .compact_todo import CompactTodoPanel


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
        self.time_memory = TimeMemory(
            persist=os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1"
        )
        self._today_note_window: TodayNoteWindow | None = None
        self._compact_todo_panel: CompactTodoPanel | None = None
        self._restore_compact_todos_after_show = False
        self._time_memory_window: TimeMemoryWindow | None = None
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
        self._owner_nickname_sync_key: tuple[str, str] | None = None
        self._owner_nickname_sync_inflight = False
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
            local_context=self.time_memory.summary.context,
            lyrics_path=lambda: self.settings.local_lyrics_path,
        )
        self.chat_manager = ChatManager(
            self.settings,
            self.ai_service,
            self.agent_manager,
            self.offline_dialogue_manager,
            self,
            local_context_provider=self.time_memory.summary.context,
            action_executor=self.time_memory.actions,
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
        self.long_press_timer.timeout.connect(self._trigger_long_press)

        self.hover_timer = QTimer(self)
        self.hover_timer.setSingleShot(True)
        self.hover_timer.timeout.connect(self._trigger_hover_curiosity)

        self.photo_timer = QTimer(self)
        self.photo_timer.setSingleShot(True)
        self.photo_timer.timeout.connect(self.photo_bubble.hide)

        self.speech_timer = QTimer(self)
        self.speech_timer.setSingleShot(True)
        self.speech_timer.timeout.connect(self.speech_bubble.hide)

        self.effect_timer = QTimer(self)
        self.effect_timer.setInterval(90)
        self.effect_timer.timeout.connect(self._effect_tick)

        self.bob_timer = QTimer(self)
        self.bob_timer.setInterval(280)
        self.bob_timer.timeout.connect(self._bob_tick)
        self.bob_timer.start()

        self.work_clock_timer = QTimer(self)
        self.work_clock_timer.setInterval(1000)
        self.work_clock_timer.timeout.connect(self._work_timer_tick)
        self.work_clock_timer.start()

        self.ambient_timer = QTimer(self)
        self.ambient_timer.setSingleShot(True)
        self.ambient_timer.timeout.connect(self._ambient_tick)
        self._schedule_ambient()

        self.song_timer = QTimer(self)
        self.song_timer.setSingleShot(True)
        self.song_timer.timeout.connect(self._song_inspiration_tick)
        self._schedule_song_inspiration()

        self.context_menu_timer = QTimer(self)
        self.context_menu_timer.setSingleShot(True)
        self.context_menu_timer.timeout.connect(self._show_deferred_context_menu)

        self.activity_timer = QTimer(self)
        self.activity_timer.setSingleShot(True)
        self.activity_timer.timeout.connect(self._activity_timeout)

        self.activity_transition_timer = QTimer(self)
        self.activity_transition_timer.setInterval(35)
        self.activity_transition_timer.timeout.connect(self._activity_transition_tick)

        self.work_activity_timer = QTimer(self)
        self.work_activity_timer.setSingleShot(True)
        self.work_activity_timer.timeout.connect(self._work_activity_tick)

        self.hourly_timer = QTimer(self)
        self.hourly_timer.setInterval(15000)
        self.hourly_timer.timeout.connect(self._hourly_tick)
        self.hourly_timer.start()

        self.app_timer = QTimer(self)
        self.app_timer.setInterval(5000)
        self.app_timer.timeout.connect(self._app_awareness_tick)
        self.app_timer.start()

        self.topmost_timer = QTimer(self)
        self.topmost_timer.setInterval(4000)
        self.topmost_timer.timeout.connect(self._ensure_on_top)
        self.topmost_timer.start()

        self._last_social_heartbeat_at = 0.0
        self._social_heartbeat_due = True
        self.social_timer = QTimer(self)
        # Refresh visible room state every 30 seconds, but write presence only
        # every 90 seconds or immediately after an explicit state transition.
        self.social_timer.setInterval(30_000)
        self.social_timer.timeout.connect(self._social_tick)
        self.social_timer.start()
        self.social_sync_timer = QTimer(self)
        self.social_sync_timer.setSingleShot(True)
        self.social_sync_timer.timeout.connect(self._social_tick)
        if self.social_client.signed_in:
            QTimer.singleShot(2500, self._social_tick)

        # The focus timer follows real keyboard/mouse activity instead of
        # continuing forever while the user has stepped away.  This timer
        # only reads the OS-level idle duration; it never records input data.
        self.input_idle_timer = QTimer(self)
        self.input_idle_timer.setInterval(5_000)
        self.input_idle_timer.timeout.connect(self._check_input_idle)
        self.input_idle_timer.start()
        self.idle_recovery_timer = QTimer(self)
        self.idle_recovery_timer.setSingleShot(True)
        self.idle_recovery_timer.timeout.connect(self._ask_idle_recovery)

        self._sync_hourly_outfit(announce=False)
        self.set_state(PetState.IDLE)
        self._schedule(self.behavior.initial_idle())
        if should_start_startup_detection():
            QTimer.singleShot(0, self.agent_manager.start_background_check)

    def _pet_window_flags(self) -> Qt.WindowType:
        """返回不占任务栏、不接收键盘焦点的宠物窗口标志。"""

        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if self.settings.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        return flags

    def _ambient_window_flags(self) -> Qt.WindowType:
        """让自动气泡跟随宠物模式，并保证显示时不激活当前应用。"""

        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if self.settings.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        return flags

    def _load_pixmaps(self) -> dict[PetState, list[QPixmap]]:
        """根据素材清单加载各状态帧序列并验证完整性。"""

        manifest_path = resource_path("assets/pet/manifest.json")
        if os.environ.get("ONEPIC_USE_DEMO_ASSETS") == "1":
            return self._load_manifest_pixmaps(manifest_path)
        try:
            custom_manifest = resource_path("user_assets/pet/manifest.json")
        except FileNotFoundError:
            pass
        else:
            if not character_is_approved(load_workflow()):
                raise WorkflowError(
                    "检测到私有宠物素材，但标准人物尚未确认；拒绝静默回退到演示角色。"
                )
            manifest_path = custom_manifest
        return self._load_manifest_pixmaps(manifest_path)

    def _load_manifest_pixmaps(
        self,
        manifest_path: Path,
    ) -> dict[PetState, list[QPixmap]]:
        """从指定清单加载帧；测试可借此固定使用公开演示素材。"""

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        animations: dict[str, list[str]] = manifest["animations"]
        motion_factors = manifest.get(
            "walk_motion_factors",
            DEFAULT_WALK_MOTION_FACTORS,
        )
        if len(motion_factors) != len(animations["walk"]):
            raise ValueError("走路位移曲线必须与走路动画帧数一致")
        self._walk_motion_factors = tuple(float(value) for value in motion_factors)
        mapping = {
            PetState.IDLE: animations["idle"],
            PetState.WALK: animations["walk"],
            PetState.SIT: animations["sit"],
            PetState.SLEEP: animations["sleep"],
            PetState.WAVE: animations["wave"],
            PetState.HAPPY: animations["happy"],
            PetState.SHY: animations["shy"],
            PetState.SURPRISED: animations["surprised"],
            PetState.ANNOYED: animations["annoyed"],
            PetState.SLEEPY: animations["sleepy"],
            PetState.CURIOUS: animations["curious"],
            PetState.SELFIE: animations["selfie"],
            PetState.DRAG: animations["drag"],
        }
        pixmaps: dict[PetState, list[QPixmap]] = {}
        for state, relative_paths in mapping.items():
            state_frames = []
            for relative in relative_paths:
                path = manifest_path.parent / relative
                if not path.is_file():
                    raise FileNotFoundError(f"缺少宠物素材：{path}")
                pixmap = QPixmap(str(path))
                if pixmap.isNull():
                    raise ValueError(f"无法加载宠物素材：{path}")
                state_frames.append(pixmap)
            if not state_frames:
                raise ValueError(f"状态 {state.value} 没有可用素材帧")
            pixmaps[state] = state_frames
        return pixmaps

    def _load_selfie_photo(self) -> QPixmap:
        """只加载用户提供的原始自拍照片，不用生成帧冒充原图。"""

        for relative in (
            "user_assets/selfie.png",
            "user_assets/selfie.jpg",
            "user_assets/selfie.jpeg",
            "user_assets/image.png",
        ):
            try:
                path = resource_path(relative)
            except FileNotFoundError:
                continue
            photo = QPixmap(str(path))
            if not photo.isNull():
                return photo
        return QPixmap()

    def place_at_start(self) -> None:
        """按已保存位置或主屏幕右下角放置窗口。"""

        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        if self.settings.start_x is None or self.settings.start_y is None:
            x = area.right() - self.width() - 24
            y = area.bottom() - self.height() - 12
        else:
            x = self.settings.start_x
            y = self.settings.start_y
        self.move(self._constrained_position(QPoint(x, y)))

    def set_state(self, state: PetState) -> None:
        """切换行为状态、重置帧序号并刷新当前图片。"""

        self.state = state
        self._frame_index = 0
        self._animation_direction = 1
        self._animation_finished = None
        self._turn_paused = False
        self._movement_x = float(self.x())
        self._last_movement_at = time.monotonic()
        self.turn_timer.stop()
        display_state = state
        frame_count = len(self._pixmaps[display_state])
        if frame_count > 1:
            self.animation_timer.start(self._frame_interval(display_state, 0))
        else:
            self.animation_timer.stop()
        if display_state is PetState.WALK:
            self._apply_frame_offset(display_state)
        else:
            self.label.move(6, 0)
        self._effect_phase = 0
        if emotion_effect_name(display_state) is None:
            self.effect_timer.stop()
        else:
            self.effect_timer.start()
        self._refresh_pixmap()

    def _frame_interval(self, state: PetState, frame_index: int) -> int:
        """返回指定动画帧的停留时间，使眨眼、过渡与行走节奏彼此独立。"""

        if state is PetState.IDLE:
            durations = (820, 360, 100, 120, 140, 720)
            return durations[frame_index % len(durations)]
        if state is PetState.WALK:
            return self.settings.walk_frame_interval_ms
        if state is PetState.SIT:
            return 160
        if state is PetState.SLEEP:
            return 180
        if state is PetState.DRAG:
            return 180
        return 380

    @staticmethod
    def _remember_cache_item(cache, key, value) -> None:
        """写入小型最近使用缓存，并限制长期运行时的内存占用。"""

        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > 96:
            cache.popitem(last=False)

    def _current_source(self) -> tuple[PetState, QPixmap]:
        """返回当前显示状态及方向处理后的原始帧。"""

        display_state = self.state
        frames = self._pixmaps[display_state]
        pixmap = frames[min(self._frame_index, len(frames) - 1)]
        if self.direction < 0 and display_state is PetState.WALK:
            pixmap = pixmap.transformed(QTransform().scale(-1, 1))
        return display_state, pixmap

    def _refresh_pixmap(self) -> None:
        """从缓存取得或按当前屏幕设备像素比栅格化当前动画帧。"""

        display_state, pixmap = self._current_source()
        ratio = max(1.0, self.devicePixelRatioF())
        direction_key = self.direction if display_state is PetState.WALK else 0
        cache_key = (
            display_state,
            self._frame_index,
            direction_key,
            round(ratio, 3),
            self.label.width(),
            self.label.height(),
        )
        scaled = self._render_cache.get(cache_key)
        if scaled is None:
            target = QSize(
                max(1, round(self.label.width() * ratio)),
                max(1, round(self.label.height() * ratio)),
            )
            scaled = pixmap.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(ratio)
            self._remember_cache_item(self._render_cache, cache_key, scaled)
        composed = draw_emotion_effect(
            scaled,
            display_state,
            self._effect_phase,
        )
        activity = self._ambient_activity
        if self.work_timer.is_running and activity in {"", "none"}:
            activity = "computer"
        composed = draw_activity_overlay(
            composed,
            activity,
            self.settings.equipped_outfit,
            self._effect_phase,
        )
        visible = self._blend_activity_transition(composed)
        self.label.setPixmap(visible)
        effect_key = self._effect_phase if emotion_effect_name(display_state) else -1
        overlay_key = hash((activity, self.settings.equipped_outfit, self._effect_phase % 2))
        self._refresh_window_mask(display_state, visible, direction_key, effect_key ^ overlay_key)

    def _blend_activity_transition(self, target: QPixmap) -> QPixmap:
        """把上一个完整动作与目标动作短暂交叉淡化，避免静态图硬切。"""

        previous = self._activity_transition_from
        if previous.isNull() or self._activity_transition_step >= self._activity_transition_steps:
            return target
        if previous.size() != target.size():
            previous = previous.scaled(
                target.size(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            previous.setDevicePixelRatio(target.devicePixelRatio())
        progress = self._activity_transition_step / self._activity_transition_steps
        result = QPixmap(target.size())
        result.fill(Qt.GlobalColor.transparent)
        result.setDevicePixelRatio(target.devicePixelRatio())
        painter = QPainter(result)
        painter.setOpacity(1.0 - progress)
        painter.drawPixmap(0, 0, previous)
        painter.setOpacity(progress)
        painter.drawPixmap(0, 0, target)
        painter.end()
        return result

    def _activity_transition_tick(self) -> None:
        """推进约 280 毫秒的动作交叉淡化；原有逐帧走路动画不经过这里。"""

        self._activity_transition_step += 1
        if self._activity_transition_step >= self._activity_transition_steps:
            self.activity_transition_timer.stop()
            self._activity_transition_from = QPixmap()
            self._mask_cache.clear()
        self._refresh_pixmap()

    def _change_ambient_activity(self, activity: str) -> None:
        """统一切换完整动作，并从当前实际画面平滑过渡到目标图。"""

        next_activity = activity if activity in ACTION_SPRITES else "none"
        if next_activity == self._ambient_activity:
            self._refresh_pixmap()
            return
        current = self.label.pixmap() if hasattr(self, "label") else QPixmap()
        self._activity_transition_from = QPixmap(current) if not current.isNull() else QPixmap()
        self._activity_transition_step = 0
        self._ambient_activity = next_activity
        self._mask_cache.clear()
        if not self._activity_transition_from.isNull():
            self.activity_transition_timer.start()
        self._refresh_pixmap()

    def _refresh_window_mask(
        self,
        display_state: PetState,
        pixmap: QPixmap,
        direction_key: int,
        effect_key: int,
    ) -> None:
        """按当前人物轮廓设置窗口遮罩，使透明留白不拦截桌面点击。"""

        cache_key = (
            display_state,
            self._frame_index,
            direction_key,
            effect_key,
            self.label.width(),
            self.label.height(),
        )
        region = self._mask_cache.get(cache_key)
        if region is None:
            logical = pixmap.scaled(
                self.label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            offset_x = (self.label.width() - logical.width()) // 2
            offset_y = (self.label.height() - logical.height()) // 2
            region = QRegion(logical.mask()).translated(offset_x, offset_y)
            self._remember_cache_item(self._mask_cache, cache_key, region)
        self.setMask(region.translated(self.label.x(), self.label.y()))

    def _effect_tick(self) -> None:
        """推进表情符号的轻微漂浮动画并刷新合成帧。"""

        if emotion_effect_name(self.state) is None:
            self.effect_timer.stop()
            return
        self._effect_phase = (self._effect_phase + 1) % 12
        self._refresh_pixmap()

    def _animation_tick(self) -> None:
        """推进循环或单次连续帧，并在反向过渡结束后执行回调。"""

        display_state = self.state
        frames = self._pixmaps[display_state]
        if len(frames) <= 1:
            return
        if self._animation_direction < 0:
            self._frame_index = max(0, self._frame_index - 1)
            if self._frame_index == 0:
                self.animation_timer.stop()
                callback = self._animation_finished
                self._animation_finished = None
                if callback is not None:
                    QTimer.singleShot(0, callback)
        elif display_state in (PetState.SIT, PetState.SLEEP, PetState.SELFIE):
            self._frame_index = min(self._frame_index + 1, len(frames) - 1)
            if self._frame_index == len(frames) - 1:
                self.animation_timer.stop()
        else:
            self._frame_index = (self._frame_index + 1) % len(frames)
        self._apply_frame_offset(display_state)
        self._refresh_pixmap()
        if self.animation_timer.isActive():
            self.animation_timer.setInterval(
                self._frame_interval(display_state, self._frame_index)
            )

    def _apply_frame_offset(self, display_state: PetState) -> None:
        """按跑步落脚、压缩和腾空阶段同步水平回弹与身体起伏。"""

        if display_state is PetState.WALK:
            x_offsets = (6, 6, 6, 6, 6, 6, 6, 6)
            y_offsets = (3, 5, 2, 0, 3, 5, 2, 0)
            phase = self._frame_index % len(y_offsets)
            self.label.move(x_offsets[phase], y_offsets[phase])

    def _movement_speed_pixels_per_second(self) -> float:
        """按旧配置的平均速度计算恒定水平速度。"""

        return (
            self.settings.movement_step
            * 1000.0
            / self.settings.movement_interval_ms
        )

    def showEvent(self, event: QShowEvent) -> None:
        """窗口首次显示时连接跨屏信号并按当前 DPI 绘制。"""

        super().showEvent(event)
        handle = self.windowHandle()
        if handle is not None and not self._screen_change_connected:
            handle.screenChanged.connect(self._on_screen_changed)
            self._screen_change_connected = True
        self._on_screen_changed(handle.screen() if handle else None)
        if self._compact_todo_panel is not None:
            if self._restore_compact_todos_after_show:
                self._compact_todo_panel.show()
            if self._compact_todo_panel.isVisible():
                self._position_compact_todos()
        QTimer.singleShot(0, self._ensure_on_top)

    def _ensure_on_top(self) -> None:
        """恢复原生窗口层级，但绝不激活窗口或夺走当前输入焦点。"""

        if not self.isVisible():
            return
        if os.name == "nt":
            try:
                import ctypes

                user32 = ctypes.windll.user32
                hwnd = int(self.winId())
                get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
                set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
                extended = int(get_style(hwnd, -20))
                extended |= 0x00000080 | 0x08000000  # WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
                set_style(hwnd, -20, extended)
                insert_after = -1 if self.settings.always_on_top else -2
                user32.SetWindowPos(
                    hwnd,
                    insert_after,
                    0,
                    0,
                    0,
                    0,
                    0x0001 | 0x0002 | 0x0010 | 0x0040,
                )
                return
            except (AttributeError, OSError, ValueError):
                pass
        if sys.platform == "darwin":
            self._apply_macos_window_behavior()
        # 其他平台由 WindowStaysOnTopHint 负责。这里不能调用 raise_()，
        # 否则 macOS/部分 Linux 桌面会在用户打字时切换当前应用。

    def _apply_macos_window_behavior(self) -> None:
        """以 NSWindow 浮动层级跨空间显示；保留人物窗口鼠标互动且不激活。"""

        if QApplication.platformName().casefold() in {"offscreen", "minimal"}:
            return
        try:
            import ctypes

            objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
            objc.sel_registerName.restype = ctypes.c_void_p
            objc.sel_registerName.argtypes = [ctypes.c_char_p]
            objc.objc_msgSend.restype = ctypes.c_void_p
            objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            view = ctypes.c_void_p(int(self.winId()))
            window = objc.objc_msgSend(view, objc.sel_registerName(b"window"))
            if not window:
                return
            send_integer = objc.objc_msgSend
            send_integer.restype = None
            send_integer.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_longlong]
            level = 3 if self.settings.always_on_top else 0  # NSFloatingWindowLevel / normal
            send_integer(window, objc.sel_registerName(b"setLevel:"), level)
            behavior = (1 << 0) | (1 << 8) if self.settings.always_on_top else 0
            send_integer(
                window,
                objc.sel_registerName(b"setCollectionBehavior:"),
                behavior,
            )
            send_bool = objc.objc_msgSend
            send_bool.restype = None
            send_bool.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
            send_bool(window, objc.sel_registerName(b"setHidesOnDeactivate:"), False)
            send_bool(window, objc.sel_registerName(b"setIgnoresMouseEvents:"), False)
        except (AttributeError, OSError, TypeError, ValueError):
            return

    def set_always_on_top(self, enabled: bool, *, persist: bool = True) -> None:
        """在 QQ 宠物式置顶与普通桌面模式间切换，显示时不抢焦点。"""

        enabled = bool(enabled)
        position = self.pos()
        was_visible = self.isVisible()
        bubble_states = (
            (self.photo_bubble, self.photo_bubble.isVisible(), self.photo_bubble.pos()),
            (self.speech_bubble, self.speech_bubble.isVisible(), self.speech_bubble.pos()),
        )
        self.settings.always_on_top = enabled
        self.setWindowFlags(self._pet_window_flags())
        self.move(position)
        for bubble, visible, bubble_position in bubble_states:
            bubble.setWindowFlags(self._ambient_window_flags())
            bubble.move(bubble_position)
            if visible:
                bubble.show()
        if self._compact_todo_panel is not None:
            self._compact_todo_panel.set_companion_topmost(enabled)
            if self._compact_todo_panel.isVisible():
                self._position_compact_todos()
        if was_visible:
            self.show()
            target_position = QPoint(position)

            def restore_position_after_show() -> None:
                # macOS may round a frameless window by one pixel while recreating
                # its native handle. Restore the user-visible position afterwards.
                self.move(target_position)
                self._ensure_on_top()

            QTimer.singleShot(0, restore_position_after_show)
        if persist:
            save_settings(self.settings)
            self.show_speech(
                "始终置顶已开启，六毛会继续待在其他窗口上方。"
                if enabled
                else "已切换为桌面模式，六毛会留在普通窗口层级。",
                3600,
            )
        self.always_on_top_changed.emit(enabled)

    def moveEvent(self, event: QMoveEvent) -> None:
        """人物移动时让仍在显示的文字气泡跟随可见轮廓。"""

        super().moveEvent(event)
        if hasattr(self, "speech_bubble") and self.speech_bubble.isVisible():
            self._position_speech_bubble()
        if self._compact_todo_panel is not None and self._compact_todo_panel.isVisible():
            self._position_compact_todos()

    def hideEvent(self, event: QHideEvent) -> None:
        """隐藏宠物时同步隐藏照片和文字气泡。"""

        self.photo_bubble.hide()
        self.speech_bubble.hide()
        self.work_controls.hide()
        self.quick_panel.hide()
        if self._compact_todo_panel is not None:
            self._restore_compact_todos_after_show = self._compact_todo_panel.isVisible()
            self._compact_todo_panel.hide()
        super().hideEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭宠物时保存计时并停止 Agent、音乐控制及独立气泡窗口。"""

        self.shutdown_work_timer()
        self.chat_manager.shutdown()
        self.music_controller.shutdown()
        self.photo_bubble.close()
        self.speech_bubble.close()
        self._buddy_visit_window.close()
        if self._today_note_window is not None:
            self._today_note_window.close()
        if self._compact_todo_panel is not None:
            self._compact_todo_panel.close()
        if self._time_memory_window is not None:
            self._time_memory_window.close()
        if self._social_thread is not None and self._social_thread.isRunning():
            self._social_thread.wait(2500)
        if self._media_player is not None:
            self._media_player.stop()
        super().closeEvent(event)

    def _on_screen_changed(self, screen: QScreen | None) -> None:
        """切换目标屏幕后重连 DPI 信号并延迟刷新素材。"""

        if self._connected_screen is not None:
            try:
                self._connected_screen.logicalDotsPerInchChanged.disconnect(
                    self._on_dpi_changed
                )
            except (RuntimeError, TypeError):
                pass
        self._connected_screen = screen
        if screen is not None:
            screen.logicalDotsPerInchChanged.connect(self._on_dpi_changed)
        self._render_cache.clear()
        QTimer.singleShot(0, self._refresh_pixmap)
        if self._compact_todo_panel is not None and self._compact_todo_panel.isVisible():
            QTimer.singleShot(0, self._position_compact_todos)

    def _on_dpi_changed(self, _dpi: float) -> None:
        """显示器缩放发生变化时刷新当前帧。"""

        self._render_cache.clear()
        QTimer.singleShot(0, self._refresh_pixmap)
        if self._compact_todo_panel is not None and self._compact_todo_panel.isVisible():
            QTimer.singleShot(0, self._position_compact_todos)

    def _schedule(self, decision: StateDecision) -> None:
        """应用状态决策并安排下一次状态切换。"""

        self.set_state(decision.state)
        if not self.dragging:
            self.state_timer.start(decision.duration_ms)

    def _state_timeout(self) -> None:
        """处理自主状态到期，并按无互动时长逐级进入坐下与睡眠。"""

        if self.dragging:
            return
        self.mood.pass_time(self.state)
        inactive_ms = self._inactive_ms()
        if self._sleep_after_sit and self.state is PetState.SIT:
            self._sleep_after_sit = False
            self._schedule(self._decision(PetState.SLEEP, 8000))
            return
        if inactive_ms >= self.settings.inactive_sleep_ms:
            if self.state is PetState.SLEEP:
                self.state_timer.start(10000)
                return
            if self.state is PetState.SIT:
                self._schedule(self._decision(PetState.SLEEP, 10000))
                return
            self._schedule_sleep_via_sit()
            return
        if inactive_ms >= self.settings.inactive_sit_ms:
            if self.state is PetState.SIT:
                remaining = self.settings.inactive_sleep_ms - inactive_ms
                self.state_timer.start(max(500, min(5000, remaining)))
                return
            self._schedule(
                self._decision(
                    PetState.SIT,
                    min(5000, self.settings.inactive_sleep_ms - inactive_ms),
                )
            )
            return
        if self.state in (PetState.SIT, PetState.SLEEP):
            self._reverse_transition_to_idle()
            return
        decision = self.behavior.next_autonomous_state(
            self.state,
            allow_walk=self._walk_allowed(),
        )
        if decision.state is PetState.SLEEP:
            self._schedule_sleep_via_sit()
        else:
            self._schedule(decision)

    @staticmethod
    def _decision(state: PetState, duration_ms: int) -> StateDecision:
        """创建窗口内部过渡使用的确定时长状态决策。"""

        return StateDecision(state, max(500, duration_ms))

    def _inactive_ms(self) -> int:
        """返回距离最近一次鼠标或菜单互动的毫秒数。"""

        return max(0, round((time.monotonic() - self._last_user_interaction) * 1000))

    def _record_user_interaction(self) -> None:
        """重置无互动计时，并取消尚未开始的自动入睡意图。"""

        self._last_user_interaction = time.monotonic()
        self._sleep_after_sit = False

    def _reset_idle_episode(self) -> None:
        """Forget the current idle episode and close its reusable prompt."""

        self._auto_paused_for_idle = False
        self._idle_prompt_pending = False
        self._idle_recovery_resolved = False
        self._idle_above_threshold_samples = 0
        self._pending_idle_seconds = 0
        self._idle_pause_started_at = None
        if hasattr(self, "idle_recovery_timer"):
            self.idle_recovery_timer.stop()
        if self._idle_recovery_dialog is not None:
            self._idle_recovery_dialog.hide()

    def _check_input_idle(self) -> None:
        """Automatically pause focus after a sustained keyboard/mouse idle.

        A pause is deliberately not auto-resumed: a user may have switched to
        reading, a meeting, or another task.  They can resume explicitly from
        the pet bubble or the study-room page, while the room receives the
        normal presence update on the next social heartbeat.
        """

        if not getattr(self.settings, "auto_pause_on_idle", True):
            self._idle_above_threshold_samples = 0
            return
        threshold = max(30, int(getattr(self.settings, "idle_pause_seconds", 300)))
        idle_seconds = max(0.0, float(system_idle_seconds()))
        if self._auto_paused_for_idle:
            # `idle_seconds` is only the OS counter at this sample.  The
            # previous implementation copied the threshold crossing (usually
            # 300 seconds) into `_pending_idle_seconds` and never advanced it,
            # so a user who was away for 40 minutes was still asked about
            # "5 minutes".  Keep the episode start time as the source of truth
            # and refresh the displayed duration on every 5-second sample.
            if self._idle_pause_started_at is not None:
                elapsed_seconds = max(
                    0,
                    int(
                        (
                            datetime.now().astimezone()
                            - self._idle_pause_started_at
                        ).total_seconds()
                    ),
                )
                self._pending_idle_seconds = max(
                    self._pending_idle_seconds,
                    elapsed_seconds,
                )
            # While the OS still reports idle, retain the larger of the native
            # reading and the wall-clock episode duration.  This also handles
            # platforms whose idle counter has a wraparound or coarse sample.
            if idle_seconds >= threshold:
                self._pending_idle_seconds = max(
                    self._pending_idle_seconds,
                    int(idle_seconds),
                )
            # The OS idle counter drops as soon as the user returns.  Delay
            # the question to the event loop so the first input is not
            # blocked by a modal dialog.  Once this episode is resolved, do
            # not ask again until the user explicitly starts a new session.
            if (
                not self._idle_recovery_resolved
                and idle_seconds < max(1, threshold - 1)
                and not self._idle_prompt_pending
            ):
                self._idle_prompt_pending = True
                self.idle_recovery_timer.start(0)
            return
        if not self.work_timer.is_running:
            self._idle_above_threshold_samples = 0
            return
        if idle_seconds < threshold:
            self._idle_above_threshold_samples = 0
            return
        # Require two consecutive OS samples.  This filters a single bad
        # native reading without adding another setting or recording input.
        self._idle_above_threshold_samples += 1
        if self._idle_above_threshold_samples < 2:
            return
        self._idle_above_threshold_samples = 0
        self._auto_paused_for_idle = True
        self._idle_recovery_resolved = False
        self._idle_pause_started_at = datetime.now().astimezone() - timedelta(seconds=int(idle_seconds))
        self._pending_idle_seconds = max(1, int(idle_seconds))
        self._focus_quality_tracker.note_away()
        self.pause_work_timer(reason="idle")

    def _ask_idle_recovery(self) -> None:
        """Show one reusable decision window for the current absence."""

        self._idle_prompt_pending = False
        if not self._auto_paused_for_idle or self._idle_recovery_resolved:
            return
        seconds = max(1, int(self._pending_idle_seconds))
        if self._idle_recovery_dialog is None:
            self._idle_recovery_dialog = IdleRecoveryDialog(self)
            self._idle_recovery_dialog.decision_requested.connect(
                self._resolve_idle_recovery
            )
        self._idle_recovery_dialog.set_away_seconds(seconds)
        self._idle_recovery_dialog.show()
        self._idle_recovery_dialog.raise_()
        self._idle_recovery_dialog.activateWindow()

    def _resolve_idle_recovery(self, decision: str) -> None:
        """Resolve the episode once; later idle samples cannot reopen it."""

        if not self._auto_paused_for_idle or self._idle_recovery_resolved:
            return
        seconds = max(1, int(self._pending_idle_seconds))
        if decision == "focus":
            # The timer remains paused until the user explicitly starts the
            # next round, but the choice is reflected in local statistics.
            self.focus_analytics.record_session(
                seconds,
                started_at=self._idle_pause_started_at or datetime.now().astimezone(),
                completed=False,
                away_count=1,
                task=str((self.focus_analytics.current_task() or {}).get("title", "")),
            )
            self.show_speech("好，这段离开时间已计入专注。需要继续时点开始专注。", 5200)
        elif decision == "rest":
            minutes = max(1, round(seconds / 60))
            self.show_speech(f"已把约 {minutes} 分钟记为休息，回来后再开一轮吧。", 4800)
        # Dismissing the window is intentionally silent, but still resolves
        # this episode so it cannot create another popup every few seconds.
        self._idle_recovery_resolved = True
        self._pending_idle_seconds = 0
        self._idle_pause_started_at = None

    def _schedule_sleep_via_sit(self) -> None:
        """先完整坐下，再从坐姿播放入睡序列。"""

        self._sleep_after_sit = True
        self._schedule(self._decision(PetState.SIT, 1400))

    def _reverse_transition_to_idle(self) -> None:
        """倒放坐下或睡眠序列，完成自然起身后再进入待机。"""

        frames = self._pixmaps[self.state]
        self._frame_index = len(frames) - 1
        self._animation_direction = -1
        self._animation_finished = self._finish_reverse_transition
        self._refresh_pixmap()
        self.animation_timer.start(self._frame_interval(self.state, self._frame_index))

    def _finish_reverse_transition(self) -> None:
        """睡醒后先回到坐姿，再倒放坐下序列恢复站立待机。"""

        if self.dragging:
            return
        if self.state is PetState.SLEEP:
            self.state = PetState.SIT
            self._frame_index = len(self._pixmaps[PetState.SIT]) - 1
            self._animation_direction = -1
            self._animation_finished = self._finish_reverse_transition
            self._refresh_pixmap()
            self.animation_timer.start(
                self._frame_interval(PetState.SIT, self._frame_index)
            )
            return
        self._schedule(self.behavior.initial_idle())

    def _screen_geometry(self):
        """返回窗口中心所在屏幕的可用区域。"""

        center = self.frameGeometry().center()
        screen = QApplication.screenAt(center) or QApplication.primaryScreen()
        return screen.availableGeometry() if screen else None

    def _constrained_position(self, position: QPoint) -> QPoint:
        """将目标位置限制在当前或主屏幕可用区域内。"""

        screen = QApplication.screenAt(position) or QApplication.primaryScreen()
        if screen is None:
            return position
        area = screen.availableGeometry()
        x = min(max(position.x(), area.left()), area.right() - self.width() + 1)
        y = min(max(position.y(), area.top()), area.bottom() - self.height() + 1)
        return QPoint(x, y)

    def _movement_tick(self) -> None:
        """按实际经过时间亚像素累计移动，并在屏幕边缘转向。"""

        now = time.monotonic()
        elapsed = min(0.1, max(0.0, now - self._last_movement_at))
        self._last_movement_at = now
        if (
            self.paused
            or self.dragging
            or self._turn_paused
            or self.state is not PetState.WALK
        ):
            self._movement_x = float(self.x())
            return
        area = self._screen_geometry()
        if area is None:
            return
        if abs(self._movement_x - self.x()) > 1.5:
            self._movement_x = float(self.x())
        maximum = area.right() - self.width() + 1
        direction = 1 if self.direction >= 0 else -1
        phase_factor = self._walk_motion_factors[
            self._frame_index % len(self._walk_motion_factors)
        ]
        self._movement_x += direction * (
            self._movement_speed_pixels_per_second()
            * phase_factor
            * elapsed
        )
        if self._movement_x <= area.left():
            self._movement_x = float(area.left())
            direction = 1
        elif self._movement_x >= maximum:
            self._movement_x = float(maximum)
            direction = -1
        if direction != self.direction:
            self.direction = direction
            self._frame_index = 0
            self._turn_paused = True
            self.animation_timer.stop()
            self._refresh_pixmap()
            self.turn_timer.start(self.settings.turn_pause_ms)
        self.move(round(self._movement_x), self.y())

    def _finish_turn(self) -> None:
        """结束屏幕边缘的短暂停顿，并从第一帧恢复行走。"""

        self._turn_paused = False
        self._movement_x = float(self.x())
        self._last_movement_at = time.monotonic()
        # A manually requested walk animation should still finish its turn;
        # the setting only controls whether autonomous state selection can
        # enter WALK in the first place.
        if self.state is PetState.WALK and not self.paused and not self.dragging:
            self.animation_timer.start(
                self._frame_interval(PetState.WALK, self._frame_index)
            )

    def _bob_tick(self) -> None:
        """通过标签轻微上下移动营造呼吸和行走起伏。"""

        if self.state is PetState.WALK:
            return
        if self.state not in (
            PetState.IDLE,
            PetState.HAPPY,
            PetState.SHY,
            PetState.SURPRISED,
            PetState.ANNOYED,
            PetState.SLEEPY,
            PetState.CURIOUS,
        ):
            self.label.move(6, 0)
            self._refresh_pixmap()
            return
        self._bob_phase = not self._bob_phase
        self.label.move(6, 2 if self._bob_phase else 0)
        self._refresh_pixmap()

    def set_paused(self, paused: bool) -> None:
        """暂停或恢复跑动；暂停期间仍继续坐下、睡眠和自拍等生活状态。"""

        self._record_user_interaction()
        self.paused = paused
        self.pause_changed.emit(paused)
        if paused and self.state is PetState.WALK:
            self.state_timer.stop()
            decision = self.behavior.next_autonomous_state(
                PetState.IDLE,
                allow_walk=False,
            )
            if decision.state is PetState.SLEEP:
                self._schedule_sleep_via_sit()
            else:
                self._schedule(decision)
        elif not paused and not self.dragging and not self.state_timer.isActive():
            self._schedule(self.behavior.initial_idle())
        if paused:
            message = "六毛先在这里安静待着。"
        elif getattr(self.settings, "allow_autonomous_walk", False):
            message = "六毛恢复跑动啦。"
        else:
            message = "自动跑动还没开启；去设置里打开后，我就能在桌面上跑啦。"
        self.show_speech(message, 3200)

    def set_allow_autonomous_walk(self, enabled: bool, *, persist: bool = True) -> None:
        """切换自主跑动总开关；不影响眨眼、坐下和互动动画。"""

        enabled = bool(enabled)
        self.settings.allow_autonomous_walk = enabled
        if not enabled and self.state is PetState.WALK:
            self.state_timer.stop()
            decision = self.behavior.next_autonomous_state(PetState.IDLE, allow_walk=False)
            self._schedule_sleep_via_sit() if decision.state is PetState.SLEEP else self._schedule(decision)
        elif enabled and not self.paused and not self.dragging and not self.state_timer.isActive():
            self._schedule(self.behavior.initial_idle())
        if persist:
            save_settings(self.settings)
        self.show_speech(
            "已开启自动跑动；六毛之后会在桌面上来回移动。"
            if enabled
            else "已关闭自动跑动；六毛会安静待在原地，但其他动画仍然正常。",
            3600,
        )

    def set_display_height(self, display_height: int) -> None:
        """应用右键菜单尺寸预设，保持窗口底部中心位置并立即重绘。"""

        self._record_user_interaction()
        old_center_x = self.x() + self.width() // 2
        old_bottom = self.y() + self.height()
        self.settings.display_height = max(100, min(360, int(display_height)))
        source = self._pixmaps[PetState.IDLE][0]
        width = round(
            self.settings.display_height * source.width() / source.height()
        )
        self.setFixedSize(width + 12, self.settings.display_height + 14)
        self.label.setGeometry(6, 0, width, self.settings.display_height + 8)
        self._render_cache.clear()
        self._mask_cache.clear()
        target = QPoint(
            old_center_x - self.width() // 2,
            old_bottom - self.height(),
        )
        self.move(self._constrained_position(target))
        self._refresh_pixmap()
        self._position_speech_bubble()

    def return_to_primary_screen(self) -> None:
        """将宠物重新放到主屏幕右下角。"""

        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - 24, area.bottom() - self.height() - 12)

    def _position_speech_bubble(self) -> None:
        """把对话气泡放在人物上方，空间不足时自动移到侧面。"""

        if not self.speech_bubble.isVisible():
            return
        area = self._screen_geometry()
        visible_bounds = self.mask().boundingRect()
        if visible_bounds.isEmpty():
            character_left = self.x()
            character_right = self.x() + self.width()
            character_top = self.y()
        else:
            character_left = self.x() + visible_bounds.left()
            character_right = self.x() + visible_bounds.right() + 1
            character_top = self.y() + visible_bounds.top()
        gap = 9
        x = (character_left + character_right - self.speech_bubble.width()) // 2
        y = character_top - self.speech_bubble.height() - gap
        if area is not None:
            x = min(max(x, area.left()), area.right() - self.speech_bubble.width() + 1)
            if y < area.top():
                x = character_right + gap
                if x + self.speech_bubble.width() > area.right() + 1:
                    x = character_left - self.speech_bubble.width() - gap
                x = min(
                    max(x, area.left()),
                    area.right() - self.speech_bubble.width() + 1,
                )
                y = max(area.top(), self.y())
        self.speech_bubble.move(x, y)

    def show_speech(self, text: str, duration_ms: int = 4800) -> None:
        """显示不会抢走键盘焦点的桌面对话气泡。"""

        self.speech_bubble.setText(text)
        self.speech_bubble.adjustSize()
        self.speech_bubble.show()
        self._position_speech_bubble()
        self.speech_timer.start(max(1200, duration_ms))

    def feed_pet(self, food_key: str) -> CompanionReply:
        """喂给 Lili 一种菜单食物，并播放对应表情与文字反馈。"""

        self._record_user_interaction()
        reply = self.companion.feed(food_key)
        if food_key in {"coffee", "tea"}:
            self._set_temporary_activity("tea", 28_000)
            self._play_action_sequence(
                (PetState.SIT, PetState.HAPPY, PetState.SIT),
                3000,
            )
        else:
            food_activity = {"apple": "bunny-carrot", "cookie": "feast", "milk": "milk-tea"}.get(food_key)
            if food_activity:
                self._set_temporary_activity(food_activity, 28_000)
            self._show_emotion(reply.state, 2200)
        self.show_speech(
            f"{reply.text}\n精力 {self.mood.energy} · 饱食 {self.mood.fullness}",
            5200,
        )
        return reply

    def talk_to_pet(self, message: str) -> CompanionReply:
        """在本地处理一条对话，并显示 Lili 的回复。"""

        self._record_user_interaction()
        reply = self.companion.reply_to(message)
        self._show_emotion(reply.state, 2600)
        self.show_speech(reply.text, 5600)
        return reply

    def perform_companion_action(self, action_key: str) -> CompanionReply:
        """播放用户选择的工作、爱意、鼓励、庆祝或安慰动作。"""

        self._record_user_interaction()
        reply = self.companion.perform_action(action_key)
        option = ACTION_BY_KEY[action_key]
        duration_ms = option.duration_ms
        self._play_action_sequence(option.sequence or (reply.state,), duration_ms)
        self.show_speech(reply.text, max(5200, duration_ms + 1800))
        return reply

    def _play_action_sequence(
        self,
        states: tuple[PetState, ...],
        duration_ms: int,
    ) -> None:
        """用现有且已验收的动作帧组成多段陪伴动作。"""

        if not states:
            return
        self._action_sequence_id += 1
        sequence_id = self._action_sequence_id
        step_ms = max(450, duration_ms // len(states))
        self.state_timer.stop()
        self.interaction_timer.stop()
        self.set_state(states[0])
        for index, state in enumerate(states[1:], start=1):
            QTimer.singleShot(
                index * step_ms,
                lambda value=state, marker=sequence_id: self._continue_action_sequence(
                    marker,
                    value,
                ),
            )
        self.interaction_timer.start(max(800, duration_ms))

    def _continue_action_sequence(self, sequence_id: int, state: PetState) -> None:
        """仅在动作序列仍有效时播放下一段，避免旧计时器抢状态。"""

        if sequence_id == self._action_sequence_id and not self.dragging:
            self.set_state(state)

    def start_work_timer(self) -> CompanionReply:
        """开始今日工作计时，并让六毛进入安静陪伴动作。"""

        self._record_user_interaction()
        self._reset_idle_episode()
        self._focus_quality_tracker.start(active_application_category())
        # The paper window selects a real Todo; keep the existing focus
        # analytics task for compatibility, but attribute new seconds to the
        # same local task record as soon as the session starts.
        started = self.focus_session.start()
        if started:
            self.set_paused(True)
        self._change_ambient_activity("computer")
        self._schedule_work_activity(25_000)
        reply = self.companion.work_started(resumed=not started)
        self._show_emotion(reply.state, 3600)
        self.show_speech(reply.text, 5600)
        self.work_timer_changed.emit(self.work_timer.is_running)
        self._schedule_social_tick()
        self._refresh_pixmap()
        return reply

    def pause_work_timer(self, reason: str = "") -> CompanionReply:
        """暂停工作计时并显示当天累计与休息建议。"""

        self._record_user_interaction()
        if reason != "idle":
            self._reset_idle_episode()
        session_seconds = self.work_timer.session_seconds()
        was_running = self.focus_session.pause()
        if was_running:
            self.time_memory.record_focus(
                session_seconds,
                completed_session=False,
                started_at=datetime.now().astimezone() - timedelta(seconds=session_seconds),
            )
            self._last_focus_quality = self.focus_analytics.record_session(
                session_seconds,
                started_at=datetime.now().astimezone() - timedelta(seconds=session_seconds),
                completed=False,
                application_switches=self._focus_quality_tracker.application_switches,
                away_count=self._focus_quality_tracker.away_count,
                task=str((self.focus_analytics.current_task() or {}).get("title", "")),
            )
            self.focus_analytics.update_current_task_progress(session_seconds)
            self.daily_stats.record_focus(session_seconds)
        self._award_focus_rewards()
        self.work_activity_timer.stop()
        self._set_temporary_activity("tea", 25_000)
        duration = format_work_duration(self.work_timer.today_seconds())
        if was_running and reason == "idle":
            reply = CompanionReply(
                "检测到一段时间没有键鼠操作，六毛先帮你暂停计时。回来后会只询问一次这段时间如何归类。",
                PetState.CURIOUS,
            )
        elif was_running:
            reply = self.companion.work_paused(duration)
        else:
            reply = CompanionReply(
                f"计时现在是暂停状态，今天累计工作 {duration}。",
                PetState.CURIOUS,
            )
        self._show_emotion(reply.state, 3200)
        quality_text = (
            f"\n本轮质量：{self._last_focus_quality.label}（{self._last_focus_quality.score}分）"
            if self._last_focus_quality else ""
        )
        self.show_speech(reply.text + quality_text, 5600)
        self.work_timer_changed.emit(False)
        self._schedule_social_tick()
        self.work_controls.hide()
        self._refresh_pixmap()
        return reply

    def finish_work_timer(self) -> CompanionReply:
        """完成本次工作、保留今日累计并播放庆祝动作。"""

        self._record_user_interaction()
        self._reset_idle_episode()
        room_id = self.focus_session.room_id
        session_seconds = self.work_timer.session_seconds()
        total = self.focus_session.finish()
        self.time_memory.record_focus(
            session_seconds,
            completed_session=True,
            started_at=datetime.now().astimezone() - timedelta(seconds=session_seconds),
        )
        self._award_focus_rewards()
        self._last_focus_quality = self.focus_analytics.record_session(
            session_seconds,
            started_at=datetime.now().astimezone() - timedelta(seconds=session_seconds),
            completed=True,
            application_switches=self._focus_quality_tracker.application_switches,
            away_count=self._focus_quality_tracker.away_count,
            task=str((self.focus_analytics.current_task() or {}).get("title", "")),
        )
        self.focus_analytics.update_current_task_progress(session_seconds)
        self.daily_stats.record_focus(session_seconds, completed=True)
        self.set_paused(False)
        reply = self.companion.work_finished(format_work_duration(total))
        self._show_emotion(reply.state, 3400)
        self.show_speech(
            f"{reply.text}\n本轮质量：{self._last_focus_quality.label}（{self._last_focus_quality.score}分）",
            6200,
        )
        self.work_timer_changed.emit(False)
        self._schedule_social_tick()
        self.work_controls.hide()
        self.work_activity_timer.stop()
        self._set_temporary_activity(random.choice(COMPLETE_ACTIONS), 45_000)
        self._show_new_outfit_unlock()
        self._generate_daily_report(show_dialog=False)
        if room_id:
            self._record_social_room_event(room_id, "focus_finish")
        return reply

    def show_today_note(self) -> None:
        """Open the configured surface: attached Todos or the standalone 便利贴."""

        if str(getattr(self.settings, "today_note_mode", "detailed")) == "compact":
            self.show_compact_todos()
        else:
            self.show_sticky_note()

    def show_sticky_note(self) -> None:
        """Open the independent free-form 便利贴 window in detailed mode."""

        self._record_user_interaction()
        if self._compact_todo_panel is not None:
            self._restore_compact_todos_after_show = False
            self._compact_todo_panel.hide()
        if self._today_note_window is None:
            self._today_note_window = TodayNoteWindow(
                self.time_memory,
                self,
                settings=self.settings,
                save_settings_callback=save_settings,
            )
            if getattr(self.settings, "today_note_always_on_top", False):
                self._today_note_window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            self._today_note_window.start_requested.connect(self._start_todo_from_note)
            self._today_note_window.select_requested.connect(self._select_todo_from_note)
            self._today_note_window.complete_requested.connect(self._complete_todo_from_note)
            self._today_note_window.task_checked.connect(self._set_todo_completion_from_note)
            self._today_note_window.checkout_requested.connect(self.checkout_today)
            self._today_note_window.rest_requested.connect(self.rest_today)
            self._today_note_window.memory_requested.connect(self.show_time_memory)
        self._today_note_window.set_mode("detailed", persist=False)
        self._today_note_window.refresh()
        if self._today_note_window.isMinimized():
            self._today_note_window.showNormal()
        else:
            self._today_note_window.show()
        self._today_note_window.raise_()
        self._today_note_window.activateWindow()

    def show_compact_todos(self) -> None:
        """Show the frameless Todo strip directly below the pet."""

        self._record_user_interaction()
        if self._today_note_window is not None:
            self._today_note_window.hide()
        self._restore_compact_todos_after_show = True
        if self._compact_todo_panel is None:
            self._compact_todo_panel = CompactTodoPanel(
                self.time_memory,
                settings=self.settings,
                save_settings_callback=save_settings,
            )
            self._compact_todo_panel.task_selected.connect(self._select_todo_from_note)
            self._compact_todo_panel.task_checked.connect(self._set_todo_completion_from_panel)
            self._compact_todo_panel.task_changed.connect(self._refresh_todo_surfaces)
            self._compact_todo_panel.set_companion_topmost(
                bool(self.settings.always_on_top or getattr(self.settings, "today_note_always_on_top", False))
            )
        self._compact_todo_panel.refresh()
        self._compact_todo_panel.show()
        self._position_compact_todos()

    def _position_compact_todos(self) -> None:
        """Keep the Todo strip attached below the visible pet bounds."""

        panel = self._compact_todo_panel
        if panel is None:
            return
        screen = QApplication.screenAt(self.geometry().center()) or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        x = self.x() + (self.width() - panel.width()) // 2
        y = self.y() + self.height() + 8
        x = max(available.left(), min(x, available.right() - panel.width() + 1))
        y = max(available.top(), min(y, available.bottom() - panel.height() + 1))
        panel.move(x, y)

    def hide_today_note(self) -> None:
        self._restore_compact_todos_after_show = False
        if self._today_note_window is not None:
            self._today_note_window.hide()
        if self._compact_todo_panel is not None:
            self._compact_todo_panel.hide()

    def hide_compact_todos(self) -> None:
        self._restore_compact_todos_after_show = False
        if self._compact_todo_panel is not None:
            self._compact_todo_panel.hide()

    def add_compact_todo(self) -> None:
        self.show_compact_todos()
        if self._compact_todo_panel is not None:
            self._compact_todo_panel.add_task()

    def hide_sticky_note(self) -> None:
        if self._today_note_window is not None:
            self._today_note_window.hide()

    def _refresh_todo_surfaces(self) -> None:
        if self._today_note_window is not None:
            self._today_note_window.refresh()
        if self._compact_todo_panel is not None:
            self._compact_todo_panel.refresh()

    def _select_todo_from_note(self, task_id: str) -> None:
        item = self.time_memory.todos.get(task_id)
        if item is None:
            return
        self.time_memory.select_task(item.id)
        self.focus_analytics.set_current_task(item.title)

    def _start_todo_from_note(self, task_id: str) -> None:
        self._select_todo_from_note(task_id)
        if not self.work_timer.is_running:
            self.start_work_timer()

    def _complete_todo_from_note(self, task_id: str) -> None:
        self.time_memory.complete_task(task_id)
        if self._today_note_window is not None:
            self._today_note_window.refresh()
        self._set_temporary_activity(random.choice(COMPLETE_ACTIONS), 25_000)
        self.show_speech("这项做完了，给你记上。", 4200)

    def _set_todo_completion_from_note(self, task_id: str, completed: bool) -> None:
        task = self.time_memory.todos.get(task_id)
        if task is None:
            return
        if completed:
            self.time_memory.complete_task(task_id)
            self._set_temporary_activity(random.choice(COMPLETE_ACTIONS), 25_000)
            self.show_speech("这项做完了，给你记上。", 4200)
        else:
            self.time_memory.todos.complete(task_id, False)
            self.time_memory.summary.refresh_tasks()
        if self._today_note_window is not None:
            self._today_note_window.refresh()

    def _set_todo_completion_from_panel(self, task_id: str, completed: bool) -> None:
        """Reflect a compact checkbox in the real local Todo store."""

        task = self.time_memory.todos.get(task_id)
        if task is None:
            return
        if completed:
            self.time_memory.complete_task(task_id)
            self._set_temporary_activity(random.choice(COMPLETE_ACTIONS), 25_000)
            self.show_speech("这项做完了，给你记上。", 4200)
        else:
            self.time_memory.todos.complete(task_id, False)
            self.time_memory.summary.refresh_tasks()
        self._refresh_todo_surfaces()

    def checkout_today(self) -> None:
        """Persist a real end-of-day record without requiring network access."""

        if self.work_timer.is_running:
            self.pause_work_timer(reason="checkout")
        summary = self.time_memory.finish_today()
        self._set_temporary_activity(random.choice(COMPLETE_ACTIONS), 30_000)
        self.show_speech(
            f"今天收工：专注{summary['focus']}，完成{summary['completed_tasks']}/{summary['total_tasks']}项。",
            6200,
        )
        if self._today_note_window is not None:
            self._today_note_window.refresh()

    def rest_today(self) -> None:
        self.time_memory.records.set_rest_day(True)
        self._set_temporary_activity("tea", 20_000)
        self.show_speech("行，那今天不算旷工。", 4200)
        if self._today_note_window is not None:
            self._today_note_window.refresh()

    def show_time_memory(self) -> None:
        if self._time_memory_window is None:
            self._time_memory_window = TimeMemoryWindow(self.time_memory, self)
        self._time_memory_window.refresh()
        if self._time_memory_window.isMinimized():
            self._time_memory_window.showNormal()
        else:
            self._time_memory_window.show()
        self._time_memory_window.raise_()
        self._time_memory_window.activateWindow()

    def show_work_time(self) -> None:
        """显示今日累计工作时长和当前计时状态。"""

        self._record_user_interaction()
        state = PetState.SIT if self.work_timer.is_running else PetState.CURIOUS
        self._show_emotion(state, 2600)
        text = (
            f"{self.work_timer.status_text()}\n"
            f"{growth_progress_text(self.work_timer.today_seconds())}\n"
            f"六毛心情：{positive_mood(self.work_timer.today_seconds(), self.work_timer.session_seconds())}"
        )
        self.show_speech(text, 6800)

    def show_daily_growth(self) -> None:
        """显示今天 0–8 小时成长节点和下一个可见奖励。"""

        seconds = self.work_timer.today_seconds()
        stage = stage_for_seconds(seconds)
        self._set_temporary_activity(stage.activity, 35_000)
        self.show_speech(
            f"今日成长 {stage.hour}/8：{stage.title}\n"
            f"当前奖励：{stage.reward}\n{growth_progress_text(seconds)}",
            7600,
        )

    def _schedule_work_activity(self, delay_ms: int | None = None) -> None:
        """计时期间安排下一次陪伴工作动作。"""

        self.work_activity_timer.stop()
        if self.work_timer.is_running:
            self.work_activity_timer.start(delay_ms or random.randint(150_000, 300_000))

    def _work_activity_tick(self) -> None:
        """在专注动作间轮换，让用户工作时六毛也持续工作。"""

        if not self.work_timer.is_running:
            return
        session = self.work_timer.session_seconds()
        choices = FOCUS_ACTIONS
        if session >= 45 * 60 and random.random() < 0.35:
            choices = ("thermos", "tea", "sleep")
        self._change_ambient_activity(random.choice(choices))
        self._manual_activity_until = time.monotonic() + 120
        self._schedule_work_activity()

    def _set_temporary_activity(self, activity: str, duration_ms: int = 30_000) -> None:
        """显示一张完整动作图，结束后回到工作或普通待机。"""

        self._change_ambient_activity(activity)
        self._manual_activity_until = time.monotonic() + duration_ms / 1000
        self.activity_timer.start(max(1500, duration_ms))

    def _activity_timeout(self) -> None:
        """结束临时动作；工作中继续轮换专注动作，否则恢复普通六毛。"""

        self._change_ambient_activity(
            random.choice(FOCUS_ACTIONS) if self.work_timer.is_running else "none"
        )

    def _work_timer_tick(self) -> None:
        """定期保存工作进度，并显示一次到期的鼓励或休息提醒。"""

        self._check_local_reminders()
        self.work_timer.checkpoint()
        self.focus_session.refresh()
        self._award_focus_rewards()
        self._sync_hourly_outfit(announce=True)
        self._show_new_outfit_unlock()
        quiet = detect_quiet_mode()
        wellness_kind = None if quiet.blocked else self.wellness.take_due(
            self.settings.water_reminder_enabled,
            self.settings.stand_reminder_enabled,
            self.settings.water_interval_minutes,
            self.settings.stand_interval_minutes,
        )
        if wellness_kind == "water":
            self._set_temporary_activity("thermos", 35_000)
            self.show_speech("喝口水吧。六毛替你把这一小会儿守住。", 6200)
        elif wellness_kind == "stand":
            self._set_temporary_activity("football", 35_000)
            self.show_speech("站起来走两步、松松肩膀吧。身体也在陪你完成今天。", 6500)
        reminder_kind = None if quiet.blocked else self.work_timer.take_due_reminder()
        if reminder_kind is None:
            return
        duration = format_work_duration(self.work_timer.session_seconds())
        reply = self.companion.work_reminder(reminder_kind, duration)
        self._show_emotion(reply.state, 3600)
        self.show_speech(reply.text, 7200)

    def _check_local_reminders(self) -> None:
        """Run the local reminder queue once per existing one-second timer."""

        if detect_quiet_mode().blocked:
            return
        for reminder in self.time_memory.reminders.due()[:3]:
            self.time_memory.reminders.mark_notified(reminder.id)
            self._set_temporary_activity("curious", 12_000)
            self.show_speech(f"提醒：{reminder.title}", 5600)

    def _show_new_outfit_unlock(self) -> None:
        """跨过当天 1–8 小时节点时显示成长状态，而非机械更换衣服。"""

        stage = stage_for_seconds(self.work_timer.today_seconds())
        if stage.hour <= self._last_growth_hour:
            return
        self._last_growth_hour = stage.hour
        self._set_temporary_activity(stage.activity, 60_000)
        self.show_speech(f"今日成长：{stage.title}\n解锁：{stage.reward}\n{stage.message}", 8200)
        if stage.hour >= 8:
            self._generate_daily_report(show_dialog=True)

    def _sync_hourly_outfit(self, *, announce: bool) -> None:
        """同步小时娃衣解锁，不覆盖用户主动选择的当前装备。

        工作时长只决定哪些套装可用；装备本身是用户偏好，必须一直保留。
        这样累计到 10 小时会解锁荒野相关套装，但不会把用户正在穿的
        一小时兔兔装、经典外观或其他已解锁套装强行替换掉。
        """

        count = self.work_timer.unlocked_outfit_count()
        latest = OUTFITS[count - 1] if count else None
        newly_unlocked = self.work_timer.take_new_outfit_unlock()
        if not announce or newly_unlocked is None or latest is None:
            return
        self._change_ambient_activity("none")
        self.show_speech(
            f"累计专注 {newly_unlocked} 小时，已解锁「{latest.name}」！\n"
            f"你可以在“换装与外观”里选择，当前装备保持不变。",
            8200,
        )

    def _award_focus_rewards(self) -> None:
        """把今日专注时间换成正向的默契奖励，不制造饥饿惩罚。"""

        completed_blocks = self.work_timer.today_seconds() // 600
        new_blocks = max(0, completed_blocks - self._rewarded_focus_blocks)
        if new_blocks:
            self.mood.receive_focus_reward(new_blocks)
            self._rewarded_focus_blocks = completed_blocks

    def shutdown_work_timer(self) -> None:
        """自然退出前暂停计时并更新当天工作卡，不把关机时间计入工作。"""

        self._reset_idle_episode()
        if hasattr(self, "work_timer"):
            if self.work_timer.is_running:
                session_seconds = self.work_timer.session_seconds()
                started_at = datetime.now().astimezone() - timedelta(seconds=session_seconds)
                # Persist the same final running segment in the local time-memory
                # store before the shared timer is paused.  Without this, a
                # normal app close could update the legacy daily card while
                # losing the Todo attribution and daily check-in record.
                self.time_memory.record_focus(
                    session_seconds,
                    completed_session=False,
                    started_at=started_at,
                )
                self.daily_stats.record_focus(session_seconds)
            self.focus_session.pause()
            if self.work_timer.today_seconds() > 0 and hasattr(self, "label"):
                self._generate_daily_report(show_dialog=False)

    def _generate_daily_report(self, *, show_dialog: bool) -> Path | None:
        """生成只保存在本机的工作日报；可选展示预览窗口。"""

        if os.environ.get("ONEPIC_USE_DEMO_ASSETS") == "1" and not show_dialog:
            return None
        photo = self.label.pixmap() if hasattr(self, "label") else QPixmap()
        try:
            path = render_daily_report(
                self.work_timer.today_seconds(),
                self.daily_stats.snapshot(),
                photo,
            )
        except OSError as exc:
            if show_dialog:
                self.show_speech(f"工作日报暂时没保存成功：{exc}", 6200)
            return None
        if show_dialog:
            self._show_daily_report_dialog(path)
        return path

    def show_daily_report(self) -> None:
        """由菜单生成并打开今天的六毛工作日报。"""

        self._record_user_interaction()
        self._generate_daily_report(show_dialog=True)

    def _show_daily_report_dialog(self, path: Path) -> None:
        """在应用内预览工作卡，并提供打开本机相册的按钮。"""

        dialog = QDialog(self)
        dialog.setWindowTitle(f"今天{self._pet_name()}陪你做了什么")
        layout = QVBoxLayout(dialog)
        preview = QLabel(dialog); preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card = QPixmap(str(path)).scaled(430, 570, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        preview.setPixmap(card); layout.addWidget(preview)
        open_button = QPushButton(f"打开{self._pet_name()}相册", dialog)
        open_button.clicked.connect(self.open_daily_album)
        layout.addWidget(open_button)
        dialog.exec()

    def open_daily_album(self) -> None:
        """打开本机六毛相册文件夹，不访问网络。"""

        directory = album_directory(); directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory.resolve())))

    def prompt_dialogue(self) -> None:
        """打开新版聊天面板；离线和在线模式共用同一个入口。"""

        self._record_user_interaction()
        if self._chat_dialog is None:
            # Keep chat as an independent utility window so it has a normal
            # taskbar/Dock entry and can be minimized without affecting pet.
            self._chat_dialog = ChatDialog(None, self._pet_name())
            self._chat_dialog.message_submitted.connect(self._submit_chat_message)
            self._chat_dialog.settings_requested.connect(self.open_settings)
            self._chat_dialog.rename_requested.connect(self.rename_pet)
            self._chat_dialog.reconnect_requested.connect(self._reconnect_ai)
            self._chat_dialog.append_message(
                self._pet_name(),
                f"巴布达！没网也可以聊天；也能在设置里连接 Codex、Claude Code、DeepSeek 或 Kimi。",
            )
        status = self.agent_manager.status(self.settings.ai_provider)
        self._chat_dialog.set_provider(
            self.settings.ai_provider,
            status.state.value,
            status.detail,
        )
        if self._chat_dialog.isMinimized():
            self._chat_dialog.showNormal()
        else:
            self._chat_dialog.show()
        self._chat_dialog.raise_()
        self._chat_dialog.activateWindow()

    def rename_pet(self) -> None:
        """Edit the owner's social nickname; the pet remains 六毛 forever."""

        self._record_user_interaction()
        name, accepted = QInputDialog.getText(
            self,
            "修改主人称呼",
            "主人称呼\n用于自习室、串门和搭子互动时区分不同六毛。\n例如填写“小梁”，其他搭子将看到“小梁家的六毛”。",
            text=self._owner_nickname(),
        )
        if not accepted:
            return
        name = clean_owner_nickname(name)
        previous_name = self._owner_nickname()
        self.settings.owner_nickname = name
        self.settings.pet_name = PET_NAME
        save_settings(self.settings)
        if name != previous_name:
            self.owner_nickname_changed.emit(name)
            self._sync_owner_nickname(name)
            if self._social_dialog is not None:
                self._social_dialog.set_owner_nickname(name)
        label = social_pet_label(name)
        self.show_speech(f"好，社交场景里就叫{label}。我还是六毛。", 4200)

    def _sync_owner_nickname(self, nickname: str) -> None:
        """Persist the social-only nickname without blocking the desktop pet."""

        if not self.social_client.signed_in:
            return
        session = getattr(self.social_client, "session", None)
        user_id = str(getattr(session, "user_id", "") or "")
        if not user_id or self._owner_nickname_sync_inflight:
            return
        clean = clean_owner_nickname(nickname)
        key = (user_id, clean)
        if self._owner_nickname_sync_key == key:
            return
        updater = getattr(self.social_client, "update_owner_nickname", None)
        if not callable(updater):
            return
        self._owner_nickname_sync_inflight = True
        thread = SocialProfileThread(self.social_client, clean, self)
        self._social_profile_threads.append(thread)
        thread.completed.connect(lambda sync_key=key: self._owner_nickname_sync_succeeded(sync_key))
        thread.failed.connect(self._owner_nickname_sync_failed)
        thread.finished.connect(
            lambda: self._social_profile_thread_finished(thread)
        )
        thread.start()

    def _maybe_sync_owner_nickname(self) -> None:
        """Sync a locally configured owner nickname after every account login."""

        if self.social_client.signed_in:
            self._sync_owner_nickname(self._owner_nickname())

    def _owner_nickname_sync_succeeded(self, key: tuple[str, str]) -> None:
        self._owner_nickname_sync_inflight = False
        self._owner_nickname_sync_key = key
        self._schedule_social_tick()

    def _owner_nickname_sync_failed(self, _message: str) -> None:
        self._owner_nickname_sync_inflight = False
        if self._social_dialog is not None:
            self._social_dialog._set_status("主人称呼已保存在本机，但云端同步失败，请稍后重试。", error=True)

    def _social_profile_thread_finished(self, thread: SocialProfileThread) -> None:
        if thread in self._social_profile_threads:
            self._social_profile_threads.remove(thread)
        thread.deleteLater()

    def _submit_chat_message(self, message: str) -> None:
        """把消息交给 ChatManager；路由只读取缓存，不做同步检测。"""

        if self._chat_dialog is None:
            return
        self._record_user_interaction()
        if self.chat_manager.busy:
            self._chat_dialog.append_message(self._pet_name(), "上一句话还在路上，稍等我一下。")
            return
        self._chat_dialog.append_message("你", message)
        history_before = self._chat_memory.snapshot().as_history()
        self._chat_memory.add("user", message)
        self._chat_dialog.show_recovery_actions(False)
        self.chat_manager.submit(message, history_before)

    def _managed_chat_reply(self, reply: ManagedChatReply) -> None:
        """统一展示 AI 或离线回复；降级时不附加连接错误正文。"""

        self._chat_memory.add("assistant", reply.text)
        if self._chat_dialog is not None:
            self._chat_dialog.append_message(self._pet_name(), reply.text)
            self._chat_dialog.show_recovery_actions(reply.show_recovery_actions)
        self._show_emotion(reply.state, 3000)
        self.show_speech(reply.text, 6500)

    def _chat_busy_changed(self, busy: bool) -> None:
        """只禁用聊天输入，宠物动画、计时和音乐继续运行。"""

        if self._chat_dialog is not None:
            self._chat_dialog.set_busy(busy)

    def _chat_notice(self, message: str) -> None:
        """显示非阻塞提示，不跳转设置页。"""

        if self._chat_dialog is not None:
            self._chat_dialog.append_message(self._pet_name(), message)

    def _agent_status_changed(self, provider: str, state: str, detail: str) -> None:
        """后台检测完成后刷新缓存状态文案，恢复后下一条自然走 AI。"""

        if self._chat_dialog is not None and provider == self.settings.ai_provider:
            self._chat_dialog.set_provider(provider, state, detail)
            if state == "connected":
                self._chat_dialog.show_recovery_actions(False)

    def _reconnect_ai(self) -> None:
        """用户主动要求重连；不会自动打开设置窗口。"""

        if self.settings.ai_provider == "offline":
            self._chat_notice("当前选择的是纯离线模式；需要 AI 时可以点“去设置”。")
            return
        if not self.chat_manager.reconnect_now():
            self._chat_notice("AI 已在后台检测中，请稍等一下。")

    def open_settings(self, source: str) -> bool:
        """只允许明确用户动作打开设置；所有自动或未知来源均拒绝并记录。"""

        if source != SETTINGS_SOURCE_USER_ACTION:
            LOGGER.debug("拒绝非用户来源打开连接与陪伴设置：source=%r", source)
            return False

        dialog = AISettingsDialog(
            self.settings,
            self.credentials,
            self,
            agent_manager=self.agent_manager,
            music_manager=self.music_provider_manager,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return True
        previous_always_on_top = self.settings.always_on_top
        previous_allow_autonomous_walk = bool(
            getattr(self.settings, "allow_autonomous_walk", False)
        )
        previous_pet_name = self._pet_name()
        previous_owner_nickname = self._owner_nickname()
        try:
            dialog.apply()
        except Exception as exc:
            self.show_speech(f"设置没有保存：{exc}", 6000)
            return True
        current_pet_name = self._pet_name()
        self.setWindowTitle(f"{APP_DISPLAY_NAME} · {current_pet_name}")
        if self._chat_dialog is not None:
            self._chat_dialog.set_pet_name(current_pet_name)
        self.quick_panel.set_pet_name(current_pet_name)
        if current_pet_name != previous_pet_name:
            self.pet_name_changed.emit(current_pet_name)
        current_owner_nickname = self._owner_nickname()
        if current_owner_nickname != previous_owner_nickname:
            self.owner_nickname_changed.emit(current_owner_nickname)
            self._sync_owner_nickname(current_owner_nickname)
            if self._social_dialog is not None:
                self._social_dialog.set_owner_nickname(current_owner_nickname)
        if self.settings.always_on_top != previous_always_on_top:
            self.set_always_on_top(self.settings.always_on_top, persist=False)
        if bool(self.settings.allow_autonomous_walk) != previous_allow_autonomous_walk:
            self.set_allow_autonomous_walk(
                self.settings.allow_autonomous_walk,
                persist=False,
            )
        save_settings(self.settings)
        self._schedule_ambient()
        self._schedule_song_inspiration()
        if self._chat_dialog is not None:
            status = self.agent_manager.status(self.settings.ai_provider)
            self._chat_dialog.set_provider(
                self.settings.ai_provider,
                status.state.value,
                status.detail,
            )
        if self.settings.ai_provider != "offline":
            self.agent_manager.start_background_check(
                (self.settings.ai_provider,),
                force=True,
            )
        preset = PROVIDER_PRESETS[self.settings.ai_provider]
        self.show_speech(f"已切换为：{preset.label}", 4200)
        return True

    def open_social_hub(self) -> None:
        """打开联网搭子与私人自习室；离线功能不依赖此窗口。"""

        self._record_user_interaction()
        if self._social_dialog is None:
            # Keep the study room as an independent top-level application
            # window so Windows gives it a normal taskbar button.  The pet
            # retains ownership through the Python reference and can restore
            # the same instance on a later menu click.
            self._social_dialog = SocialHubDialog(
                self.social_client,
                self.settings.equipped_outfit,
                self._owner_nickname(),
                None,
            )
            self._social_dialog.active_visit.connect(self._show_buddy_visit)
            self._social_dialog.room_event_received.connect(self._room_event_received)
            self._social_dialog.buddy_subscription_notice.connect(self._buddy_subscription_notice)
            self._social_dialog.finished.connect(self._social_dialog_finished)
            self._social_dialog.focus_start_requested.connect(self.start_work_timer)
            self._social_dialog.focus_pause_requested.connect(self.pause_work_timer)
            self._social_dialog.focus_finish_requested.connect(self.finish_work_timer)
            self._social_dialog.focus_task_requested.connect(self._set_focus_task)
            self._social_dialog.tomorrow_review_requested.connect(self._set_tomorrow_review)
            self._social_dialog.room_ritual_due.connect(self._room_ritual_due)
            self._social_dialog.room_changed.connect(self._social_room_changed)
            self._social_dialog.quick_action_requested.connect(self._room_quick_action)
            self._social_dialog.set_focus_snapshot(self.focus_session.snapshot())
            self._social_dialog.set_focus_analytics(self.focus_analytics.snapshot())
        # A second click on the menu must restore a minimized study-room
        # window instead of leaving it hidden in the taskbar/Dock.
        if self._social_dialog.isMinimized():
            self._social_dialog.showNormal()
        else:
            self._social_dialog.show()
        self._social_dialog.raise_(); self._social_dialog.activateWindow()

    def _focus_snapshot_changed(self, snapshot: object) -> None:
        if self._social_dialog is not None:
            self._social_dialog.set_focus_snapshot(snapshot)
            self._social_dialog.set_focus_analytics(self.focus_analytics.snapshot())

    def _room_event_received(self, event: dict) -> None:
        """Play a received room interaction on this desktop pet."""

        if detect_quiet_mode().blocked:
            return
        kind = str(event.get("kind") or "")
        actor = social_pet_label(event.get("nickname"))
        message = str(event.get("message") or "")
        labels = {"poke": "戳了戳你", "cheer": "给你加油", "drink": "递给你一杯奶茶"}
        if kind == "phrase":
            text = f"{actor}：{message[:100]}"
            activity = "happy"
        else:
            text = f"{actor}{labels.get(kind, '给你发来一条房间动态')}"
            activity = {"poke": "surprised", "cheer": "pointing", "drink": "tea"}.get(kind, "happy")
        self._set_temporary_activity(activity, 20_000)
        self.show_speech(text, 5200)

    def _set_focus_task(self, title: str, minutes: int) -> None:
        task = self.time_memory.todos.find(title)
        if task is None and str(title).strip():
            task = self.time_memory.todos.add(str(title).strip())
        if task is not None:
            self.time_memory.select_task(task.id)
        due_at = None
        if int(minutes) > 0:
            due_at = (datetime.now().astimezone() + timedelta(minutes=int(minutes))).isoformat()
        self.focus_analytics.set_current_task(title, due_at=due_at, target_seconds=max(0, int(minutes)) * 60)
        if self._social_dialog is not None:
            self._social_dialog.set_focus_analytics(self.focus_analytics.snapshot())
        self.show_speech(f"这轮只盯一件事：{title[:80]}", 4200)

    def _set_tomorrow_review(self, title: str) -> None:
        self.focus_analytics.set_tomorrow_task(title)
        if title:
            self.show_speech(f"明天第一件事记好了：{title[:80]}", 4200)
        else:
            self.show_speech("明天第一件事已清空。", 3200)

    def _room_ritual_due(self, label: str) -> None:
        if detect_quiet_mode().blocked:
            return
        self.show_speech(f"房间提醒：{label}！大家一起动起来。", 5000)

    def _buddy_subscription_notice(self, message: str) -> None:
        if detect_quiet_mode().blocked:
            return
        self.show_speech(message, 4200)

    def _social_dialog_finished(self) -> None:
        if self._social_dialog is not None:
            self._social_dialog.deleteLater(); self._social_dialog = None

    def _social_tick(self) -> None:
        """每 30 秒刷新房间状态；心跳按需发送，失败时保留离线桌宠。"""

        if not self.social_client.signed_in or (self._social_thread is not None and self._social_thread.isRunning()):
            return
        self._maybe_sync_owner_nickname()
        selected_room = self._social_dialog.current_room_id if self._social_dialog is not None else None
        # A persisted local room ID is not an invitation to re-enter a room.
        # Only the room explicitly selected in the study-room window is sent.
        room_id = selected_room
        if room_id != self.focus_session.room_id:
            self.focus_session.set_room_id(room_id)
        snapshot = self.focus_session.snapshot()
        presence = {
            "working": snapshot.is_running,
            "today_seconds": snapshot.today_seconds,
            "session_started_at": snapshot.session_started_at,
            "outfit_key": self.settings.equipped_outfit,
            "room_id": room_id,
            "quick_status": self._active_room_quick_status(),
            "quick_status_expires_at": self._room_quick_status_expires_at.isoformat()
            if self._room_quick_status_expires_at is not None else None,
        }
        send_heartbeat = self._social_heartbeat_due or time.monotonic() - self._last_social_heartbeat_at >= 90.0
        if send_heartbeat:
            self._last_social_heartbeat_at = time.monotonic()
            self._social_heartbeat_due = False
        thread = SocialSyncThread(self.social_client, presence, self, send_heartbeat=send_heartbeat)
        self._social_thread = thread
        thread.completed.connect(self._social_dashboard_received)
        thread.failed.connect(self._social_sync_failed)
        thread.finished.connect(self._social_thread_finished)
        thread.start()

    def _social_dashboard_received(self, data: dict) -> None:
        """显示新串门提醒，并在双方本地打开双六毛画面。"""

        # The sync thread already fetched this dashboard.  Render that exact
        # payload instead of issuing a second blocking request from the UI
        # thread; this is what makes a peer's fresh focus state visible within
        # the same heartbeat.
        if self._social_dialog is not None:
            self._social_dialog.apply_dashboard(data)

        # A cached snapshot is useful for explaining the last known state,
        # but it is not permission to reopen a visit window or emit a new
        # interaction.  Only a server-confirmed payload may trigger social
        # side effects.
        if data.get("_sync_offline") or data.get("data_source") == "local_cache":
            return

        if detect_quiet_mode().blocked:
            return
        for visit in data.get("visits") or []:
            visit_id = str(visit.get("id", ""))
            if visit_id and visit_id not in self._seen_visit_ids:
                self._seen_visit_ids.add(visit_id)
                self._set_temporary_activity("pointing", 20_000)
                self.show_speech(f"{social_pet_label(visit.get('owner_nickname') or visit.get('nickname'))}来串门啦！\n打开“搭子自习室”可以接受。", 7600)
        active = data.get("active_visits") or []
        if active:
            self._show_buddy_visit(active[0])

    def _social_sync_failed(self, message: str) -> None:
        """Keep the pet quiet while making an unavailable room understandable."""

        if self._social_dialog is not None:
            if self._social_dialog.current_room_id:
                self._social_dialog._set_status(
                    f"自习室暂时离线：{message}"
                    "；六毛仍会本地计时，网络恢复后自动重试。"
                )
            elif self.focus_session.snapshot().is_running:
                self._social_dialog._set_status(
                    "本地专注已开始；你还没有加入自习室，搭子状态会在网络恢复后自动同步。"
                )
            else:
                self._social_dialog._set_status(
                    "你还没有加入自习室；本地功能不受影响，联网后搭子状态会自动同步。"
                )

    def _record_social_room_event(self, room_id: str, kind: str) -> None:
        """Record a lifecycle event without blocking the desktop pet."""

        if not self.social_client.signed_in:
            return
        thread = SocialEventThread(
            self.social_client,
            {"room_id": room_id, "kind": kind, "target_id": None, "message": ""},
            self,
        )
        self._social_event_threads.append(thread)
        thread.finished.connect(lambda: self._social_event_finished(thread))
        thread.start()

    def _social_event_finished(self, thread: SocialEventThread) -> None:
        if thread in self._social_event_threads:
            self._social_event_threads.remove(thread)
        thread.deleteLater()

    def _social_room_changed(self, room_id: object) -> None:
        """Bind room selection to the single local focus session."""

        self.focus_session.set_room_id(str(room_id) if room_id else None)
        if not room_id:
            self._room_quick_status = ""
            self._room_quick_status_expires_at = None
        self._schedule_social_tick()

    def _active_room_quick_status(self) -> str:
        if self._room_quick_status_expires_at is not None and datetime.now().astimezone() >= self._room_quick_status_expires_at:
            self._room_quick_status = ""
            self._room_quick_status_expires_at = None
        return self._room_quick_status

    def _room_quick_action(self, action: str) -> None:
        """Turn room action phrases into real local focus state changes."""

        action = str(action).strip()
        if action == "我也开工了":
            self._room_quick_status = ""
            self._room_quick_status_expires_at = None
            self.start_work_timer()
        elif action == "再卷 30 分钟":
            self._room_quick_status = "再卷30分钟"
            self._room_quick_status_expires_at = datetime.now().astimezone() + timedelta(minutes=30)
            if not self.work_timer.is_running:
                self.start_work_timer()
            elif self._social_dialog is not None:
                self._social_dialog.set_room_quick_status(self._room_quick_status, self._room_quick_status_expires_at)
        elif action == "去喝水":
            self._room_quick_status = "去喝水"
            self._room_quick_status_expires_at = datetime.now().astimezone() + timedelta(minutes=10)
            self.pause_work_timer()
        else:
            return
        if self._social_dialog is not None:
            self._social_dialog.set_room_quick_status(
                self._active_room_quick_status(), self._room_quick_status_expires_at
            )
        self._schedule_social_tick()

    def _schedule_social_tick(self) -> None:
        """Push work/room transitions promptly instead of waiting 30 seconds."""

        if not self.social_client.signed_in:
            return
        timer = getattr(self, "social_sync_timer", None)
        if timer is not None:
            self._social_heartbeat_due = True
            timer.start(250)

    def _show_buddy_visit(self, peer: dict) -> None:
        if detect_quiet_mode().blocked:
            return
        # Active visits normally carry a database id.  Keep a deterministic
        # fallback for older backend responses so a 30-second heartbeat does
        # not repeatedly reopen a minimized visit window.
        visit_id = str(
            peer.get("id")
            or peer.get("visit_id")
            or f"{peer.get('user_id', '')}:{peer.get('visit_started_at', '')}:{peer.get('nickname', '')}"
        )
        if visit_id and visit_id in self._shown_active_visit_ids:
            return
        if visit_id:
            self._shown_active_visit_ids.add(visit_id)
        self._buddy_visit_window.show_peer(
            peer,
            self.settings.equipped_outfit,
            self.work_timer.today_seconds(),
        )

    def _social_thread_finished(self) -> None:
        if self._social_thread is not None:
            self._social_thread.deleteLater(); self._social_thread = None

    def set_automatic_grumbling(self, enabled: bool) -> None:
        """启用或停用只在本机生成的间歇牢骚。"""

        self.settings.automatic_grumbling = bool(enabled)
        save_settings(self.settings)
        self._schedule_ambient()
        self.show_speech("偶尔发牢骚已开启。" if enabled else "偶尔发牢骚已关闭。", 3000)

    def set_hourly_announcement(self, enabled: bool) -> None:
        """启用或停用整点报时。"""

        self.settings.hourly_announcement = bool(enabled)
        self._last_announced_hour = ""
        save_settings(self.settings)
        self.show_speech("整点报时已开启。" if enabled else "整点报时已关闭。", 3200)

    def _app_awareness_tick(self) -> None:
        """只根据前台应用类别切换配饰动作，不读取标题或文档内容。"""

        category = active_application_category()
        if self.work_timer.is_running:
            self._focus_quality_tracker.note_application_switch(category)

        if (
            not self.settings.app_awareness
            or self.work_timer.is_running
            or time.monotonic() < self._manual_activity_until
        ):
            return
        if category == self._last_app_category:
            return
        self._last_app_category = category
        mapping = {"music": "headphones", "office": "work-study", "coding": "deep-focus", "reading": "night-reading"}
        self._change_ambient_activity(mapping.get(category, "none"))

    def play_random_song(self) -> str:
        """自动寻找最可用的本机播放器并随机开始播放陈楚生。"""

        if self.music_controller.play_song("", "陈楚生", random_artist=True):
            self.show_speech("正在自动寻找可用播放器，并随机播放一首陈楚生…", 4200)
        else:
            self.show_speech("音乐操作正在处理中，请稍等一下。", 3200)
        return "陈楚生随机歌曲"

    def _play_random_song_legacy(self) -> str:
        """从搜索结果中的陈楚生歌曲行随机选择，再执行播放与媒体校验。"""

        if self.music_controller.play_song("", "陈楚生", random_artist=True):
            self.show_speech("正在从陈楚生的歌曲结果中随机选择，并核对实际播放歌曲……", 4200)
        else:
            self.show_speech("上一项音乐操作还在处理中，请稍等一下。", 3200)
        return "陈楚生随机歌曲"

    def control_music(self, action: str) -> bool:
        """异步控制刚才真正开始播放的 Provider，不重新选择其他播放器。"""

        if self.music_controller.perform(action):
            self.show_speech("正在连接系统播放器…", 2200)
            return True
        self.show_speech("音乐控制还在处理中，请稍等一下。", 3200)
        return False

    def _music_control_result(self, result: MusicControlResult | SongPlaybackResult) -> None:
        """只展示系统控制层返回的真实结果和能力等级。"""

        is_status = isinstance(result, MusicControlResult) and result.action == "status"
        if isinstance(result, MusicControlResult):
            track_artist = result.status.track.artist if result.status.track else ""
            track_title = result.status.track.title if result.status.track else ""
        else:
            track_artist = result.current_artist
            track_title = result.current_title
        family_music = family_music_mode(track_artist, track_title)
        if family_music:
            # 听到爹的歌时让六毛先听歌，暂时减少普通主动打扰。
            self._show_emotion(PetState.SIT, 2400)
            self._set_temporary_activity("headphones", 120_000)
            self._manual_activity_until = time.monotonic() + 120
        if result.success and not is_status and not family_music:
            self._change_ambient_activity("headphones")
            self._manual_activity_until = time.monotonic() + 45
        if isinstance(result, SongPlaybackResult):
            save_settings(self.settings)
        self.show_speech(result.message, 6200)

    def set_activity(self, activity: str) -> None:
        """手动选择修正版动作表中的任意完整动作。"""

        self._set_temporary_activity(activity, 120_000)
        self.show_speech("动作已切换，六毛开始表演啦。", 2800)

    def equip_outfit(self, outfit_key: str) -> None:
        """装备已解锁娃衣；空字符串恢复经典外观。"""

        allowed = {item.key for item in unlocked_outfits(self.work_timer.unlocked_outfit_count())}
        if outfit_key and outfit_key not in allowed:
            self.show_speech("这套娃衣还在秘密王国里，再累计工作一小时就更近一点。", 5200)
            return
        self.settings.equipped_outfit = outfit_key
        save_settings(self.settings)
        self._mask_cache.clear(); self._refresh_pixmap()
        label = next((item.name for item in OUTFITS if item.key == outfit_key), "经典六毛")
        self.show_speech(f"已换上：{label}。", 3200)

    def open_size_control(self) -> None:
        """打开连续尺寸滑块并实时应用，不改变不同动作之间的比例。"""

        dialog = SizeControlDialog(self.settings.display_height, self, self._pet_name())
        dialog.value_changed.connect(self.set_display_height)
        dialog.exec()
        save_settings(self.settings)

    def _position_floating_panel(self, panel: QWidget) -> None:
        """把快捷面板放在宠物旁边并限制在当前屏幕可见区域。"""

        panel.adjustSize()
        area = self._screen_geometry()
        x = self.x() - panel.width() - 10
        y = self.y() + max(0, self.height() // 3)
        if area is not None:
            if x < area.left():
                x = self.x() + self.width() + 10
            x = min(max(x, area.left()), area.right() - panel.width() + 1)
            y = min(max(y, area.top()), area.bottom() - panel.height() + 1)
        panel.move(x, y)

    def show_quick_panel(self) -> None:
        """双击切换快捷口袋；再次双击立即收起。"""

        if self.quick_panel.isVisible():
            self.quick_panel.hide()
            return
        self._position_floating_panel(self.quick_panel)
        self.quick_panel.show(); self.quick_panel.raise_()

    def show_work_controls(self) -> None:
        """在计时运行时显示暂停和结束两个操作气泡。"""

        if not self.work_timer.is_running:
            self.start_work_timer()
            return
        self._position_floating_panel(self.work_controls)
        self.work_controls.show(); self.work_controls.raise_()

    def _quick_work_action(self) -> None:
        """快捷面板的工作入口：未运行时开始，运行时展示控制。"""

        if self.work_timer.is_running:
            self.show_work_controls()
        else:
            self.start_work_timer()

    def _schedule_ambient(self) -> None:
        """用随机间隔安排六毛主动出现，保持存在感但避免频繁打扰。"""

        if not hasattr(self, "ambient_timer"):
            return
        self.ambient_timer.stop()
        if self.settings.automatic_grumbling:
            self.ambient_timer.start(random.randint(8 * 60_000, 18 * 60_000))

    def _ambient_tick(self) -> None:
        """按时段、专注长度与低概率彩蛋让六毛主动找用户。"""

        try:
            busy = self.chat_manager.busy
            if self.isVisible() and not self.dragging and not busy:
                idle_seconds = time.monotonic() - self._last_user_interaction
                if self.work_timer.is_running and self.work_timer.session_seconds() >= 2 * 3600:
                    activity, text = "thermos", "连续工作两小时啦。六毛把水杯端来了：先休息一下？"
                elif idle_seconds >= 30 * 60:
                    activity, text = "pointing", "你很久没动啦，六毛偷偷探头看看你还在不在。"
                elif self.work_timer.today_seconds() >= 3 * 3600 and random.random() < 0.06:
                    activity, text = "wild-king", "极低概率彩蛋：荒野国王路过你的桌面。"
                elif random.random() < 0.55:
                    decision = self.companion_behavior.decide(
                        now_hour=datetime.now().hour,
                        working=self.work_timer.is_running,
                        session_seconds=self.work_timer.session_seconds(),
                        today_seconds=self.work_timer.today_seconds(),
                        idle_seconds=int(idle_seconds),
                        music_playing=self._manual_activity_until > time.monotonic(),
                    )
                    activity = decision.activity
                    if activity == "idle":
                        activity, text = time_of_day_activity(datetime.now(), self.work_timer.is_running)
                    elif activity == "night-reading":
                        text = "我陪你读一会儿，慢慢来。"
                    elif activity == "sleepy":
                        text = "六毛有点累啦，记得给自己留一点休息时间。"
                    elif activity == "sit":
                        text = "我坐在这里陪你，把这一小段完成就好。"
                    elif activity == "headphones":
                        text = "音乐响起来啦，和你一起专注。"
                    else:
                        text = self.companion.ambient_grumble(self.work_timer.is_running).text
                else:
                    activity = random.choice(RANDOM_ACTIONS)
                    text = self.companion.ambient_grumble(self.work_timer.is_running).text
                self.daily_stats.record_event(activity)
                if activity == "sleep":
                    self.daily_stats.record_sleep()
                self._set_temporary_activity(activity, 42_000)
                self.show_speech(text, 6800)
        finally:
            self._schedule_ambient()

    def _schedule_song_inspiration(self) -> None:
        """按用户设置单独安排歌词气泡，不再与低概率牢骚共用计时器。"""

        if not hasattr(self, "song_timer"):
            return
        self.song_timer.stop()
        if self.settings.lyric_inspiration_enabled:
            base = self.settings.lyric_interval_minutes * 60_000
            self.song_timer.start(max(60_000, round(base * random.uniform(0.85, 1.15))))

    def _song_inspiration_tick(self) -> None:
        """显示本机歌词短行；未选择文件时显示原创歌名意象短句。"""

        try:
            busy = self.chat_manager.busy
            if self.isVisible() and not self.dragging and not busy:
                local_lines = load_local_lines(self.settings.local_lyrics_path)
                if local_lines:
                    self._show_emotion(PetState.SIT, 2400)
                    self.show_speech(f"♪ {random.choice(local_lines)}", 6800)
                else:
                    reply = self.companion.song_inspiration()
                    self._show_emotion(reply.state, 2400)
                    self.show_speech(f"♪ {reply.text}", 6800)
        finally:
            self._schedule_song_inspiration()

    def _hourly_tick(self) -> None:
        """周期检查整点报时，关闭时不产生任何气泡。"""

        self._maybe_announce_hour(datetime.now())

    def _maybe_announce_hour(self, now: datetime) -> bool:
        """在每个整点窗口内只播报一次，返回是否实际播报。"""

        if not self.settings.hourly_announcement or now.minute != 0:
            return False
        key = now.strftime("%Y-%m-%d-%H")
        if key == self._last_announced_hour:
            return False
        self._last_announced_hour = key
        reply = self.companion.hourly_announcement(now.hour)
        self._show_emotion(reply.state, 2800)
        self.show_speech(reply.text, 6200)
        return True

    def show_companion_status(self) -> None:
        """用气泡显示当前会话内亲密、精力和饱食状态。"""

        self._record_user_interaction()
        count = self.work_timer.unlocked_outfit_count()
        next_text = "12 套小时娃衣已全部解锁"
        if count < len(OUTFITS):
            remaining = max(0, (count + 1) * 3600 - self.work_timer.lifetime_seconds())
            next_text = f"距下一套娃衣约 {format_work_duration(remaining)}"
        self.show_speech(
            f"{self.companion.status_text(self.work_timer.today_seconds() // 600)}\n{next_text}",
            6200,
        )

    def trigger_interaction(self) -> None:
        """结合当前情绪数值触发友好表情或挥手反馈。"""

        if self.dragging:
            return
        self._record_user_interaction()
        if self.mood.energy < 30:
            state = PetState.SLEEPY
        elif self.mood.boredom > 70:
            state = PetState.CURIOUS
        else:
            state = random.choice((PetState.WAVE, PetState.HAPPY, PetState.SHY))
        self._show_emotion(state, 1800)
        now = datetime.now()
        if (
            not self._late_wakeup_shown
            and self.settings.lyric_inspiration_enabled
            and 10 <= now.hour < 13
            and self.work_timer.today_seconds() == 0
        ):
            self._late_wakeup_shown = True
            reply = self.companion.song_inspiration(late_wakeup=True)
            self.show_speech(reply.text, 6500)
        else:
            self.show_speech("巴布达！六毛听见你啦。", 3000)

    def _show_emotion(self, state: PetState, duration_ms: int = 1600) -> None:
        """显示一次短暂互动表情，并在计时结束后恢复自主生活。"""

        self.state_timer.stop()
        self.set_state(state)
        self.interaction_timer.start(max(500, duration_ms))

    def trigger_selfie(self) -> None:
        """显式播放一次举起相机、闪光和查看照片的自拍序列。"""

        if self.dragging:
            return
        self._record_user_interaction()
        self._show_emotion(PetState.SELFIE, 2600)

    def _interaction_zone(self, point: QPoint) -> str:
        """按窗口内相对位置划分头顶、脸部、身体和相机互动区域。"""

        x = (point.x() - self.label.x()) / max(1, self.label.width())
        y = (point.y() - self.label.y()) / max(1, self.label.height())
        if self.work_timer.is_running and 0.52 <= y <= 0.88 and 0.18 <= x <= 0.84:
            return "work_device"
        if y < 0.24:
            return "head"
        if y < 0.46:
            return "face"
        if 0.42 <= y <= 0.82 and x < 0.43:
            return "camera"
        return "body"

    def _handle_click(self, point: QPoint) -> None:
        """根据点击区域更新情绪并选择对应反馈。"""

        zone = self._interaction_zone(point)
        self._record_user_interaction()
        self.daily_stats.record_touch()
        if zone == "work_device" or (zone == "head" and self.work_timer.is_running):
            self.show_work_controls()
            return
        if zone == "camera":
            self.trigger_selfie()
            return
        if zone == "head":
            self.mood.receive_affection()
            self._show_emotion(PetState.CURIOUS, 1700)
            self.show_speech("六毛歪着头看你：是在叫我吗？", 3200)
            return
        if zone == "face":
            self.mood.receive_poke(False)
            self._show_emotion(PetState.SURPRISED, 1300)
            return

        now = time.monotonic()
        self._poke_times.append(now)
        while self._poke_times and now - self._poke_times[0] > 2.5:
            self._poke_times.popleft()
        repeated = len(self._poke_times) >= 5
        self.mood.receive_poke(repeated)
        self._show_emotion(
            PetState.ANNOYED if repeated else PetState.SHY,
            1800 if repeated else 1200,
        )
        self.show_speech(
            "戳够五下啦，六毛真的要生气一点点。" if repeated else "六毛赶紧护住肚子：这里不许乱戳。",
            3600,
        )

    def play_babuda_voice(self) -> None:
        """双击右键时轮换播放用户本地音频；缺少文件则用系统语音轻微变调。"""

        if not self.settings.voice_enabled:
            self.show_speech("巴布达！", 2600)
            return
        variants = find_audio_variants(self.settings.babuda_audio_path)
        index = self._babuda_variant_index
        self._babuda_variant_index += 1
        if variants and self._media_player is not None:
            path = variants[index % len(variants)]
            self._media_player.stop()
            self._media_player.setPlaybackRate((0.96, 1.0, 1.05)[index % 3])
            self._media_player.setSource(QUrl.fromLocalFile(str(path.resolve())))
            self._media_player.play()
        elif self._speech_engine is not None:
            self._speech_engine.setRate((-0.08, 0.0, 0.08)[index % 3])
            self._speech_engine.setPitch((-0.05, 0.0, 0.08)[index % 3])
            self._speech_engine.say("巴布达")
        self._show_emotion(random.choice((PetState.HAPPY, PetState.SHY, PetState.SURPRISED)), 1500)
        self.show_speech(random.choice(("巴布达！", "巴——布达。", "巴布达？六毛在呢。")), 2800)

    def _trigger_long_press(self) -> None:
        """长按六毛让他原地睡觉，释放鼠标时不再触发普通点击。"""

        if not self._press_pending or self.dragging:
            return
        self._press_pending = False
        self._long_press_triggered = True
        self.daily_stats.record_sleep()
        self._set_temporary_activity("sleep", 60_000)
        self.show_speech("长按成功。六毛决定就地睡一小会儿。", 4200)

    def _track_passive_motion(self, point: QPoint) -> None:
        """跟踪无按键悬停；停留触发好奇，头部往返移动判定为摸头。"""

        zone = self._interaction_zone(point)
        self._hover_zone = zone
        if self.state is PetState.IDLE and not self.interaction_timer.isActive():
            self.hover_timer.start(700)
        if zone != "head":
            self._stroke_points.clear()
            return

        now = time.monotonic()
        self._stroke_points.append((now, point))
        while self._stroke_points and now - self._stroke_points[0][0] > 1.2:
            self._stroke_points.popleft()
        distance = sum(
            (current[1] - previous[1]).manhattanLength()
            for previous, current in zip(
                self._stroke_points,
                list(self._stroke_points)[1:],
            )
        )
        if distance >= 70 and now - self._last_stroke_reaction >= 2.0:
            self._last_stroke_reaction = now
            self._stroke_points.clear()
            self.mood.receive_affection()
            self._record_user_interaction()
            self.daily_stats.record_touch()
            state = PetState.SHY if self.mood.affinity >= 70 else PetState.HAPPY
            self._show_emotion(state, 1600)
            self.show_speech("摸摸收到。六毛的红毛都开心得翘起来啦。", 3400)

    def _trigger_hover_curiosity(self) -> None:
        """鼠标在宠物附近稳定停留时显示好奇注视。"""

        if (
            self._hover_zone
            and self.state is PetState.IDLE
            and not self.dragging
            and not self._press_pending
            and not self.interaction_timer.isActive()
        ):
            self._record_user_interaction()
            self._show_emotion(PetState.CURIOUS, 1300)
            if time.monotonic() >= self._manual_activity_until:
                self._set_temporary_activity("pointing", 5000)

    def _show_photo_bubble(self) -> None:
        """在宠物旁显示独立自拍成片，并在数秒后自动隐藏。"""

        if self._selfie_photo.isNull():
            return
        ratio = max(1.0, self.devicePixelRatioF())
        photo = self._scaled_selfie_photo(ratio)
        logical_size = QSize(
            max(1, round(photo.width() / ratio)),
            max(1, round(photo.height() / ratio)),
        )
        self.photo_bubble.setPixmap(photo)
        self.photo_bubble.setFixedSize(logical_size)
        area = self._screen_geometry()
        visible_bounds = self.mask().boundingRect()
        if visible_bounds.isEmpty():
            character_left = self.x()
            character_right = self.x() + self.width()
        else:
            character_left = self.x() + visible_bounds.left()
            character_right = self.x() + visible_bounds.right() + 1
        gap = 8
        x = character_left - self.photo_bubble.width() - gap
        if area is not None and x < area.left():
            x = character_right + gap
        y = self.y() + max(0, (self.height() - self.photo_bubble.height()) // 2)
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
        todo_menu = work_menu.addMenu("待办")
        show_todos = QAction("显示待办", self)
        show_todos.triggered.connect(self.show_compact_todos)
        todo_menu.addAction(show_todos)
        hide_todos = QAction("隐藏待办", self)
        hide_todos.triggered.connect(self.hide_compact_todos)
        todo_menu.addAction(hide_todos)
        add_paper = QAction("添加待办…", self)
        add_paper.triggered.connect(self.add_compact_todo)
        todo_menu.addAction(add_paper)
        paper_menu = work_menu.addMenu("便利贴")
        show_paper = QAction("打开便利贴", self)
        show_paper.triggered.connect(self.show_sticky_note)
        paper_menu.addAction(show_paper)
        hide_paper = QAction("隐藏便利贴", self)
        hide_paper.triggered.connect(self.hide_sticky_note)
        paper_menu.addAction(hide_paper)
        memory_action = QAction("我的时光…", self)
        memory_action.triggered.connect(self.show_time_memory)
        work_menu.addAction(memory_action)
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
        todo_menu = focus_group.addMenu("待办")
        todo_show = QAction("显示待办", self)
        todo_show.triggered.connect(self.show_compact_todos)
        todo_menu.addAction(todo_show)
        todo_hide = QAction("隐藏待办", self)
        todo_hide.triggered.connect(self.hide_compact_todos)
        todo_menu.addAction(todo_hide)
        todo_add = QAction("添加待办…", self)
        todo_add.triggered.connect(self.add_compact_todo)
        todo_menu.addAction(todo_add)
        paper_menu = focus_group.addMenu("便利贴")
        paper_show = QAction("打开便利贴", self)
        paper_show.triggered.connect(self.show_sticky_note)
        paper_menu.addAction(paper_show)
        paper_hide = QAction("隐藏便利贴", self)
        paper_hide.triggered.connect(self.hide_sticky_note)
        paper_menu.addAction(paper_hide)
        timeline_action = QAction("我的时光…", self)
        timeline_action.triggered.connect(self.show_time_memory)
        focus_group.addAction(timeline_action)

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

