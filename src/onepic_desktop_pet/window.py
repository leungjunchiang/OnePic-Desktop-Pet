Warning: truncated output (original token count: 91727)
Total output lines: 8046

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
- 键鼠空闲或视频/游戏全屏自动暂停后，回到屏幕时显示可关闭的闹钟风格“继续工作”卡片；
- Windows 与 macOS 均只向真正的视频/游戏全屏让位，普通最大化文档窗口不遮挡桌宠；
- 根据前台应用粗粒度类别显示电脑、耳机、吉他、鼓、阅读或写字图层；
- 支持头部摸动、脸部/身体/相机分区点击、连续戳击、悬停注视和拖拽后表情；
- 通过与角色素材解耦的矢量图层增强开心、害羞、惊讶、生气、困倦、疑惑、自拍和拖拽反馈；
- 优先从用户私有素材目录显示自拍成片气泡，按当前屏幕 DPI 保持清晰度，并贴近人物真实轮廓定位；
- 标准角色确认后加载本地宠物供现场验收；走路确认仍作为打包门禁；
- 维护亲密度、精力、无聊度与饱食度的会话内状态；
- 使用 QTimer 驱动状态切换及水平移动，并限制窗口不脱离当前屏幕。
- Qt 定时器、平台探测和异步同步回调均设置故障边界，单次失败只记录日志，不终止桌宠进程；
- 宠物图层使用透明顶层窗口；工作状态、串门和提示卡片使用可读的实色背景，避免平台默认背景造成黑色或透明内容区。
- 桌面待办浮层独立于工作报告窗口，两个窗口可以同时显示且互不改变可见状态。

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
from functools import wraps
from pathlib import Path
from typing import Callable

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QRect,
    QSize,
    QTime,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
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
from .alarm_ui import AlarmCard, AlarmCenterDialog, AwayRecoveryCard
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
    RoundedSurfaceLabel,
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
from .login_rewards import (
    LOGIN_REWARD_KEY,
    LoginRewardStore,
    login_reward_granted,
    login_streak_days,
)
from .liumao_worldview import family_music_mode
from .music import ARTIST_MUSIC_SERVICE_LABELS, open_chen_artist_page as launch_chen_artist_page
from .resources import resource_path
from .quiet_mode import detect_quiet_mode
from .qt_lifecycle import request_stop_all, running_threads
from .lifecycle_log import lifecycle_log
from .performance import EventLoopLagTracker, PerformanceMonitor
from .social import SocialClient, _session_user_id
from .social_ui import (
    BuddyVisitWindow,
    IncomingVisitNotice,
    SocialEventThread,
    SocialHeartbeatWorker,
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


from .compact_todo import CompactTodoPanel, compact_todo_candidates
from .menu_model import UnifiedMenuModel, populate_qmenu
from .night_limited import night_limited_activity
from . import __version__


_TAUNT_FOLLOWUP_MESSAGES = (
    "工位有人，工作没人。",
    "不急，DDL会替你急。",
    "任务还在，你倒先下线了。",
    "今日研究方法：观察任务自然消失。",
    "样本没跑，你先跑了。",
    "论文没动，鼠标倒挺活跃。",
    "Codex都醒了，你还没开工？",
    "任务：0%，精神内耗：100%。",
    "就这？",
    "离开工只差一个开始按钮。",
    "恭喜，被搭子当场抓获。",
    "有人已经发现你没在干活了。",
    "还欠20分钟，别装作没看见。",
)


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


def _guard_qt_callback(method):
    """Keep a transient native/API failure from stopping Qt's event loop.

    Timers and queued signal callbacks are invoked by C++ and an exception
    escaping one of them can terminate a packaged Windows process without a
    visible error.  The application-level ``QApplication.notify`` guard
    covers widget events; this decorator covers the callbacks that are
    invoked directly by ``QTimer.timeout``/worker signals, especially the
    platform probes used while an external debugger or updater is active.
    """

    @wraps(method)
    def guarded(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            LOGGER.exception("[Qt] callback failed; continuing: %s", method.__qualname__)
            return None

    return guarded
SETTINGS_SOURCE_USER_ACTION = "user_action"


DEFAULT_WALK_MOTION_FACTORS = (0.45, 0.7, 1.2, 1.65, 0.45, 0.7, 1.2, 1.65)


# Keep menus readable when the pet is over a dark wallpaper.  This is scoped
# to QMenu instances only; it does not alter the transparent pet silhouette or
# the native AppKit menu used by macOS Dock/status-item rendering.
UNIFIED_MENU_STYLE = """
QMenu {
    background: #f8fbfd;
    color: #203847;
    border: 1px solid #b9d1dc;
    padding: 4px;
}
QMenu::item {
    background: transparent;
    padding: 6px 26px 6px 10px;
}
QMenu::item:selected {
    background: #d9eeeb;
    color: #203847;
}
QMenu::item:disabled { color: #8fa0a8; }
QMenu::separator {
    height: 1px;
    background: #d6e1e6;
    margin: 4px 8px;
}
"""


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
            "QWidget { background: #ffffff; border: 1px solid #b9d1dc; "
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
        lifecycle_log("pet_window.construct.begin", self)
        self.destroyed.connect(
            lambda _obj=None: lifecycle_log(
                "pet_window.destroy", class_name="PetWindow"
            )
        )
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
        self._focus_projection_revision = 0
        self._focus_projection_cache: dict[str, object] | None = None
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
        self._time_memory_window: TimeMemoryWindow | None = None
        self._todo_center_window: TodoCenterWindow | None = None
        self._economy_dialog: EconomyDialog | None = None
        self._food_scene_dialog: FoodSceneDialog | None = None
        self._work_report_dialog: WorkReportDialog | None = None
        self._alarm_center_dialog: AlarmCenterDialog | None = None
        self._alarm_card: AlarmCard | None = None
        # Keep a closing card alive until its queued QMediaPlayer stop has
        # completed.  Deleting it immediately can destroy the native media
        # backend while it is still processing a button/close event.
        self._retired_alarm_cards: list[AlarmCard] = []
        self._away_recovery_card: AwayRecoveryCard | None = None
        self.focus_analytics = FocusAnalyticsStore(
            persist=os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1"
        )
        self.focus_session.set_period_seconds_provider(self._shared_focus_period_seconds)
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
        self._performance = PerformanceMonitor()
        self._event_loop_lag = EventLoopLagTracker(self._performance)
        self._last_perf_summary_at = time.monotonic()
        self._last_focus_analytics_ui_refresh = 0.0
        self._last_focus_snapshot_status = ""
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
        # The taunt is server-authoritative and survives local animation or
        # focus transitions.  It is cleared only when the taunt RPC says the
        # target's start+20 minute punishment window has ended.
        self._taunt_active = False
        self._taunt_id = ""
        self._taunt_sender_nickname = ""
        self._taunt_sender_names: list[str] = []
        self._taunt_message = ""
        self._taunt_messages: list[str] = []
        self._taunt_chatter_last_message = ""
        self._taunt_remaining_work_seconds = 1200
        self._taunt_countdown_last_tick = time.monotonic()
        self._encouragement_active = False
        self._encouragement_id = ""
        self._encouragement_sender_nickname = ""
        self._encouragement_message = ""
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
        self._away_recovery_reason: str | None = None
        self._away_recovery_started_at: float | None = None
        self._away_recovery_prompt_shown = False
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
        self._close_retry_scheduled = False
        self._social_thread: SocialSyncThread | None = None
        self._social_request_generation = 0
        self._last_applied_social_generation = 0
        # Presence must not wait behind the dashboard/statistics request chain.
        # Keep the worker parented for Qt ownership, while closeEvent also
        # explicitly stops and waits for its cooperative condition loop.
        self._social_heartbeat_thread = SocialHeartbeatWorker(self.social_client)
        self._social_personal_sync_due = True
        self._last_social_personal_sync_at = 0.0
        self._last_social_leaderboard_at = 0.0
        self._last_social_reaction_state_at = 0.0
        self._social_presence_context_signature: tuple[str, str, str, str] | None = None
        self._social_event_threads: list[SocialEventThread] = []
        self._social_profile_threads: list[SocialProfileThread] = []
        self._owner_nickname_sync_key: tuple[str, str] | None = None
        self._owner_nickname_sync_inflight = False
        # A fresh computer must first read the account profile before its
        # empty local setting is allowed to sync back to the server.  Keep the
        # cursor account-scoped so switching accounts cannot leak a nickname.
        self._owner_nickname_remote_loaded_for = ""
        # Economy events are an append-only local ledger.  A complete,
        # idempotent replay is used when an account becomes available so a
        # transient offline period cannot leave the leaderboard behind the
        # local supply-station balance forever.
        self._economy_sync_lock = threading.Lock()
        self._economy_sync_inflight = False
        self._economy_sync_pending = False
        self._economy_sync_user_id = ""
        self._personal_outfit_sync_pending = False
        self._personal_outfit_sync_user_id = ""
        self._personal_outfit_fence_key = ""
        self._personal_outfit_fence_until = 0.0
        self._login_reward_account_id = self._current_social_user_id()
        self._login_reward_store = LoginRewardStore(
            self._login_reward_account_id,
            persist=os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1",
        )
        # The selected outfit remains a compatibility hint only while no
        # account is known.  Once signed in, the account-scoped entitlement or
        # the login RPC must prove ownership.
        self._login_reward_unlocked = self._login_reward_store.is_unlocked(
            LOGIN_REWARD_KEY
        ) or (
            not self._login_reward_account_id
            and self.settings.equipped_outfit == LOGIN_REWARD_OUTFIT.key
        )
        self._buddy_visit_window = BuddyVisitWindow()
        self.visit_status_bubble = VisitStatusBubble()
        self._seen_visit_ids: set[str] = set()
        # A notification that the user has accepted, rejected, or explicitly
        # deferred must not be recreated from the same dashboard snapshot.
        # The server remains the source of truth; this is only a short-lived
        # UI fence for the current account/process.
        self._handled_visit_ids: set[str] = set()
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
            self._shared_work_status_text,
            lambda: self._shared_today_focus_seconds() // 3600,
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
        self._last_growth_hour = stage_for_seconds(self._shared_today_focus_seconds()).hour
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
            self._media_player.playbackStateChanged.connect(
                lambda state: lifecycle_log(
                    "media.player.state_changed",
                    self._media_player,
                    owner="PetWindow",
                    signal="playbackStateChanged",
                    state=str(state),
                )
            )
            self._media_player.errorOccurred.connect(
                lambda error: lifecycle_log(
                    "media.player.error",
                    self._media_player,
                    owner="PetWindow",
                    error=str(error),
                )
            )

        self.setWindowFlags(self._pet_window_flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle(f"{APP_DISPLAY_NAME} · {self._pet_name()}")
        self.setMouseTracking(True)

        source = self._pixmaps[PetState.IDLE][0]
        width = round(settings.display_height * source.width() / source.height())
        self.setFixedSize(width + 12, settings.display_height + 14)
        self.label = QLabel(self)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.label.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.label.setAutoFillBackground(False)
        self.label.setStyleSheet("background: transparent;")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setGeometry(6, 0, width, settings.display_height + 8)

        self.photo_bubble = QLabel()
        self.photo_bubble.setWindowFlags(self._ambient_window_flags())
        self.photo_bubble.setAttribute(
            Qt.WidgetAttribute.WA_ShowWithoutActivating,
            True,
        )
        self.photo_bubble.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.photo_bubble.setAutoFillBackground(False)
        self.photo_bubble.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_bubble.setStyleSheet("background: transparent;")

        self.speech_bubble = RoundedSurfaceLabel(
            None,
            fill="#eff5f8",
            border="#4b6070",
            radius=15,
        )
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
            "QLabel { background: transparent; "
            "color: #27313d; border: none; border-radius: 15px; "
            "padding: 10px 13px; font-size: 14px; }"
        )
        self.speech_bubble.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

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
        self.quick_panel.layout_changed.connect(self._position_quick_panel)
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

        self.taunt_chatter_timer = QTimer(self)
        self.taunt_chatter_timer.setSingleShot(True)
        self.taunt_chatter_timer.timeout.connect(self._taunt_chatter_tick)

        self.effect_timer = QTimer(self)
        self.effect_timer.setInterval(90)
        self.effect_timer.timeout.connect(self._effect_tick)

        self.bob_timer …71727 tokens truncated…od_inventory({
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

        # The right-click work dock and the double-click shortcut dock are
        # mutually exclusive.  In particular, hide the detached report
        # button owned by the shortcut dock before placing this control bar.
        self.quick_panel.hide()
        snapshot = self.focus_session.snapshot(include_projection=False)
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

        # The report shortcut is a detached top-level window above the work
        # button. Collapse the whole shortcut dock before changing focus
        # state so macOS cannot leave that secondary button behind while the
        # primary start/pause shortcut disappears.
        self.quick_panel.hide()
        if self.focus_session.snapshot(include_projection=False).status == "focus":
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

    def _refresh_shortcut_state(self, snapshot=None) -> None:
        """Keep the quick panel's work label aligned with the shared session."""

        snapshot = snapshot or self.focus_session.snapshot(include_projection=False)
        labels = {
            "idle": "开始工作",
            "focus": "暂停工作",
            "rest": "继续工作",
        }
        self.quick_panel.set_work_action_label(
            labels.get(str(getattr(snapshot, "status", "idle") or "idle"), "开始工作")
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
        snapshot = self.focus_session.snapshot(include_projection=False)
        labels = {"idle": "开始工作", "focus": "暂停工作", "rest": "继续工作"}
        work_status_text = ""
        if snapshot.status in {"focus", "rest"}:
            work_status_text = f"⏱ 今日已工作 {format_elapsed_clock(snapshot.today_seconds)}"
        unlocked_keys = {
            item.key for item in unlocked_outfits(self.work_timer.unlocked_outfit_count())
        }
        if self._login_reward_unlocked:
            unlocked_keys.add(LOGIN_REWARD_OUTFIT.key)
        outfit_options: list[dict[str, object]] = [
            {
                "title": f"经典{self._pet_name()}",
                "command": "outfit_classic",
                "enabled": True,
                "checkable": True,
                "checked": not bool(self.settings.equipped_outfit),
            },
            {"separator": True},
        ]
        outfit_options.extend(
            {
                "title": outfit.name,
                "command": f"outfit_{outfit.key}",
                "enabled": outfit.key in unlocked_keys,
                "checkable": True,
                "checked": outfit.key == self.settings.equipped_outfit,
            }
            for outfit in ALL_OUTFITS
        )
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
            "outfit_options": outfit_options,
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
        callbacks["outfit_classic"] = lambda _checked=False: self.equip_outfit("")
        callbacks.update(
            {
                f"outfit_{outfit.key}": (
                    lambda _checked=False, key=outfit.key: self.equip_outfit(key)
                )
                for outfit in ALL_OUTFITS
            }
        )
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
        lifecycle_log("menu.create", menu, context=context)
        menu.destroyed.connect(
            lambda _obj=None, value=context: lifecycle_log(
                "menu.destroy", class_name="QMenu", context=value
            )
        )
        menu.setStyleSheet(UNIFIED_MENU_STYLE)
        self.refresh_unified_menu(menu, context)
        return menu

    def refresh_unified_menu(self, menu: QMenu, context: str = "pet") -> None:
        """Refresh an existing menu without replacing its native owner.

        Replacing a QSystemTrayIcon menu from ``aboutToShow`` can leave the
        platform status-item bridge holding the old action tree. Updating the
        existing standalone menu keeps the status item stable while dynamic
        work/visibility state is refreshed.
        """

        menu.setStyleSheet(UNIFIED_MENU_STYLE)
        menu.clear()
        populate_qmenu(menu, self.unified_menu_model(), context)

    def _schedule_ambient(self) -> None:
        """用随机间隔安排六毛主动出现，保持存在感但避免频繁打扰。"""

        if not hasattr(self, "ambient_timer"):
            return
        self.ambient_timer.stop()
        if self.settings.automatic_grumbling:
            self.ambient_timer.start(random.randint(8 * 60_000, 18 * 60_000))

    @_guard_qt_callback
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

    @_guard_qt_callback
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
                # Use the same cross-application input clock as the work
                # safety net. A user typing in another app must not look
                # idle merely because the pet itself was not clicked.
                idle_seconds = self._inactive_ms() / 1000.0
                # All duration-based ambient decisions use the same
                # reconciled snapshot as the bottom work bubble.  Reading
                # WorkTimer.session_seconds() directly here could resurrect a
                # stale checkpoint and produce a reminder longer than today's
                # actual total (for example, 2:05 above a 1:48 day total).
                focus_snapshot = self.focus_session.snapshot()
                today_seconds = max(0, int(focus_snapshot.today_seconds))
                session_seconds = min(
                    max(0, int(focus_snapshot.session_seconds)),
                    today_seconds,
                )
                continuous_seconds = min(
                    max(0, int(getattr(focus_snapshot, "current_continuous_seconds", 0) or 0)),
                    today_seconds,
                )
                if self.work_timer.is_running and continuous_seconds >= 2 * 3600:
                    activity, text = "thermos", "连续工作两小时啦。六毛把水杯端来了：先休息一下？"
                elif idle_seconds >= 30 * 60:
                    activity, text = "pointing", "你很久没动啦，六毛偷偷探头看看你还在不在。"
                elif today_seconds >= 3 * 3600 and random.random() < 0.06:
                    activity, text = "wild-king", "极低概率彩蛋：荒野国王路过你的桌面。"
                elif random.random() < 0.55:
                    decision = self.companion_behavior.decide(
                        now_hour=datetime.now().hour,
                        working=self.work_timer.is_running,
                        session_seconds=session_seconds,
                        today_seconds=today_seconds,
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
                # Automatic companion animations must not announce a rest
                # state while the shared work timer is still running. A
                # deliberate pause/food scene remains unaffected because it
                # does not pass through this ambient path.
                if self.work_timer.is_running and activity in {
                    "sleep",
                    "sleepy",
                    "daydream",
                    "coconut",
                    "sunbath",
                    "movie",
                    "pointing",
                }:
                    activity = "office"
                    text = "六毛继续陪你专注，等你一起完成这一小段。"
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

    @_guard_qt_callback
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

    @_guard_qt_callback
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
            f"{self.companion.status_text(self._shared_today_focus_seconds() // 600)}\n{next_text}",
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
            and self._shared_today_focus_seconds() == 0
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
            lifecycle_log(
                "media.player.stop",
                self._media_player,
                owner="PetWindow",
                reason="play_babuda_voice.replace_source",
            )
            self._media_player.stop()
            self._media_player.setPlaybackRate((0.96, 1.0, 1.05)[index % 3])
            self._media_player.setSource(QUrl.fromLocalFile(str(path.resolve())))
            lifecycle_log(
                "media.player.play",
                self._media_player,
                owner="PetWindow",
                source_kind="babuda_voice",
            )
            self._media_player.play()
        elif self._speech_engine is not None:
            self._speech_engine.setRate((-0.08, 0.0, 0.08)[index % 3])
            self._speech_engine.setPitch((-0.05, 0.0, 0.08)[index % 3])
            self._speech_engine.say("巴布达")
        self._show_emotion(random.choice((PetState.HAPPY, PetState.SHY, PetState.SURPRISED)), 1500)
        self.show_speech(random.choice(("巴布达！", "巴——布达。", "巴布达？六毛在呢。")), 2800)

    @_guard_qt_callback
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

    @_guard_qt_callback
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
        # Some Qt/macOS backends apply a native frame offset when a detached
        # translucent window is first shown.  Correct against the actual
        # top-level coordinate so the visible photo edge remains exactly
        # ``gap`` pixels from the character mask (and does not drift by the
        # platform's invisible frame margin).
        actual_x = self.photo_bubble.x()
        if actual_x != x:
            self.photo_bubble.move(x + (x - actual_x), y)
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

    @_guard_qt_callback
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
        lifecycle_log("menu.create", menu, context="context")
        menu.destroyed.connect(
            lambda _obj=None: lifecycle_log(
                "menu.destroy", class_name="QMenu", context="context"
            )
        )
        menu.setStyleSheet(UNIFIED_MENU_STYLE)
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
        self.context_menu_timer.start(QApplication.doubleClickInterval())
        event.accept()

    @_guard_qt_callback
    def _show_deferred_context_menu(self) -> None:
        """确认不是双击后，打开六毛本体菜单。"""

        if time.monotonic() >= self._suppress_context_until:
            self.work_controls.hide()
            menu = self._build_context_menu()
            lifecycle_log("menu.show.request", menu, context="context")
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
            lifecycle_log("menu.close.complete", menu, context="context")

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

