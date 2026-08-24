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
- 在内存中保留最近三十轮完整聊天，并把更早内容滚动压缩为长期摘要；聊天记录由用户控制保存在本机；
- 将连接与陪伴设置收口到唯一入口，只有显式 ``user_action`` 来源才允许创建设置窗口；
- 自动评分并依次尝试本机音乐 Provider，成功后把基础控制锁定到实际播放的平台；
- 支持电脑图层、摸头工作气泡、今日/终身计时、每小时娃衣解锁、夜间限定造型及健康提醒；
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
右键菜单和附属窗口使用宠物所在显示器的 Qt 全局逻辑坐标，不混用物理像素。
"""

from __future__ import annotations

import json
import logging
import os
import random
import sys
import threading
import time
import uuid
from collections import OrderedDict, deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, QTime, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QContextMenuEvent,
    QCursor,
    QDesktopServices,
    QFont,
    QGuiApplication,
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
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QInputDialog,
    QMenu,
    QMessageBox,
    QPushButton,
    QTimeEdit,
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
from .alarm_ui import AlarmCard, AlarmCenterDialog
from .accessories import (
    ALL_OUTFITS,
    LOGIN_REWARD_OUTFIT,
    OUTFITS,
    SPECIAL_LIMITED_ACTIVITY_SPRITES,
    draw_activity_overlay,
    unlocked_outfits,
)
from .activity import (
    active_application_category,
    active_application_name,
    active_fullscreen_game,
    active_fullscreen_video,
    active_window_is_fullscreen,
)
from .behavior import (
    BehaviorModel,
    CompanionBehaviorController,
    PetMood,
    PetState,
    StateDecision,
)
from .chat import AISettingsDialog, ChatDialog, ChatHistoryDialog
from .chat_manager import (
    AgentManager,
    ChatManager,
    ManagedChatReply,
    OfflineDialogueManager,
    should_start_startup_detection,
)
from .chat_memory import (
    ChatHistoryStore,
    ConversationMemory,
)
from .companion import (
    ACTION_BY_KEY,
    APP_DISPLAY_NAME,
    CompanionModel,
    CompanionReply,
    FOOD_OPTIONS,
)
from .config import PET_NAME, PetSettings, clean_owner_nickname, save_settings, social_pet_label
from .controls import (
    CoffeeScenePrompt,
    QuickControlPanel,
    SizeControlDialog,
    WorkControlBubble,
    WorkDurationBubble,
    VisitStatusBubble,
)
from .economy import EconomyLedger
from .economy_ui import EconomyDialog
from .food_scene_ui import FoodSceneDialog
from .input_activity import system_idle_seconds, system_session_state
from .idle_classifier import IdleClassification, IdleEvidence, classify_idle
from .emotion_effects import draw_emotion_effect, emotion_effect_name
from .daily_report import render_daily_report
from .diary import DailyCompanionStats, album_directory
from .focus_analytics import BEIJING_TIMEZONE, FocusAnalyticsStore, FocusQualityTracker
from .focus_session import FocusSessionManager
from .work_report import WorkReportDialog, build_work_report
from .growth import (
    ACTION_GROUPS,
    ACTION_SPRITES,
    COMPLETE_ACTIONS,
    FOOD_LIMITED_ACTIVITIES,
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
from .music import ARTIST_MUSIC_SERVICE_LABELS, open_chen_artist_page as launch_chen_artist_page
from .resources import resource_path
from .quiet_mode import detect_quiet_mode
from .social import SocialClient
from .social_ui import (
    BuddyVisitWindow,
    IncomingVisitNotice,
    SocialEventThread,
    SocialHubDialog,
    SocialProfileThread,
    SocialSyncThread,
    SocialVisitResponseThread,
)
from .music_control import MusicControlResult, MusicController, MusicProviderManager
from .music_playback import SongPlaybackResult
from .wellness import WellnessReminderModel
from .work_timer import WorkTimerModel, format_elapsed_clock, format_work_duration
from .workflow import WorkflowError, character_is_approved, load_workflow
from .time_memory import TimeMemory
from .today_note import TimeMemoryWindow, TodayNoteWindow
from .todo_center import TodoCenterWindow


from .compact_todo import CompactTodoPanel
from .menu_model import UnifiedMenuModel, populate_qmenu
from .night_limited import night_limited_activity
from . import __version__


def clamp_global_popup_position(global_pos: QPoint, popup_size: QSize, available: QRect) -> QPoint:
    """Clamp a Qt logical global popup point to one monitor, including negatives."""

    width = max(1, int(popup_size.width()))
    height = max(1, int(popup_size.height()))
    right = available.right() - width + 1
    bottom = available.bottom() - height + 1
    x = min(max(global_pos.x(), available.left()), max(available.left(), right))
    y = min(max(global_pos.y(), available.top()), max(available.top(), bottom))
    return QPoint(x, y)


def context_menu_position_for_pet(
    event_global: QPoint,
    pet_center: QPoint,
    pet_screen_geometry: QRect | None,
    *,
    macos: bool = False,
) -> QPoint:
    """Keep a macOS context menu on the screen that owns the pet.

    On macOS with mixed-DPI external displays, a context-menu event can
    occasionally report a global point in the other display's coordinate
    space.  Using that point to choose the popup screen makes the native menu
    appear to jump between displays.  If the event point is outside the pet's
    actual screen, use the pet's global center as a stable same-screen anchor.
    Other platforms retain the precise event position.
    """

    point = QPoint(event_global)
    if macos and pet_screen_geometry is not None and not pet_screen_geometry.contains(point):
        return QPoint(pet_center)
    return point


LOGGER = logging.getLogger(__name__)
SETTINGS_SOURCE_USER_ACTION = "user_action"


DEFAULT_WALK_MOTION_FACTORS = (0.45, 0.7, 1.2, 1.65, 0.45, 0.7, 1.2, 1.65)


class IdleRecoveryDialog(QWidget):
    """Show one non-modal correction hint for an automatically classified gap.

    The class name remains for compatibility with older callers, but this is
    no longer a decision dialog.  The default classification is recorded
    before it is shown; the only action offered is a lightweight correction.
    It never raises, activates, flashes, or steals focus from the user's app.
    """

    decision_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMinimumWidth(330)
        self.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 10)
        layout.setSpacing(6)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("font-size: 13px; color: #27313d;")
        layout.addWidget(self.summary_label)
        self.detail_label = QLabel(
            "已自动记为休息；如果刚才仍在工作，可以改成专注。"
        )
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet("color: #667784; font-size: 11px;")
        layout.addWidget(self.detail_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.focus_button = QPushButton("改成专注")
        self.focus_button.setAutoDefault(False)
        self.focus_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.focus_button.clicked.connect(lambda: self._request_decision("focus"))
        buttons.addWidget(self.focus_button)
        layout.addLayout(buttons)
        self.setStyleSheet(
            "QWidget { background: rgba(255, 255, 255, 242); border: 1px solid #b9d1dc; "
            "border-radius: 10px; } QPushButton { background: #d9eeeb; color: #245965; "
            "border: 0; border-radius: 8px; padding: 5px 12px; }"
        )

    def set_away_seconds(self, seconds: int) -> None:
        """Update the post-grace-period absence shown by the hint."""

        seconds = max(1, int(seconds))
        minutes, remainder = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            duration = f"{hours} 小时 {minutes} 分钟"
        elif minutes:
            duration = f"{minutes} 分 {remainder:02d} 秒"
        else:
            duration = f"{remainder} 秒"
        self.summary_label.setText(f"刚才离开 {duration}，我先算休息啦")

    def show_hint(self, anchor: QWidget) -> None:
        """Show beside the pet without activating the native window."""

        self.adjustSize()
        screen = anchor.screen() or QApplication.primaryScreen()
        if screen is not None:
            bounds = screen.availableGeometry()
            point = anchor.mapToGlobal(QPoint(anchor.width() + 8, max(8, anchor.height() // 3)))
            x = point.x()
            y = point.y()
            if x + self.width() > bounds.right():
                x = max(bounds.left(), anchor.mapToGlobal(QPoint(-self.width() - 8, 8)).x())
            if y + self.height() > bounds.bottom():
                y = max(bounds.top(), bounds.bottom() - self.height() - 8)
            self.move(x, y)
        self.show()
        QTimer.singleShot(6500, self.hide)

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
        self._menu_external_callbacks: dict[str, Callable[[bool], object]] = {}
        self.behavior = BehaviorModel(settings)
        self.companion_behavior = CompanionBehaviorController()
        self.mood = PetMood()
        self.companion = CompanionModel(self.mood)
        self.work_timer = work_timer or WorkTimerModel(
            persist=os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1"
        )
        self.focus_session = FocusSessionManager(self.work_timer, self)
        self._rewarded_focus_blocks = self.work_timer.today_seconds() // 600
        self.daily_stats = DailyCompanionStats(
            persist=os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1"
        )
        self.time_memory = TimeMemory(
            persist=os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1"
        )
        self.economy = EconomyLedger(
            persist=os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1"
        )
        self._today_note_window: TodayNoteWindow | None = None
        self._compact_todo_panel: CompactTodoPanel | None = None
        self._restore_compact_todos_after_show = False
        self._compact_todos_manually_hidden = False
        self._restore_compact_todos_after_report = False
        self._time_memory_window: TimeMemoryWindow | None = None
        self._todo_center_window: TodoCenterWindow | None = None
        self._economy_dialog: EconomyDialog | None = None
        self._food_scene_dialog: FoodSceneDialog | None = None
        self._work_report_dialog: WorkReportDialog | None = None
        self._alarm_center_dialog: AlarmCenterDialog | None = None
        self._alarm_card: AlarmCard | None = None
        self.focus_analytics = FocusAnalyticsStore(
            persist=os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1"
        )
        self.focus_session.set_today_seconds_provider(self._shared_today_focus_seconds)
        self._focus_quality_tracker = FocusQualityTracker()
        self._active_focus_account_id = ""
        # session_seconds() is cumulative across pauses/resumes.  This cursor
        # ensures each WORKING second is credited to wages and statistics once.
        self._recorded_focus_session_seconds = (
            self.work_timer.analytics_recorded_session_seconds()
        )
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
        # Social food is a temporary shared visual moment.  Keep its expiry
        # separate from the local inventory scene so accepting a buddy's
        # drink/cake never pauses or rewrites the local focus session.
        self._social_food_activity_until = 0.0
        self._turn_paused = False
        self._last_user_interaction = time.monotonic()
        self._auto_paused_for_idle = False
        self._idle_pause_started_at: datetime | None = None
        self._pending_idle_seconds = 0
        self._idle_prompt_pending = False
        self._idle_recovery_resolved = False
        self._idle_above_threshold_samples = 0
        self._idle_context: IdleEvidence | None = None
        self._idle_hint_classification: IdleClassification | None = None
        self._idle_hint_record: dict[str, object] | None = None
        self._idle_recovery_dialog: IdleRecoveryDialog | None = None
        self._last_session_probe = {"locked": False, "sleeping": False}
        self._fullscreen_video_started_at: float | None = None
        self._pause_notice_shown = False
        self._fullscreen_hidden = False
        self._manually_hidden = False
        self._fullscreen_restore_visible: dict[QWidget, bool] = {}
        self._process_started_at = datetime.now().astimezone()
        self._sleep_after_sit = False
        self._room_quick_status = ""
        self._room_quick_status_expires_at: datetime | None = None
        self._screen_change_connected = False
        self._connected_screen: QScreen | None = None
        # Native macOS panel configuration is cached per Qt widget/native
        # handle.  Speech bubbles and quick controls are separate top-level
        # windows, so they need the same non-activating guard as the pet, but
        # repeatedly re-applying it would recreate the original focus bug.
        self._macos_native_window_configs: dict[int, tuple[int, bool]] = {}
        self._duration_layout_reflowing = False
        self._pixmaps = self._load_pixmaps()
        self._selfie_photo = self._load_selfie_photo()
        self._render_cache: OrderedDict[tuple[object, ...], QPixmap] = OrderedDict()
        self._mask_cache: OrderedDict[tuple[object, ...], QRegion] = OrderedDict()
        self.credentials = CredentialStore()
        self.ai_service = AIChatService(
            self.credentials,
            codex_path=getattr(self.settings, "codex_executable_path", ""),
        )
        self.social_client = SocialClient(
            persist_tokens=os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1"
        )
        self._social_dialog: SocialHubDialog | None = None
        self._social_thread: SocialSyncThread | None = None
        self._social_event_threads: list[SocialEventThread] = []
        self._social_profile_threads: list[SocialProfileThread] = []
        self._owner_nickname_sync_key: tuple[str, str] | None = None
        self._owner_nickname_sync_inflight = False
        # Economy events are an append-only local ledger.  A complete,
        # idempotent replay is used when an account becomes available so a
        # transient offline period cannot leave the leaderboard behind the
        # local supply-station balance forever.
        self._economy_sync_lock = threading.Lock()
        self._economy_sync_inflight = False
        self._economy_sync_pending = False
        self._economy_sync_user_id = ""
        self._personal_outfit_sync_pending = False
        self._login_reward_unlocked = False
        self._buddy_visit_window = BuddyVisitWindow()
        self.visit_status_bubble = VisitStatusBubble()
        self._seen_visit_ids: set[str] = set()
        self._shown_active_visit_ids: set[str] = set()
        self._seen_buddy_request_ids: set[str] = set()
        self._muted_buddy_ids: set[str] = set()
        self._incoming_visit_notice: IncomingVisitNotice | None = None
        self._incoming_visit_queue: list[dict] = []
        self._incoming_visit_response_threads: list[SocialVisitResponseThread] = []
        self._chat_dialog: ChatDialog | None = None
        self._chat_streaming_active = False
        # A failed request can finish and re-enable the send button before a
        # second queued click is delivered.  Keep one short-lived submission
        # fence at the UI boundary so one user action cannot duplicate a chat
        # turn or its local history entry.  Intentional repeats remain allowed
        # after the fence expires.
        self._last_chat_submission = ""
        self._last_chat_submission_at = 0.0
        self._chat_submission_active = False
        self._chat_memory = ConversationMemory(
            max_recent_rounds=30,
            account_scoped=os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1",
        )
        self._chat_history = ChatHistoryStore(
            account_scoped=os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1",
        )
        self._chat_history_dialog: ChatHistoryDialog | None = None
        self.agent_manager = AgentManager(
            self.settings,
            self.credentials,
            self,
            ai_service=self.ai_service,
        )
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
            todo_now_provider=self.time_memory.now,
            local_command_handler=self._handle_chat_local_command,
        )
        # All account-aware local stores now exist, so a restored Supabase
        # session can safely select its namespace during startup.
        self._switch_focus_account(self._current_social_user_id())
        self.music_provider_manager = MusicProviderManager(self.settings)
        self.music_controller = MusicController(
            self.settings,
            self.music_provider_manager,
            self,
        )
        self.agent_manager.status_changed.connect(self._agent_status_changed)
        self.chat_manager.reply_ready.connect(self._managed_chat_reply)
        self.chat_manager.reply_started.connect(self._chat_reply_started)
        self.chat_manager.reply_delta.connect(self._chat_reply_delta)
        self.chat_manager.action_executed.connect(self._chat_action_executed)
        self.chat_manager.busy_changed.connect(self._chat_busy_changed)
        self.chat_manager.notice.connect(self._chat_notice)
        self.music_controller.result_ready.connect(self._music_control_result)
        self.focus_session.changed.connect(self._focus_snapshot_changed)
        self._action_sequence_id = 0
        self._last_announced_hour = ""
        self._ambient_activity = "none"
        self._night_limited_activity = ""
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
        self.coffee_scene_prompt = CoffeeScenePrompt()
        self.work_duration_bubble = WorkDurationBubble()
        self._qt_application = QApplication.instance()
        if self._qt_application is not None:
            self._qt_application.installEventFilter(self)
        self.work_controls.start_requested.connect(self._start_work_from_control)
        self.work_controls.pause_requested.connect(self.pause_work_timer)
        self.work_controls.resume_requested.connect(self._resume_work_from_control)
        self.work_controls.finish_requested.connect(self.finish_work_timer)
        self.coffee_scene_prompt.continue_requested.connect(self._continue_after_coffee_scene)
        self.coffee_scene_prompt.finish_requested.connect(self._finish_after_coffee_scene)
        self.quick_panel = QuickControlPanel(self._pet_name())
        self.quick_panel.set_window_behavior_callback(self._apply_macos_window_behavior)
        self.quick_panel.chat_requested.connect(self.prompt_dialogue)
        self.quick_panel.work_requested.connect(self._quick_work_action)
        self.quick_panel.work_report_requested.connect(self.show_work_report)
        self.quick_panel.chen_artist_requested.connect(self.open_chen_artist_page)
        self.quick_panel.artist_music_service_requested.connect(self.set_artist_music_service)
        self.quick_panel.todo_requested.connect(self.show_todo_center)
        self.quick_panel.social_requested.connect(self.open_social_hub)
        self.quick_panel.music_control_requested.connect(self.control_music)
        self.quick_panel.food_requested.connect(self._quick_food_action)
        self.quick_panel.supply_requested.connect(self.show_food_scene_dialog)
        self.quick_panel.size_requested.connect(self.open_size_control)
        self.quick_panel.rename_requested.connect(self.rename_pet)
        self.quick_panel.content_update_requested.connect(
            lambda: self._invoke_menu_external("content_update")
        )
        self.quick_panel.program_update_requested.connect(
            lambda: self._invoke_menu_external("program_update")
        )
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

        self.night_limited_timer = QTimer(self)
        self.night_limited_timer.setInterval(15_000)
        self.night_limited_timer.timeout.connect(self._night_limited_tick)
        self.night_limited_timer.start()

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

        self.food_scene_timer = QTimer(self)
        self.food_scene_timer.setSingleShot(True)
        self.food_scene_timer.timeout.connect(self._food_scene_timeout)

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
        # On macOS, repeatedly touching an NSWindow's level/collection
        # behavior can cause AppKit to reconsider the owning application as
        # the active app.  That is especially disruptive for a desktop pet:
        # the user can click Word or a browser, only for Lili to take the
        # application focus back a few seconds later.  The native behavior is
        # applied once after show (and again only when the window flags are
        # deliberately changed); there is no reason to poll it on macOS.
        if sys.platform != "darwin":
            self.topmost_timer.start()

        self._last_social_heartbeat_at = 0.0
        self._social_heartbeat_due = True
        self.social_timer = QTimer(self)
        # Keep incoming visits and food interactions responsive without
        # requiring the study-room window to be opened or manually refreshed.
        # Heartbeats are still throttled independently below, so this is a
        # lightweight dashboard poll rather than a heartbeat every 5 seconds.
        self.social_timer.setInterval(5_000)
        self.social_timer.timeout.connect(self._social_tick)
        self.social_timer.start()
        self.social_sync_timer = QTimer(self)
        self.social_sync_timer.setSingleShot(True)
        self.social_sync_timer.timeout.connect(self._social_tick)
        if self.social_client.signed_in:
            QTimer.singleShot(2500, self._social_tick)

        # A real video/PPT fullscreen window must own the whole display.  Poll
        # the coarse, privacy-preserving geometry signal separately from the
        # work-idle policy so the pet and its accessories disappear quickly,
        # then return with exactly the visibility state they had before.
        self.fullscreen_poll_timer = QTimer(self)
        self.fullscreen_poll_timer.setInterval(1000)
        self.fullscreen_poll_timer.timeout.connect(self._sync_fullscreen_visibility)
        self.fullscreen_poll_timer.start()

        # Keep a low-frequency, cross-platform system probe.  It is the one
        # place that may auto-pause: 10 minutes of aggregate keyboard+mouse
        # silence, a verified lock/sleep boundary, or a known player/browser
        # video in real fullscreen. None of those paths ever auto-resume.
        self.input_idle_timer = QTimer(self)
        self.input_idle_timer.setInterval(5_000)
        self.input_idle_timer.timeout.connect(self._check_input_idle)
        self.input_idle_timer.start()
        self.idle_recovery_timer = QTimer(self)
        self.idle_recovery_timer.setSingleShot(True)
        self.idle_recovery_timer.timeout.connect(self._ask_idle_recovery)

        self._sync_hourly_outfit(announce=False)
        self.set_state(PetState.IDLE)
        self._night_limited_tick()
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
        """Restore a saved global position, or use the primary-screen default."""

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
        food_scene = self.economy.active_food_scene() or {}
        scene_activity = {
            "coffee": "work-study",
            "expensive_coffee": "deep-focus",
            "milk_tea": "milk-tea",
            "cake": "feast",
            "tea": "tea",
        }.get(str(food_scene.get("item_key") or ""))
        food_scene_active = False
        if scene_activity and not bool(food_scene.get("expired")):
            # Tea is intentionally a short companion animation; its stored
            # scene remains a diary event but does not pin the sprite forever.
            if str(food_scene.get("item_key") or "") != "tea" or activity == "tea":
                activity = scene_activity
                food_scene_active = True
        # A social food interaction is not stored in the local food-scene
        # ledger: it belongs to both users and must not affect inventory or
        # focus accounting.  It still needs the same rendering precedence so
        # a permanent hourly outfit cannot hide the food-only sprite.
        if (
            activity in FOOD_LIMITED_ACTIVITIES
            and time.monotonic() < self._social_food_activity_until
        ):
            food_scene_active = True
        if self.work_timer.is_running and activity in {"", "none"}:
            activity = "computer"
        composed = draw_activity_overlay(
            composed,
            activity,
            self.settings.equipped_outfit,
            self._effect_phase,
            food_scene=food_scene_active,
        )
        visible = self._blend_activity_transition(composed)
        self.label.setPixmap(visible)
        effect_key = self._effect_phase if emotion_effect_name(display_state) else -1
        overlay_key = hash((activity, self.settings.equipped_outfit, food_scene_active, self._effect_phase % 2))
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

        valid_activities = set(ACTION_SPRITES) | set(SPECIAL_LIMITED_ACTIVITY_SPRITES)
        next_activity = activity if activity in valid_activities else "none"
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
        self._update_work_duration_bubble()
        if self._compact_todo_panel is not None:
            if self._restore_compact_todos_after_show:
                self._show_nonactivating(
                    self._compact_todo_panel,
                    always_on_top=bool(
                        self.settings.always_on_top
                        or getattr(self.settings, "today_note_always_on_top", False)
                    ),
                )
        self._position_accessories()
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

    @staticmethod
    def _raise_accessory(widget: QWidget) -> None:
        """Raise a pet accessory only where it is known to be non-activating.

        On macOS, an order change for a no-focus accessory can still make the
        owning application frontmost.  The native panel is ordered by its
        level when shown, so animation and refresh paths must not call
        ``raise_`` there.
        """

        if sys.platform != "darwin":
            widget.raise_()

    def _show_nonactivating(self, widget: QWidget, *, always_on_top: bool | None = None) -> None:
        """Configure a pet surface before showing it on macOS.

        Applying the NSPanel style after ``show()`` is too late on some Qt
        builds: AppKit briefly activates Lili, and repeated speech/status
        updates can then steal ChatGPT's text focus.  Creating/configuring the
        native handle first makes every passive surface display-only.
        """

        # A focus/session refresh can request a passive surface after the
        # fullscreen poll has already hidden it (the duration bubble is
        # refreshed every timer tick).  Do not let that refresh punch through
        # a video or presentation fullscreen window.
        if (
            getattr(self, "_manually_hidden", False)
            or getattr(self, "_fullscreen_hidden", False)
        ) and widget in self._fullscreen_surfaces():
            widget.hide()
            return

        if sys.platform == "darwin":
            self._apply_macos_window_behavior(widget, always_on_top=always_on_top)
        widget.show()

    def _apply_macos_window_behavior(
        self,
        widget: QWidget | None = None,
        *,
        always_on_top: bool | None = None,
    ) -> None:
        """以非激活的 NSPanel 浮动层级显示桌宠。

        ``WindowDoesNotAcceptFocus`` 是 Qt 层面的保证，但在 macOS 上
        仍需要把 Qt 创建的原生 NSWindow/NSPanel 标记为
        ``NSNonactivatingPanelMask``。否则窗口虽然没有键盘焦点，AppKit
        仍可能在重新设置浮动层级时把 Lili 重新变成前台应用。
        """

        if QApplication.platformName().casefold() in {"offscreen", "minimal"}:
            return
        target = widget or self
        topmost = (
            bool(self.settings.always_on_top)
            if always_on_top is None
            else bool(always_on_top)
        )
        try:
            import ctypes

            objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
            objc.sel_registerName.restype = ctypes.c_void_p
            objc.sel_registerName.argtypes = [ctypes.c_char_p]
            objc.objc_msgSend.restype = ctypes.c_void_p
            objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            native_handle = int(target.winId())
            cache_key = id(target)
            cache_value = (native_handle, topmost)
            if self._macos_native_window_configs.get(cache_key) == cache_value:
                return
            view = ctypes.c_void_p(native_handle)
            window = objc.objc_msgSend(view, objc.sel_registerName(b"window"))
            if not window:
                return
            send_integer = objc.objc_msgSend
            send_integer.restype = None
            send_integer.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_longlong]
            level = 3 if topmost else 0  # NSFloatingWindowLevel / normal
            send_integer(window, objc.sel_registerName(b"setLevel:"), level)
            behavior = (1 << 0) | (1 << 8) if topmost else 0
            send_integer(
                window,
                objc.sel_registerName(b"setCollectionBehavior:"),
                behavior,
            )
            # Qt::Tool normally creates an NSPanel on macOS.  Explicitly add
            # the non-activating panel style so clicking another application
            # cannot be undone by AppKit's panel activation rules.  This is a
            # style-mask operation only; it never calls activate/raise/front.
            send_integer.restype = ctypes.c_ulonglong
            send_integer.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            style_mask = int(
                send_integer(window, objc.sel_registerName(b"styleMask"))
                or 0
            )
            send_integer.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_ulonglong,
            ]
            send_integer(
                window,
                objc.sel_registerName(b"setStyleMask:"),
                style_mask | (1 << 7),  # NSWindowStyleMaskNonactivatingPanel
            )
            send_bool = objc.objc_msgSend
            send_bool.restype = None
            send_bool.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
            send_bool(window, objc.sel_registerName(b"setHidesOnDeactivate:"), False)
            send_bool(window, objc.sel_registerName(b"setIgnoresMouseEvents:"), False)
            # NSPanel exposes this selector.  Guard it so the fallback still
            # works if a future Qt build gives us a plain NSWindow instead.
            send_pointer = objc.objc_msgSend
            send_pointer.restype = ctypes.c_void_p
            send_pointer.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
            ]
            selector = objc.sel_registerName(b"setBecomesKeyOnlyIfNeeded:")
            responds = objc.sel_registerName(b"respondsToSelector:")
            if send_pointer(window, responds, selector):
                send_bool(window, selector, True)
            self._macos_native_window_configs[cache_key] = cache_value
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
        if sys.platform == "darwin":
            self._apply_macos_window_behavior(self, always_on_top=enabled)
        for bubble, visible, bubble_position in bubble_states:
            bubble.setWindowFlags(self._ambient_window_flags())
            bubble.move(bubble_position)
            if visible:
                self._show_nonactivating(bubble, always_on_top=enabled)
        if self._compact_todo_panel is not None:
            self._compact_todo_panel.set_companion_topmost(enabled)
            if self._compact_todo_panel.isVisible():
                self._position_compact_todos()
                if sys.platform == "darwin":
                    self._apply_macos_window_behavior(
                        self._compact_todo_panel,
                        always_on_top=enabled or bool(
                            getattr(self.settings, "today_note_always_on_top", False)
                        ),
                    )
        if sys.platform == "darwin":
            self._apply_macos_window_behavior(
                self.work_duration_bubble,
                always_on_top=enabled,
            )
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
        """人物移动时让所有附属窗口跟随固定的宠物窗口位置。"""

        super().moveEvent(event)
        self._position_accessories()

    def hideEvent(self, event: QHideEvent) -> None:
        """隐藏宠物时同步隐藏照片和文字气泡。"""

        self.photo_bubble.hide()
        self.speech_bubble.hide()
        self.work_controls.hide()
        self.coffee_scene_prompt.hide()
        self.work_duration_bubble.hide()
        self.visit_status_bubble.hide()
        self.quick_panel.hide()
        if self._compact_todo_panel is not None:
            self._restore_compact_todos_after_show = self._compact_todo_panel.isVisible()
            self._compact_todo_panel.hide()
        super().hideEvent(event)

    def hide_pet(self) -> None:
        """Hide the pet and every detached accessory until explicitly shown."""

        self._manually_hidden = True
        # These are top-level windows, not children of PetWindow. Hide them
        # explicitly so a later focus/session refresh cannot leave the live
        # duration badge behind on the desktop.
        for widget in self._fullscreen_surfaces():
            if widget is not self:
                widget.hide()
        self.hide()

    def show_pet(self) -> None:
        """Explicitly show the pet again, respecting an active full-screen app."""

        self._manually_hidden = False
        if active_window_is_fullscreen():
            self._sync_fullscreen_visibility()
            return
        self.show()

    def _fullscreen_surfaces(self) -> list[QWidget]:
        """Return the pet and every passive surface that can cover fullscreen."""

        surfaces: list[QWidget] = [
            self,
            self.quick_panel,
            self.work_controls,
            self.coffee_scene_prompt,
            self.work_duration_bubble,
            self.speech_bubble,
            self.photo_bubble,
            self.visit_status_bubble,
        ]
        for optional in (
            self._compact_todo_panel,
            self._alarm_card,
            self._idle_recovery_dialog,
        ):
            if optional is not None:
                surfaces.append(optional)
        return list(dict.fromkeys(surfaces))

    def _sync_fullscreen_visibility(self) -> None:
        """Temporarily yield the display to any real fullscreen foreground app."""

        fullscreen = bool(active_window_is_fullscreen())
        if fullscreen:
            if not self._fullscreen_hidden:
                self._fullscreen_restore_visible = {
                    widget: bool(widget.isVisible())
                    for widget in self._fullscreen_surfaces()
                }
                self._fullscreen_hidden = True
            # Re-hide on every poll as a defensive measure. Some passive
            # widgets (especially WorkDurationBubble) update their own
            # visibility from a live FocusSession snapshot after the first
            # fullscreen transition.
            for widget in self._fullscreen_surfaces():
                if widget.isVisible():
                    widget.hide()
            return

        if not self._fullscreen_hidden:
            return
        restore = self._fullscreen_restore_visible
        self._fullscreen_restore_visible = {}
        self._fullscreen_hidden = False
        for widget, was_visible in restore.items():
            if was_visible and not self._manually_hidden:
                self._show_nonactivating(widget)
        self._position_accessories()

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭宠物时保存计时并停止 Agent、音乐控制及独立气泡窗口。"""

        if self._qt_application is not None:
            self._qt_application.removeEventFilter(self)
        self.fullscreen_poll_timer.stop()
        self.shutdown_work_timer()
        self.chat_manager.shutdown()
        if self._chat_history_dialog is not None:
            self._chat_history_dialog.close()
        self.music_controller.shutdown()
        self.photo_bubble.close()
        self.speech_bubble.close()
        self.coffee_scene_prompt.close()
        self.work_duration_bubble.close()
        self.visit_status_bubble.close()
        self._buddy_visit_window.close()
        if self._today_note_window is not None:
            self._today_note_window.close()
        if self._compact_todo_panel is not None:
            self._compact_todo_panel.close()
        if self._time_memory_window is not None:
            self._time_memory_window.close()
        if self._todo_center_window is not None:
            self._todo_center_window.close()
        if self._alarm_center_dialog is not None:
            self._alarm_center_dialog.close()
        if self._alarm_card is not None:
            self._close_alarm_card()
        if self._economy_dialog is not None:
            self._economy_dialog.close()
        if self._work_report_dialog is not None:
            self._work_report_dialog.close()
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
        QTimer.singleShot(0, self._position_accessories)

    def _on_dpi_changed(self, _dpi: float) -> None:
        """显示器缩放发生变化时刷新当前帧。"""

        self._render_cache.clear()
        QTimer.singleShot(0, self._refresh_pixmap)
        QTimer.singleShot(0, self._position_accessories)

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
        """Forget the current idle episode and close its one-shot hint."""

        self._auto_paused_for_idle = False
        self._idle_prompt_pending = False
        self._idle_recovery_resolved = False
        self._idle_above_threshold_samples = 0
        self._pending_idle_seconds = 0
        self._idle_pause_started_at = None
        self._idle_context = None
        self._idle_hint_classification = None
        self._idle_hint_record = None
        if hasattr(self, "idle_recovery_timer"):
            self.idle_recovery_timer.stop()
        if self._idle_recovery_dialog is not None:
            self._idle_recovery_dialog.hide()

    def _idle_history_path(self) -> Path:
        """Return the local, non-synced idle classification history path."""

        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / ".desktop_pet"
        return root / "Lili" / "idle-classification-history.json"

    def _write_idle_history(self, record: dict[str, object]) -> None:
        """Atomically retain a small, user-local review history."""

        path = self._idle_history_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            history: list[dict[str, object]] = []
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    history = [item for item in raw if isinstance(item, dict)][-199:]
            history.append(record)
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(history[-200:], ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            LOGGER.debug("无法保存离开分类记录: %s", exc)

    def _capture_idle_context(self) -> IdleEvidence:
        """Capture only coarse foreground/session evidence at the threshold."""

        app_name = active_application_name()
        category = active_application_category()
        session = system_session_state()
        media_playing = False
        player = getattr(self, "_media_player", None)
        if player is not None:
            try:
                state = player.playbackState()
                media_playing = "PlayingState" in str(state)
            except Exception:
                media_playing = False
        return IdleEvidence(
            app_name=app_name,
            app_category=category,
            locked=bool(session.get("locked")),
            sleeping=bool(session.get("sleeping")),
            fullscreen=bool(active_window_is_fullscreen()),
            media_playing=media_playing,
            user_rule=str(
                getattr(self.settings, "idle_classification_rules", {}).get(
                    app_name.casefold().strip() or category,
                    "",
                )
            ),
        )

    def _classify_idle_episode(self, seconds: int) -> IdleClassification:
        evidence = self._idle_context or self._capture_idle_context()
        classification = classify_idle(evidence)
        record: dict[str, object] = {
            "id": str(time.time_ns()),
            "created_at": datetime.now().astimezone().isoformat(),
            "away_seconds": max(0, int(seconds)),
            "decision": classification.decision,
            "confidence": classification.confidence,
            "reason": classification.reason,
            "app_key": classification.app_key,
            "corrected_to": None,
        }
        self._idle_hint_classification = classification
        self._idle_hint_record = record
        self._write_idle_history(record)
        return classification

    def _update_idle_history_correction(self, decision: str) -> None:
        record = self._idle_hint_record
        if not record:
            return
        record["corrected_to"] = decision
        record["corrected_at"] = datetime.now().astimezone().isoformat()
        self._write_idle_history(record)

    def _save_idle_app_rule(self, app_key: str, decision: str) -> None:
        if not app_key or decision not in {"rest", "focus"}:
            return
        rules = dict(getattr(self.settings, "idle_classification_rules", {}) or {})
        rules[app_key] = decision
        self.settings.idle_classification_rules = rules
        save_settings(self.settings)

    def _complete_idle_episode(self, decision: str) -> None:
        """Commit one classification; no prompt is needed afterwards."""

        if self._idle_recovery_resolved:
            return
        seconds = max(1, int(self._pending_idle_seconds))
        if decision == "focus":
            self.focus_analytics.record_session(
                seconds,
                started_at=self._idle_pause_started_at or datetime.now().astimezone(),
                completed=False,
                away_count=1,
                task=str((self.focus_analytics.current_task() or {}).get("title", "")),
            )
        self._idle_recovery_resolved = True
        self._pending_idle_seconds = 0
        self._idle_pause_started_at = None

    def _check_input_idle(self) -> None:
        """Apply the only automatic pause rules; never resume from input."""

        session = system_session_state()
        locked = bool(session.get("locked"))
        sleeping = bool(session.get("sleeping"))
        self._last_session_probe = {"locked": locked, "sleeping": sleeping}

        if self.work_timer.is_running:
            if sleeping:
                self._focus_quality_tracker.note_away()
                self.pause_work_timer(reason="sleep")
                return
            if locked:
                self._focus_quality_tracker.note_away()
                self.pause_work_timer(reason="lock")
                return

            # A real player/browser video or known game fullscreen counts, and
            # it must remain fullscreen for a few seconds to avoid a false
            # transition while switching apps. Ordinary maximised windows are
            # excluded by the native fullscreen detector.
            fullscreen_video = active_fullscreen_video()
            fullscreen_game = active_fullscreen_game()
            if bool(getattr(self.settings, "auto_pause_on_fullscreen_video", True)) and (
                fullscreen_video or fullscreen_game
            ):
                if self._fullscreen_video_started_at is None:
                    self._fullscreen_video_started_at = time.monotonic()
                elif time.monotonic() - self._fullscreen_video_started_at >= 4.0:
                    self._focus_quality_tracker.note_away()
                    self.pause_work_timer(reason="fullscreen_video")
                    self._fullscreen_video_started_at = None
                    return
            else:
                self._fullscreen_video_started_at = None

            threshold = max(300, int(getattr(self.settings, "idle_pause_seconds", 600)))
            if bool(getattr(self.settings, "auto_pause_on_idle", True)) and system_idle_seconds() >= threshold:
                self._focus_quality_tracker.note_away()
                self.pause_work_timer(reason="idle_10m")
                return
            return

        # Input only proves that the user is back.  It is never a resume
        # command.  Show one non-modal hint for an idle pause; the state stays
        # paused until the user presses the explicit Continue action.
        if (
            self.work_timer.has_active_session
            and self.work_timer.pause_reason == "idle_10m"
            and system_idle_seconds() < max(300, int(getattr(self.settings, "idle_pause_seconds", 600)))
            and not self._pause_notice_shown
        ):
            self._pause_notice_shown = True
            self.show_speech("回来啦，刚才十分钟没动，我帮你停表了。点‘继续工作’再开。", 6200)

    def _ask_idle_recovery(self) -> None:
        """Automatically classify once; show a single hint only if uncertain."""

        self._idle_prompt_pending = False
        if not self._auto_paused_for_idle or self._idle_recovery_resolved:
            return
        seconds = max(1, int(self._pending_idle_seconds))
        classification = self._classify_idle_episode(seconds)
        if classification.confidence >= 0.75:
            self._complete_idle_episode(classification.decision)
            return
        # Low confidence defaults to rest, but leaves one reversible hint.
        self._complete_idle_episode("rest")
        if self._idle_recovery_dialog is None:
            self._idle_recovery_dialog = IdleRecoveryDialog(self)
            self._idle_recovery_dialog.decision_requested.connect(self._resolve_idle_recovery)
        self._idle_recovery_dialog.set_away_seconds(seconds)
        self._idle_recovery_dialog.show_hint(self)

    def _resolve_idle_recovery(self, decision: str) -> None:
        """Apply the only supported correction from the one-shot hint."""

        if decision != "focus" or self._idle_hint_record is None:
            return
        seconds = max(1, int(self._idle_hint_record.get("away_seconds", 0)))
        self.focus_analytics.record_session(
            seconds,
            started_at=datetime.now().astimezone() - timedelta(seconds=seconds),
            completed=False,
            away_count=1,
            task=str((self.focus_analytics.current_task() or {}).get("title", "")),
        )
        classification = self._idle_hint_classification
        if classification is not None:
            self._save_idle_app_rule(classification.app_key, "focus")
        self._update_idle_history_correction("focus")
        if self._idle_recovery_dialog is not None:
            self._idle_recovery_dialog.hide()
        self._idle_hint_record = None

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
        bottom = area.bottom() - self.height() + 1
        if self._duration_space_reserved():
            bottom -= self.work_duration_bubble.height() + 5
        y = min(max(position.y(), area.top()), max(area.top(), bottom))
        return QPoint(x, y)

    def _duration_space_reserved(self) -> bool:
        """Whether the pet should keep a stable slot for the live timer."""

        return bool(
            getattr(self.settings, "show_work_duration", True)
            and hasattr(self, "work_duration_bubble")
            and self.work_duration_bubble.isVisible()
        )

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
        self._position_accessories()

    def return_to_primary_screen(self) -> None:
        """将宠物重新放到主屏幕右下角。"""

        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(self._constrained_position(QPoint(
            area.right() - self.width() - 24,
            area.bottom() - self.height() - 12,
        )))

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
        self._show_nonactivating(self.speech_bubble)
        self._position_speech_bubble()
        self.speech_timer.start(max(1200, duration_ms))

    def feed_pet(self, food_key: str) -> CompanionReply:
        """喂给 Lili 一种菜单食物，并播放对应表情与文字反馈。"""

        self._record_user_interaction()
        reply = self.companion.feed(food_key)
        if food_key in {"coffee", "tea"}:
            food_activity = {"coffee": "work-study", "tea": "tea"}[food_key]
            self._set_temporary_activity(food_activity, 28_000)
            self._play_action_sequence(
                (PetState.SIT, PetState.HAPPY, PetState.SIT),
                3000,
            )
        else:
            food_activity = {"apple": "bunny-carrot", "cookie": "feast", "milk": "bunny-carrot"}.get(food_key)
            if food_activity:
                self._set_temporary_activity(food_activity, 28_000)
            self._show_emotion(reply.state, 2200)
        self.show_speech(
            f"{reply.text}\n精力 {self.mood.energy} · 饱食 {self.mood.fullness}",
            5200,
        )
        return reply


    def _todo_choices_for_food(self) -> list[dict[str, object]]:
        """Expose the existing Todo store to food scenes without duplicating Todo data."""
        result: list[dict[str, object]] = []
        try:
            today = self.time_memory.todos.today()
        except Exception:
            today = []
        items = getattr(today, "items", today)
        if callable(items):
            items = items()
        if isinstance(items, dict):
            items = list(items.values())
        for task in items or []:
            title = str(getattr(task, "title", "") or "").strip()
            if not title:
                continue
            result.append({
                "id": str(getattr(task, "id", "") or ""),
                "title": title,
                "completed": bool(getattr(task, "completed", False)),
            })
        return result

    def show_food_scene_dialog(self) -> None:
        """Open/reuse the taskbar-capable 六毛补给站 window."""
        self._record_user_interaction()
        if self._food_scene_dialog is None:
            self._food_scene_dialog = FoodSceneDialog(
                self.economy,
                self._todo_choices_for_food(),
                witness_choices=self._achievement_witness_choices,
                achievement_submitter=self._submit_achievement_witness,
                buddy_choices=self._achievement_witness_choices,
                cake_share_submitter=self._submit_cake_share,
            )
            self._food_scene_dialog.scene_requested.connect(self._start_food_scene)
        else:
            self._food_scene_dialog.todo_choices = self._todo_choices_for_food()
        self._food_scene_dialog.refresh()
        if self._food_scene_dialog.isMinimized():
            self._food_scene_dialog.showNormal()
        self._food_scene_dialog.show()
        self._food_scene_dialog.raise_()
        self._food_scene_dialog.activateWindow()

    def _achievement_witness_choices(self) -> list[dict[str, object]]:
        """Return the currently synced accepted buddies for manual selection."""

        dialog = self._social_dialog
        data = (getattr(dialog, "data", {}) or {}) if dialog is not None else {}
        if not data and getattr(self.social_client, "signed_in", False):
            try:
                data = self.social_client.dashboard(allow_cache=True) or {}
            except Exception:
                data = {}
        return [
            dict(item) for item in (data.get("buddies") or [])
            if isinstance(item, dict) and not bool(item.get("is_self"))
        ]

    def _submit_achievement_witness(self, name: str, note: str, witness_ids: list[str]) -> dict[str, object]:
        """Submit a manually addressed achievement claim to the shared RPC."""

        if not getattr(self.social_client, "signed_in", False):
            raise ValueError("请先登录搭子自习室，再邀请搭子见证成果。")
        if len({str(value).strip() for value in witness_ids if str(value).strip()}) != 2:
            raise ValueError("请选择两名不同的搭子。")
        result = self.social_client.rpc(
            "lili_submit_achievement",
            {
                "p_kind": "其他成果",
                "p_name": str(name).strip()[:90],
                "p_amount": 200,
                "p_note": str(note).strip()[:160],
                "p_witness_ids": [str(value).strip() for value in witness_ids],
            },
        )
        if isinstance(result, dict):
            return result
        return {"status": "pending"}

    def _submit_cake_share(self, message: str, buddy_ids: list[str]) -> dict[str, object]:
        """Create one server-authoritative group cake event for 1–3 buddies."""

        if not self.social_client.signed_in:
            raise ValueError("请先登录搭子自习室，再邀请搭子分享蛋糕。")
        ids = []
        for value in buddy_ids:
            clean = str(value or "").strip()
            if clean and clean not in ids:
                ids.append(clean)
        if not 1 <= len(ids) <= 3:
            raise ValueError("小蛋糕需要邀请 1～3 位好友。")
        status = self.economy.cake_share_status()
        if not status.get("can_start"):
            raise ValueError("今天已经发起过蛋糕分享，或仓库里暂时没有小蛋糕。")
        result = self.social_client.rpc(
            "lili_create_cake_share",
            {
                "p_recipient_ids": ids,
                "p_message": str(message or "").strip()[:160] or "今天值得庆祝一下。",
            },
        )
        if not isinstance(result, dict):
            raise ValueError("服务器没有返回蛋糕分享结果。")
        share_id = str(result.get("share_id") or result.get("id") or uuid.uuid4().hex)
        consumed = self.economy.consume_cake_for_share(
            ids,
            message=str(message or "").strip()[:160],
            operation_key=share_id,
        )
        if consumed is None:
            raise ValueError("蛋糕分享已送达，但本机库存状态没有完成扣除，请重新同步钱袋。")
        self._sync_economy_events([dict(consumed.get("event") or {})])
        # The host joins the same celebration immediately.  This is a social
        # pose only: it must not create a second local food scene or pause
        # focus, even when the host is currently working.
        self._set_social_food_activity("cake", 0)
        self.show_speech(f"🍰 已请 {len(ids)} 位搭子吃蛋糕。\n{str(message or '').strip()[:100] or '今天值得庆祝一下。'}", 6200)
        return result

    def _start_food_scene(
        self,
        item_key: str,
        duration_minutes: int,
        todo_id: str,
        todo_title: str,
        *,
        consume_inventory: bool = True,
        source: str = "food_scene",
    ) -> bool:
        """Turn a food item into a real focus/rest/companion scene."""
        item_key = str(item_key or "").strip()
        if item_key == "cake" and consume_inventory:
            self.show_food_scene_dialog()
            return False
        snapshot = self.focus_session.snapshot()
        status = str(getattr(snapshot, "status", "") or "")
        start_error = self.economy.food_scene_start_error(
            item_key, consume_inventory=consume_inventory,
        )
        if start_error == "inventory":
            spec = self.economy.catalog().get(item_key) or {}
            name = str(spec.get("name") or item_key)
            self.show_speech(
                f"仓库里没有「{name}」。补给站已经按最新库存刷新，请先购买或等待补给。",
                5200,
            )
            if self._food_scene_dialog is not None:
                self._food_scene_dialog.refresh()
                self._food_scene_dialog.show()
                self._food_scene_dialog.raise_()
            return False
        if start_error == "active_scene":
            current = self.economy.active_food_scene() or {}
            current_name = str(current.get("name") or "上一段补给场景")
            self.show_speech(
                f"六毛正在{current_name}场景里，先等这一段结束再用新的补给。",
                5200,
            )
            if self._food_scene_dialog is not None:
                self._food_scene_dialog.refresh()
                self._food_scene_dialog.show()
                self._food_scene_dialog.raise_()
            return False
        if start_error == "invalid_item":
            self.show_speech("这个补给暂时不能使用。", 4200)
            return False
        resume_after_rest = item_key == "milk_tea" and status == "focus"
        if resume_after_rest:
            self.pause_work_timer(reason="food")
        coffee_scene_started_work_timer = bool(
            item_key in {"coffee", "expensive_coffee"}
            and not self.work_timer.is_running
        )
        scene_metadata: dict[str, object] | None = None
        if item_key == "milk_tea":
            scene_metadata = {"resume_work": resume_after_rest}
        elif item_key in {"coffee", "expensive_coffee"}:
            scene_metadata = {
                "coffee_scene_started_work_timer": coffee_scene_started_work_timer,
            }
            if item_key == "expensive_coffee":
                # If coffee starts a new work episode, a previously paused
                # episode must not be inherited. When the user is already
                # working, count the threshold from the moment it is used.
                scene_metadata["work_episode_seconds_at_start"] = (
                    self.work_timer.episode_seconds()
                    if self.work_timer.is_running
                    else 0
                )
        result = self.economy.start_food_scene(
            item_key,
            duration_minutes=int(duration_minutes) if int(duration_minutes or 0) > 0 else None,
            todo_id=todo_id,
            todo_title=todo_title,
            consume_inventory=consume_inventory,
            source=source,
            scene_metadata=scene_metadata,
        )
        if result is None:
            if resume_after_rest:
                self.start_work_timer()
            self.show_speech("补给状态刚发生变化，请重新打开仓库后再试。", 4800)
            if self._food_scene_dialog is not None:
                self._food_scene_dialog.refresh()
                self._food_scene_dialog.show()
                self._food_scene_dialog.raise_()
            return False
        scene = dict(result.get("scene") or {})
        self._sync_economy_events([dict(result.get("event") or {})])
        if item_key in {"coffee", "expensive_coffee"}:
            if todo_title:
                self._set_focus_task(todo_title, 0)
            self.start_work_timer()
            activity = "deep-focus" if item_key == "expensive_coffee" else "work-study"
            minutes = int(scene.get("duration_minutes") or (150 if item_key == "expensive_coffee" else 30))
            self._set_temporary_activity(activity, minutes * 60 * 1000)
            self.food_scene_timer.start(max(1000, minutes * 60 * 1000))
            label = "☕ 喝贵的 · 深度工作中" if item_key == "expensive_coffee" else "☕ 咖啡开工"
            detail = f"\n{todo_title[:80]}" if todo_title else "\n无任务开工"
            self.show_speech(f"{label}{detail}\n{result.get('feedback') or ''}", 6200)
        elif item_key == "milk_tea":
            minutes = int(scene.get("duration_minutes") or 10)
            self._set_temporary_activity("milk-tea", minutes * 60 * 1000)
            self.food_scene_timer.start(max(1000, minutes * 60 * 1000))
            self.show_speech(f"🧋 奶茶时间 · {minutes:02d}:00\n{result.get('feedback') or ''}", 5200)
        elif item_key == "cake":
            self._set_temporary_activity("feast", 20_000)
            self.food_scene_timer.start(20_000)
            title = todo_title or "今天完成的一件事"
            self.show_speech(f"🍰 今天庆祝过\n{title[:100]}", 6200)
        else:
            self._set_temporary_activity("tea", 60_000)
            self.show_speech("🍵 喝会儿茶\n今天不用赶，六毛陪你待一会儿。", 5600)
        self._refresh_pixmap()
        return True

    def _food_scene_timeout(self) -> None:
        scene = self.economy.active_food_scene()
        if not scene:
            return
        item_key = str(scene.get("item_key") or "")
        if item_key in {"coffee", "expensive_coffee"}:
            metadata = scene.get("metadata") if isinstance(scene.get("metadata"), dict) else {}
            # This flag is intentionally read from the persisted scene for
            # auditing and future receipts. It does not grant the timeout
            # permission to finish a shared Work Session.
            _started_by_coffee = bool(metadata.get("coffee_scene_started_work_timer"))
            if not self.economy.finish_food_scene("timer"):
                return
            self.food_scene_timer.stop()
            if self.work_timer.is_running:
                message = "咖啡喝完啦，半小时到了。\n要继续工作，还是结束这一轮？"
            elif self.work_timer.has_active_session:
                message = "咖啡喝完啦，半小时到了。\n当前工作还暂停着，要继续还是结束这一轮？"
            else:
                message = "咖啡喝完啦，半小时到了。\n要开始工作，还是结束这一轮？"
            self._show_coffee_scene_prompt(message)
            return
        finished = self.economy.finish_food_scene("timer")
        if not finished:
            return
        if item_key == "milk_tea":
            # Ending a break is not permission to restart the work timer.
            # The user must explicitly press “继续工作”.
            self.show_speech("奶茶喝完了。\n工作还暂停着，要继续时点‘继续工作’。", 5200)
        elif item_key == "cake":
            self.show_speech("庆祝结束，今天这件事已经被六毛记下来了。", 4200)

    def _show_coffee_scene_prompt(self, message: str) -> None:
        """Ask what to do next without changing the user's work decision."""

        self.coffee_scene_prompt.set_message(message)
        self._position_coffee_scene_prompt()
        self._show_nonactivating(self.coffee_scene_prompt)
        self._raise_accessory(self.coffee_scene_prompt)

    def _continue_after_coffee_scene(self) -> None:
        self.coffee_scene_prompt.hide()
        if not self.work_timer.is_running:
            self.start_work_timer()
        else:
            self.show_speech("好，继续工作。", 3200)

    def _finish_after_coffee_scene(self) -> None:
        self.coffee_scene_prompt.hide()
        if self.work_timer.has_active_session:
            self.finish_work_timer()

    def _send_food_interaction(self, buddy: dict, kind: str) -> None:
        """Send a food scene invitation; gifts are charged locally and never create income."""
        if not self.social_client.signed_in:
            self.show_speech("先登录搭子自习室，才能给搭子送吃的。", 4200)
            return
        target = str(buddy.get("user_id") or buddy.get("id") or "").strip()
        if not target:
            self.show_speech("没找到这位搭子的账号。", 4200)
            return
        item_key = {
            "food_coffee": "coffee",
            "food_milk_tea": "milk_tea",
            "food_tea": "tea",
            "food_cake": "cake",
        }.get(str(kind))
        if not item_key:
            return
        if item_key == "cake":
            self.show_speech("小蛋糕不能单独请一位搭子；请打开补给站，邀请 1～3 位好友一起分享。", 5200)
            return
        catalog = self.economy.catalog().get(item_key) or {}
        price = int(catalog.get("price") or 0)
        if self.economy.balance < price:
            self.show_speech("哥们，钱袋有点瘪。", 4200)
            return
        recipient_label = str(
            buddy.get("private_note_name")
            or buddy.get("owner_nickname")
            or buddy.get("nickname")
            or "搭子"
        )[:80]
        duration = {"coffee": 30, "milk_tea": 10, "tea": 0, "cake": 0}.get(item_key, 0)
        operation_key = uuid.uuid4().hex
        payload = {
            "item_key": item_key,
            "duration_minutes": duration,
            "operation_key": operation_key,
            "message": {
                "coffee": "要不要一起干 30 分钟？",
                "milk_tea": "一起歇会儿？",
                "tea": "过来坐会儿？",
                "cake": "这件事值得庆祝一下。",
            }.get(item_key, ""),
        }
        try:
            self.social_client.rpc(
                "lili_send_food_interaction",
                {"p_target": target, "p_kind": str(kind), "p_payload": payload},
            )
        except Exception as exc:
            self.show_speech(f"没送出去：{str(exc)[:120]}", 5200)
            return
        event = self.economy.record_food_gift_sent(
            target,
            recipient_label,
            item_key,
            operation_key=operation_key,
        )
        if event is None:
            self.show_speech("邀请已发出，但本地钱袋扣款失败，请先检查余额。", 5200)
            return
        self._sync_economy_events([event.as_dict()])
        # A drink invitation is a shared visual moment, not a local break:
        # the sender changes into the same limited food pose immediately and
        # keeps any active focus timer running.
        self._set_social_food_activity(item_key, duration)
        text = {
            "coffee": f"☕ 已邀请 {recipient_label} 一起开工 30 分钟。",
            "milk_tea": f"🧋 已邀请 {recipient_label} 一起歇会儿。",
            "tea": f"🍵 已给 {recipient_label} 敬茶。",
            "cake": f"🍰 已请 {recipient_label} 庆祝一下。",
        }.get(item_key, "互动已经送出。")
        self.show_speech(text, 5200)

    def _set_social_food_activity(self, item_key: str, duration_minutes: int = 0) -> None:
        """Show a food-interaction pose without pausing or starting focus."""

        item_key = str(item_key or "")
        activity = {
            "coffee": "work-study",
            "milk_tea": "milk-tea",
            "tea": "tea",
            "cake": "feast",
        }.get(item_key)
        if not activity:
            return
        if item_key == "cake" and not duration_minutes:
            duration_ms = 20_000
        else:
            default_minutes = {"coffee": 30, "milk_tea": 10, "tea": 1}
            minutes = max(1, int(duration_minutes or default_minutes.get(item_key, 1)))
            duration_ms = minutes * 60 * 1000
        self._social_food_activity_until = time.monotonic() + duration_ms / 1000
        self._set_temporary_activity(activity, duration_ms)
        self._refresh_pixmap()

    def _handle_food_interaction_accepted(self, event: dict) -> None:
        kind = str(event.get("kind") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        item_key = {
            "food_coffee": "coffee",
            "food_milk_tea": "milk_tea",
            "food_tea": "tea",
            "food_cake": "cake",
            "food_cake_share": "cake",
        }.get(kind)
        if not item_key:
            return
        duration = int(payload.get("duration_minutes") or 0)
        # Accepted social food is always a shared visual moment.  Do not
        # start a local inventory scene here: that would pause focus for some
        # items and could make an incoming cake look like local inventory.
        self._set_social_food_activity(item_key, duration)
        labels = {
            "coffee": "☕ 一起喝咖啡",
            "milk_tea": "🧋 一起喝奶茶",
            "tea": "🍵 一起喝茶",
            "cake": "🍰 一起吃蛋糕",
        }
        self.show_speech(f"{labels[item_key]}，六毛继续陪你专注。", 4800)

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
        self._pause_notice_shown = False
        self._fullscreen_video_started_at = None
        if not self.work_timer.has_active_session:
            self._recorded_focus_session_seconds = 0
        self._focus_quality_tracker.start(active_application_category())
        # The paper window selects a real Todo; keep the existing focus
        # analytics task for compatibility, but attribute new seconds to the
        # same local task record as soon as the session starts.
        started = self.focus_session.start()
        self._recorded_focus_session_seconds = (
            self.work_timer.analytics_recorded_session_seconds()
        )
        self.focus_analytics.begin_focus_session()
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
        if self.work_controls.isVisible():
            self._show_work_controls()
        return reply

    def _handle_chat_local_command(self, message: str) -> ManagedChatReply | None:
        """Route explicit work controls through the existing shared session."""

        text = " ".join(str(message or "").replace("六毛", "", 1).split())
        text = text.strip(" ，,。.!！")
        reply: CompanionReply | None = None
        if text in {"开始工作", "开工", "开始计时"}:
            reply = self.start_work_timer()
        elif text in {"暂停", "暂停工作", "暂停计时"}:
            reply = self.pause_work_timer()
        elif text in {"继续", "继续工作", "恢复计时"}:
            reply = self.resume_work()
        elif text in {"结束工作", "结束计时", "收工"}:
            reply = self.finish_work_timer()
        if reply is None:
            return None
        return ManagedChatReply(reply.text, reply.state, "local-action")

    def pause_work_timer(self, reason: str = "") -> CompanionReply:
        """Pause the shared timer through one path, preserving the reason."""

        reason = str(reason or "manual").strip().casefold()
        automatic_reason = reason in {"idle", "idle_10m", "lock", "sleep", "fullscreen_video", "video"}
        if not automatic_reason:
            self._record_user_interaction()
            self._reset_idle_episode()
        session_seconds = self.work_timer.session_seconds()
        was_running = self.focus_session.pause(reason)
        if was_running:
            self.focus_analytics.pause_focus_session()
            self._record_focus_segment(session_seconds, completed=False)
            # FocusSession emits its pause snapshot before the analytics
            # segment is committed. Publish one more snapshot so the study
            # room and report immediately see the same reconciled day total.
            self.focus_session.refresh()
            self._pause_notice_shown = False
            if automatic_reason and reason in {"idle", "idle_10m", "lock", "sleep", "fullscreen_video", "video"}:
                self._pause_notice_shown = reason != "idle_10m"
        self._award_focus_rewards()
        self.work_activity_timer.stop()
        self._set_temporary_activity("thermos", 25_000)
        duration = format_work_duration(self.work_timer.today_seconds())
        if was_running and reason in {"sleep", "lock"}:
            system_event = "电脑已锁屏" if reason == "lock" else "电脑进入睡眠"
            reply = CompanionReply(
                f"{system_event}，六毛已暂停这轮计时；回来后点继续工作就好。",
                PetState.SLEEPY,
            )
        elif was_running and reason in {"idle", "idle_10m"}:
            reply = CompanionReply(
                "十分钟没有键鼠操作，六毛先帮你停表了；回来后点继续工作。",
                PetState.CURIOUS,
            )
        elif was_running and reason in {"fullscreen_video", "video"}:
            reply = CompanionReply(
                "检测到视频或游戏全屏，六毛先帮你停表了；结束后点继续工作。",
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
        # 直接操作完成后收起控制条；下一次右键六毛时会按最新状态重建。
        self.work_controls.hide()
        self._refresh_pixmap()
        if was_running and automatic_reason and reason in {"idle", "idle_10m", "lock", "sleep", "fullscreen_video", "video"}:
            # A food-led work scene is an active commitment, not a second
            # timer.  Once the system has auto-paused, close that scene so its
            # visual state cannot claim that coffee work is still active.
            scene = self.economy.active_food_scene()
            if scene and str(scene.get("item_key") or "") in {"coffee", "expensive_coffee"}:
                self.economy.finish_food_scene(f"work_paused:{reason}")
                self.food_scene_timer.stop()
        return reply

    def finish_work_timer(self) -> CompanionReply:
        """完成本次工作、保留今日累计并播放庆祝动作。"""

        self._record_user_interaction()
        self._reset_idle_episode()
        self._pause_notice_shown = False
        self._fullscreen_video_started_at = None
        room_id = self.focus_session.room_id
        session_seconds = self.work_timer.session_seconds()
        session_id = self.work_timer.focus_session_id
        # Commit the final analytics segment while the timer still owns its
        # stable session ID.  The timer is reset immediately afterwards.
        self._record_focus_segment(session_seconds, completed=True, session_id=session_id)
        total = self.focus_session.finish()
        self.focus_analytics.finish_focus_session(completed=True)
        self._award_focus_rewards()
        self.set_paused(False)
        self._recorded_focus_session_seconds = 0
        reply = self.companion.work_finished(format_work_duration(total))
        self._show_emotion(reply.state, 3400)
        quality_text = (
            f"\n本轮质量：{self._last_focus_quality.label}（{self._last_focus_quality.score}分）"
            if self._last_focus_quality else ""
        )
        self.show_speech(
            reply.text + quality_text,
            6200,
        )
        self.work_timer_changed.emit(False)
        self._schedule_social_tick()
        self.work_controls.hide()
        self.work_activity_timer.stop()
        self._set_temporary_activity(random.choice(COMPLETE_ACTIONS), 45_000)
        self._show_new_outfit_unlock()
        if room_id:
            self._record_social_room_event(room_id, "focus_finish")
        food_scene = self.economy.active_food_scene()
        if food_scene and str(food_scene.get("scene_type") or "") in {"focus", "deep_focus"}:
            self.economy.finish_food_scene("work_finished")
            self.food_scene_timer.stop()
        if self._work_report_dialog is not None and self._work_report_dialog.isVisible():
            self._work_report_dialog.refresh()
        return reply

    # Public state-machine commands.  The older *_work_timer names remain as
    # compatibility entry points for menus and plugins, while all callers
    # still share the same timer/session implementation above.
    def pause_work(self, reason: str = "manual") -> CompanionReply:
        return self.pause_work_timer(reason=reason)

    def resume_work(self, source: str = "user") -> CompanionReply:
        del source
        return self.start_work_timer()

    def finish_work(self, source: str = "user") -> CompanionReply:
        del source
        return self.finish_work_timer()

    def show_today_note(self, *, passive: bool = False) -> None:
        """Open the configured surface without stealing focus when passive."""

        if str(getattr(self.settings, "today_note_mode", "compact")) == "compact":
            self.show_compact_todos()
        else:
            self.show_sticky_note(passive=passive)

    def show_sticky_note(self, *, passive: bool = False) -> None:
        """Open the independent free-form 便利贴 window in detailed mode."""

        if not passive:
            self._record_user_interaction()
        if self._compact_todo_panel is not None:
            self._restore_compact_todos_after_show = False
            self._compact_todo_panel.hide()
        if self._today_note_window is None:
            self._today_note_window = TodayNoteWindow(
                self.time_memory,
                None,
                owner=self,
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
        self._today_note_window.setAttribute(
            Qt.WidgetAttribute.WA_ShowWithoutActivating,
            bool(passive),
        )
        if passive:
            self._today_note_window.show()
            self._position_sticky_note()
            return
        if self._today_note_window.isMinimized():
            self._today_note_window.showNormal()
        else:
            self._today_note_window.show()
        self._today_note_window.raise_()
        self._today_note_window.activateWindow()
        self._position_sticky_note()

    def show_compact_todos(self, *, manual: bool = False) -> None:
        """Show the frameless Todo strip directly below the pet."""

        if manual:
            self._compact_todos_manually_hidden = False
            if str(getattr(self.settings, "today_note_mode", "compact")) == "hidden":
                self.settings.today_note_mode = "compact"
                save_settings(self.settings)
        self._record_user_interaction()
        if self._work_report_dialog is not None and self._work_report_dialog.isVisible():
            # The report is a normal independent window; its content must not
            # be covered by the desktop Todo accessory while it is open.
            return
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
        has_visible_tasks = self._compact_todo_panel.refresh()
        if not has_visible_tasks:
            # An empty compact strip is not a useful accessory.  Keep the
            # widget instance so a later Todo write can reuse it, but do not
            # show it (or its ⋯/＋ action rail).
            self._restore_compact_todos_after_show = False
            self._compact_todo_panel.hide()
            return
        self._show_nonactivating(
            self._compact_todo_panel,
            always_on_top=bool(
                self.settings.always_on_top
                or getattr(self.settings, "today_note_always_on_top", False)
            ),
        )
        self._position_compact_todos()
        # The pet and the compact panel are separate native windows.  Raise
        # the panel after positioning so a transient pet repaint cannot cover
        # the label or the trailing menu button.  The panel itself never
        # accepts focus, so this does not steal keyboard input.
        self._raise_accessory(self._compact_todo_panel)

    def _position_compact_todos(self) -> None:
        """Keep the Todo strip close to the pet, preferring its left side.

        The compact Todo panel is a pet accessory rather than an independent
        dashboard.  It therefore uses the visible pet mask as its anchor and
        chooses a side in a deterministic order: left, right, then below.  A
        final clamp keeps the companion inside the active monitor's work area.
        """

        panel = self._compact_todo_panel
        if panel is None:
            return
        screen = QApplication.screenAt(self.geometry().center()) or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        visible_bounds = self.mask().boundingRect()
        if visible_bounds.isEmpty():
            left = self.x()
            right = self.x() + self.width()
            top = self.y()
            bottom = self.y() + self.height()
        else:
            left = self.x() + visible_bounds.left()
            right = self.x() + visible_bounds.right() + 1
            top = self.y() + visible_bounds.top()
            bottom = self.y() + visible_bounds.bottom() + 1

        # A mask can end a few pixels before the visible anti-aliased sprite
        # edge on Windows/DPI-scaled displays.  Reserve that edge explicitly;
        # otherwise the pet can visually sit on top of the panel's final
        # characters and its ⋯ button even when the Qt rectangles do not
        # mathematically intersect.
        pet_safety = 6
        left -= pet_safety
        right += pet_safety
        top -= pet_safety
        bottom += pet_safety
        gap_x = 8
        gap_y = 6
        panel_width = panel.width()
        panel_height = panel.height()
        center_y = (top + bottom - panel_height) // 2
        left_x = left - panel_width - gap_x
        right_x = right + gap_x
        can_place_left = left_x >= available.left()
        can_place_right = right_x + panel_width <= available.right() + 1

        pet_rect = QRect(left, top, max(1, right - left), max(1, bottom - top))
        candidates = []
        if can_place_left:
            candidates.append((left_x, center_y))
        if can_place_right:
            candidates.append((right_x, center_y))
        # Both sides can be unavailable near a monitor edge.  Use a below or
        # above placement only then, but still reject any rectangle touching
        # the pet.  This is a real non-overlap guarantee, not just a visual
        # offset guess.
        candidates.extend(
            [
                ((left + right - panel_width) // 2, bottom + gap_y),
                ((left + right - panel_width) // 2, top - panel_height - gap_y),
            ]
        )
        x = y = None
        for candidate_x, candidate_y in candidates:
            candidate = QRect(candidate_x, candidate_y, panel_width, panel_height)
            if not available.contains(candidate):
                continue
            if candidate.intersects(pet_rect):
                continue
            x, y = candidate_x, candidate_y
            break
        if x is None or y is None:
            # Extremely small work areas can make every ideal placement
            # impossible.  Clamp to the monitor as a last resort, then raise
            # the panel so its controls remain usable.
            x = max(available.left(), min(left_x, available.right() - panel_width + 1))
            y = max(available.top(), min(center_y, available.bottom() - panel_height + 1))
        x = max(available.left(), min(x, available.right() - panel_width + 1))
        y = max(available.top(), min(y, available.bottom() - panel_height + 1))
        panel.move(x, y)
        self._raise_accessory(panel)
        LOGGER.debug(
            "[TodoLayout] host=(x=%s,y=%s,w=%s,h=%s) panel=(x=%s,y=%s,w=%s,h=%s) "
            "available=(x=%s,y=%s,w=%s,h=%s) pet_bounds=(left=%s,top=%s,right=%s,bottom=%s)",
            self.x(), self.y(), self.width(), self.height(),
            panel.x(), panel.y(), panel.width(), panel.height(),
            available.x(), available.y(), available.width(), available.height(),
            left, top, right, bottom,
        )

    def _position_sticky_note(self) -> None:
        """Place the detailed 便利贴 beside the pet, with screen-edge fallback."""

        note = self._today_note_window
        if note is None or not note.isVisible():
            return
        screen = QApplication.screenAt(self.geometry().center()) or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        visible_bounds = self.mask().boundingRect()
        if visible_bounds.isEmpty():
            left = self.x()
            right = self.x() + self.width()
            top = self.y()
        else:
            left = self.x() + visible_bounds.left()
            right = self.x() + visible_bounds.right() + 1
            top = self.y() + visible_bounds.top()
        gap = 10
        x = right + gap
        if x + note.width() > available.right() + 1:
            x = left - note.width() - gap
        x = max(available.left(), min(x, available.right() - note.width() + 1))
        y = max(available.top(), min(top, available.bottom() - note.height() + 1))
        note.move(x, y)

    def _position_visit_status_bubble(self) -> None:
        """Place the visit label in the lower red-zone beside work duration.

        The compact todo panel occupies the pet's upper-left area.  Putting
        the visit label at "bottom - height" made it float over those todo
        rows, especially when the pet was dragged to the lower-right corner.
        Keep it on the same baseline as the work-duration bubble, immediately
        to that bubble's left, so both status labels share one reserved row.
        """

        bubble = self.visit_status_bubble
        if not bubble.isVisible():
            return
        bubble.adjustSize()
        area = self._screen_geometry()
        bounds = self.mask().boundingRect()
        left = self.x() + (bounds.left() if not bounds.isEmpty() else 0)
        right = self.x() + (bounds.right() + 1 if not bounds.isEmpty() else self.width())
        bottom = self.y() + (bounds.bottom() + 1 if not bounds.isEmpty() else self.height())
        gap = 7
        duration_bubble = getattr(self, "work_duration_bubble", None)
        if duration_bubble is not None and duration_bubble.isVisible():
            duration_bubble.adjustSize()
            x = duration_bubble.x() - bubble.width() - gap
            y = duration_bubble.y()
        else:
            x = left - bubble.width() - gap
            y = bottom + gap
        if area is not None:
            if x < area.left():
                x = max(area.left(), right + gap)
            x = min(max(x, area.left()), area.right() - bubble.width() + 1)
            y = min(max(y, area.top()), area.bottom() - bubble.height() + 1)
        bubble.move(x, y)

    def _position_accessories(self) -> None:
        """Reflow every pet accessory from the pet as its single anchor."""

        if hasattr(self, "speech_bubble") and self.speech_bubble.isVisible():
            self._position_speech_bubble()
        if hasattr(self, "quick_panel") and self.quick_panel.isVisible():
            self._position_quick_panel()
        if hasattr(self, "work_controls") and self.work_controls.isVisible():
            self._position_work_controls()
        if hasattr(self, "coffee_scene_prompt") and self.coffee_scene_prompt.isVisible():
            self._position_coffee_scene_prompt()
        if hasattr(self, "work_duration_bubble") and self.work_duration_bubble.isVisible():
            self._position_work_duration_bubble()
        if hasattr(self, "visit_status_bubble") and self.visit_status_bubble.isVisible():
            self._position_visit_status_bubble()
        if self._compact_todo_panel is not None and self._compact_todo_panel.isVisible():
            self._position_compact_todos()
        self._position_sticky_note()

    def hide_today_note(self) -> None:
        self._restore_compact_todos_after_show = False
        if self._today_note_window is not None:
            self._today_note_window.hide()
        if self._compact_todo_panel is not None:
            self._compact_todo_panel.hide()

    def hide_compact_todos(self) -> None:
        self._restore_compact_todos_after_show = False
        self._compact_todos_manually_hidden = True
        if self._compact_todo_panel is not None:
            self._compact_todo_panel.hide()

    def add_compact_todo(self) -> None:
        self.show_compact_todos(manual=True)
        if self._compact_todo_panel is not None:
            self._compact_todo_panel.add_task()
            # ``add_task`` refreshes before emitting its change signal.  The
            # window therefore needs one explicit show pass after the modal
            # input closes so a previously empty strip reappears immediately.
            if self._compact_todo_panel.visible_task_ids:
                self.show_compact_todos()

    def hide_sticky_note(self) -> None:
        if self._today_note_window is not None:
            self._today_note_window.hide()

    def _refresh_todo_surfaces(self) -> None:
        if self._today_note_window is not None:
            self._today_note_window.refresh()
        compact_mode = str(getattr(self.settings, "today_note_mode", "compact")) == "compact"
        display_mode = str(getattr(self.settings, "today_note_display_mode", "always"))
        if self._compact_todo_panel is None:
            # A Todo can be created from the center window before the compact
            # accessory has ever been instantiated.  In compact mode, create
            # it lazily as soon as there is something eligible to display.
            if compact_mode and display_mode != "hidden" and self.time_memory.todo_view_upcoming():
                self.show_compact_todos()
        else:
            panel = self._compact_todo_panel
            was_visible = panel.isVisible()
            has_visible_tasks = panel.refresh()
            if has_visible_tasks:
                # Pending Todos are authoritative: if any unfinished item
                # remains, a hidden compact panel must recover automatically.
                # Manual hiding is only effective while there are no pending
                # items; this prevents the panel from silently disappearing
                # while the user still has work to do.
                if (
                    compact_mode
                    and display_mode != "hidden"
                    and not panel.isVisible()
                ):
                    self.show_compact_todos()
                elif was_visible:
                    # A longer/shorter task changes the panel width, so its
                    # pet-relative position must be recalculated in the same
                    # UI turn rather than waiting for the next pet movement.
                    self._position_compact_todos()
            elif was_visible:
                # Once every Todo is completed or removed, the compact panel
                # and its action rail should disappear together.
                self._compact_todo_panel.hide()

        if self._todo_center_window is not None:
            self._todo_center_window.refresh()

    def _chat_action_executed(self, result: object) -> None:
        """Refresh every Todo surface after a real chat-side local write."""

        action = str(getattr(result, "action", "") or "")
        if action in {
            "create_todo", "update_todo", "complete_todo", "delete_todo",
            "create_countdown", "update_countdown", "delete_countdown",
            "complete_countdown", "create_anniversary", "update_anniversary",
            "delete_anniversary", "move_pending_to_today",
        }:
            self._refresh_todo_surfaces()
            # Respect an intentionally hidden panel.  If compact mode is the
            # configured surface, create it lazily so a successful chat action
            # becomes visible without requiring a manual refresh.
            if (
                self._compact_todo_panel is None
                and str(getattr(self.settings, "today_note_mode", "compact")) == "compact"
                and str(getattr(self.settings, "today_note_display_mode", "always")) != "hidden"
            ):
                self.show_compact_todos()

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
        task = self.time_memory.get_todo_view_item(task_id)
        was_open = task is not None and not bool(getattr(task, "completed", False))
        if not self.time_memory.complete_todo_view_item(task_id, True):
            return
        if was_open and task is not None:
            self._record_economy_performance(str(getattr(task, "title", "完成待办")), str(task_id))
        if self._today_note_window is not None:
            self._today_note_window.refresh()
        self._set_temporary_activity(random.choice(COMPLETE_ACTIONS), 25_000)
        self.show_speech("这项做完了，给你记上。", 4200)

    def _set_todo_completion_from_note(self, task_id: str, completed: bool) -> None:
        task = self.time_memory.get_todo_view_item(task_id)
        if task is None:
            return
        if completed:
            was_open = not bool(getattr(task, "completed", False))
            self.time_memory.complete_todo_view_item(task_id, True)
            if was_open:
                self._record_economy_performance(str(getattr(task, "title", "完成待办")), str(task_id))
            self._set_temporary_activity(random.choice(COMPLETE_ACTIONS), 25_000)
            self.show_speech("这项做完了，给你记上。", 4200)
        else:
            self.time_memory.complete_todo_view_item(task_id, False)
            self.time_memory.summary.refresh_tasks()
        if self._today_note_window is not None:
            self._today_note_window.refresh()

    def _set_todo_completion_from_panel(self, task_id: str, completed: bool) -> None:
        """Reflect a compact checkbox in the real local Todo store."""

        task = self.time_memory.get_todo_view_item(task_id)
        if task is None:
            return
        if completed:
            was_open = not bool(getattr(task, "completed", False))
            self.time_memory.complete_todo_view_item(task_id, True)
            if was_open:
                self._record_economy_performance(str(getattr(task, "title", "完成待办")), str(task_id))
            self._set_temporary_activity(random.choice(COMPLETE_ACTIONS), 25_000)
            self.show_speech("这项做完了，给你记上。", 4200)
        else:
            self.time_memory.complete_todo_view_item(task_id, False)
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
        self._set_temporary_activity("daydream", 20_000)
        self.show_speech("行，那今天不算旷工。", 4200)
        if self._today_note_window is not None:
            self._today_note_window.refresh()

    def show_todo_center(self) -> None:
        """Open the full TodoCenter while keeping CompactTodo as the light view."""

        self._record_user_interaction()
        if self._todo_center_window is None:
            self._todo_center_window = TodoCenterWindow(self.time_memory)
            self._todo_center_window.changed.connect(self._refresh_todo_surfaces)
        self._todo_center_window.refresh()
        if self._todo_center_window.isMinimized():
            self._todo_center_window.showNormal()
        else:
            self._todo_center_window.show()
        self._todo_center_window.raise_()
        self._todo_center_window.activateWindow()

    def show_alarm_center(self) -> None:
        """Open the local alarm editor without creating a second reminder system."""

        self._record_user_interaction()
        todos = list(self.time_memory.todos.items)
        if self._alarm_center_dialog is None:
            self._alarm_center_dialog = AlarmCenterDialog(
                self.time_memory.alarms,
                todos,
                parent=None,
                sound_library=self.time_memory.alarm_sounds,
            )
        else:
            self._alarm_center_dialog.todos = todos
            self._alarm_center_dialog.refresh()
        if self._alarm_center_dialog.isMinimized():
            self._alarm_center_dialog.showNormal()
        else:
            self._alarm_center_dialog.show()
        self._alarm_center_dialog.raise_()
        self._alarm_center_dialog.activateWindow()

    def show_time_memory(self) -> None:
        if self._time_memory_window is None:
            self._time_memory_window = TimeMemoryWindow(self.time_memory)
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

    def show_economy(self) -> None:
        """Open the local wallet and month-end salary slip."""

        self._record_user_interaction()
        if self._economy_dialog is None:
            self._economy_dialog = EconomyDialog(self.economy, None)
            self._economy_dialog.changed.connect(self._on_economy_changed)
        self._economy_dialog.refresh()
        if self._economy_dialog.isMinimized():
            self._economy_dialog.showNormal()
        else:
            self._economy_dialog.show()
        self._economy_dialog.raise_()
        self._economy_dialog.activateWindow()

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

        # A shared drink or cake takes precedence over the normal focus
        # animation rotation until its visual moment expires.
        if time.monotonic() < self._social_food_activity_until:
            self._schedule_work_activity()
            return
        if night_limited_activity(datetime.now()) is not None:
            self._night_limited_tick()
            self._schedule_work_activity()
            return
        if not self.work_timer.is_running:
            return
        session = self.work_timer.session_seconds()
        choices = FOCUS_ACTIONS
        if session >= 45 * 60 and random.random() < 0.35:
            choices = ("thermos", "sleep", "daydream")
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

        self._social_food_activity_until = 0.0
        if night_limited_activity(datetime.now()) is not None:
            self._night_limited_tick()
            return
        self._change_ambient_activity(
            random.choice(FOCUS_ACTIONS) if self.work_timer.is_running else "none"
        )

    def _work_timer_tick(self) -> None:
        """定期保存工作进度，并显示一次到期的鼓励或休息提醒。"""

        self._check_local_alarms()
        self._check_local_reminders()
        self.work_timer.checkpoint()
        snapshot = self.focus_session.refresh()
        self._update_work_duration_bubble(snapshot)
        if self.work_controls.isVisible():
            self.work_controls.set_duration_visible(bool(self.settings.show_work_duration))
            self.work_controls.set_session_duration(
                "本轮 " + format_work_duration(snapshot.session_seconds)
                if snapshot.status in {"focus", "rest"} else "本轮未开始"
            )
        self._award_focus_rewards()
        self._check_expensive_coffee_reward()
        self._sync_hourly_outfit(announce=True)
        self._show_new_outfit_unlock()
        quiet = detect_quiet_mode()
        food_scene = self.economy.active_food_scene() or {}
        deep_food_scene = bool(food_scene.get("deep_focus"))
        wellness_kind = None if quiet.blocked or deep_food_scene else self.wellness.take_due(
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
        reminder_kind = None if quiet.blocked or deep_food_scene else self.work_timer.take_due_reminder()
        if reminder_kind is None:
            return
        duration = format_work_duration(self.work_timer.session_seconds())
        reply = self.companion.work_reminder(reminder_kind, duration)
        self._show_emotion(reply.state, 3600)
        self.show_speech(reply.text, 7200)

    def _check_expensive_coffee_reward(self) -> None:
        """Reward one ordinary coffee after two real continuous work hours."""

        if not self.work_timer.is_running:
            return
        scene = self.economy.active_food_scene() or {}
        if str(scene.get("item_key") or "") != "expensive_coffee":
            return
        metadata = scene.get("metadata") if isinstance(scene.get("metadata"), dict) else {}
        try:
            baseline = max(0, int(metadata.get("work_episode_seconds_at_start", 0) or 0))
        except (TypeError, ValueError):
            baseline = 0
        episode_seconds = self.work_timer.episode_seconds()
        if episode_seconds < baseline:
            # An explicit pause/resume starts a new uninterrupted episode.
            # Reset the coffee baseline instead of allowing the old episode's
            # seconds to make the two-hour reward arrive too early.
            baseline = episode_seconds
            self.economy.update_active_food_scene_metadata(
                {"work_episode_seconds_at_start": baseline}
            )
        if episode_seconds - baseline < 2 * 60 * 60:
            return
        event = self.economy.grant_expensive_coffee_focus_reward(str(scene.get("id") or ""))
        if event is None:
            return
        self._sync_economy_events([event.as_dict()])
        self.show_speech("☕ 这场深度工作超过 2 小时了。普通咖啡 ×1，六毛给你留着。", 6500)
        if self._food_scene_dialog is not None:
            self._food_scene_dialog.refresh()

    def _check_local_reminders(self) -> None:
        """Run the local reminder queue once per existing one-second timer."""

        if detect_quiet_mode().blocked or bool((self.economy.active_food_scene() or {}).get("deep_focus")):
            return
        for reminder in self.time_memory.reminders.due()[:3]:
            self.time_memory.reminders.mark_notified(reminder.id)
            self._set_temporary_activity("curious", 12_000)
            self.show_speech(f"提醒：{reminder.title}", 5600)

    def _check_local_alarms(self) -> None:
        """Present due alarms as a non-modal card, never by stealing focus."""

        quiet = detect_quiet_mode()
        deep_food_scene = bool((self.economy.active_food_scene() or {}).get("deep_focus"))
        claimed = self.time_memory.alarms.claim_due(
            allow_during_dnd=not (quiet.blocked or deep_food_scene),
        )
        if self._alarm_card is not None and self._alarm_card.isVisible():
            return
        candidates = claimed or self.time_memory.alarms.active()
        if candidates:
            self._show_alarm_card(candidates[0])

    def _show_alarm_card(self, alarm) -> None:
        """Show one normal alarm window; queued alarms remain persisted/local."""

        if self._alarm_card is not None:
            self._close_alarm_card()
        self._alarm_card = AlarmCard(
            alarm,
            sound_library=self.time_memory.alarm_sounds,
        )
        self._alarm_card.start_requested.connect(self._start_alarm_work)
        self._alarm_card.snooze_requested.connect(self._snooze_alarm)
        self._alarm_card.dismiss_requested.connect(self._dismiss_alarm)
        # Center only once.  After the user drags or minimizes the native
        # window, accessory reflows must never move it back to the pet.
        self._alarm_card.center_on_current_screen()
        self._alarm_card.show()

    def _close_alarm_card(self) -> None:
        card = self._alarm_card
        self._alarm_card = None
        if card is not None:
            card.close_from_app()
            card.deleteLater()

    def _start_alarm_work(self, alarm_id: str) -> None:
        alarm = self.time_memory.alarms.get(alarm_id)
        if alarm is None:
            self._close_alarm_card()
            return
        self.time_memory.alarms.dismiss(alarm_id)
        if alarm.linked_todo_id:
            self.time_memory.select_task(alarm.linked_todo_id)
        self._close_alarm_card()
        self.start_work_timer()

    def _snooze_alarm(self, alarm_id: str, minutes: int) -> None:
        try:
            self.time_memory.alarms.snooze(alarm_id, minutes)
        except KeyError:
            pass
        self._close_alarm_card()

    def _dismiss_alarm(self, alarm_id: str) -> None:
        try:
            self.time_memory.alarms.dismiss(alarm_id)
        except KeyError:
            pass
        self._close_alarm_card()

    def _show_new_outfit_unlock(self) -> None:
        """跨过当天 1–8 小时节点时显示成长状态，而非机械更换衣服。"""

        stage = stage_for_seconds(self.work_timer.today_seconds())
        if stage.hour <= self._last_growth_hour:
            return
        self._last_growth_hour = stage.hour
        self._set_temporary_activity(stage.activity, 60_000)
        self.show_speech(f"今日成长：{stage.title}\n解锁：{stage.reward}\n{stage.message}", 8200)

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

    def _record_economy_focus(self, seconds: int, started_at: datetime) -> None:
        """Credit a real focus segment locally and sync only its safe ledger rows."""

        result = self.economy.record_focus(seconds, started_at=started_at)
        events = list(result.get("events") or [])
        self._sync_economy_events(events)
        if self._food_scene_dialog is not None:
            self._food_scene_dialog.refresh()

    def _record_focus_segment(
        self,
        session_seconds: int,
        *,
        completed: bool,
        session_id: str | None = None,
    ) -> int:
        """Credit only newly completed WORKING seconds in this session.

        ``WorkTimerModel.session_seconds()`` remains cumulative across a
        pause/resume cycle.  All wage, daily-stat, and focus-quality sinks
        must receive the delta since the previous pause/finish checkpoint,
        otherwise a resumed session would be counted more than once.
        """

        total = max(0, int(session_seconds))
        seconds = max(0, total - self._recorded_focus_session_seconds)
        if seconds <= 0:
            if completed:
                # The actual WORKING seconds may already have been credited
                # at an earlier pause, but finishing the Session still needs
                # to be represented once in the local daily card.
                self.daily_stats.record_focus(0, completed=True)
            return 0
        started_at = datetime.now(BEIJING_TIMEZONE) - timedelta(seconds=seconds)
        self.time_memory.record_focus(
            seconds,
            completed_session=completed,
            started_at=started_at,
        )
        self._record_economy_focus(seconds, started_at)
        self._last_focus_quality = self.focus_analytics.record_session(
            seconds,
            started_at=started_at,
            completed=completed,
            application_switches=self._focus_quality_tracker.application_switches,
            away_count=self._focus_quality_tracker.away_count,
            task=str((self.focus_analytics.current_task() or {}).get("title", "")),
            record_id=(
                f"{session_id or self.work_timer.focus_session_id}:{total}"
                if (session_id or self.work_timer.focus_session_id)
                else None
            ),
        )
        self.focus_analytics.update_current_task_progress(seconds)
        self.daily_stats.record_focus(seconds, completed=completed)
        self.work_timer.mark_analytics_recorded(total)
        self._recorded_focus_session_seconds = total
        return seconds

    def _shared_today_focus_seconds(self) -> int:
        """Return the reconciled day total used by every focus surface.

        FocusAnalytics contains the durable account-scoped segments while
        WorkTimer contributes only the currently running monotonic segment.
        This prevents stale cumulative checkpoint values from inflating the
        report and makes the study-room card, report, heartbeat, and duration
        bubble agree on the same number.
        """

        moment = datetime.now(BEIJING_TIMEZONE)
        projection = self.focus_analytics.period_summary("day", moment)
        recorded = max(0, int(projection.get("total_seconds", 0) or 0))
        if int(projection.get("local_record_count", 0) or 0) > 0:
            live = self.work_timer.current_elapsed_seconds() if self.work_timer.is_running else 0
            return min(24 * 60 * 60, recorded + max(0, int(live)))
        return max(0, int(self.work_timer.today_seconds()))

    def _record_economy_performance(self, title: str, task_id: str) -> None:
        events = []
        event = self.economy.record_performance(
            f"任务绩效：{title[:90]}",
            source_key=f"todo:{task_id}:{datetime.now().date().isoformat()}",
        )
        if event is not None:
            events.append(event.as_dict())
        task = self.time_memory.get_todo_view_item(task_id)
        if bool(getattr(task, "important", False)):
            cake = self.economy.record_important_todo_completion(task_id, title)
            if cake is not None:
                events.append(cake.as_dict())
        self._sync_economy_events(events)
        if self._food_scene_dialog is not None:
            self._food_scene_dialog.refresh()

    def _on_economy_changed(self) -> None:
        """同步钱袋新增/消费事件；服务端按 source_key 幂等去重。"""
        self._sync_economy_events(
            [event.as_dict() for event in self.economy.events]
        )

    def _sync_economy_events(self, events: list[dict]) -> None:
        if not events or not getattr(self.social_client, "signed_in", False):
            return
        recorder = getattr(self.social_client, "record_economy_event", None)
        if not callable(recorder):
            return
        normalized = [
            dict(event)
            for event in events
            if isinstance(event, dict) and int(event.get("amount") or 0) != 0
        ]
        if not normalized:
            return
        with self._economy_sync_lock:
            if self._economy_sync_inflight:
                self._economy_sync_pending = True
                return
            self._economy_sync_inflight = True

        def sync() -> None:
            try:
                for event in normalized:
                    try:
                        recorder(
                            event_id=str(event.get("event_id") or ""),
                            category=str(event.get("category") or "other"),
                            amount=int(event.get("amount") or 0),
                            label=str(event.get("label") or "")[:120],
                            source_key=str(event.get("source_key") or "")[:160],
                            occurred_on=str(event.get("occurred_on") or "")[:10],
                        )
                    except Exception:
                        LOGGER.info("economy event sync deferred", exc_info=True)
            finally:
                with self._economy_sync_lock:
                    rerun = self._economy_sync_pending
                    self._economy_sync_pending = False
                    self._economy_sync_inflight = False
                if rerun and getattr(self.social_client, "signed_in", False):
                    self._sync_economy_events(
                        [event.as_dict() for event in self.economy.events]
                    )

        threading.Thread(target=sync, name="lili-economy-sync", daemon=True).start()

    def shutdown_work_timer(self) -> None:
        """自然退出前暂停计时并更新当天工作卡，不把关机时间计入工作。"""

        self._reset_idle_episode()
        if hasattr(self, "work_timer"):
            if self.work_timer.is_running:
                session_seconds = self.work_timer.session_seconds()
                # Persist the same final running segment in the local time-memory
                # store before the shared timer is paused.  Without this, a
                # normal app close could update the legacy daily card while
                # losing the Todo attribution and daily check-in record.
                self._record_focus_segment(session_seconds, completed=False)
            self.focus_session.pause()

    def _generate_daily_report(self, *, show_dialog: bool, mark_generated: bool = False) -> Path | None:
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
        if mark_generated:
            self.daily_stats.mark_report_generated()
        return path

    def _maybe_generate_scheduled_daily_report(self, now: datetime) -> bool:
        """Generate one local report after the configured daily cutoff."""

        if not getattr(self.settings, "daily_report_enabled", True):
            return False
        try:
            hour, minute = (int(part) for part in str(getattr(self.settings, "daily_report_time", "22:30")).split(":", 1))
        except (TypeError, ValueError):
            hour, minute = 22, 30
        cutoff = hour * 60 + minute
        if now.hour * 60 + now.minute < cutoff:
            return False
        if self.daily_stats.report_generated_for(now.date().isoformat()):
            return False
        return self._generate_daily_report(show_dialog=False, mark_generated=True) is not None

    def show_daily_report(self) -> None:
        """兼容旧菜单入口；报告现在改为实时页签窗口，不保存图片。"""

        self.show_work_report()

    def _best_buddy_for_report(self) -> str:
        """Read the latest local self-study leaderboard snapshot only."""

        dialog = self._social_dialog
        rows = getattr(dialog, "_leaderboard_rows", []) if dialog is not None else []
        if not isinstance(rows, list):
            return "暂无自习室排行榜数据"
        own_id = self._current_social_user_id()
        candidates: list[tuple[int, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            user_id = str(row.get("user_id") or row.get("id") or "")
            if own_id and user_id == own_id:
                continue
            try:
                seconds = max(0, int(row.get("week_seconds") or row.get("period_seconds") or 0))
            except (TypeError, ValueError, OverflowError):
                seconds = 0
            name = social_pet_label(
                row.get("owner_nickname") or row.get("nickname") or row.get("pet_name") or "搭子"
            )
            if seconds > 0:
                candidates.append((seconds, name))
        if not candidates:
            return "暂无可用的本周排行榜数据"
        seconds, name = max(candidates, key=lambda item: (item[0], item[1]))
        return f"{name} · 本周 {format_work_duration(seconds)}"

    def _work_report_snapshot(self) -> dict[str, object]:
        """Build a fresh report from the currently active account namespace."""

        moment = datetime.now(BEIJING_TIMEZONE)
        current_date = moment.date()
        day_projection = self.focus_analytics.period_summary("day", moment)
        if int(day_projection.get("local_record_count", 0) or 0) > 0:
            # One-time repair for timer files that inherited a stale remote
            # maximum.  Do this only when detailed local records exist; a new
            # device without history must still be able to use the server
            # fallback.
            self.work_timer.reconcile_today_seconds(
                int(day_projection.get("total_seconds", 0) or 0)
            )
        return build_work_report(
            self.focus_analytics,
            self.work_timer,
            self.daily_stats,
            best_buddy=self._best_buddy_for_report(),
            focus_snapshot=self.focus_session.snapshot(),
            task_stats={
                "day": self.time_memory.records.stats(start=current_date, end=current_date),
                "week": self.time_memory.records.week_stats(current_date.isoformat()),
                "month": self.time_memory.records.month_stats(current_date.isoformat()),
            },
            now=moment,
        )

    def _restore_compact_todos_after_report_close(self) -> None:
        """Restore the Todo strip only if it was visible before the report."""

        should_restore = bool(self._restore_compact_todos_after_report)
        self._restore_compact_todos_after_report = False
        if should_restore and not bool(getattr(self, "_manually_hidden", False)):
            self.show_compact_todos()

    def show_work_report(self) -> None:
        """Open the live day/week/month report without creating a local image."""

        self._record_user_interaction()
        # Close the floating shortcut first. Showing both top-level windows
        # in one mouse event can leave the report behind the dock on macOS.
        self.quick_panel.hide()
        if self._compact_todo_panel is not None:
            self._restore_compact_todos_after_report = self._compact_todo_panel.isVisible()
            self._compact_todo_panel.hide()
        if self._work_report_dialog is None:
            self._work_report_dialog = WorkReportDialog(
                self._work_report_snapshot,
                pet_name=self._pet_name(),
                parent=None,
            )
            self._work_report_dialog.finish_requested.connect(self.finish_work_timer)
            self._work_report_dialog.closed.connect(self._restore_compact_todos_after_report_close)
        self._work_report_dialog.refresh()
        self._work_report_dialog.showNormal()
        self._work_report_dialog.raise_()
        self._work_report_dialog.activateWindow()

    def configure_daily_report(self) -> None:
        """从“工作记录”直接设置日报开关和每天的生成时间。"""

        self._record_user_interaction()
        dialog = QDialog(self)
        dialog.setWindowTitle("设置工作报告总结时间")
        dialog.setMinimumWidth(390)
        layout = QVBoxLayout(dialog)
        intro = QLabel("日报会在当天设定时间后自动生成一次；不会再按累计 8 小时触发。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        enabled = QCheckBox("每天自动生成工作日报")
        enabled.setChecked(bool(getattr(self.settings, "daily_report_enabled", True)))
        time_edit = QTimeEdit()
        report_time = QTime.fromString(
            str(getattr(self.settings, "daily_report_time", "22:30")),
            "HH:mm",
        )
        time_edit.setTime(report_time if report_time.isValid() else QTime(22, 30))
        time_edit.setDisplayFormat("HH:mm")
        time_edit.setWrapping(True)
        time_edit.setToolTip("鼠标滚轮选择小时和分钟")
        enabled.toggled.connect(time_edit.setEnabled)
        time_edit.setEnabled(enabled.isChecked())
        form.addRow(enabled)
        form.addRow("每天截止", time_edit)
        layout.addLayout(form)
        hint = QLabel("时间按本机时区计算。关闭后只保留手动生成日报。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#667784;font-size:11px;")
        layout.addWidget(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.settings.daily_report_enabled = enabled.isChecked()
        self.settings.daily_report_time = time_edit.time().toString("HH:mm")
        save_settings(self.settings)
        if self.settings.daily_report_enabled:
            self.show_speech(
                f"工作日报已设置为每天 {self.settings.daily_report_time} 自动生成。",
                4200,
            )
        else:
            self.show_speech("自动工作日报已关闭，仍可从工作记录手动生成。", 4200)

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

        directory = album_directory(self._active_focus_account_id); directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory.resolve())))

    def prompt_dialogue(self) -> None:
        """打开新版聊天面板；离线和在线模式共用同一个入口。"""

        self._record_user_interaction()
        if self._chat_dialog is None:
            # Keep chat as an independent utility window so it has a normal
            # taskbar/Dock entry and can be minimized without affecting pet.
            self._chat_dialog = ChatDialog(None, self._pet_name())
            # Opening the chat repeatedly must not add another signal
            # connection.  A duplicate connection would submit one user
            # message multiple times and make every retry look like a second
            # pet or a second conversation.
            self._chat_dialog.message_submitted.connect(
                self._submit_chat_message,
                Qt.ConnectionType.UniqueConnection,
            )
            self._chat_dialog.stop_requested.connect(self._interrupt_chat)
            self._chat_dialog.settings_requested.connect(self.open_settings)
            self._chat_dialog.rename_requested.connect(self.rename_pet)
            self._chat_dialog.reconnect_requested.connect(self._reconnect_ai)
            self._chat_dialog.clear_display_requested.connect(self._clear_chat_display)
            self._chat_dialog.new_conversation_requested.connect(self._start_new_conversation)
            self._chat_dialog.history_requested.connect(self._show_chat_history)
            if not self._chat_history.sessions():
                self._chat_history.bootstrap(self._chat_memory.recent)
            saved_messages = self._chat_history.current_messages()
            if saved_messages:
                self._chat_dialog.load_transcript(
                    [
                        ("你" if role == "user" else self._pet_name(), text)
                        for role, text in saved_messages
                    ]
                )
            else:
                self._chat_dialog.append_message(
                    self._pet_name(),
                    "巴布达！没网也可以聊天；也能在设置里连接 Codex、Claude Code、DeepSeek 或 Kimi。",
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
        message = " ".join(str(message or "").split())
        if not message:
            return
        now = time.monotonic()
        if (
            message == self._last_chat_submission
            and (
                self._chat_submission_active
                or now - self._last_chat_submission_at < 1.5
            )
        ):
            LOGGER.debug("suppressed duplicate chat submission")
            return
        self._record_user_interaction()
        if self.chat_manager.busy:
            self._chat_dialog.append_message(self._pet_name(), "上一句话还在路上，稍等我一下。")
            return
        self._last_chat_submission = message
        self._last_chat_submission_at = now
        self._chat_submission_active = True
        self._chat_dialog.append_message("你", message)
        history_before = self._chat_memory.snapshot().as_history()
        self._chat_memory.add("user", message)
        self._chat_history.append("user", message)
        self._chat_dialog.show_recovery_actions(False)
        if not self.chat_manager.submit(message, history_before):
            self._chat_submission_active = False

    def _managed_chat_reply(self, reply: ManagedChatReply) -> None:
        """统一展示 AI 或离线回复；降级时不附加连接错误正文。"""

        self._chat_memory.add("assistant", reply.text)
        self._chat_history.append("assistant", reply.text)
        self._chat_submission_active = False
        if self._chat_dialog is not None:
            if self._chat_streaming_active:
                self._chat_dialog.finish_streaming_message(reply.text)
            else:
                self._chat_dialog.append_message(self._pet_name(), reply.text)
            self._chat_dialog.show_recovery_actions(reply.show_recovery_actions)
        self._chat_streaming_active = False
        self._show_emotion(reply.state, 3000)
        self.show_speech(reply.text, 6500)

    def _chat_reply_started(self) -> None:
        """Open an assistant bubble before the first network token arrives."""

        if self._chat_dialog is not None:
            self._chat_dialog.begin_streaming_message(self._pet_name())
        self._chat_streaming_active = True

    def _chat_reply_delta(self, delta: str) -> None:
        """Render App Server agentMessage/delta without waiting for turn completion."""

        if self._chat_dialog is None:
            return
        if not self._chat_streaming_active:
            self._chat_reply_started()
        self._chat_dialog.append_streaming_delta(delta)

    def _chat_busy_changed(self, busy: bool) -> None:
        """只禁用聊天输入，宠物动画、计时和音乐继续运行。"""

        if self._chat_dialog is not None:
            self._chat_dialog.set_interrupt_available(self.settings.ai_provider == "codex")
            self._chat_dialog.set_busy(busy)
        if self._chat_history_dialog is not None:
            self._chat_history_dialog.set_mutation_enabled(not busy)

    def _interrupt_chat(self) -> None:
        """Stop only the active Codex App Server turn."""

        if self.chat_manager.interrupt():
            return
        self._chat_notice("这一句暂时还不能中断，我再等它一下。")

    def _chat_notice(self, message: str) -> None:
        """显示非阻塞提示，不跳转设置页。"""

        if self._chat_dialog is not None:
            self._chat_dialog.append_message(self._pet_name(), message)

    def _clear_chat_display(self) -> None:
        """只清除聊天窗口当前显示，不删除记录、待办或 AI 上下文。"""

        if self.chat_manager.busy:
            self._chat_notice("这一句还在生成中，等它结束后再清空显示。")
            return
        if self._chat_dialog is not None:
            self._chat_dialog.clear_transcript()

    def _start_new_conversation(self) -> None:
        """确认后清掉当前上下文并启动新的本地会话，待办保持不变。"""

        if self.chat_manager.busy:
            self._chat_notice("上一句话还在路上，等它结束后再开始新对话。")
            return
        answer = QMessageBox.question(
            self._chat_dialog or self,
            "开始新对话",
            "这会清除六毛当前的聊天上下文，并让下一句话创建新的 AI 对话。\n"
            "已有聊天仍会保留在“聊天记录”里，待办和提醒不会被删除。\n\n继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if not self.chat_manager.reset_conversation():
            return
        self._chat_memory.clear()
        self._chat_history.start_new_session()
        self._chat_streaming_active = False
        self._last_chat_submission = ""
        self._last_chat_submission_at = 0.0
        self._chat_submission_active = False
        if self._chat_dialog is not None:
            self._chat_dialog.clear_transcript()
            self._chat_dialog.append_message(
                self._pet_name(),
                "好，新的聊天开始啦。之前的聊天记录还在，待办和提醒也都保留着。",
            )

    def _show_chat_history(self) -> None:
        """打开本机聊天记录查看窗口，不启动 AI 或改变当前对话。"""

        if self._chat_history_dialog is None:
            self._chat_history_dialog = ChatHistoryDialog(
                self._chat_history,
                self._pet_name(),
                None,
            )
            self._chat_history_dialog.clear_all_requested.connect(
                self._clear_all_chat_history
            )
            self._chat_history_dialog.clear_display_requested.connect(
                self._clear_chat_display
            )
            self._chat_history_dialog.new_conversation_requested.connect(
                self._start_new_conversation
            )
            self._chat_history_dialog.session_deleted_requested.connect(
                self._chat_history_session_deleted
            )
        self._chat_history_dialog.set_mutation_enabled(not self.chat_manager.busy)
        self._chat_history_dialog.refresh()
        self._chat_history_dialog.show()
        self._chat_history_dialog.raise_()
        self._chat_history_dialog.activateWindow()

    def _chat_history_session_deleted(self, _session_id: str, was_current: bool) -> None:
        """删除当前会话时同步清掉 AI 上下文，待办和提醒保持不变。"""

        if not was_current:
            return
        if not self.chat_manager.reset_conversation():
            return
        self._chat_memory.clear()
        self._chat_streaming_active = False
        if self._chat_dialog is not None:
            self._chat_dialog.clear_transcript()
            self._chat_dialog.append_message(
                self._pet_name(),
                "这段聊天记录已删除，新的聊天会从零开始；待办和提醒没有改变。",
            )

    def _clear_all_chat_history(self) -> None:
        """删除全部本地聊天记录，同时重置 AI 上下文但不碰待办。"""

        if self.chat_manager.busy:
            self._chat_notice("上一句话还在路上，等它结束后再删除聊天记录。")
            return
        answer = QMessageBox.question(
            self._chat_history_dialog or self,
            "删除全部聊天记录",
            "确定删除本机保存的全部聊天记录并重置六毛的 AI 上下文吗？\n"
            "待办、提醒和其他应用数据不会受到影响。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if not self.chat_manager.reset_conversation():
            return
        self._chat_history.clear_all()
        self._chat_memory.clear()
        self._chat_streaming_active = False
        if self._chat_history_dialog is not None:
            self._chat_history_dialog.refresh()
        if self._chat_dialog is not None:
            self._chat_dialog.clear_transcript()
            self._chat_dialog.append_message(
                self._pet_name(),
                "聊天记录已经清空，新的聊天会从零开始；待办和提醒没有改变。",
            )

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
        update_signal = getattr(dialog, "program_update_requested", None)
        if update_signal is not None:
            update_signal.connect(
                lambda: self._invoke_menu_external("program_update")
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
        self.ai_service.codex_path = str(
            getattr(self.settings, "codex_executable_path", "") or ""
        ).strip()
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
            self._social_dialog.account_state_changed.connect(self._social_account_state_changed)
            self._social_dialog.login_streak_updated.connect(self._login_streak_updated)
            self._social_dialog.food_interaction_requested.connect(self._send_food_interaction)
            self._social_dialog.food_interaction_accepted.connect(self._handle_food_interaction_accepted)
            self._social_dialog.buddy_request_received.connect(self._buddy_request_received)
            self._social_dialog.room_event_received.connect(self._room_event_received)
            self._social_dialog.buddy_subscription_notice.connect(self._buddy_subscription_notice)
            self._social_dialog.finished.connect(self._social_dialog_finished)
            self._social_dialog.focus_start_requested.connect(self.start_work_timer)
            self._social_dialog.focus_pause_requested.connect(self.pause_work_timer)
            self._social_dialog.focus_finish_requested.connect(self.finish_work_timer)
            self._social_dialog.work_report_requested.connect(self.show_work_report)
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
        self._refresh_shortcut_state()
        self._update_work_duration_bubble(snapshot)
        if self.work_controls.isVisible():
            status = str(getattr(snapshot, "status", "idle"))
            seconds = int(getattr(snapshot, "session_seconds", 0) or 0)
            self.work_controls.set_session_status(status)
            self.work_controls.set_duration_visible(bool(self.settings.show_work_duration))
            self.work_controls.set_session_duration(
                "本轮 " + format_work_duration(seconds)
                if status in {"focus", "rest"} else "本轮未开始"
            )
        if self._social_dialog is not None:
            self._social_dialog.set_focus_snapshot(snapshot)
            self._social_dialog.set_focus_analytics(self.focus_analytics.snapshot())

    def _room_event_received(self, event: dict) -> None:
        """Play a received room interaction on this desktop pet."""

        actor_id = str(
            event.get("actor_id")
            or event.get("sender_id")
            or event.get("user_id")
            or ""
        )
        if actor_id and actor_id in self._muted_buddy_ids:
            return
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

    def _buddy_request_received(self, request: dict) -> None:
        """Give a fast desktop-pet notice for a new buddy request."""

        if not isinstance(request, dict):
            return
        sender_id = str(request.get("sender_id") or request.get("requester_id") or "")
        if sender_id and sender_id in self._muted_buddy_ids:
            return
        if detect_quiet_mode().blocked:
            return
        label = social_pet_label(request.get("owner_nickname") or request.get("nickname"))
        self._set_temporary_activity("pointing", 20_000)
        self.show_speech(f"收到{label}的搭子申请。打开‘互动’页即可处理。", 5600)

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

        if not self.social_client.signed_in:
            self._economy_sync_user_id = ""
            return
        if self._social_thread is not None and self._social_thread.isRunning():
            return
        session = getattr(self.social_client, "session", None)
        user_id = str(getattr(session, "user_id", "") or "")
        if user_id and user_id != self._economy_sync_user_id:
            self._economy_sync_user_id = user_id
            # The server is authoritative when an account is opened on a new
            # computer.  An explicit local outfit change is the only action
            # allowed to write the durable outfit key back.
            self._personal_outfit_sync_pending = False
            self._sync_economy_events(
                [event.as_dict() for event in self.economy.events]
            )
        self._maybe_sync_owner_nickname()
        selected_room = self._social_dialog.current_room_id if self._social_dialog is not None else None
        # A persisted local room ID is not an invitation to re-enter a room.
        # Only the room explicitly selected in the study-room window is sent.
        room_id = selected_room
        if room_id != self.focus_session.room_id:
            self.focus_session.set_room_id(room_id)
        snapshot = self.focus_session.snapshot()
        today = datetime.now(BEIJING_TIMEZONE).date()
        week_start = today - timedelta(days=today.weekday())
        # Build the heartbeat totals from the same Beijing-local report
        # projection used by the UI.  ``focus_analytics.snapshot()`` may
        # contain an old server maximum; echoing that maximum here creates a
        # feedback loop that permanently republishes bad 5h/53h values.
        day_projection = self.focus_analytics.period_summary("day")
        week_projection = self.focus_analytics.period_summary("week")
        local_today = max(0, int(day_projection.get("total_seconds", 0) or 0))
        if int(day_projection.get("local_record_count", 0) or 0) > 0:
            # Use only the current monotonic segment.  ``snapshot.session_seconds``
            # is cumulative across pauses/checkpoints and is not a live delta.
            live_elapsed = self.work_timer.current_elapsed_seconds() if snapshot.is_running else 0
            today_seconds = min(24 * 60 * 60, local_today + live_elapsed)
        else:
            today_seconds = max(0, int(snapshot.today_seconds or 0))
        week_seconds = max(0, int(week_projection.get("total_seconds", 0) or 0))
        if today_seconds > local_today:
            week_seconds += today_seconds - local_today
        analytics = self.focus_analytics.snapshot()
        focus_history = self.focus_analytics.daily_history(days=8)
        today_key = today.isoformat()
        focus_history = [
            item for item in focus_history
            if str(item.get("focus_date") or "")[:10] != today_key
        ]
        if today_seconds > 0:
            focus_history.append({"focus_date": today_key, "seconds": today_seconds})
        focus_history.sort(key=lambda item: str(item.get("focus_date") or ""))
        presence = {
            "working": snapshot.is_running,
            "session_active": bool(self.work_timer.has_active_session),
            "work_state": str(getattr(snapshot, "state", "idle") or "idle"),
            "pause_reason": getattr(snapshot, "pause_reason", None),
            "today_seconds": today_seconds,
            "today_interruptions": int(analytics.get("today_interruptions") or 0),
            "longest_continuous_seconds": int(analytics.get("longest_continuous_seconds") or 0),
            "session_started_at": snapshot.session_started_at,
            "outfit_key": self.settings.equipped_outfit,
            "room_id": room_id,
            "quick_status": self._active_room_quick_status(),
            "quick_status_expires_at": self._room_quick_status_expires_at.isoformat()
            if self._room_quick_status_expires_at is not None else None,
            "personal_state": {
                "focus_date": today.isoformat(),
                "today_seconds": today_seconds,
                "lifetime_seconds": self.work_timer.lifetime_seconds(),
                "week_start": week_start.isoformat(),
                "week_seconds": week_seconds,
                "today_interruptions": int(analytics.get("today_interruptions") or 0),
                "longest_continuous_seconds": int(analytics.get("longest_continuous_seconds") or 0),
                "focus_history": focus_history,
                "outfit_key": self.settings.equipped_outfit,
                "outfit_set": self._personal_outfit_sync_pending,
            },
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

        self._muted_buddy_ids = {
            str(item).strip()
            for item in (data.get("muted_buddy_ids") or [])
            if str(item).strip()
        }
        self._merge_remote_personal_state(data)
        if self._personal_outfit_sync_pending and not data.get("_sync_offline"):
            self._personal_outfit_sync_pending = False

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
        def sender_id(item: object) -> str:
            if not isinstance(item, dict):
                return ""
            return str(
                item.get("sender_id")
                or item.get("requester_id")
                or item.get("peer_id")
                or item.get("user_id")
                or ""
            )

        for request in data.get("requests") or []:
            if sender_id(request) not in self._muted_buddy_ids:
                self._enqueue_buddy_request_notice(request)
        for visit in data.get("visits") or []:
            if sender_id(visit) not in self._muted_buddy_ids:
                self._enqueue_incoming_visit_notice(visit)
        active = self._active_visits_after_startup(
            [
                item for item in (data.get("active_visits") or [])
                if sender_id(item) not in self._muted_buddy_ids
            ]
        )
        if active:
            self._show_buddy_visit(active[0])
        else:
            self._hide_buddy_visit()

    @staticmethod
    def _buddy_request_id(request: dict) -> str:
        """Return a stable ID so one pending request cannot spam the pet."""

        return str(
            request.get("id")
            or request.get("request_id")
            or f"{request.get('sender_id') or request.get('user_id') or ''}:{request.get('created_at') or ''}"
        ).strip()

    def _enqueue_buddy_request_notice(self, request: object) -> None:
        """Show a lightweight pet notification for a newly observed buddy request."""

        if not isinstance(request, dict):
            return
        item = dict(request)
        request_id = self._buddy_request_id(item)
        if not request_id or request_id in self._seen_buddy_request_ids:
            return
        self._seen_buddy_request_ids.add(request_id)
        nickname = social_pet_label(
            item.get("owner_nickname")
            or item.get("nickname")
            or item.get("sender_nickname")
            or "新搭子"
        )
        self._set_temporary_activity("pointing", 20_000)
        self.show_speech(f"💌 {nickname} 发来搭子申请\n打开“互动”处理。", 7000)
        if self._social_dialog is not None:
            self._social_dialog._set_status(f"💌 {nickname} 发来搭子申请，请到“互动”处理。")

    def _active_visits_after_startup(self, active: object) -> list[dict]:
        """Ignore visits that were already active before this process started.

        The server intentionally keeps accepted visits alive for a short
        period so a heartbeat gap does not flicker.  That is useful during a
        running session but should not resurrect a stale local companion
        scene after the application is relaunched.
        """

        if not isinstance(active, list):
            return []
        fresh: list[dict] = []
        for item in active:
            if not isinstance(item, dict):
                continue
            started_text = (
                item.get("responded_at")
                or item.get("visit_started_at")
                or item.get("created_at")
            )
            try:
                started = datetime.fromisoformat(str(started_text).replace("Z", "+00:00"))
                if started.tzinfo is None:
                    started = started.astimezone()
                if started >= self._process_started_at:
                    fresh.append(dict(item))
            except (TypeError, ValueError, OverflowError):
                # Missing/invalid timestamps are treated as stale on startup;
                # a later new event with a valid server timestamp can still
                # open normally.
                continue
        return fresh

    def _merge_remote_personal_state(self, data: dict) -> None:
        """Merge same-account focus and outfit state from the server."""

        if data.get("_sync_offline") or data.get("data_source") == "local_cache":
            return
        profile = data.get("me") if isinstance(data, dict) else None
        profile = profile if isinstance(profile, dict) else {}
        presence = data.get("me_presence") if isinstance(data, dict) else None
        presence = presence if isinstance(presence, dict) else {}
        remote_date = profile.get("focus_today_date")
        remote_today = profile.get("focus_today_seconds")
        if remote_today is None:
            remote_today = presence.get("today_seconds")
        remote_lifetime = profile.get("focus_lifetime_seconds")
        if remote_date is not None or remote_today is not None or remote_lifetime is not None:
            self.work_timer.merge_remote_state(
                today_seconds=int(remote_today or 0),
                lifetime_seconds=int(remote_lifetime or 0),
                date_key=str(remote_date or datetime.now(BEIJING_TIMEZONE).date().isoformat()),
            )

        remote_week_start = profile.get("focus_week_start_date")
        remote_week_seconds = profile.get("focus_week_seconds")
        if remote_week_seconds is None:
            remote_week_seconds = presence.get("week_seconds")
        analytics_changed = self.focus_analytics.merge_remote_state(
            focus_date=str(remote_date or ""),
            today_seconds=int(remote_today or 0),
            lifetime_seconds=int(remote_lifetime or 0),
            week_start=str(remote_week_start or ""),
            week_seconds=int(remote_week_seconds or 0),
        )
        history_changed = self.focus_analytics.merge_remote_history(data.get("_focus_history"))
        local_day = self.focus_analytics.period_summary("day")
        if int(local_day.get("local_record_count", 0) or 0) > 0:
            self.work_timer.reconcile_today_seconds(
                int(local_day.get("total_seconds", 0) or 0)
            )
        if (analytics_changed or history_changed) and self._social_dialog is not None:
            self._social_dialog.set_focus_analytics(self.focus_analytics.snapshot())

        if "outfit_key" not in profile:
            return
        remote_outfit = str(profile.get("outfit_key") or "")[:60]
        allowed_outfits = {
            item.key for item in unlocked_outfits(self.work_timer.unlocked_outfit_count())
        }
        if self._login_reward_unlocked:
            allowed_outfits.add(LOGIN_REWARD_OUTFIT.key)
        if remote_outfit and remote_outfit not in allowed_outfits:
            return
        if remote_outfit == self.settings.equipped_outfit:
            return
        self.settings.equipped_outfit = remote_outfit
        save_settings(self.settings)
        self.activity_transition_timer.stop()
        self._activity_transition_from = QPixmap()
        self._activity_transition_step = self._activity_transition_steps
        self._mask_cache.clear()
        self._refresh_pixmap()
        if self._social_dialog is not None:
            self._social_dialog.outfit_key = remote_outfit

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

    def _social_account_state_changed(self, signed_in: bool) -> None:
        """切换账号时同步切换本地专注数据，防止跨账号复用计时文件。"""

        account_id = self._current_social_user_id() if signed_in else ""
        self._switch_focus_account(account_id)
        if self._social_dialog is not None:
            # A room selected under another account is not an invitation for
            # the new account to keep sending heartbeats into that room.
            self._social_dialog.current_room_id = None
            self._social_dialog._room_selection_explicit = False
            self._social_dialog._room_refresh_pending = False

        if not signed_in:
            self._economy_sync_user_id = ""
            return
        self._economy_sync_user_id = ""
        self._schedule_social_tick()

    def _login_streak_updated(self, result: dict) -> None:
        """Apply the server-authoritative three-day login wardrobe unlock."""

        if not isinstance(result, dict):
            return
        unlocked = bool(result.get("reward_unlocked"))
        newly_unlocked = bool(result.get("newly_unlocked"))
        was_unlocked = self._login_reward_unlocked
        self._login_reward_unlocked = unlocked
        if newly_unlocked and not was_unlocked:
            self._set_temporary_activity("happy", 30_000)
            self.show_speech(
                "连续登录 3 天，已解锁「三日连登搭子」！\n"
                "可以在“换装与外观”里穿上它。",
                8200,
            )
        # A server profile may already contain the reward outfit when this
        # result arrives. Re-render immediately so the account-bound outfit
        # does not wait for the next five-second social heartbeat.
        if unlocked and self.settings.equipped_outfit == LOGIN_REWARD_OUTFIT.key:
            self._refresh_pixmap()

    def _current_social_user_id(self) -> str:
        client = getattr(self, "social_client", None)
        if client is None or not getattr(client, "signed_in", False):
            return ""
        session = getattr(client, "session", None)
        return str(getattr(session, "user_id", "") or "").strip()

    def _switch_focus_account(self, account_id: str | None) -> None:
        """在本地加载目标账号的计时与分析命名空间。"""

        clean = str(account_id or "").strip()
        if clean == self._active_focus_account_id:
            return
        self.focus_session.switch_account(clean or None)
        self.focus_analytics.switch_account(clean or None)
        self.daily_stats.switch_account(clean or None)
        self.time_memory.switch_account(clean or None)
        self.economy.switch_account(clean or None)
        if hasattr(self, "_chat_memory"):
            self._chat_memory.switch_account(clean or None)
        if hasattr(self, "_chat_history"):
            self._chat_history.switch_account(clean or None)
        if hasattr(self, "offline_dialogue_manager"):
            self.offline_dialogue_manager.local_context = self.time_memory.summary.context
        if hasattr(self, "chat_manager"):
            # TimeMemory rebuilds its coordinator objects when changing
            # account; keep the chat fast-action path on the new namespace.
            self.chat_manager.action_executor = self.time_memory.actions
        self._active_focus_account_id = clean
        self._recorded_focus_session_seconds = (
            self.work_timer.analytics_recorded_session_seconds()
        )
        self._rewarded_focus_blocks = self.work_timer.today_seconds() // 600
        self._focus_quality_tracker.reset()
        self._last_focus_quality = None
        self._seen_buddy_request_ids.clear()
        self._muted_buddy_ids.clear()

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
        # A visit is a lightweight presence state. Keep the large historical
        # two-pet window available for compatibility, but do not open it for
        # ordinary room visits; the small label follows the pet instead.
        self._buddy_visit_window.hide_visit()
        nickname = str(
            peer.get("private_note_name")
            or peer.get("owner_nickname")
            or peer.get("nickname")
            or "搭子"
        ).strip()
        if sys.platform == "darwin":
            self._apply_macos_window_behavior(self.visit_status_bubble)
        self.visit_status_bubble.set_visitor(nickname)
        self._position_visit_status_bubble()
        self._raise_accessory(self.visit_status_bubble)

    def _hide_buddy_visit(self) -> None:
        """Hide the compact visit label after the server ends the visit."""

        self.visit_status_bubble.hide()
        self._buddy_visit_window.hide_visit()

    @staticmethod
    def _incoming_visit_id(event: dict) -> str:
        return str(
            event.get("id")
            or event.get("visit_id")
            or f"{event.get('sender_id') or event.get('user_id') or ''}:{event.get('created_at') or ''}:{event.get('kind') or ''}"
        )

    def _enqueue_incoming_visit_notice(self, event: dict) -> None:
        """Show each new pending interaction once and queue simultaneous ones."""

        if not isinstance(event, dict):
            return
        event = dict(event)
        event_id = self._incoming_visit_id(event)
        if not event_id or event_id in self._seen_visit_ids:
            return
        self._seen_visit_ids.add(event_id)
        self._set_temporary_activity("pointing", 20_000)
        if self._incoming_visit_notice is not None:
            if all(self._incoming_visit_id(item) != event_id for item in self._incoming_visit_queue):
                self._incoming_visit_queue.append(event)
            return
        self._present_incoming_visit_notice(event)

    def _present_incoming_visit_notice(self, event: dict) -> None:
        notice = IncomingVisitNotice(event, self)
        self._incoming_visit_notice = notice
        notice.accept_requested.connect(self._accept_incoming_visit)
        notice.reject_requested.connect(self._reject_incoming_visit)
        notice.later_requested.connect(self._defer_incoming_visit)
        notice.show()
        notice.raise_()
        notice.activateWindow()

    def _finish_incoming_visit_notice(self, event: dict) -> None:
        notice = self._incoming_visit_notice
        if notice is None or self._incoming_visit_id(notice._event_payload) != self._incoming_visit_id(event):
            return
        self._incoming_visit_notice = None
        notice.hide()
        notice.deleteLater()
        if self._incoming_visit_queue:
            self._present_incoming_visit_notice(self._incoming_visit_queue.pop(0))

    def _defer_incoming_visit(self, event: dict) -> None:
        self._finish_incoming_visit_notice(event)

    def _accept_incoming_visit(self, event: dict) -> None:
        self._respond_to_incoming_visit(event, True)

    def _reject_incoming_visit(self, event: dict) -> None:
        self._respond_to_incoming_visit(event, False)

    def _respond_to_incoming_visit(self, event: dict, accept: bool) -> None:
        notice = self._incoming_visit_notice
        if notice is None:
            return
        notice.set_busy(True, "正在接受…" if accept else "正在拒绝…")
        thread = SocialVisitResponseThread(self.social_client, event, accept, self)
        self._incoming_visit_response_threads.append(thread)
        thread.completed.connect(self._incoming_visit_response_completed)
        thread.failed.connect(self._incoming_visit_response_failed)
        thread.finished.connect(lambda current=thread: self._incoming_visit_response_finished(current))
        thread.start()

    def _incoming_visit_response_completed(self, event: dict, accepted: bool) -> None:
        self._finish_incoming_visit_notice(event)
        nickname = str(event.get("owner_nickname") or event.get("nickname") or "搭子")
        if accepted:
            kind = str(event.get("kind") or "visit")
            if kind.startswith("food_"):
                self._handle_food_interaction_accepted(event)
            else:
                self._show_buddy_visit(event)
            self.show_speech(f"已接受 {nickname} 的互动。", 4200)
        else:
            self.show_speech(f"已拒绝 {nickname} 的互动。", 3200)
        self._schedule_social_tick()

    def _incoming_visit_response_failed(self, event: dict, message: str) -> None:
        notice = self._incoming_visit_notice
        if notice is not None and self._incoming_visit_id(notice._event_payload) == self._incoming_visit_id(event):
            notice.set_busy(False)
        self.show_speech(f"处理互动失败：{message[:120]}", 5200)

    def _incoming_visit_response_finished(self, thread: SocialVisitResponseThread) -> None:
        if thread in self._incoming_visit_response_threads:
            self._incoming_visit_response_threads.remove(thread)
        thread.deleteLater()

    def _social_thread_finished(self) -> None:
        if self._social_thread is not None:
            self._social_thread.deleteLater(); self._social_thread = None

    def set_automatic_grumbling(self, enabled: bool) -> None:
        """启用或停用只在本机生成的间歇牢骚。"""

        self.settings.automatic_grumbling = bool(enabled)
        save_settings(self.settings)
        self._schedule_ambient()
        self.show_speech("偶尔发牢骚已开启。" if enabled else "偶尔发牢骚已关闭。", 3000)

    def set_work_duration_display(self, enabled: bool) -> None:
        """Persist whether the floating work-control bubble shows live duration."""

        self.settings.show_work_duration = bool(enabled)
        save_settings(self.settings)
        self.work_controls.set_duration_visible(self.settings.show_work_duration)
        self._update_work_duration_bubble()
        self.show_speech(
            "本轮工作时长显示已开启。" if enabled else "本轮工作时长显示已关闭。",
            3000,
        )

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
        mapping = {"music": "headphones", "office": "computer", "coding": "computer", "reading": "night-reading"}
        self._change_ambient_activity(mapping.get(category, "none"))

    def play_random_song(self) -> str:
        """兼容旧调用：统一转到浏览器歌手主页，不再随机唤起播放器。"""

        return self.open_chen_artist_page()

    def open_music_collection(self) -> str:
        """兼容旧调用：保留方法名，但行为与新入口完全一致。"""

        return self.open_chen_artist_page()

    def open_chen_artist_page(self) -> str:
        """按手动选择或 MP3 默认关联，用系统浏览器打开陈楚生主页。"""

        launch = launch_chen_artist_page(self.settings)
        label = ARTIST_MUSIC_SERVICE_LABELS.get(launch.service, launch.service)
        if launch.success:
            suffix = "（自动识别）" if launch.used_auto_detection else ""
            self.show_speech(f"已打开{label} · 陈楚生{suffix}", 3200)
        else:
            self.show_speech("暂时没能打开音乐网页，请确认系统浏览器可用。", 3600)
        return "听陈楚生"

    def set_artist_music_service(self, service: str) -> None:
        """Persist the platform used by the browser-based artist shortcut."""

        value = str(service or "auto").strip().casefold()
        if value not in {"auto", "netease", "qq", "apple", "kugou", "qishui"}:
            value = "auto"
        self.settings.artist_music_service = value
        save_settings(self.settings)
        self.quick_panel.set_artist_music_service(value)
        self.show_speech(
            f"听陈楚生已设为{ARTIST_MUSIC_SERVICE_LABELS.get(value, '跟随系统默认')}。",
            2600,
        )

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
        """只在用户操作完成后给出轻量反馈，不显示歌曲或 Now Playing 状态。"""

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
        if result.success:
            if isinstance(result, SongPlaybackResult):
                feedback = result.message or "给你挑了一首歌♪"
            else:
                feedback = {
                    "toggle": "播放状态已切换。",
                    "previous": "已切换到上一首。",
                    "next": "已切换到下一首。",
                }.get(result.action, "音乐操作已完成。")
        else:
            # 失败时也只反馈本次操作，避免把播放器返回的歌曲名、媒体状态或
            # Now Playing 文案重新带回六毛气泡。音乐面板是控制入口，不是状态面板。
            if isinstance(result, SongPlaybackResult):
                feedback = "随机播放暂时没有成功，请确认播放器可用。"
            else:
                feedback = {
                    "toggle": "播放/暂停暂时无法执行。",
                    "previous": "上一首暂时无法执行。",
                    "next": "下一首暂时无法执行。",
                    "play": "播放暂时无法执行。",
                    "pause": "暂停暂时无法执行。",
                }.get(result.action, "音乐操作暂时无法执行。")
        self.show_speech(feedback, 3200 if result.success else 4200)

    def set_activity(self, activity: str) -> None:
        """手动选择修正版动作表中的任意完整动作。"""

        self._set_temporary_activity(activity, 120_000)
        self.show_speech("动作已切换，六毛开始表演啦。", 2800)

    def equip_outfit(self, outfit_key: str) -> None:
        """装备已解锁娃衣；空字符串恢复经典外观。"""

        allowed = {item.key for item in unlocked_outfits(self.work_timer.unlocked_outfit_count())}
        if self._login_reward_unlocked:
            allowed.add(LOGIN_REWARD_OUTFIT.key)
        if outfit_key and outfit_key not in allowed:
            if outfit_key == LOGIN_REWARD_OUTFIT.key:
                self.show_speech("连续登录 3 天后，这套娃衣就会解锁。", 5200)
            else:
                self.show_speech("这套娃衣还在秘密王国里，再累计工作一小时就更近一点。", 5200)
            return
        self.settings.equipped_outfit = outfit_key
        save_settings(self.settings)
        self._personal_outfit_sync_pending = True
        # Cancel a half-finished action cross-fade so the newly selected outfit
        # is visible immediately, even while a transient work action is ending.
        self.activity_transition_timer.stop()
        self._activity_transition_from = QPixmap()
        self._activity_transition_step = self._activity_transition_steps
        self._mask_cache.clear()
        self._refresh_pixmap()
        self.update()
        label = next((item.name for item in ALL_OUTFITS if item.key == outfit_key), "经典六毛")
        self.show_speech(f"已换上：{label}。", 3200)

    def open_size_control(self) -> None:
        """打开连续尺寸滑块并实时应用，不改变不同动作之间的比例。"""

        dialog = SizeControlDialog(self.settings.display_height, self, self._pet_name())
        dialog.value_changed.connect(self.set_display_height)
        dialog.exec()
        save_settings(self.settings)

    def _populate_outfit_menu(self, menu: QMenu, *, default_label: str) -> None:
        """Populate an outfit submenu so pet and full menus share one selector."""

        classic = menu.addAction(default_label)
        classic.setCheckable(True)
        classic.setChecked(not self.settings.equipped_outfit)
        classic.triggered.connect(lambda: self.equip_outfit(""))
        menu.addSeparator()
        unlocked = unlocked_outfits(self.work_timer.unlocked_outfit_count())
        unlocked_keys = {item.key for item in unlocked}
        if self._login_reward_unlocked:
            unlocked_keys.add(LOGIN_REWARD_OUTFIT.key)
        for outfit in ALL_OUTFITS:
            action = menu.addAction(outfit.name)
            available = outfit.key in unlocked_keys
            action.setEnabled(available)
            action.setCheckable(True)
            action.setChecked(outfit.key == self.settings.equipped_outfit)
            if outfit.key == LOGIN_REWARD_OUTFIT.key and not available:
                action.setToolTip("连续登录 3 天后解锁")
            if available:
                action.triggered.connect(
                    lambda _checked=False, key=outfit.key: self.equip_outfit(key)
                )

    def show_outfit_menu(self) -> None:
        """Open the outfit selector from the shared menu without duplicating menus."""

        menu = QMenu(self)
        self._populate_outfit_menu(menu, default_label=f"经典{self._pet_name()}")
        menu.exec(QCursor.pos())

    def _populate_pet_companion_menu(self, menu: QMenu) -> None:
        """Keep direct affection actions here; food is a separate scenario entry."""

        for label, action_key in (
            ("给我一个抱抱", "love"),
            ("为我加油", "encourage"),
            ("提醒我休息", "rest"),
        ):
            action = menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, key=action_key: self.perform_companion_action(key)
            )

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

    def _position_quick_panel(self) -> None:
        """Place the icon dock at a fixed point above the pet's head.

        Keep the dock horizontally centered on the pet. Side placement made
        the shortcut icons cover unrelated desktop content, especially on
        macOS at the lower-right screen edge. Only switch to below-the-head
        placement when the top edge genuinely has no room; never choose a
        left/right candidate based on another accessory's geometry.
        """

        panel = self.quick_panel
        panel.adjustSize()
        area = self._screen_geometry()
        gap = 12
        pet_rect = QRect(self.x(), self.y(), self.width(), self.height())
        center_x = self.x() + (self.width() - panel.width()) // 2
        upper_y = self.y() - panel.height() - gap
        below_y = self.y() + self.height() + gap
        candidates = (
            QRect(center_x, upper_y, panel.width(), panel.height()),
            QRect(center_x, below_y, panel.width(), panel.height()),
        )
        chosen = next(
            (
                candidate
                for candidate in candidates
                if (area is None or area.contains(candidate))
                and not candidate.intersects(pet_rect)
            ),
            candidates[0],
        )
        if area is not None:
            chosen = QRect(
                min(max(chosen.x(), area.left()), area.right() - panel.width() + 1),
                min(max(chosen.y(), area.top()), area.bottom() - panel.height() + 1),
                panel.width(),
                panel.height(),
            )
        panel.move(chosen.topLeft())
        panel.position_report_button()

    def _position_work_controls(self) -> None:
        """Keep the right-click work controls above the pet with edge fallback."""

        panel = self.work_controls
        panel.adjustSize()
        area = self._screen_geometry()
        gap = 10
        pet_rect = QRect(self.x(), self.y(), self.width(), self.height())
        blocked = [pet_rect]
        for accessory in (self._compact_todo_panel, self.speech_bubble, self.quick_panel):
            if accessory is not None and accessory.isVisible():
                blocked.append(accessory.geometry())
        center_x = self.x() + (self.width() - panel.width()) // 2
        candidates = [
            (center_x, self.y() - panel.height() - gap),
            (center_x, self.y() + self.height() + gap),
            (self.x() - panel.width() - gap, self.y() + self.height() - panel.height()),
            (self.x() + self.width() + gap, self.y() + self.height() - panel.height()),
        ]
        chosen = None
        for candidate_x, candidate_y in candidates:
            candidate = QRect(candidate_x, candidate_y, panel.width(), panel.height())
            if area is not None and not area.contains(candidate):
                continue
            if any(candidate.intersects(item) for item in blocked):
                continue
            chosen = candidate
            break
        if chosen is None:
            candidate_x, candidate_y = candidates[0]
            if area is not None:
                candidate_x = min(max(candidate_x, area.left()), area.right() - panel.width() + 1)
                candidate_y = min(max(candidate_y, area.top()), area.bottom() - panel.height() + 1)
            chosen = QRect(candidate_x, candidate_y, panel.width(), panel.height())
        panel.move(chosen.topLeft())

    def _position_coffee_scene_prompt(self) -> None:
        """Place the coffee completion prompt near the pet without stealing focus."""

        panel = self.coffee_scene_prompt
        panel.adjustSize()
        area = self._screen_geometry()
        gap = 10
        pet_rect = QRect(self.x(), self.y(), self.width(), self.height())
        blocked = [pet_rect]
        for accessory in (self._compact_todo_panel, self.speech_bubble, self.quick_panel, self.work_controls):
            if accessory is not None and accessory.isVisible():
                blocked.append(accessory.geometry())
        center_x = self.x() + (self.width() - panel.width()) // 2
        candidates = [
            (center_x, self.y() - panel.height() - gap),
            (center_x, self.y() + self.height() + gap),
            (self.x() - panel.width() - gap, self.y() + self.height() - panel.height()),
            (self.x() + self.width() + gap, self.y() + self.height() - panel.height()),
        ]
        chosen = None
        for candidate_x, candidate_y in candidates:
            candidate = QRect(candidate_x, candidate_y, panel.width(), panel.height())
            if area is not None and not area.contains(candidate):
                continue
            if any(candidate.intersects(item) for item in blocked):
                continue
            chosen = candidate
            break
        if chosen is None:
            candidate_x, candidate_y = candidates[0]
            if area is not None:
                candidate_x = min(max(candidate_x, area.left()), area.right() - panel.width() + 1)
                candidate_y = min(max(candidate_y, area.top()), area.bottom() - panel.height() + 1)
            chosen = QRect(candidate_x, candidate_y, panel.width(), panel.height())
        panel.move(chosen.topLeft())

    def _position_work_duration_bubble(self) -> None:
        """Keep the live duration label directly below the pet.

        The pet is moved upward once when necessary to reserve this space.
        This removes the edge-dependent candidate search that caused the Mac
        label to jump above and below the character while the character was
        dragged to the lower-right corner.
        """

        bubble = self.work_duration_bubble
        bubble.adjustSize()
        area = self._screen_geometry()
        gap = 5
        if area is not None and not self._duration_layout_reflowing:
            target = self._constrained_position(QPoint(self.x(), self.y()))
            if target.y() != self.y():
                self._duration_layout_reflowing = True
                try:
                    self.move(target)
                finally:
                    self._duration_layout_reflowing = False
                area = self._screen_geometry()
        center_x = self.x() + (self.width() - bubble.width()) // 2
        x = center_x
        y = self.y() + self.height() + gap
        if area is not None:
            x = min(max(x, area.left()), area.right() - bubble.width() + 1)
            y = min(max(y, area.top()), area.bottom() - bubble.height() + 1)
        bubble.move(x, y)

    def _update_work_duration_bubble(self, snapshot=None) -> None:
        """Render the shared focus snapshot without creating a second timer."""

        if not hasattr(self, "work_duration_bubble"):
            return
        current = snapshot or self.focus_session.snapshot()
        show_duration = bool(getattr(self.settings, "show_work_duration", True))
        was_visible = self.work_duration_bubble.isVisible()
        if sys.platform == "darwin":
            # WorkDurationBubble may show itself while applying the session;
            # prepare its native panel before that happens.
            self._apply_macos_window_behavior(
                self.work_duration_bubble,
                always_on_top=bool(self.settings.always_on_top),
            )
        self.work_duration_bubble.set_session(
            str(getattr(current, "status", "idle")),
            int(getattr(current, "session_seconds", 0) or 0),
            show_duration,
        )
        if getattr(self, "_manually_hidden", False) or getattr(self, "_fullscreen_hidden", False):
            # set_session() intentionally owns the normal visible/hidden
            # state, so enforce fullscreen's temporary override afterwards.
            self.work_duration_bubble.hide()
        if self.work_duration_bubble.isVisible():
            self._position_work_duration_bubble()
        if not was_visible and self.work_duration_bubble.isVisible() and sys.platform != "darwin":
            self._apply_macos_window_behavior(self.work_duration_bubble)
        if self.work_duration_bubble.isVisible():
            self._raise_accessory(self.work_duration_bubble)

    def show_quick_panel(self) -> None:
        """双击切换快捷口袋；再次双击立即收起。"""

        if self.quick_panel.isVisible():
            self.quick_panel.hide()
            return
        # The double-click is an explicit request for the head shortcut dock;
        # dismiss the transient speech/working popups so they do not cover
        # the pet's body or compete with the dock.
        self.speech_bubble.hide()
        self.work_controls.hide()
        self._refresh_shortcut_state()
        self.quick_panel.set_food_inventory({
            key: self.economy.inventory_count(key)
            for key in ("coffee", "expensive_coffee", "milk_tea", "cake", "tea")
        })
        self._position_quick_panel()
        self._show_nonactivating(self.quick_panel)
        self._raise_accessory(self.quick_panel)
        # A newly positioned top-level panel can receive a synthetic
        # enterEvent on headless/offscreen runners when it opens beneath the
        # pointer. Re-arm the normal eight-second auto-hide after showing so
        # the explicit open action remains deterministic across platforms.
        self.quick_panel.hide_timer.start(8000)

    def show_work_controls(self) -> None:
        """在六毛上方显示当前状态唯一有效的工作操作。"""

        self._show_work_controls()

    def _show_work_controls(self) -> None:
        """Show the work dock above the pet using the shared focus state."""

        snapshot = self.focus_session.snapshot()
        self._update_work_duration_bubble(snapshot)
        self.work_controls.set_session_status(snapshot.status)
        self.work_controls.set_duration_visible(bool(self.settings.show_work_duration))
        duration = format_work_duration(snapshot.session_seconds)
        self.work_controls.set_session_duration(
            "本轮 " + duration if snapshot.status in {"focus", "rest"} else "本轮未开始"
        )
        self._position_work_controls()
        self._show_nonactivating(self.work_controls)
        self._raise_accessory(self.work_controls)

    def _start_work_from_control(self) -> None:
        """Start from the IDLE right-click control, then collapse it."""

        self.start_work_timer()
        self.work_controls.hide()

    def _resume_work_from_control(self) -> None:
        """Resume from the PAUSED right-click control, then collapse it."""

        self.start_work_timer()
        self.work_controls.hide()

    def _quick_work_action(self) -> None:
        """快捷入口直接切换开始、暂停和继续，不再弹出第三层控制。"""

        if self.focus_session.snapshot().status == "focus":
            self.pause_work_timer()
        else:
            self.start_work_timer()

    def _quick_food_action(self, item_key: str) -> None:
        """Use one food directly from the lightweight shortcut pocket."""

        item_key = str(item_key or "").strip()
        if item_key not in {"coffee", "expensive_coffee", "milk_tea", "cake", "tea"}:
            return
        if item_key == "cake":
            self.show_food_scene_dialog()
            return
        self._start_food_scene(
            item_key,
            10 if item_key == "milk_tea" else 0,
            "",
            "",
            source="quick_food",
        )

    def _refresh_shortcut_state(self) -> None:
        """Keep the quick panel's work label aligned with the shared session."""

        labels = {
            "idle": "开始工作",
            "focus": "暂停工作",
            "rest": "继续工作",
        }
        self.quick_panel.set_work_action_label(
            labels.get(self.focus_session.snapshot().status, "开始工作")
        )
        self.quick_panel.set_artist_music_service(
            getattr(self.settings, "artist_music_service", "auto")
        )

    def set_menu_external_callbacks(
        self, callbacks: dict[str, Callable[[bool], object]]
    ) -> None:
        """Add application-level commands used by tray/status projections."""

        self._menu_external_callbacks = dict(callbacks)

    def _invoke_menu_external(self, command: str) -> None:
        """Run an application-owned command from the quick settings menu."""

        callback = self._menu_external_callbacks.get(command)
        if callback is not None:
            callback(False)
            return
        self.show_speech("这个设置要等应用服务准备好后再执行。", 2600)

    def _menu_state(self) -> dict[str, object]:
        snapshot = self.focus_session.snapshot()
        labels = {"idle": "开始工作", "focus": "暂停工作", "rest": "继续工作"}
        work_status_text = ""
        if snapshot.status in {"focus", "rest"}:
            work_status_text = f"⏱ 已工作 {format_elapsed_clock(snapshot.session_seconds)}"
        return {
            "work_action_label": labels.get(snapshot.status, "开始工作"),
            "work_status": snapshot.status,
            "work_status_text": work_status_text,
            "visible": self.isVisible(),
            "always_on_top": bool(self.settings.always_on_top),
            "show_work_duration": bool(self.settings.show_work_duration),
            "artist_music_service": getattr(self.settings, "artist_music_service", "auto"),
            "program_version": __version__,
            "content_version": "内置内容",
        }

    def _menu_callbacks(self) -> dict[str, Callable[[bool], object]]:
        """Return commands shared by the pet, tray, and status-item menus."""

        callbacks: dict[str, Callable[[bool], object]] = {
            "chat": lambda _checked=False: self.prompt_dialogue(),
            "work": lambda _checked=False: self._quick_work_action(),
            "work_pause": lambda _checked=False: self.pause_work_timer(),
            "work_resume": lambda _checked=False: self.start_work_timer(),
            "work_finish": lambda _checked=False: self.finish_work_timer(),
            "social": lambda _checked=False: self.open_social_hub(),
            "quick_panel": lambda _checked=False: self.show_quick_panel(),
            "music_toggle": lambda _checked=False: self.control_music("toggle"),
            "music_previous": lambda _checked=False: self.control_music("previous"),
            "music_next": lambda _checked=False: self.control_music("next"),
            "chen_artist": lambda _checked=False: self.open_chen_artist_page(),
            "artist_music_auto": lambda _checked=False: self.set_artist_music_service("auto"),
            "artist_music_netease": lambda _checked=False: self.set_artist_music_service("netease"),
            "artist_music_qq": lambda _checked=False: self.set_artist_music_service("qq"),
            "artist_music_apple": lambda _checked=False: self.set_artist_music_service("apple"),
            "artist_music_kugou": lambda _checked=False: self.set_artist_music_service("kugou"),
            "artist_music_qishui": lambda _checked=False: self.set_artist_music_service("qishui"),
            "outfit": lambda _checked=False: self.show_outfit_menu(),
            "rename": lambda _checked=False: self.rename_pet(),
            "settings": lambda _checked=False: self.open_settings(SETTINGS_SOURCE_USER_ACTION),
            "show_work_duration": lambda checked=False: self.set_work_duration_display(checked),
            "size": lambda _checked=False: self.open_size_control(),
            "show_todos": lambda _checked=False: self.show_compact_todos(manual=True),
            "hide_todos": lambda _checked=False: self.hide_compact_todos(),
            "add_todo": lambda _checked=False: self.add_compact_todo(),
            "time_memory": lambda _checked=False: self.show_time_memory(),
            "show_work_time": lambda _checked=False: self.show_work_time(),
            "economy": lambda _checked=False: self.show_economy(),
            "alarms": lambda _checked=False: self.show_alarm_center(),
            "show_growth": lambda _checked=False: self.show_daily_growth(),
            "show_report": lambda _checked=False: self.show_daily_report(),
            "configure_daily_report": lambda _checked=False: self.configure_daily_report(),
            "open_album": lambda _checked=False: self.open_daily_album(),
            "topmost_on": lambda _checked=False: self.set_always_on_top(True),
            "topmost_off": lambda _checked=False: self.set_always_on_top(False),
            "visibility": lambda _checked=False: self.show_pet()
            if not self.isVisible()
            else self.hide_pet(),
            "quit": lambda _checked=False: self.quit_requested.emit(),
        }
        callbacks.update(self._menu_external_callbacks)
        return callbacks

    def unified_menu_model(self) -> UnifiedMenuModel:
        """Expose the same model to the pet, tray, and macOS status item."""

        return UnifiedMenuModel(
            pet_name=self._pet_name(),
            state_provider=self._menu_state,
            callbacks=self._menu_callbacks(),
        )

    def build_unified_menu(self, parent=None, context: str = "pet") -> QMenu:
        """Render the unified menu for the requested platform entrance."""

        # Tray/status menus must not inherit the pet window's active/enabled
        # state. A standalone menu remains usable while another app has focus
        # or while the pet itself is hidden.
        menu = QMenu(parent) if parent is not None else QMenu()
        self.refresh_unified_menu(menu, context)
        return menu

    def refresh_unified_menu(self, menu: QMenu, context: str = "pet") -> None:
        """Refresh an existing menu without replacing its native owner.

        Replacing a QSystemTrayIcon menu from ``aboutToShow`` can leave the
        platform status-item bridge holding the old action tree. Updating the
        existing standalone menu keeps the status item stable while dynamic
        work/visibility state is refreshed.
        """

        menu.clear()
        populate_qmenu(menu, self.unified_menu_model(), context)

    def _schedule_ambient(self) -> None:
        """用随机间隔安排六毛主动出现，保持存在感但避免频繁打扰。"""

        if not hasattr(self, "ambient_timer"):
            return
        self.ambient_timer.stop()
        if self.settings.automatic_grumbling:
            self.ambient_timer.start(random.randint(8 * 60_000, 18 * 60_000))

    def _night_limited_tick(self) -> None:
        """在本地 00:30–06:30 显示当天限定造型，06:30 到点恢复普通状态。"""

        selected = night_limited_activity(datetime.now())
        if selected is None:
            previous = self._night_limited_activity
            self._night_limited_activity = ""
            if previous and self._ambient_activity == previous:
                self.activity_timer.stop()
                self._manual_activity_until = 0.0
                self._change_ambient_activity(
                    random.choice(FOCUS_ACTIONS) if self.work_timer.is_running else "none"
                )
            return
        self._night_limited_activity = selected
        if time.monotonic() < self._manual_activity_until:
            return
        self.activity_timer.stop()
        self._change_ambient_activity(selected)

    def _automatic_work_companion_tick(self) -> bool:
        """偶尔在工作中主动给出拥抱或加油，不再要求用户点菜单。"""

        if not self.work_timer.is_running or random.random() >= 0.42:
            return False
        action_key = random.choice(("love", "encourage"))
        reply = self.companion.perform_action(action_key)
        option = ACTION_BY_KEY[action_key]
        self._play_action_sequence(option.sequence or (reply.state,), option.duration_ms)
        self.show_speech(reply.text, max(5200, option.duration_ms + 1800))
        return True

    def _ambient_tick(self) -> None:
        """按时段、专注长度与低概率彩蛋让六毛主动找用户。"""

        try:
            if night_limited_activity(datetime.now()) is not None:
                self._night_limited_tick()
                return
            busy = self.chat_manager.busy
            if self.isVisible() and not self.dragging and not busy:
                if self._automatic_work_companion_tick():
                    self.daily_stats.record_event("work_companion")
                    return
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
        """周期检查整点报时，工作报告改为用户按需打开。"""

        now = datetime.now()
        self._maybe_announce_hour(now)

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
        # Work controls are available from explicit work/menu actions only;
        # a normal left click on the pet must never create a floating button bar.
        self.work_controls.hide()
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
        self._show_nonactivating(self.photo_bubble)
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
        """Build the same complete app menu used by the tray and status item."""

        menu = QMenu(self)
        populate_qmenu(menu, self.unified_menu_model(), "pet")
        return menu

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """稍候显示六毛本体菜单，为双击右键语音留出判定时间。"""

        self._record_user_interaction()
        if time.monotonic() < self._suppress_context_until:
            event.accept()
            return
        global_position = getattr(event, "globalPosition", None)
        self._pending_context_global = (
            global_position().toPoint()
            if callable(global_position)
            else event.globalPos()
        )
        self.context_menu_timer.start(QApplication.doubleClickInterval() + 60)
        event.accept()

    def _show_deferred_context_menu(self) -> None:
        """确认不是双击后，打开六毛本体菜单。"""

        if time.monotonic() >= self._suppress_context_until:
            self.work_controls.hide()
            menu = self._build_context_menu()
            if sys.platform != "darwin" and bool(getattr(self.settings, "always_on_top", False)):
                # Keep the popup above the desktop-mode pet without changing
                # the ownership or flags of the real pet window.
                menu.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            menu.ensurePolished()
            pet_center = self.frameGeometry().center()
            screen = (
                QGuiApplication.screenAt(pet_center)
                or self.screen()
                or QGuiApplication.primaryScreen()
            )
            point = context_menu_position_for_pet(
                self._pending_context_global,
                pet_center,
                screen.geometry() if screen is not None else None,
                macos=sys.platform == "darwin",
            )
            if screen is not None:
                point = clamp_global_popup_position(
                    point,
                    menu.sizeHint(),
                    screen.availableGeometry(),
                )
                LOGGER.debug(
                    "context menu popup: point=%s screen=%s available=%s",
                    point,
                    screen.name(),
                    screen.availableGeometry(),
                )
            menu.exec(point)

    def eventFilter(self, watched, event) -> bool:
        """收起工作条并记录 Lili 窗口焦点变化，绝不主动重新激活。"""

        if event.type() in {
            QEvent.Type.WindowActivate,
            QEvent.Type.WindowDeactivate,
            QEvent.Type.FocusIn,
            QEvent.Type.FocusOut,
        }:
            surfaces = (
                self,
                self.quick_panel,
                self.work_controls,
                self.coffee_scene_prompt,
                self.work_duration_bubble,
                self.speech_bubble,
                self.photo_bubble,
            )
            if watched in surfaces:
                LOGGER.debug(
                    "LILI_FOCUS_EVENT type=%s object=%s active_window=%s focus_window=%s",
                    event.type().name,
                    type(watched).__name__,
                    type(QApplication.activeWindow()).__name__
                    if QApplication.activeWindow() is not None
                    else "None",
                    type(QApplication.focusWidget()).__name__
                    if QApplication.focusWidget() is not None
                    else "None",
                )

        if (
            event.type() == QEvent.Type.MouseButtonPress
            and self.work_controls.isVisible()
        ):
            watched_is_widget = isinstance(watched, QWidget)
            belongs_to_pet = watched is self or (
                watched_is_widget and self.isAncestorOf(watched)
            )
            belongs_to_controls = watched is self.work_controls or (
                watched_is_widget and self.work_controls.isAncestorOf(watched)
            )
            if not belongs_to_pet and not belongs_to_controls:
                self.work_controls.hide()
        return super().eventFilter(watched, event)

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
