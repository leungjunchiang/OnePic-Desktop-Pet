Warning: truncated output (original token count: 87660)
Total output lines: 7724

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
        lifecycle_log("pet_window.construct.complete", self)

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

    @_guard_qt_callback
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
        # A punishment outfit is deliberately stronger than ordinary ambient
        # or food animations: the server state, bubble, and sprite must end
        # together instead of one local timer restoring the old outfit early.
        if self._taunt_active:
            activity = "taunt"
            food_scene_active = False
        elif self._encouragement_active:
            # A persistent encouragement uses the normal work-cheer sprite,
            # but keeps it pinned until the receiver pauses or the hour ends.
            activity = "work-cheer"
            food_scene_active = False
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

    @_guard_qt_callback
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

    @_guard_qt_callback
    def _effect_tick(self) -> None:
        """推进表情符号的轻微漂浮动画并刷新合成帧。"""

        if emotion_effect_name(self.state) is None:
            self.effect_timer.stop()
            return
        self._effect_phase = (self._effect_phase + 1) % 12
        self._refresh_pixmap()

    @_guard_qt_callback
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

        lifecycle_log("pet_window.show_event.begin", self)
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
        lifecycle_log("pet_window.show_event.end", self)

    @_guard_qt_callback
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
                # The pet owns several detached, non-activating top-level
                # surfaces (status bubbles, controls and the quick panel).
                # Qt's WindowStaysOnTopHint is not consistently retained for
                # those windows after a hide/show cycle on Windows. Reapply
                # the same native z-order to every visible surface so Word or
                # a normal maximised browser cannot cover only part of Lili.
                if self.settings.always_on_top:
                    for accessory in self._fullscreen_surfaces():
                        if accessory is self or not accessory.isVisible():
                            continue
                        try:
                            accessory_hwnd = int(accessory.winId())
                            user32.SetWindowPos(
                                accessory_hwnd,
                                -1,
                                0,
                                0,
                                0,
                                0,
                                0x0001 | 0x0002 | 0x0010 | 0x0040,
                            )
                        except (AttributeError, OSError, TypeError, ValueError):
                            continue
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

        lifecycle_log("pet_window.hide_event.begin", self)
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
        lifecycle_log("pet_window.hide_event.end", self)

    def hide_pet(self) -> None:
        """Hide the pet and every detached accessory until explicitly shown."""

        lifecycle_log("pet_window.hide.request", self, source="explicit")
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

        lifecycle_log("pet_window.show.request", self, source="explicit")
        self._manually_hidden = False
        # Use the same platform-aware policy as the polling timer.  On macOS
        # a normal maximised Word/browser window can be screen-sized without
        # being a media/game takeover, so the raw geometry helper is too broad
        # here and could leave the pet hidden after an explicit Show action.
        self._sync_fullscreen_visibility()
        if self._fullscreen_hidden:
            lifecycle_log("pet_window.show.blocked_fullscreen", self)
            return
        self.show()
        lifecycle_log("pet_window.show.call", self, source="explicit")

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
            self._away_recovery_card,
        ):
            if optional is not None:
                surfaces.append(optional)
        return list(dict.fromkeys(surfaces))

    @_guard_qt_callback
    def _sync_fullscreen_visibility(self) -> None:
        """Temporarily yield only to fullscreen media or games.

        macOS exposes many ordinary maximised windows as screen-sized Quartz
        windows.  Treating the geometry alone as a fullscreen takeover makes
        the desktop pet disappear behind Word, browsers, terminals, and
        other everyday apps.  On macOS we therefore require both the native
        fullscreen geometry and a known media/game process.  Other platforms
        keep the existing conservative geometry fallback for compatibility.
        """

        # A screen-sized Word, browser or terminal window is still an ordinary
        # desktop window. Use the same process-aware media/game policy on every
        # desktop platform; only a known video player/browser video fullscreen
        # or a known game fullscreen may temporarily cover the pet.
        fullscreen = bool(active_fullscreen_video() or active_fullscreen_game())
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
        # A Todo may have changed while media/game fullscreen was covering
        # the desktop. Re-evaluate the unread projection after restoring.
        self._refresh_todo_surfaces()
        if (
            self.work_timer.has_active_session
            and self.work_timer.pause_reason == "fullscreen_video"
        ):
            self._show_away_recovery_prompt("fullscreen_video")

    def _thread_shutdown_roots(self) -> tuple[QObject | None, ...]:
        """Return every top-level object that can own a Qt worker thread."""

        return (
            self,
            self._social_dialog,
            self._chat_dialog,
            self._chat_history_dialog,
        )

    @_guard_qt_callback
    def _retry_close_after_threads_stop(self) -> None:
        self._close_retry_scheduled = False
        lifecycle_log("pet_window.close.retry", self)
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭宠物时保存计时并停止 Agent、音乐控制及独立气泡窗口。"""

        lifecycle_log("pet_window.close_event.begin", self)
        if self._qt_application is not None:
            self._qt_application.removeEventFilter(self)
        # Ask every descendant/top-level utility window worker to stop before
        # any parent widget can be closed.  The study-room and chat windows
        # are intentionally top-level, so they are not children of ``self``.
        thread_roots = self._thread_shutdown_roots()
        request_stop_all(*thread_roots)
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
        if self._away_recovery_card is not None:
            self._close_away_recovery_card()
        if self._economy_dialog is not None:
            self._economy_dialog.close()
        if self._work_report_dialog is not None:
            self._work_report_dialog.close()
        if self._social_dialog is not None:
            self._social_dialog.close()
        if self._chat_dialog is not None:
            self._chat_dialog.close()
        if self._social_thread is not None and self._social_thread.isRunning():
            self._social_thread.wait(2500)
        if self._media_player is not None:
            lifecycle_log(
                "media.player.stop",
                self._media_player,
                owner="PetWindow",
                reason="pet_window.close_event",
            )
            self._media_player.stop()

        running = running_threads(*thread_roots)
        if running:
            names = ", ".join(type(thread).__name__ for thread in running)
            LOGGER.warning(
                "[Lifecycle] delaying PetWindow close until QThreads stop: %s",
                names,
            )
            event.ignore()
            if not self._close_retry_scheduled:
                self._close_retry_scheduled = True
                QTimer.singleShot(250, self._retry_close_after_threads_stop)
            lifecycle_log("pet_window.close_event.ignored_threads_running", self)
            return
        super().closeEvent(event)
        lifecycle_log("pet_window.close_event.end", self)

    @_guard_qt_callback
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

    @_guard_qt_callback
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

    @_guard_qt_callback
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
        """返回用户无输入时长，工作中优先采用系统键鼠事件时钟。

        The local cursor/menu clock only sees interactions with 六毛 itself.
        During a focus session, that would make the pet sit or sleep even
        while the user is typing in another app. The native system probe
        aggregates keyboard and mouse input across applications; when it is
        unavailable it returns zero, which is the safe choice for a running
        work session rather than inferring that the user stopped working.
        """

        local_ms = max(0, round((time.monotonic() - self._last_user_interaction) * 1000))
        if not self.work_timer.is_running:
            return local_ms
        try:
            system_seconds = max(0.0, float(system_idle_seconds()))
        except (TypeError, ValueError, OverflowError):
            system_seconds = 0.0
        if system_seconds <= 0:
            return 0
        return min(local_ms, round(system_seconds * 1000))

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
        self._away_recovery_reason = None
        self._away_recovery_started_at = None
        self._away_recovery_prompt_shown = False
        self._close_away_recovery_card()

    def _close_away_recovery_card(self) -> None:
        """Close the transient return-to-work card without changing focus."""

        card = self._away_recovery_card
        self._away_recovery_card = None
        if card is not None:
            lifecycle_log("away_recovery.close.entry", card, source="PetWindow")
            self._fullscreen_restore_visible.pop(card, None)
            card.close_from_app()
            card.deleteLater()

    def _show_away_recovery_prompt(self, reason: str) -> None:
        """Offer an explicit resume action after an automatic away pause."""

        reason = str(reason or "").strip().casefold()
        if reason not in {"idle_10m", "fullscreen_video"}:
            return
        if not bool(getattr(self.settings, "show_away_recovery_prompt", True)):
            return
        if not self.work_timer.has_active_session:
            return
        if self.work_timer.pause_reason != reason:
            return
        if self._away_recovery_prompt_shown:
            return
        self._away_recovery_prompt_shown = True
        started = self._away_recovery_started_at or time.monotonic()
        away_seconds = max(1, round(time.monotonic() - started))
        self._close_away_recovery_card()
        card = AwayRecoveryCard(reason, away_seconds)
        card.continue_requested.connect(self._continue_from_away_recovery)
        card.dismiss_requested.connect(self._dismiss_away_recovery)
        self._away_recovery_card = card
        card.center_on_current_screen()
        lifecycle_log("away_recovery.show.request", card, reason=reason)
        self._show_nonactivating(card, always_on_top=True)

    def _continue_from_away_recovery(self) -> None:
        """Resume the shared focus session only after an explicit click."""

        self._close_away_recovery_card()
        self.start_work_timer()

    def _dismiss_away_recovery(self) -> None:
        """Keep the session paused when the user closes the return card."""

        self._close_away_recovery_card()

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

    @_guard_qt_callback
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

            threshold = max(600, int(getattr(self.settings, "idle_pause_seconds", 600)))
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
            and system_idle_seconds() < max(600, int(getattr(self.settings, "idle_pause_seconds", 600)))
            and not self._pause_notice_shown
        ):
            self._pause_notice_shown = True
            self._show_away_recovery_prompt("idle_10m")
            if not bool(getattr(self.settings, "show_away_recovery_prompt", True)):
                self.show_speech(
                    "回来啦，刚才十分钟没动，我帮你停表了。点‘继续工作’再开。",
                    6200,
                )

    @_guard_qt_callback
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
        self.animation_timer.start(self._frame_interval(se…37660 tokens truncated…nt_payload)
        for visit in pending_visits:
            visit_id = self._incoming_visit_id(visit) if isinstance(visit, dict) else ""
            if (
                sender_id(visit) not in self._muted_buddy_ids
                and visit_id not in self._handled_visit_ids
            ):
                self._enqueue_incoming_visit_notice(visit)
        active = self._active_visits_after_startup(
            [
                item for item in (data.get("active_visits") or [])
                if sender_id(item) not in self._muted_buddy_ids
            ]
        )
        if taunt_active or encouragement_active:
            # The compact bubble is shared with normal串门状态.  A taunt is
            # intentionally the higher-priority label while its punishment is
            # active; both persistent reactions keep the historical two-pet
            # visit window closed.
            self._buddy_visit_window.hide_visit()
        elif active:
            self._show_buddy_visit(active[0])
        else:
            self._hide_buddy_visit()

    @staticmethod
    def _taunt_text_list(value: object, *, limit: int = 6) -> list[str]:
        """Normalize a server list (or an older JSON/string fallback)."""

        candidate = value
        if isinstance(candidate, str):
            raw = candidate.strip()
            if raw.startswith("["):
                try:
                    candidate = json.loads(raw)
                except (TypeError, ValueError):
                    candidate = [raw]
            else:
                candidate = [raw]
        if not isinstance(candidate, (list, tuple)):
            return []
        result: list[str] = []
        for item in candidate:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text[:120])
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _format_taunt_senders(names: list[str], support_count: int) -> str:
        """Return the compact label shown beside today's work duration."""

        clean = [str(name).strip()[:40] for name in names if str(name).strip()]
        if not clean:
            return "搭子"
        if len(clean) == 1:
            count = max(1, int(support_count or 1))
            return f"{clean[0]}等{count}位搭子" if count > 1 else clean[0]
        if len(clean) == 2:
            return f"{clean[0]}和{clean[1]}"
        return "、".join(clean[:-1]) + f"和{clean[-1]}"

    def _apply_taunt_state(self, state: object) -> bool:
        """Render the receiver's persistent taunt and return whether active."""

        if not isinstance(state, dict) or "active" not in state:
            # Older relays may omit the optional RPC.  Never clear a valid
            # punishment merely because a compatibility response was sparse.
            return self._taunt_active
        active = bool(state.get("active"))
        if active:
            taunt_id = str(state.get("id") or state.get("taunt_id") or "")
            is_new_taunt = not self._taunt_active or taunt_id != self._taunt_id
            sender_names = self._taunt_text_list(
                state.get("sender_display_names")
                or state.get("sender_nicknames")
                or state.get("taunter_nicknames")
                or state.get("sender_names")
            )
            fallback_sender = str(
                state.get("sender_display_name")
                or state.get("private_note_name")
                or state.get("sender_nickname")
                or state.get("nickname")
                or state.get("sender_name")
                or "搭子"
            ).strip()[:40]
            if not sender_names and fallback_sender:
                sender_names = [fallback_sender]
            support_count = int(state.get("support_count") or len(sender_names) or 1)
            sender = self._format_taunt_senders(sender_names, support_count)
            raw_remaining = state.get("remaining_work_seconds")
            if raw_remaining is not None:
                try:
                    self._taunt_remaining_work_seconds = max(0, int(raw_remaining))
                except (TypeError, ValueError):
                    pass
            elif is_new_taunt:
                self._taunt_remaining_work_seconds = 1200
            self._taunt_countdown_last_tick = time.monotonic()
            taunt_messages = self._taunt_text_list(
                state.get("messages") or state.get("taunt_messages"),
                limit=14,
            )
            self._taunt_active = True
            self._taunt_id = taunt_id
            self._taunt_sender_nickname = sender
            self._taunt_sender_names = sender_names
            self._taunt_message = str(state.get("message") or "怎么，今天准备靠意念完成？")[:120]
            self._taunt_messages = taunt_messages
            self._change_ambient_activity("taunt")
            if sys.platform == "darwin":
                self._apply_macos_window_behavior(self.visit_status_bubble)
            # Keep this call compatible with older builds whose bubble only
            # accepts the taunter nickname.  The server still carries the
            # supporter count, while the compact bubble text remains stable.
            self.visit_status_bubble.set_taunter(
                sender,
                support_count=support_count,
                remaining_seconds=self._taunt_remaining_work_seconds,
            )
            if is_new_taunt:
                self._taunt_chatter_last_message = self._taunt_message
                self.show_speech(f"{sender}：{self._taunt_message}", 5200)
                self._schedule_taunt_chatter()
            elif not self.taunt_chatter_timer.isActive():
                # Recover the periodic chatter after a sleep/resume or a
                # transient timer reset without repeating the current line.
                self._schedule_taunt_chatter()
            self._position_visit_status_bubble()
            self._raise_accessory(self.visit_status_bubble)
            return True
        if self._taunt_active:
            self._taunt_active = False
            self.taunt_chatter_timer.stop()
            self._taunt_id = ""
            self._taunt_sender_nickname = ""
            self._taunt_sender_names = []
            self._taunt_message = ""
            self._taunt_messages = []
            self._taunt_chatter_last_message = ""
            self._taunt_remaining_work_seconds = 1200
            self._taunt_countdown_last_tick = time.monotonic()
            if self._ambient_activity == "taunt":
                self._change_ambient_activity("none")
            self.visit_status_bubble.hide()
            if not detect_quiet_mode().blocked:
                self.show_speech(random.choice((
                    "行，20分钟到了，放你一马。",
                    "这次算你还清了。",
                    "嘲讽解除，勉强合格。",
                    "赎身成功，下次别让我抓到。",
                )), 5200)
        return False

    def _apply_encouragement_state(self, state: object) -> bool:
        """Render the one-hour working encouragement from a buddy."""

        if not isinstance(state, dict) or "active" not in state:
            return self._encouragement_active
        active = bool(state.get("active"))
        if active:
            encouragement_id = str(state.get("id") or state.get("encouragement_id") or "")
            is_new_encouragement = not self._encouragement_active or encouragement_id != self._encouragement_id
            sender = str(
                state.get("sender_display_name")
                or state.get("private_note_name")
                or state.get("sender_nickname")
                or state.get("nickname")
                or "搭子"
            ).strip()[:40]
            self._encouragement_active = True
            self._encouragement_id = encouragement_id
            self._encouragement_sender_nickname = sender
            self._encouragement_message = str(state.get("message") or "抓到一个真在干活的。")[:120]
            # A redeemable taunt always wins. Keep the encouragement state in
            # memory so it can appear after the debt is cleared, but never
            # replace the taunt sprite, bubble, or ambient activity.
            if self._taunt_active:
                return False
            self._change_ambient_activity("work-cheer")
            self.visit_status_bubble.set_encourager(sender)
            if is_new_encouragement:
                self.show_speech(f"{sender}：{self._encouragement_message}", 5200)
            self._position_visit_status_bubble()
            self._raise_accessory(self.visit_status_bubble)
            return True
        if self._encouragement_active:
            self._encouragement_active = False
            self._encouragement_id = ""
            self._encouragement_sender_nickname = ""
            self._encouragement_message = ""
            if self._ambient_activity == "work-cheer":
                self._change_ambient_activity("computer" if self.work_timer.is_running else "none")
            if not self._taunt_active:
                self.visit_status_bubble.hide()
        return False

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
        """Merge same-account nickname, focus and outfit state from the server."""

        if data.get("_sync_offline") or data.get("data_source") == "local_cache":
            return
        profile = data.get("me") if isinstance(data, dict) else None
        profile = profile if isinstance(profile, dict) else {}
        user_id = self._current_social_user_id()
        if user_id:
            remote_raw_nickname = profile.get("owner_nickname")
            if remote_raw_nickname is None:
                remote_raw_nickname = profile.get("nickname")
            remote_nickname = clean_owner_nickname(remote_raw_nickname)
            # An explicit rename is already being uploaded; do not let a
            # stale in-flight dashboard response undo that local action.
            if not self._owner_nickname_sync_inflight:
                if remote_nickname != self._owner_nickname():
                    self.settings.owner_nickname = remote_nickname
                    self.settings.pet_name = PET_NAME
                    save_settings(self.settings)
                    self.owner_nickname_changed.emit(remote_nickname)
                    if self._social_dialog is not None:
                        self._social_dialog.set_owner_nickname(remote_nickname)
                elif self._social_dialog is not None:
                    self._social_dialog.set_owner_nickname(remote_nickname)
            self._owner_nickname_remote_loaded_for = user_id
        presence = data.get("me_presence") if isinstance(data, dict) else None
        presence = presence if isinstance(presence, dict) else {}
        # The personal-state RPC returns the server's merged values even when
        # an older dashboard function omits the newer profile columns.  Prefer
        # that response, then fall back to the dashboard/profile payload for
        # compatibility with older relays.
        personal_state = data.get("_personal_state") if isinstance(data, dict) else None
        personal_state = personal_state if isinstance(personal_state, dict) else {}
        remote_date = personal_state.get("focus_today_date")
        if remote_date is None:
            remote_date = profile.get("focus_today_date")
        remote_today = personal_state.get("focus_today_seconds")
        if remote_today is None:
            remote_today = profile.get("focus_today_seconds")
        if remote_today is None:
            remote_today = presence.get("today_seconds")
        remote_lifetime = personal_state.get("focus_lifetime_seconds")
        if remote_lifetime is None:
            remote_lifetime = profile.get("focus_lifetime_seconds")
        if remote_date is not None or remote_today is not None or remote_lifetime is not None:
            # Keep the timer's daily bucket strictly local.  The remote day
            # value is retained in FocusAnalyticsStore as a cross-device
            # fallback, but copying it into WorkTimer would make a stale
            # server maximum indistinguishable from seconds actually worked
            # on this computer.  Lifetime remains safe to merge because it is
            # monotonic and is only used for account-wide outfit unlocks.
            self.work_timer.merge_remote_state(
                today_seconds=0,
                lifetime_seconds=int(remote_lifetime or 0),
                date_key=str(remote_date or datetime.now(BEIJING_TIMEZONE).date().isoformat()),
            )

        remote_week_start = personal_state.get("focus_week_start_date")
        if remote_week_start is None:
            remote_week_start = profile.get("focus_week_start_date")
        remote_week_seconds = personal_state.get("focus_week_seconds")
        if remote_week_seconds is None:
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
        segment_changed = self.focus_analytics.merge_remote_segments(data.get("_focus_segments"))
        derived_changed = self.focus_analytics.reconcile_derived_totals()
        local_day = self.focus_analytics.period_summary("day")
        if (
            bool(local_day.get("raw_period_evidence"))
            and not self.work_timer.has_active_session
        ):
            self.work_timer.reconcile_today_seconds(
                int(local_day.get("total_seconds", 0) or 0)
            )
        if (analytics_changed or history_changed or segment_changed or derived_changed) and self._social_dialog is not None:
            self._social_dialog.set_focus_analytics(self.focus_analytics.snapshot())

        if "outfit_key" not in profile and "outfit_key" not in personal_state:
            return
        remote_outfit_value = (
            personal_state.get("outfit_key")
            if "outfit_key" in personal_state
            else profile.get("outfit_key")
        )
        remote_outfit = str(remote_outfit_value or "")[:60]
        # A server profile that already contains the account-bound outfit is
        # itself a durable ownership proof.  This repairs users whose login
        # RPC result was missed by an older client.
        if remote_outfit == LOGIN_REWARD_OUTFIT.key:
            self._remember_login_reward_entitlement()
        # Do not let a stale profile/dashboard response overwrite an explicit
        # local selection while its durable sync is still in flight.  This is
        # particularly important for the account-bound three-day login outfit
        # on a second computer or an older relay that still returns the old
        # profile value.
        if self._personal_outfit_sync_pending:
            return
        if self._outfit_response_is_stale(remote_outfit):
            return
        allowed_outfits = {
            item.key for item in unlocked_outfits(self.work_timer.unlocked_outfit_count())
        }
        if self._login_reward_unlocked:
            allowed_outfits.add(LOGIN_REWARD_OUTFIT.key)
        if remote_outfit and remote_outfit not in allowed_outfits:
            return
        if remote_outfit == self.settings.equipped_outfit:
            return
        panel_was_visible = self._qt_object_is_alive(self._compact_todo_panel) and bool(
            self._compact_todo_panel.isVisible()
        )
        self.settings.equipped_outfit = remote_outfit
        save_settings(self.settings)
        self.activity_transition_timer.stop()
        self._activity_transition_from = QPixmap()
        self._activity_transition_step = self._activity_transition_steps
        self._mask_cache.clear()
        self._refresh_pixmap()
        self._reflow_compact_todos_after_outfit(panel_was_visible)
        if self._social_dialog is not None:
            self._social_dialog.outfit_key = remote_outfit

    @_guard_qt_callback
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
        self._set_login_reward_account(account_id)
        if self._social_dialog is not None:
            # A room selected under another account is not an invitation for
            # the new account to keep sending heartbeats into that room.
            self._social_dialog.current_room_id = None
            self._social_dialog._room_selection_explicit = False
            self._social_dialog._room_refresh_pending = False

        if not signed_in:
            self._economy_sync_user_id = ""
            self._personal_outfit_sync_pending = False
            self._personal_outfit_sync_user_id = ""
            self._personal_outfit_fence_key = ""
            self._personal_outfit_fence_until = 0.0
            return
        if (
            self._personal_outfit_sync_pending
            and self._personal_outfit_sync_user_id
            and self._personal_outfit_sync_user_id != account_id
        ):
            # A pending selection belongs to the account that made it.  Do
            # not carry it across an explicit account switch.
            self._personal_outfit_sync_pending = False
            self._personal_outfit_sync_user_id = ""
        self._economy_sync_user_id = ""
        self._schedule_social_tick()

    def _set_login_reward_account(self, account_id: str | None) -> None:
        """Load the permanent login entitlement for the active account."""

        clean = str(account_id or "").strip()
        self._login_reward_account_id = clean
        self._login_reward_store = LoginRewardStore(
            clean,
            persist=os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1",
        )
        self._login_reward_unlocked = self._login_reward_store.is_unlocked(
            LOGIN_REWARD_KEY
        ) or (
            not clean
            and self.settings.equipped_outfit == LOGIN_REWARD_OUTFIT.key
        )

    def _remember_login_reward_entitlement(self) -> None:
        """Cache an ownership proof returned by the account backend."""

        account_id = self._current_social_user_id()
        if not account_id:
            return
        if account_id != self._login_reward_account_id:
            self._set_login_reward_account(account_id)
        self._login_reward_store.grant(LOGIN_REWARD_KEY)
        self._login_reward_unlocked = True

    def _outfit_response_is_stale(self, remote_outfit: str) -> bool:
        """Reject an older dashboard response after a local wardrobe choice."""

        if time.monotonic() >= self._personal_outfit_fence_until:
            return False
        expected = self._personal_outfit_fence_key or self.settings.equipped_outfit
        return str(remote_outfit or "") != str(expected or "")

    def _login_streak_updated(self, result: dict) -> None:
        """Reconcile a server login streak into a permanent wardrobe grant."""

        if not isinstance(result, dict):
            return
        account_id = self._current_social_user_id()
        if account_id != self._login_reward_account_id:
            self._set_login_reward_account(account_id)
        selected_reward = self.settings.equipped_outfit == LOGIN_REWARD_OUTFIT.key
        was_unlocked = self._login_reward_unlocked
        days = login_streak_days(result)
        payload_grants = login_reward_granted(result)
        if payload_grants:
            self._remember_login_reward_entitlement()
        unlocked = self._login_reward_unlocked or payload_grants or (
            not account_id and selected_reward
        )
        self._login_reward_unlocked = unlocked
        newly_unlocked = bool(result.get("newly_unlocked")) or (
            days >= 3 and not was_unlocked
        )
        if newly_unlocked and not was_unlocked:
            self._set_temporary_activity("happy", 30_000)
            self.show_speech(
                "连续登录 3 天，已解锁「三日连登搭子」！\n"
                "可以在“百变六毛”里穿上它。",
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
        if hasattr(self, "_owner_nickname_remote_loaded_for"):
            self._owner_nickname_remote_loaded_for = ""
        self.focus_session.switch_account(clean or None)
        self.focus_analytics.switch_account(clean or None)
        self.daily_stats.switch_account(clean or None)
        self.time_memory.switch_account(clean or None)
        self._rebind_todo_surfaces_to_current_memory()
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
        self._rewarded_focus_blocks = self._shared_today_focus_seconds() // 600
        self._focus_quality_tracker.reset()
        self._last_focus_quality = None
        self._seen_buddy_request_ids.clear()
        self._seen_visit_ids.clear()
        self._handled_visit_ids.clear()
        self._muted_buddy_ids.clear()

    def _rebind_todo_surfaces_to_current_memory(self) -> None:
        """Keep resident Todo windows on the same account namespace as the pet."""

        panel = self._compact_todo_panel
        if self._qt_object_is_alive(panel):
            panel.memory = self.time_memory
            panel.selected_task_id = str(self.time_memory.current_task_id or "")
            lifecycle_log(
                "todo.panel.rebind",
                panel,
                account_id=getattr(self.time_memory, "_account_id", ""),
            )
        if self._today_note_window is not None:
            self._today_note_window.memory = self.time_memory
            self._today_note_window.refresh()
        if self._todo_center_window is not None:
            self._todo_center_window.memory = self.time_memory
            alarm_center = getattr(self._todo_center_window, "_alarm_center", None)
            if alarm_center is not None:
                alarm_center.alarms = self.time_memory.alarms
                alarm_center.todos = list(self.time_memory.todos.items)
                alarm_center.sound_library = self.time_memory.alarm_sounds
            self._todo_center_window.refresh()
        if self._time_memory_window is not None:
            self._time_memory_window.memory = self.time_memory
            self._time_memory_window.refresh()
        # The application can create an empty hidden panel before a restored
        # login session becomes available.  Rebinding the memory alone does
        # not run the normal show/empty-state policy, so explicitly re-run the
        # shared surface refresh after every account switch.  This also
        # recreates the panel when no panel existed during anonymous startup.
        self._refresh_todo_surfaces()

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
        thread.finished.connect(lambda: self._social_event_finished(thread), Qt.ConnectionType.QueuedConnection)
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
        if self._taunt_active:
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
        event_id = self._incoming_visit_id(event)
        if event_id:
            self._handled_visit_ids.add(event_id)
        notice = self._incoming_visit_notice
        if notice is None:
            return
        current_id = self._incoming_visit_id(notice._event_payload)
        if event_id and current_id and current_id != event_id:
            return
        self._incoming_visit_notice = None
        notice.hide()
        notice.deleteLater()
        self._incoming_visit_queue = [
            item for item in self._incoming_visit_queue
            if self._incoming_visit_id(item) != event_id
        ]
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
        thread.finished.connect(lambda current=thread: self._incoming_visit_response_finished(current), Qt.ConnectionType.QueuedConnection)
        thread.start()

    @_guard_qt_callback
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

    @_guard_qt_callback
    def _incoming_visit_response_failed(self, event: dict, message: str) -> None:
        notice = self._incoming_visit_notice
        expired_or_already_handled = any(
            marker in str(message)
            for marker in ("已过期", "已处理", "无权处理", "不存在")
        )
        if expired_or_already_handled:
            # A stale pending row should not pin a desktop notification
            # forever after the server has already invalidated it.
            self._finish_incoming_visit_notice(event)
        elif notice is not None and self._incoming_visit_id(notice._event_payload) == self._incoming_visit_id(event):
            notice.set_busy(False)
        self.show_speech(f"处理互动失败：{message[:120]}", 5200)

    @_guard_qt_callback
    def _incoming_visit_response_finished(self, thread: SocialVisitResponseThread) -> None:
        if thread in self._incoming_visit_response_threads:
            self._incoming_visit_response_threads.remove(thread)
        thread.deleteLater()

    @_guard_qt_callback
    def _social_thread_finished(self, thread: SocialSyncThread) -> None:
        lifecycle_log("social_sync_thread.finished", thread)
        if self._social_thread is thread:
            self._social_thread = None
        thread.deleteLater()

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

    @_guard_qt_callback
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
        panel_was_visible = self._qt_object_is_alive(self._compact_todo_panel) and bool(
            self._compact_todo_panel.isVisible()
        )
        self.settings.equipped_outfit = outfit_key
        save_settings(self.settings)
        self._personal_outfit_sync_pending = True
        self._personal_outfit_sync_user_id = self._current_social_user_id()
        # Heartbeats can complete out of order. Keep a short fence even after
        # the selected key is echoed once, so an older in-flight response
        # cannot put the previous outfit back on the pet.
        self._personal_outfit_fence_key = outfit_key
        self._personal_outfit_fence_until = time.monotonic() + 60.0
        if self._social_dialog is not None:
            # The social window keeps a local member projection.  Update it in
            # the same UI turn so it cannot repaint the previous outfit before
            # the next heartbeat confirms the durable profile write.
            self._social_dialog.outfit_key = outfit_key
        # Cancel a half-finished action cross-fade so the newly selected outfit
        # is visible immediately, even while a transient work action is ending.
        self.activity_transition_timer.stop()
        self._activity_transition_from = QPixmap()
        self._activity_transition_step = self._activity_transition_steps
        self._mask_cache.clear()
        self._refresh_pixmap()
        self._reflow_compact_todos_after_outfit(panel_was_visible)
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
            int(getattr(current, "today_seconds", 0) or 0),
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

        # The right-click work dock and the double-click shortcut dock are
        # mutually exclusive.  In particular, hide the detached report
        # button owned by the shortcut dock before placing this control bar.
        self.quick_panel.hide()
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

        # The report shortcut is a detached top-level window above the work
        # button. Collapse the whole shortcut dock before changing focus
        # state so macOS cannot leave that secondary button behind while the
        # primary start/pause shortcut disappears.
        self.quick_panel.hide()
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
        self.context_menu_timer.start(QApplication.doubleClickInterval() + 60)
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

