"""
��ģ��ʵ����������͸�����ڡ�������������꽻������ݿ��ƺ��龳��顣

ְ��Χ��
- �����ޱ߿�͸������ѡʼ���ö��� QWidget��
- ʹ�� Windows/macOS ԭ�����ڲ㼶��ǿ�ö���ͬʱ���ֲ������ռ������������������͸��
- �ṩ��ʼ���ö�/����ģʽ����ʱ�л����־û����л�ʱ���ƻ��������϶��ͻ���״̬��
- ����ѭ���򵥴� PNG ���У���֧����ק�����¡�������˯�ͷ���������
- �������ҷ�ת����Եת��ͣ�١�������ʱ�������ƶ���ͬ�����������
- �ô���������������͸������͸�������
- ���治ͬ DPI �µ�����֡�����ڴ��ڿ���ʾ�����±�������դ�񻯣�
- ֧������϶���������Ϸ��˫����ݿڴ����޻����ּ���Ϣ�������ߴ绬�飻
- ֧�ָ���ëιʳ����Ʒ�����ö�����͸���������ݷ���״̬��
- ֧�� Agent ״̬���桢�첽 AI���޷����߽����Լ����������⡢�����Ͱ�ο������
- ���ڴ��б��������ʮ���������죬���Ѹ������ݹ���ѹ��Ϊ����ժҪ�������¼���û����Ʊ����ڱ�����
- ����������������տڵ�Ψһ��ڣ�ֻ����ʽ ``user_action`` ��Դ�������������ô��ڣ�
- �Զ����ֲ����γ��Ա������� Provider���ɹ���ѻ�������������ʵ�ʲ��ŵ�ƽ̨��
- ֧�ֵ���ͼ�㡢��ͷ�������ݡ�����/������ʱ��ÿСʱ���½�����ҹ���޶����ͼ��������ѣ�
- ����ǰ̨Ӧ�ô����������ʾ���ԡ��������������ġ��Ķ���д��ͼ�㣻
- ֧��ͷ������������/����/������������������������ͣע�Ӻ���ק����飻
- ͨ�����ɫ�زĽ����ʸ��ͼ����ǿ���ġ����ߡ����ȡ����������롢�ɻ����ĺ���ק������
- ���ȴ��û�˽���ز�Ŀ¼��ʾ���ĳ�Ƭ���ݣ�����ǰ��Ļ DPI ���������ȣ�������������ʵ������λ��
- ��׼��ɫȷ�Ϻ���ر��س��﹩�ֳ����գ���·ȷ������Ϊ����Ž���
- ά�����ܶȡ����������Ķ��뱥ʳ�ȵĻỰ��״̬��
- ʹ�� QTimer ����״̬�л���ˮƽ�ƶ��������ƴ��ڲ����뵱ǰ��Ļ��

Agent ���ٶ�λ��
- ���ڳ�ʼ���ͼ�ʱ������λ�� PetWindow.__init__()��
- ״̬��ʾ���λ�� set_state()���� DPI �ػ�λ�� _refresh_pixmap()��
- �Զ��ƶ�λ�� _movement_tick()��
- ����¼�λ�� mousePressEvent() �� Qt �¼�������
- �˳��� quit_requested �źŽ���Ӧ����������ģ�鴦����

����Ϊ PetSettings���ز��嵥�Ϳ�ѡ���û�������Ƭ��Դ�����Ϊ�ɽ����� Qt ���ڡ�
��ģ��������ֻ�ں�̨��Ƶ��� Agent��ÿ�����첻�ظ�������⣬��ͨ��������ɧ�ͱ�ʱ�����������硣
API ������ϵͳƾ�ݿ�����������ı������̣�λ�ó־û��� app.py ���˳�ʱ��ɡ�
`user_assets/` Ĭ�ϲ����� Git��ֻ���û��������������ͼƬ�Ż��ڱ�����ʾ��
�Ҽ��˵��͸�������ʹ�ó���������ʾ���� Qt ȫ���߼����꣬�������������ء�
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

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, QUrl, Signal
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
    QDialog,
    QHBoxLayout,
    QLabel,
    QInputDialog,
    QMenu,
    QMessageBox,
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
from .alarm_ui import AlarmCard, AlarmCenterDialog
from .accessories import (
    OUTFITS,
    SPECIAL_LIMITED_ACTIVITY_SPRITES,
    draw_activity_overlay,
    unlocked_outfits,
)
from .activity import (
    active_application_category,
    active_application_name,
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
    conversation_history_path,
    conversation_memory_path,
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
)
from .economy import EconomyLedger
from .economy_ui import EconomyDialog
from .food_scene_ui import FoodSceneDialog
from .input_activity import system_idle_seconds, system_session_state
from .idle_classifier import IdleClassification, IdleEvidence, classify_idle
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
            "���Զ���Ϊ��Ϣ������ղ����ڹ��������Ըĳ�רע��"
        )
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet("color: #667784; font-size: 11px;")
        layout.addWidget(self.detail_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.focus_button = QPushButton("�ĳ�רע")
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
            duration = f"{hours} Сʱ {minutes} ����"
        elif minutes:
            duration = f"{minutes} �� {remainder:02d} ��"
        else:
            duration = f"{remainder} ��"
        self.summary_label.setText(f"�ղ��뿪 {duration}����������Ϣ��")

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
    """��ʾ�����Ƶ�����������͸�����㴰�ڡ�"""

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
        """���ص�ǰ�Ƿ�������ë���������ܶ���"""

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
        self._time_memory_window: TimeMemoryWindow | None = None
        self._todo_center_window: TodoCenterWindow | None = None
        self._economy_dialog: EconomyDialog | None = None
        self._food_scene_dialog: FoodSceneDialog | None = None
        self._alarm_center_dialog: AlarmCenterDialog | None = None
        self._alarm_card: AlarmCard | None = None
        self.focus_analytics = FocusAnalyticsStore(
            persist=os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1"
        )
        self._focus_quality_tracker = FocusQualityTracker()
        # session_seconds() is cumulative across pauses/resumes.  This cursor
        # ensures each WORKING second is credited to wages and statistics once.
        self._recorded_focus_session_seconds = 0
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
        self._idle_context: IdleEvidence | None = None
        self._idle_hint_classification: IdleClassification | None = None
        self._idle_hint_record: dict[str, object] | None = None
        self._idle_recovery_dialog: IdleRecoveryDialog | None = None
        self._last_session_probe = {"locked": False, "sleeping": False}
        self._fullscreen_video_started_at: float | None = None
        self._pause_notice_shown = False
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
        self._buddy_visit_window = BuddyVisitWindow()
        self._seen_visit_ids: set[str] = set()
        self._shown_active_visit_ids: set[str] = set()
        self._chat_dialog: ChatDialog | None = None
        self._chat_streaming_active = False
        self._chat_memory = ConversationMemory(
            max_recent_rounds=30,
            persist_path=conversation_memory_path()
            if os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1"
            else None,
        )
        self._chat_history = ChatHistoryStore(
            persist_path=conversation_history_path()
            if os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1"
            else None,
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
        self.setWindowTitle(f"{APP_DISPLAY_NAME} �� {self._pet_name()}")
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
        self.quick_panel.chat_requested.connect(self.prompt_dialogue)
        self.quick_panel.work_requested.connect(self._quick_work_action)
        self.quick_panel.todo_requested.connect(self.show_todo_center)
        self.quick_panel.social_requested.connect(self.open_social_hub)
        self.quick_panel.music_control_requested.connect(self.control_music)
        self.quick_panel.music_requested.connect(self.play_random_song)
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

        # Keep a low-frequency, cross-platform system probe.  It is the one
        # place that may auto-pause: 10 minutes of aggregate keyboard+mouse
        # silence, a verified lock/sleep boundary, or a known video player in
        # real fullscreen.  None of those paths ever auto-resume.
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
        """���ز�ռ�������������ռ��̽���ĳ��ﴰ�ڱ�־��"""

        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if self.settings.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        return flags

    def _ambient_window_flags(self) -> Qt.WindowType:
        """���Զ����ݸ������ģʽ������֤��ʾʱ�����ǰӦ�á�"""

        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if self.settings.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        return flags

    def _load_pixmaps(self) -> dict[PetState, list[QPixmap]]:
        """�����ز��嵥���ظ�״̬֡���в���֤�����ԡ�"""

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
                    "��⵽˽�г����زģ�����׼������δȷ�ϣ��ܾ���Ĭ���˵���ʾ��ɫ��"
                )
            manifest_path = custom_manifest
        return self._load_manifest_pixmaps(manifest_path)

    def _load_manifest_pixmaps(
        self,
        manifest_path: Path,
    ) -> dict[PetState, list[QPixmap]]:
        """��ָ���嵥����֡�����Կɽ�˹̶�ʹ�ù�����ʾ�زġ�"""

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        animations: dict[str, list[str]] = manifest["animations"]
        motion_factors = manifest.get(
            "walk_motion_factors",
            DEFAULT_WALK_MOTION_FACTORS,
        )
        if len(motion_factors) != len(animations["walk"]):
            raise ValueError("��·λ�����߱�������·����֡��һ��")
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
                    raise FileNotFoundError(f"ȱ�ٳ����زģ�{path}")
                pixmap = QPixmap(str(path))
                if pixmap.isNull():
                    raise ValueError(f"�޷����س����زģ�{path}")
                state_frames.append(pixmap)
            if not state_frames:
                raise ValueError(f"״̬ {state.value} û�п����ز�֡")
            pixmaps[state] = state_frames
        return pixmaps

    def _load_selfie_photo(self) -> QPixmap:
        """ֻ�����û��ṩ��ԭʼ������Ƭ����������֡ð��ԭͼ��"""

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
        """�л���Ϊ״̬������֡��Ų�ˢ�µ�ǰͼƬ��"""

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
        """����ָ������֡��ͣ��ʱ�䣬ʹգ�ۡ����������߽���˴˶�����"""

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
        """д��С�����ʹ�û��棬�����Ƴ�������ʱ���ڴ�ռ�á�"""

        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > 96:
            cache.popitem(last=False)

    def _current_source(self) -> tuple[PetState, QPixmap]:
        """���ص�ǰ��ʾ״̬�����������ԭʼ֡��"""

        display_state = self.state
        frames = self._pixmaps[display_state]
        pixmap = frames[min(self._frame_index, len(frames) - 1)]
        if self.direction < 0 and display_state is PetState.WALK:
            pixmap = pixmap.transformed(QTransform().scale(-1, 1))
        return display_state, pixmap

    def _refresh_pixmap(self) -> None:
        """�ӻ���ȡ�û򰴵�ǰ��Ļ�豸���ر�դ�񻯵�ǰ����֡��"""

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
        """����һ������������Ŀ�궯�����ݽ��浭�������⾲̬ͼӲ�С�"""

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
        """�ƽ�Լ 280 ����Ķ������浭����ԭ����֡��·�������������"""

        self._activity_transition_step += 1
        if self._activity_transition_step >= self._activity_transition_steps:
            self.activity_transition_timer.stop()
            self._activity_transition_from = QPixmap()
            self._mask_cache.clear()
        self._refresh_pixmap()

    def _change_ambient_activity(self, activity: str) -> None:
        """ͳһ�л��������������ӵ�ǰʵ�ʻ���ƽ�����ɵ�Ŀ��ͼ��"""

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
        """����ǰ�����������ô������֣�ʹ͸�����ײ�������������"""

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
        """�ƽ�������ŵ���΢Ư��������ˢ�ºϳ�֡��"""

        if emotion_effect_name(self.state) is None:
            self.effect_timer.stop()
            return
        self._effect_phase = (self._effect_phase + 1) % 12
        self._refresh_pixmap()

    def _animation_tick(self) -> None:
        """�ƽ�ѭ���򵥴�����֡�����ڷ�����ɽ�����ִ�лص���"""

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
        """���ܲ���š�ѹ�����ڿս׶�ͬ��ˮƽ�ص������������"""

        if display_state is PetState.WALK:
            x_offsets = (6, 6, 6, 6, 6, 6, 6, 6)
            y_offsets = (3, 5, 2, 0, 3, 5, 2, 0)
            phase = self._frame_index % len(y_offsets)
            self.label.move(x_offsets[phase], y_offsets[phase])

    def _movement_speed_pixels_per_second(self) -> float:
        """�������õ�ƽ���ٶȼ���㶨ˮƽ�ٶȡ�"""

        return (
            self.settings.movement_step
            * 1000.0
            / self.settings.movement_interval_ms
        )

    def showEvent(self, event: QShowEvent) -> None:
        """�����״���ʾʱ���ӿ����źŲ�����ǰ DPI ���ơ�"""

        super().showEvent(event)
        handle = self.windowHandle()
        if handle is not None and not self._screen_change_connected:
            handle.screenChanged.connect(self._on_screen_changed)
            self._screen_change_connected = True
        self._on_screen_changed(handle.screen() if handle else None)
        self._update_work_duration_bubble()
        if self._compact_todo_panel is not None:
            if self._restore_compact_todos_after_show:
                self._compact_todo_panel.show()
        self._position_accessories()
        QTimer.singleShot(0, self._ensure_on_top)

    def _ensure_on_top(self) -> None:
        """�ָ�ԭ�����ڲ㼶������������ڻ���ߵ�ǰ���뽹�㡣"""

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
        # ����ƽ̨�� WindowStaysOnTopHint �������ﲻ�ܵ��� raise_()��
        # ���� macOS/���� Linux ��������û�����ʱ�л���ǰӦ�á�

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

    def _apply_macos_window_behavior(
        self,
        widget: QWidget | None = None,
        *,
        always_on_top: bool | None = None,
    ) -> None:
        """�ԷǼ���� NSPanel �����㼶��ʾ���衣

        ``WindowDoesNotAcceptFocus`` �� Qt ����ı�֤������ macOS ��
        ����Ҫ�� Qt ������ԭ�� NSWindow/NSPanel ���Ϊ
        ``NSNonactivatingPanelMask``�����򴰿���Ȼû�м��̽��㣬AppKit
        �Կ������������ø����㼶ʱ�� Lili ���±��ǰ̨Ӧ�á�
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
        """�� QQ ����ʽ�ö�����ͨ����ģʽ���л�����ʾʱ�������㡣"""

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
                "ʼ���ö��ѿ�������ë������������������Ϸ���"
                if enabled
                else "���л�Ϊ����ģʽ����ë��������ͨ���ڲ㼶��",
                3600,
            )
        self.always_on_top_changed.emit(enabled)

    def moveEvent(self, event: QMoveEvent) -> None:
        """�����ƶ�ʱ�����и������ڸ���̶��ĳ��ﴰ��λ�á�"""

        super().moveEvent(event)
        self._position_accessories()

    def hideEvent(self, event: QHideEvent) -> None:
        """���س���ʱͬ��������Ƭ���������ݡ�"""

        self.photo_bubble.hide()
        self.speech_bubble.hide()
        self.work_controls.hide()
        self.coffee_scene_prompt.hide()
        self.work_duration_bubble.hide()
        self.quick_panel.hide()
        if self._compact_todo_panel is not None:
            self._restore_compact_todos_after_show = self._compact_todo_panel.isVisible()
            self._compact_todo_panel.hide()
        super().hideEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        """�رճ���ʱ�����ʱ��ֹͣ Agent�����ֿ��Ƽ��������ݴ��ڡ�"""

        if self._qt_application is not None:
            self._qt_application.removeEventFilter(self)
        self.shutdown_work_timer()
        self.chat_manager.shutdown()
        if self._chat_history_dialog is not None:
            self._chat_history_dialog.close()
        self.music_controller.shutdown()
        self.photo_bubble.close()
        self.speech_bubble.close()
        self.coffee_scene_prompt.close()
        self.work_duration_bubble.close()
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
        if self._social_thread is not None and self._social_thread.isRunning():
            self._social_thread.wait(2500)
        if self._media_player is not None:
            self._media_player.stop()
        super().closeEvent(event)

    def _on_screen_changed(self, screen: QScreen | None) -> None:
        """�л�Ŀ����Ļ������ DPI �źŲ��ӳ�ˢ���زġ�"""

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
        """��ʾ�����ŷ����仯ʱˢ�µ�ǰ֡��"""

        self._render_cache.clear()
        QTimer.singleShot(0, self._refresh_pixmap)
        QTimer.singleShot(0, self._position_accessories)

    def _schedule(self, decision: StateDecision) -> None:
        """Ӧ��״̬���߲�������һ��״̬�л���"""

        self.set_state(decision.state)
        if not self.dragging:
            self.state_timer.start(decision.duration_ms)

    def _state_timeout(self) -> None:
        """��������״̬���ڣ������޻���ʱ���𼶽���������˯�ߡ�"""

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
        """���������ڲ�����ʹ�õ�ȷ��ʱ��״̬���ߡ�"""

        return StateDecision(state, max(500, duration_ms))

    def _inactive_ms(self) -> int:
        """���ؾ������һ������˵������ĺ�������"""

        return max(0, round((time.monotonic() - self._last_user_interaction) * 1000))

    def _record_user_interaction(self) -> None:
        """�����޻�����ʱ����ȡ����δ��ʼ���Զ���˯��ͼ��"""

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
            LOGGER.debug("�޷������뿪�����¼: %s", exc)

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

            # Browser/document fullscreen is deliberately ignored.  Only a
            # known media player counts, and it must remain fullscreen for a
            # few seconds to avoid a false transition while switching apps.
            if bool(getattr(self.settings, "auto_pause_on_fullscreen_video", True)) and active_fullscreen_video():
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
            self.show_speech("���������ղ�ʮ����û�����Ұ���ͣ���ˡ��㡮�����������ٿ���", 6200)

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
        """���������£��ٴ����˲�����˯���С�"""

        self._sleep_after_sit = True
        self._schedule(self._decision(PetState.SIT, 1400))

    def _reverse_transition_to_idle(self) -> None:
        """�������»�˯�����У������Ȼ�������ٽ��������"""

        frames = self._pixmaps[self.state]
        self._frame_index = len(frames) - 1
        self._animation_direction = -1
        self._animation_finished = self._finish_reverse_transition
        self._refresh_pixmap()
        self.animation_timer.start(self._frame_interval(self.state, self._frame_index))

    def _finish_reverse_transition(self) -> None:
        """˯�Ѻ��Ȼص����ˣ��ٵ����������лָ�վ��������"""

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
        """���ش�������������Ļ�Ŀ�������"""

        center = self.frameGeometry().center()
        screen = QApplication.screenAt(center) or QApplication.primaryScreen()
        return screen.availableGeometry() if screen else None

    def _constrained_position(self, position: QPoint) -> QPoint:
        """��Ŀ��λ�������ڵ�ǰ������Ļ���������ڡ�"""

        screen = QApplication.screenAt(position) or QApplication.primaryScreen()
        if screen is None:
            return position
        area = screen.availableGeometry()
        x = min(max(position.x(), area.left()), area.right() - self.width() + 1)
        y = min(max(position.y(), area.top()), area.bottom() - self.height() + 1)
        return QPoint(x, y)

    def _movement_tick(self) -> None:
        """��ʵ�ʾ���ʱ���������ۼ��ƶ���������Ļ��Եת��"""

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
        """������Ļ��Ե�Ķ���ͣ�٣����ӵ�һ֡�ָ����ߡ�"""

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
        """ͨ����ǩ��΢�����ƶ�Ӫ����������������"""

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
        """��ͣ��ָ��ܶ�����ͣ�ڼ��Լ������¡�˯�ߺ����ĵ�����״̬��"""

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
            message = "��ë�������ﰲ�����š�"
        elif getattr(self.settings, "allow_autonomous_walk", False):
            message = "��ë�ָ��ܶ�����"
        else:
            message = "�Զ��ܶ���û������ȥ������򿪺��Ҿ�����������������"
        self.show_speech(message, 3200)

    def set_allow_autonomous_walk(self, enabled: bool, *, persist: bool = True) -> None:
        """�л������ܶ��ܿ��أ���Ӱ��գ�ۡ����ºͻ���������"""

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
            "�ѿ����Զ��ܶ�����ë֮����������������ƶ���"
            if enabled
            else "�ѹر��Զ��ܶ�����ë�ᰲ������ԭ�أ�������������Ȼ������",
            3600,
        )

    def set_display_height(self, display_height: int) -> None:
        """Ӧ���Ҽ��˵��ߴ�Ԥ�裬���ִ��ڵײ�����λ�ò������ػ档"""

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
        """���������·ŵ�����Ļ���½ǡ�"""

        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - 24, area.bottom() - self.height() - 12)

    def _position_speech_bubble(self) -> None:
        """�ѶԻ����ݷ��������Ϸ����ռ䲻��ʱ�Զ��Ƶ����档"""

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
        """��ʾ�������߼��̽��������Ի����ݡ�"""

        self.speech_bubble.setText(text)
        self.speech_bubble.adjustSize()
        self.speech_bubble.show()
        if sys.platform == "darwin":
            self._apply_macos_window_behavior(self.speech_bubble)
        self._position_speech_bubble()
        self.speech_timer.start(max(1200, duration_ms))

    def feed_pet(self, food_key: str) -> CompanionReply:
        """ι�� Lili һ�ֲ˵�ʳ������Ŷ�Ӧ���������ַ�����"""

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
            f"{reply.text}\n���� {self.mood.energy} �� ��ʳ {self.mood.fullness}",
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
        """Open/reuse the taskbar-capable ��ë����վ window."""
        self._record_user_interaction()
        if self._food_scene_dialog is None:
            self._food_scene_dialog = FoodSceneDialog(
                self.economy,
                self._todo_choices_for_food(),
                witness_choices=self._achievement_witness_choices,
                achievement_submitter=self._submit_achievement_witness,
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
            raise ValueError("���ȵ�¼������ϰ�ң���������Ӽ�֤�ɹ���")
        if len({str(value).strip() for value in witness_ids if str(value).strip()}) != 2:
            raise ValueError("��ѡ��������ͬ�Ĵ��ӡ�")
        result = self.social_client.rpc(
            "lili_submit_achievement",
            {
                "p_kind": "�����ɹ�",
                "p_name": str(name).strip()[:90],
                "p_amount": 200,
                "p_note": str(note).strip()[:160],
                "p_witness_ids": [str(value).strip() for value in witness_ids],
            },
        )
        if isinstance(result, dict):
            return result
        return {"status": "pending"}

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
        snapshot = self.focus_session.snapshot()
        status = str(getattr(snapshot, "status", "") or "")
        start_error = self.economy.food_scene_start_error(
            item_key, consume_inventory=consume_inventory,
        )
        if start_error == "inventory":
            spec = self.economy.catalog().get(item_key) or {}
            name = str(spec.get("name") or item_key)
            self.show_speech(
                f"�ֿ���û�С�{name}��������վ�Ѿ������¿��ˢ�£����ȹ����ȴ�������",
                5200,
            )
            if self._food_scene_dialog is not None:
                self._food_scene_dialog.refresh()
                self._food_scene_dialog.show()
                self._food_scene_dialog.raise_()
            return False
        if start_error == "active_scene":
            current = self.economy.active_food_scene() or {}
            current_name = str(current.get("name") or "��һ�β�������")
            self.show_speech(
                f"��ë����{current_name}������ȵ���һ�ν��������µĲ�����",
                5200,
            )
            if self._food_scene_dialog is not None:
                self._food_scene_dialog.refresh()
                self._food_scene_dialog.show()
                self._food_scene_dialog.raise_()
            return False
        if start_error == "invalid_item":
            self.show_speech("���������ʱ����ʹ�á�", 4200)
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
            self.show_speech("����״̬�շ����仯�������´򿪲ֿ�����ԡ�", 4800)
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
            label = "? �ȹ�� �� ��ȹ�����" if item_key == "expensive_coffee" else "? ���ȿ���"
            detail = f"\n{todo_title[:80]}" if todo_title else "\n�����񿪹�"
            self.show_speech(f"{label}{detail}\n{result.get('feedback') or ''}", 6200)
        elif item_key == "milk_tea":
            minutes = int(scene.get("duration_minutes") or 10)
            self._set_temporary_activity("milk-tea", minutes * 60 * 1000)
            self.food_scene_timer.start(max(1000, minutes * 60 * 1000))
            self.show_speech(f"?? �̲�ʱ�� �� {minutes:02d}:00\n{result.get('feedback') or ''}", 5200)
        elif item_key == "cake":
            self._set_temporary_activity("feast", 20_000)
            self.food_scene_timer.start(20_000)
            title = todo_title or "������ɵ�һ����"
            self.show_speech(f"?? ������ף��\n{title[:100]}", 6200)
        else:
            self._set_temporary_activity("tea", 60_000)
            self.show_speech("?? �Ȼ����\n���첻�øϣ���ë�����һ�����", 5600)
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
                message = "���Ⱥ���������Сʱ���ˡ�\nҪ�������������ǽ�����һ�֣�"
            elif self.work_timer.has_active_session:
                message = "���Ⱥ���������Сʱ���ˡ�\n��ǰ��������ͣ�ţ�Ҫ�������ǽ�����һ�֣�"
            else:
                message = "���Ⱥ���������Сʱ���ˡ�\nҪ��ʼ���������ǽ�����һ�֣�"
            self._show_coffee_scene_prompt(message)
            return
        finished = self.economy.finish_food_scene("timer")
        if not finished:
            return
        if item_key == "milk_tea":
            # Ending a break is not permission to restart the work timer.
            # The user must explicitly press ������������.
            self.show_speech("�̲�����ˡ�\n��������ͣ�ţ�Ҫ����ʱ�㡮������������", 5200)
        elif item_key == "cake":
            self.show_speech("��ף����������������Ѿ�����ë�������ˡ�", 4200)

    def _show_coffee_scene_prompt(self, message: str) -> None:
        """Ask what to do next without changing the user's work decision."""

        self.coffee_scene_prompt.set_message(message)
        self._position_coffee_scene_prompt()
        self.coffee_scene_prompt.show()
        if sys.platform == "darwin":
            self._apply_macos_window_behavior(self.coffee_scene_prompt)
        self._raise_accessory(self.coffee_scene_prompt)

    def _continue_after_coffee_scene(self) -> None:
        self.coffee_scene_prompt.hide()
        if not self.work_timer.is_running:
            self.start_work_timer()
        else:
            self.show_speech("�ã�����������", 3200)

    def _finish_after_coffee_scene(self) -> None:
        self.coffee_scene_prompt.hide()
        if self.work_timer.has_active_session:
            self.finish_work_timer()

    def _send_food_interaction(self, buddy: dict, kind: str) -> None:
        """Send a food scene invitation; gifts are charged locally and never create income."""
        if not self.social_client.signed_in:
            self.show_speech("�ȵ�¼������ϰ�ң����ܸ������ͳԵġ�", 4200)
            return
        target = str(buddy.get("user_id") or buddy.get("id") or "").strip()
        if not target:
            self.show_speech("û�ҵ���λ���ӵ��˺š�", 4200)
            return
        item_key = {
            "food_coffee": "coffee",
            "food_milk_tea": "milk_tea",
            "food_tea": "tea",
            "food_cake": "cake",
        }.get(str(kind))
        if not item_key:
            return
        catalog = self.economy.catalog().get(item_key) or {}
        price = int(catalog.get("price") or 0)
        if self.economy.balance < price:
            self.show_speech("���ǣ�Ǯ���е��", 4200)
            return
        recipient_label = str(
            buddy.get("private_note_name")
            or buddy.get("owner_nickname")
            or buddy.get("nickname")
            or "����"
        )[:80]
        duration = {"coffee": 30, "milk_tea": 10, "tea": 0, "cake": 0}.get(item_key, 0)
        operation_key = uuid.uuid4().hex
        payload = {
            "item_key": item_key,
            "duration_minutes": duration,
            "operation_key": operation_key,
            "message": {
                "coffee": "Ҫ��Ҫһ��� 30 ���ӣ�",
                "milk_tea": "һ��Ъ�����",
                "tea": "�����������",
                "cake": "�����ֵ����ףһ�¡�",
            }.get(item_key, ""),
        }
        try:
            self.social_client.rpc(
                "lili_send_food_interaction",
                {"p_target": target, "p_kind": str(kind), "p_payload": payload},
            )
        except Exception as exc:
            self.show_speech(f"û�ͳ�ȥ��{str(exc)[:120]}", 5200)
            return
        event = self.economy.record_food_gift_sent(
            target,
            recipient_label,
            item_key,
            operation_key=operation_key,
        )
        if event is None:
            self.show_speech("�����ѷ�����������Ǯ���ۿ�ʧ�ܣ����ȼ����", 5200)
            return
        self._sync_economy_events([event.as_dict()])
        text = {
            "coffee": f"? ������ {recipient_label} һ�𿪹� 30 ���ӡ�",
            "milk_tea": f"?? ������ {recipient_label} һ��Ъ�����",
            "tea": f"?? �Ѹ� {recipient_label} ���衣",
            "cake": f"?? ���� {recipient_label} ��ףһ�¡�",
        }.get(item_key, "�����Ѿ��ͳ���")
        self.show_speech(text, 5200)

    def _handle_food_interaction_accepted(self, event: dict) -> None:
        kind = str(event.get("kind") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        item_key = {
            "food_coffee": "coffee",
            "food_milk_tea": "milk_tea",
            "food_tea": "tea",
            "food_cake": "cake",
        }.get(kind)
        if not item_key:
            return
        duration = int(payload.get("duration_minutes") or 0)
        todo_title = str(payload.get("todo_title") or "")
        self._start_food_scene(
            item_key,
            duration,
            "",
            todo_title,
            consume_inventory=False,
            source="buddy_food_received",
        )

    def talk_to_pet(self, message: str) -> CompanionReply:
        """�ڱ��ش���һ���Ի�������ʾ Lili �Ļظ���"""

        self._record_user_interaction()
        reply = self.companion.reply_to(message)
        self._show_emotion(reply.state, 2600)
        self.show_speech(reply.text, 5600)
        return reply

    def perform_companion_action(self, action_key: str) -> CompanionReply:
        """�����û�ѡ��Ĺ��������⡢��������ף��ο������"""

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
        """�������������յĶ���֡��ɶ����鶯����"""

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
        """���ڶ�����������Чʱ������һ�Σ�����ɼ�ʱ����״̬��"""

        if sequence_id == self._action_sequence_id and not self.dragging:
            self.set_state(state)

    def start_work_timer(self) -> CompanionReply:
        """��ʼ���չ�����ʱ��������ë���밲����鶯����"""

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

        text = " ".join(str(message or "").replace("��ë", "", 1).split())
        text = text.strip(" ��,��.!��")
        reply: CompanionReply | None = None
        if text in {"��ʼ����", "����", "��ʼ��ʱ"}:
            reply = self.start_work_timer()
        elif text in {"��ͣ", "��ͣ����", "��ͣ��ʱ"}:
            reply = self.pause_work_timer()
        elif text in {"����", "��������", "�ָ���ʱ"}:
            reply = self.resume_work()
        elif text in {"��������", "������ʱ", "�չ�"}:
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
            self._record_focus_segment(session_seconds, completed=False)
            self._pause_notice_shown = False
            if automatic_reason and reason in {"idle", "idle_10m", "lock", "sleep", "fullscreen_video", "video"}:
                self._pause_notice_shown = reason != "idle_10m"
        self._award_focus_rewards()
        self.work_activity_timer.stop()
        self._set_temporary_activity("tea", 25_000)
        duration = format_work_duration(self.work_timer.today_seconds())
        if was_running and reason in {"sleep", "lock"}:
            system_event = "����������" if reason == "lock" else "���Խ���˯��"
            reply = CompanionReply(
                f"{system_event}����ë����ͣ���ּ�ʱ�����������������ͺá�",
                PetState.SLEEPY,
            )
        elif was_running and reason in {"idle", "idle_10m"}:
            reply = CompanionReply(
                "ʮ����û�м����������ë�Ȱ���ͣ���ˣ�����������������",
                PetState.CURIOUS,
            )
        elif was_running and reason in {"fullscreen_video", "video"}:
            reply = CompanionReply(
                "��⵽������ȫ������ë�Ȱ���ͣ���ˣ����������������",
                PetState.CURIOUS,
            )
        elif was_running:
            reply = self.companion.work_paused(duration)
        else:
            reply = CompanionReply(
                f"��ʱ��������ͣ״̬�������ۼƹ��� {duration}��",
                PetState.CURIOUS,
            )
        self._show_emotion(reply.state, 3200)
        quality_text = (
            f"\n����������{self._last_focus_quality.label}��{self._last_focus_quality.score}�֣�"
            if self._last_focus_quality else ""
        )
        self.show_speech(reply.text + quality_text, 5600)
        self.work_timer_changed.emit(False)
        self._schedule_social_tick()
        # ֱ�Ӳ�����ɺ��������������һ���Ҽ���ëʱ�ᰴ����״̬�ؽ���
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
        """��ɱ��ι��������������ۼƲ�������ף������"""

        self._record_user_interaction()
        self._reset_idle_episode()
        self._pause_notice_shown = False
        self._fullscreen_video_started_at = None
        room_id = self.focus_session.room_id
        session_seconds = self.work_timer.session_seconds()
        total = self.focus_session.finish()
        self._record_focus_segment(session_seconds, completed=True)
        self._award_focus_rewards()
        self.set_paused(False)
        self._recorded_focus_session_seconds = 0
        reply = self.companion.work_finished(format_work_duration(total))
        self._show_emotion(reply.state, 3400)
        quality_text = (
            f"\n����������{self._last_focus_quality.label}��{self._last_focus_quality.score}�֣�"
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
        self._generate_daily_report(show_dialog=False)
        if room_id:
            self._record_social_room_event(room_id, "focus_finish")
        food_scene = self.economy.active_food_scene()
        if food_scene and str(food_scene.get("scene_type") or "") in {"focus", "deep_focus"}:
            self.economy.finish_food_scene("work_finished")
            self.food_scene_timer.stop()
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

        if str(getattr(self.settings, "today_note_mode", "detailed")) == "compact":
            self.show_compact_todos()
        else:
            self.show_sticky_note(passive=passive)

    def show_sticky_note(self, *, passive: bool = False) -> None:
        """Open the independent free-form ������ window in detailed mode."""

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
        if sys.platform == "darwin":
            self._apply_macos_window_behavior(
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
        # characters and its ? button even when the Qt rectangles do not
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
        """Place the detailed ������ beside the pet, with screen-edge fallback."""

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
            if self._compact_todo_panel.isVisible():
                # A longer/shorter task changes the panel width, so its
                # pet-relative position must be recalculated in the same UI
                # turn rather than waiting for the next pet movement.
                self._position_compact_todos()

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
                and str(getattr(self.settings, "today_note_mode", "detailed")) == "compact"
                and str(getattr(self.settings, "today_note_display_mode", "pending")) != "hidden"
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
            self._record_economy_performance(str(getattr(task, "title", "��ɴ���")), str(task_id))
        if self._today_note_window is not None:
            self._today_note_window.refresh()
        self._set_temporary_activity(random.choice(COMPLETE_ACTIONS), 25_000)
        self.show_speech("���������ˣ�������ϡ�", 4200)

    def _set_todo_completion_from_note(self, task_id: str, completed: bool) -> None:
        task = self.time_memory.get_todo_view_item(task_id)
        if task is None:
            return
        if completed:
            was_open = not bool(getattr(task, "completed", False))
            self.time_memory.complete_todo_view_item(task_id, True)
            if was_open:
                self._record_economy_performance(str(getattr(task, "title", "��ɴ���")), str(task_id))
            self._set_temporary_activity(random.choice(COMPLETE_ACTIONS), 25_000)
            self.show_speech("���������ˣ�������ϡ�", 4200)
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
                self._record_economy_performance(str(getattr(task, "title", "��ɴ���")), str(task_id))
            self._set_temporary_activity(random.choice(COMPLETE_ACTIONS), 25_000)
            self.show_speech("���������ˣ�������ϡ�", 4200)
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
            f"�����չ���רע{summary['focus']}�����{summary['completed_tasks']}/{summary['total_tasks']}�",
            6200,
        )
        if self._today_note_window is not None:
            self._today_note_window.refresh()

    def rest_today(self) -> None:
        self.time_memory.records.set_rest_day(True)
        self._set_temporary_activity("tea", 20_000)
        self.show_speech("�У��ǽ��첻�������", 4200)
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
        """��ʾ�����ۼƹ���ʱ���͵�ǰ��ʱ״̬��"""

        self._record_user_interaction()
        state = PetState.SIT if self.work_timer.is_running else PetState.CURIOUS
        self._show_emotion(state, 2600)
        text = (
            f"{self.work_timer.status_text()}\n"
            f"{growth_progress_text(self.work_timer.today_seconds())}\n"
            f"��ë���飺{positive_mood(self.work_timer.today_seconds(), self.work_timer.session_seconds())}"
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
        """��ʾ���� 0�C8 Сʱ�ɳ��ڵ����һ���ɼ�������"""

        seconds = self.work_timer.today_seconds()
        stage = stage_for_seconds(seconds)
        self._set_temporary_activity(stage.activity, 35_000)
        self.show_speech(
            f"���ճɳ� {stage.hour}/8��{stage.title}\n"
            f"��ǰ������{stage.reward}\n{growth_progress_text(seconds)}",
            7600,
        )

    def _schedule_work_activity(self, delay_ms: int | None = None) -> None:
        """��ʱ�ڼ䰲����һ����鹤��������"""

        self.work_activity_timer.stop()
        if self.work_timer.is_running:
            self.work_activity_timer.start(delay_ms or random.randint(150_000, 300_000))

    def _work_activity_tick(self) -> None:
        """��רע�������ֻ������û�����ʱ��ëҲ����������"""

        if night_limited_activity(datetime.now()) is not None:
            self._night_limited_tick()
            self._schedule_work_activity()
            return
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
        """��ʾһ����������ͼ��������ص���������ͨ������"""

        self._change_ambient_activity(activity)
        self._manual_activity_until = time.monotonic() + duration_ms / 1000
        self.activity_timer.start(max(1500, duration_ms))

    def _activity_timeout(self) -> None:
        """������ʱ�����������м����ֻ�רע����������ָ���ͨ��ë��"""

        if night_limited_activity(datetime.now()) is not None:
            self._night_limited_tick()
            return
        self._change_ambient_activity(
            random.choice(FOCUS_ACTIONS) if self.work_timer.is_running else "none"
        )

    def _work_timer_tick(self) -> None:
        """���ڱ��湤�����ȣ�����ʾһ�ε��ڵĹ�������Ϣ���ѡ�"""

        self._check_local_alarms()
        self._check_local_reminders()
        self.work_timer.checkpoint()
        snapshot = self.focus_session.refresh()
        self._update_work_duration_bubble(snapshot)
        if self.work_controls.isVisible():
            self.work_controls.set_duration_visible(bool(self.settings.show_work_duration))
            self.work_controls.set_session_duration(
                "���� " + format_work_duration(snapshot.session_seconds)
                if snapshot.status in {"focus", "rest"} else "����δ��ʼ"
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
            self.show_speech("�ȿ�ˮ�ɡ���ë�������һС�����ס��", 6200)
        elif wellness_kind == "stand":
            self._set_temporary_activity("football", 35_000)
            self.show_speech("վ���������������ɼ��ɡ�����Ҳ��������ɽ��졣", 6500)
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
        self.show_speech("? �ⳡ��ȹ������� 2 Сʱ�ˡ���ͨ���� ��1����ë�������š�", 6500)
        if self._food_scene_dialog is not None:
            self._food_scene_dialog.refresh()

    def _check_local_reminders(self) -> None:
        """Run the local reminder queue once per existing one-second timer."""

        if detect_quiet_mode().blocked or bool((self.economy.active_food_scene() or {}).get("deep_focus")):
            return
        for reminder in self.time_memory.reminders.due()[:3]:
            self.time_memory.reminders.mark_notified(reminder.id)
            self._set_temporary_activity("curious", 12_000)
            self.show_speech(f"���ѣ�{reminder.title}", 5600)

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
        """������� 1�C8 Сʱ�ڵ�ʱ��ʾ�ɳ�״̬�����ǻ�е�����·���"""

        stage = stage_for_seconds(self.work_timer.today_seconds())
        if stage.hour <= self._last_growth_hour:
            return
        self._last_growth_hour = stage.hour
        self._set_temporary_activity(stage.activity, 60_000)
        self.show_speech(f"���ճɳ���{stage.title}\n������{stage.reward}\n{stage.message}", 8200)
        if stage.hour >= 8:
            self._generate_daily_report(show_dialog=True)

    def _sync_hourly_outfit(self, *, announce: bool) -> None:
        """ͬ��Сʱ���½������������û�����ѡ��ĵ�ǰװ����

        ����ʱ��ֻ������Щ��װ���ã�װ���������û�ƫ�ã�����һֱ������
        �����ۼƵ� 10 Сʱ�������Ұ�����װ����������û����ڴ���
        һСʱ����װ��������ۻ������ѽ�����װǿ���滻����
        """

        count = self.work_timer.unlocked_outfit_count()
        latest = OUTFITS[count - 1] if count else None
        newly_unlocked = self.work_timer.take_new_outfit_unlock()
        if not announce or newly_unlocked is None or latest is None:
            return
        self._change_ambient_activity("none")
        self.show_speech(
            f"�ۼ�רע {newly_unlocked} Сʱ���ѽ�����{latest.name}����\n"
            f"������ڡ���װ����ۡ���ѡ�񣬵�ǰװ�����ֲ��䡣",
            8200,
        )

    def _award_focus_rewards(self) -> None:
        """�ѽ���רעʱ�任�������Ĭ�������������켢���ͷ���"""

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

    def _record_focus_segment(self, session_seconds: int, *, completed: bool) -> int:
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
        started_at = datetime.now().astimezone() - timedelta(seconds=seconds)
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
        )
        self.focus_analytics.update_current_task_progress(seconds)
        self.daily_stats.record_focus(seconds, completed=completed)
        self._recorded_focus_session_seconds = total
        return seconds

    def _record_economy_performance(self, title: str, task_id: str) -> None:
        events = []
        event = self.economy.record_performance(
            f"����Ч��{title[:90]}",
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
        """ͬ��Ǯ������/�����¼�������˰� source_key �ݵ�ȥ�ء�"""
        self._sync_economy_events(
            [event.as_dict() for event in self.economy.events]
        )

    def _sync_economy_events(self, events: list[dict]) -> None:
        if not events or not getattr(self.social_client, "signed_in", False):
            return
        recorder = getattr(self.social_client, "record_economy_event", None)
        if not callable(recorder):
            return

        def sync() -> None:
            for event in events:
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

        threading.Thread(target=sync, name="lili-economy-sync", daemon=True).start()

    def shutdown_work_timer(self) -> None:
        """��Ȼ�˳�ǰ��ͣ��ʱ�����µ��칤���������ѹػ�ʱ����빤����"""

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
            if self.work_timer.today_seconds() > 0 and hasattr(self, "label"):
                self._generate_daily_report(show_dialog=False)

    def _generate_daily_report(self, *, show_dialog: bool) -> Path | None:
        """����ֻ�����ڱ����Ĺ����ձ�����ѡչʾԤ�����ڡ�"""

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
                self.show_speech(f"�����ձ���ʱû����ɹ���{exc}", 6200)
            return None
        if show_dialog:
            self._show_daily_report_dialog(path)
        return path

    def show_daily_report(self) -> None:
        """�ɲ˵����ɲ��򿪽������ë�����ձ���"""

        self._record_user_interaction()
        self._generate_daily_report(show_dialog=True)

    def _show_daily_report_dialog(self, path: Path) -> None:
        """��Ӧ����Ԥ�������������ṩ�򿪱������İ�ť��"""

        dialog = QDialog(self)
        dialog.setWindowTitle(f"����{self._pet_name()}��������ʲô")
        layout = QVBoxLayout(dialog)
        preview = QLabel(dialog); preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card = QPixmap(str(path)).scaled(430, 570, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        preview.setPixmap(card); layout.addWidget(preview)
        open_button = QPushButton(f"��{self._pet_name()}���", dialog)
        open_button.clicked.connect(self.open_daily_album)
        layout.addWidget(open_button)
        dialog.exec()

    def open_daily_album(self) -> None:
        """�򿪱�����ë����ļ��У����������硣"""

        directory = album_directory(); directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory.resolve())))

    def prompt_dialogue(self) -> None:
        """���°�������壻���ߺ�����ģʽ����ͬһ����ڡ�"""

        self._record_user_interaction()
        if self._chat_dialog is None:
            # Keep chat as an independent utility window so it has a normal
            # taskbar/Dock entry and can be minimized without affecting pet.
            self._chat_dialog = ChatDialog(None, self._pet_name())
            self._chat_dialog.message_submitted.connect(self._submit_chat_message)
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
                        ("��" if role == "user" else self._pet_name(), text)
                        for role, text in saved_messages
                    ]
                )
            else:
                self._chat_dialog.append_message(
                    self._pet_name(),
                    "�Ͳ��û��Ҳ�������죻Ҳ�������������� Codex��Claude Code��DeepSeek �� Kimi��",
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
        """Edit the owner's social nickname; the pet remains ��ë forever."""

        self._record_user_interaction()
        name, accepted = QInputDialog.getText(
            self,
            "�޸����˳ƺ�",
            "���˳ƺ�\n������ϰ�ҡ����źʹ��ӻ���ʱ���ֲ�ͬ��ë��\n������д��С�������������ӽ�������С���ҵ���ë����",
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
        self.show_speech(f"�ã��罻������ͽ�{label}���һ�����ë��", 4200)

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
            self._social_dialog._set_status("���˳ƺ��ѱ����ڱ��������ƶ�ͬ��ʧ�ܣ����Ժ����ԡ�", error=True)

    def _social_profile_thread_finished(self, thread: SocialProfileThread) -> None:
        if thread in self._social_profile_threads:
            self._social_profile_threads.remove(thread)
        thread.deleteLater()

    def _submit_chat_message(self, message: str) -> None:
        """����Ϣ���� ChatManager��·��ֻ��ȡ���棬����ͬ����⡣"""

        if self._chat_dialog is None:
            return
        self._record_user_interaction()
        if self.chat_manager.busy:
            self._chat_dialog.append_message(self._pet_name(), "��һ�仰����·�ϣ��Ե���һ�¡�")
            return
        self._chat_dialog.append_message("��", message)
        history_before = self._chat_memory.snapshot().as_history()
        self._chat_memory.add("user", message)
        self._chat_history.append("user", message)
        self._chat_dialog.show_recovery_actions(False)
        self.chat_manager.submit(message, history_before)

    def _managed_chat_reply(self, reply: ManagedChatReply) -> None:
        """ͳһչʾ AI �����߻ظ�������ʱ���������Ӵ������ġ�"""

        self._chat_memory.add("assistant", reply.text)
        self._chat_history.append("assistant", reply.text)
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
        """ֻ�����������룬���ﶯ������ʱ�����ּ������С�"""

        if self._chat_dialog is not None:
            self._chat_dialog.set_interrupt_available(self.settings.ai_provider == "codex")
            self._chat_dialog.set_busy(busy)
        if self._chat_history_dialog is not None:
            self._chat_history_dialog.set_mutation_enabled(not busy)

    def _interrupt_chat(self) -> None:
        """Stop only the active Codex App Server turn."""

        if self.chat_manager.interrupt():
            return
        self._chat_notice("��һ����ʱ�������жϣ����ٵ���һ�¡�")

    def _chat_notice(self, message: str) -> None:
        """��ʾ��������ʾ������ת����ҳ��"""

        if self._chat_dialog is not None:
            self._chat_dialog.append_message(self._pet_name(), message)

    def _clear_chat_display(self) -> None:
        """ֻ������촰�ڵ�ǰ��ʾ����ɾ����¼������� AI �����ġ�"""

        if self.chat_manager.busy:
            self._chat_notice("��һ�仹�������У������������������ʾ��")
            return
        if self._chat_dialog is not None:
            self._chat_dialog.clear_transcript()

    def _start_new_conversation(self) -> None:
        """ȷ�Ϻ������ǰ�����Ĳ������µı��ػỰ�����챣�ֲ��䡣"""

        if self.chat_manager.busy:
            self._chat_notice("��һ�仰����·�ϣ������������ٿ�ʼ�¶Ի���")
            return
        answer = QMessageBox.question(
            self._chat_dialog or self,
            "��ʼ�¶Ի�",
            "��������ë��ǰ�����������ģ�������һ�仰�����µ� AI �Ի���\n"
            "���������Իᱣ���ڡ������¼�����������Ѳ��ᱻɾ����\n\n������",
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
        if self._chat_dialog is not None:
            self._chat_dialog.clear_transcript()
            self._chat_dialog.append_message(
                self._pet_name(),
                "�ã��µ����쿪ʼ����֮ǰ�������¼���ڣ����������Ҳ�������š�",
            )

    def _show_chat_history(self) -> None:
        """�򿪱��������¼�鿴���ڣ������� AI ��ı䵱ǰ�Ի���"""

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
        """ɾ����ǰ�Ựʱͬ����� AI �����ģ���������ѱ��ֲ��䡣"""

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
                "��������¼��ɾ�����µ��������㿪ʼ�����������û�иı䡣",
            )

    def _clear_all_chat_history(self) -> None:
        """ɾ��ȫ�����������¼��ͬʱ���� AI �����ĵ��������졣"""

        if self.chat_manager.busy:
            self._chat_notice("��һ�仰����·�ϣ�������������ɾ�������¼��")
            return
        answer = QMessageBox.question(
            self._chat_history_dialog or self,
            "ɾ��ȫ�������¼",
            "ȷ��ɾ�����������ȫ�������¼��������ë�� AI ��������\n"
            "���졢���Ѻ�����Ӧ�����ݲ����ܵ�Ӱ�졣",
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
                "�����¼�Ѿ���գ��µ��������㿪ʼ�����������û�иı䡣",
            )

    def _agent_status_changed(self, provider: str, state: str, detail: str) -> None:
        """��̨�����ɺ�ˢ�»���״̬�İ����ָ�����һ����Ȼ�� AI��"""

        if self._chat_dialog is not None and provider == self.settings.ai_provider:
            self._chat_dialog.set_provider(provider, state, detail)
            if state == "connected":
                self._chat_dialog.show_recovery_actions(False)

    def _reconnect_ai(self) -> None:
        """�û�����Ҫ�������������Զ������ô��ڡ�"""

        if self.settings.ai_provider == "offline":
            self._chat_notice("��ǰѡ����Ǵ�����ģʽ����Ҫ AI ʱ���Ե㡰ȥ���á���")
            return
        if not self.chat_manager.reconnect_now():
            self._chat_notice("AI ���ں�̨����У����Ե�һ�¡�")

    def open_settings(self, source: str) -> bool:
        """ֻ������ȷ�û����������ã������Զ���δ֪��Դ���ܾ�����¼��"""

        if source != SETTINGS_SOURCE_USER_ACTION:
            LOGGER.debug("�ܾ����û���Դ��������������ã�source=%r", source)
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
            self.show_speech(f"����û�б��棺{exc}", 6000)
            return True
        self.ai_service.codex_path = str(
            getattr(self.settings, "codex_executable_path", "") or ""
        ).strip()
        current_pet_name = self._pet_name()
        self.setWindowTitle(f"{APP_DISPLAY_NAME} �� {current_pet_name}")
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
        self.show_speech(f"���л�Ϊ��{preset.label}", 4200)
        return True

    def open_social_hub(self) -> None:
        """������������˽����ϰ�ң����߹��ܲ������˴��ڡ�"""

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
            self._social_dialog.food_interaction_requested.connect(self._send_food_interaction)
            self._social_dialog.food_interaction_accepted.connect(self._handle_food_interaction_accepted)
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
        self._refresh_shortcut_state()
        self._update_work_duration_bubble(snapshot)
        if self.work_controls.isVisible():
            status = str(getattr(snapshot, "status", "idle"))
            seconds = int(getattr(snapshot, "session_seconds", 0) or 0)
            self.work_controls.set_session_status(status)
            self.work_controls.set_duration_visible(bool(self.settings.show_work_duration))
            self.work_controls.set_session_duration(
                "���� " + format_work_duration(seconds)
                if status in {"focus", "rest"} else "����δ��ʼ"
            )
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
        labels = {"poke": "���˴���", "cheer": "�������", "drink": "�ݸ���һ���̲�"}
        if kind == "phrase":
            text = f"{actor}��{message[:100]}"
            activity = "happy"
        else:
            text = f"{actor}{labels.get(kind, '���㷢��һ�����䶯̬')}"
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
        self.show_speech(f"����ֻ��һ���£�{title[:80]}", 4200)

    def _set_tomorrow_review(self, title: str) -> None:
        self.focus_analytics.set_tomorrow_task(title)
        if title:
            self.show_speech(f"�����һ���¼Ǻ��ˣ�{title[:80]}", 4200)
        else:
            self.show_speech("�����һ��������ա�", 3200)

    def _room_ritual_due(self, label: str) -> None:
        if detect_quiet_mode().blocked:
            return
        self.show_speech(f"�������ѣ�{label}�����һ��������", 5000)

    def _buddy_subscription_notice(self, message: str) -> None:
        if detect_quiet_mode().blocked:
            return
        self.show_speech(message, 4200)

    def _social_dialog_finished(self) -> None:
        if self._social_dialog is not None:
            self._social_dialog.deleteLater(); self._social_dialog = None

    def _social_tick(self) -> None:
        """ÿ 30 ��ˢ�·���״̬���������跢�ͣ�ʧ��ʱ�����������衣"""

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
            "session_active": bool(self.work_timer.has_active_session),
            "work_state": str(getattr(snapshot, "state", "idle") or "idle"),
            "pause_reason": getattr(snapshot, "pause_reason", None),
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
        """��ʾ�´������ѣ�����˫�����ش�˫��ë���档"""

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
                self.show_speech(f"{social_pet_label(visit.get('owner_nickname') or visit.get('nickname'))}����������\n�򿪡�������ϰ�ҡ����Խ��ܡ�", 7600)
        active = data.get("active_visits") or []
        if active:
            self._show_buddy_visit(active[0])

    def _social_sync_failed(self, message: str) -> None:
        """Keep the pet quiet while making an unavailable room understandable."""

        if self._social_dialog is not None:
            if self._social_dialog.current_room_id:
                self._social_dialog._set_status(
                    f"��ϰ����ʱ���ߣ�{message}"
                    "����ë�Ի᱾�ؼ�ʱ������ָ����Զ����ԡ�"
                )
            elif self.focus_session.snapshot().is_running:
                self._social_dialog._set_status(
                    "����רע�ѿ�ʼ���㻹û�м�����ϰ�ң�����״̬��������ָ����Զ�ͬ����"
                )
            else:
                self._social_dialog._set_status(
                    "�㻹û�м�����ϰ�ң����ع��ܲ���Ӱ�죬���������״̬���Զ�ͬ����"
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
        if action == "��Ҳ������":
            self._room_quick_status = ""
            self._room_quick_status_expires_at = None
            self.start_work_timer()
        elif action == "�پ� 30 ����":
            self._room_quick_status = "�پ�30����"
            self._room_quick_status_expires_at = datetime.now().astimezone() + timedelta(minutes=30)
            if not self.work_timer.is_running:
                self.start_work_timer()
            elif self._social_dialog is not None:
                self._social_dialog.set_room_quick_status(self._room_quick_status, self._room_quick_status_expires_at)
        elif action == "ȥ��ˮ":
            self._room_quick_status = "ȥ��ˮ"
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
        """���û�ͣ��ֻ�ڱ������ɵļ�Ъ��ɧ��"""

        self.settings.automatic_grumbling = bool(enabled)
        save_settings(self.settings)
        self._schedule_ambient()
        self.show_speech("ż������ɧ�ѿ�����" if enabled else "ż������ɧ�ѹرա�", 3000)

    def set_work_duration_display(self, enabled: bool) -> None:
        """Persist whether the floating work-control bubble shows live duration."""

        self.settings.show_work_duration = bool(enabled)
        save_settings(self.settings)
        self.work_controls.set_duration_visible(self.settings.show_work_duration)
        self._update_work_duration_bubble()
        self.show_speech(
            "���ֹ���ʱ����ʾ�ѿ�����" if enabled else "���ֹ���ʱ����ʾ�ѹرա�",
            3000,
        )

    def set_hourly_announcement(self, enabled: bool) -> None:
        """���û�ͣ�����㱨ʱ��"""

        self.settings.hourly_announcement = bool(enabled)
        self._last_announced_hour = ""
        save_settings(self.settings)
        self.show_speech("���㱨ʱ�ѿ�����" if enabled else "���㱨ʱ�ѹرա�", 3200)

    def _app_awareness_tick(self) -> None:
        """ֻ����ǰ̨Ӧ������л����ζ���������ȡ������ĵ����ݡ�"""

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
        """�Զ�Ѱ������õı����������������ʼ���ų³�����"""

        if self.music_controller.play_song("", "�³���", random_artist=True):
            self.show_speech("�����Զ�Ѱ�ҿ��ò����������������һ�׳³�����", 4200)
        else:
            self.show_speech("���ֲ������ڴ����У����Ե�һ�¡�", 3200)
        return "�³����������"

    def _play_random_song_legacy(self) -> str:
        """����������еĳ³������������ѡ����ִ�в�����ý��У�顣"""

        if self.music_controller.play_song("", "�³���", random_artist=True):
            self.show_speech("���ڴӳ³����ĸ�����������ѡ�񣬲��˶�ʵ�ʲ��Ÿ�������", 4200)
        else:
            self.show_speech("��һ�����ֲ������ڴ����У����Ե�һ�¡�", 3200)
        return "�³����������"

    def control_music(self, action: str) -> bool:
        """�첽���Ƹղ�������ʼ���ŵ� Provider��������ѡ��������������"""

        if self.music_controller.perform(action):
            self.show_speech("��������ϵͳ��������", 2200)
            return True
        self.show_speech("���ֿ��ƻ��ڴ����У����Ե�һ�¡�", 3200)
        return False

    def _music_control_result(self, result: MusicControlResult | SongPlaybackResult) -> None:
        """ֻ���û�������ɺ������������������ʾ������ Now Playing ״̬��"""

        is_status = isinstance(result, MusicControlResult) and result.action == "status"
        if isinstance(result, MusicControlResult):
            track_artist = result.status.track.artist if result.status.track else ""
            track_title = result.status.track.title if result.status.track else ""
        else:
            track_artist = result.current_artist
            track_title = result.current_title
        family_music = family_music_mode(track_artist, track_title)
        if family_music:
            # �������ĸ�ʱ����ë�����裬��ʱ������ͨ�������š�
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
                feedback = "���������������"
            else:
                feedback = {
                    "toggle": "����״̬���л���",
                    "previous": "���л�����һ�ס�",
                    "next": "���л�����һ�ס�",
                }.get(result.action, "���ֲ�������ɡ�")
        else:
            # ʧ��ʱҲֻ�������β���������Ѳ��������صĸ�������ý��״̬��
            # Now Playing �İ����´�����ë���ݡ���������ǿ�����ڣ�����״̬��塣
            if isinstance(result, SongPlaybackResult):
                feedback = "���������ʱû�гɹ�����ȷ�ϲ��������á�"
            else:
                feedback = {
                    "toggle": "����/��ͣ��ʱ�޷�ִ�С�",
                    "previous": "��һ����ʱ�޷�ִ�С�",
                    "next": "��һ����ʱ�޷�ִ�С�",
                    "play": "������ʱ�޷�ִ�С�",
                    "pause": "��ͣ��ʱ�޷�ִ�С�",
                }.get(result.action, "���ֲ�����ʱ�޷�ִ�С�")
        self.show_speech(feedback, 3200 if result.success else 4200)

    def set_activity(self, activity: str) -> None:
        """�ֶ�ѡ�������涯�����е���������������"""

        self._set_temporary_activity(activity, 120_000)
        self.show_speech("�������л�����ë��ʼ��������", 2800)

    def equip_outfit(self, outfit_key: str) -> None:
        """װ���ѽ������£����ַ����ָ�������ۡ�"""

        allowed = {item.key for item in unlocked_outfits(self.work_timer.unlocked_outfit_count())}
        if outfit_key and outfit_key not in allowed:
            self.show_speech("�������»���������������ۼƹ���һСʱ�͸���һ�㡣", 5200)
            return
        self.settings.equipped_outfit = outfit_key
        save_settings(self.settings)
        # Cancel a half-finished action cross-fade so the newly selected outfit
        # is visible immediately, even while a transient work action is ending.
        self.activity_transition_timer.stop()
        self._activity_transition_from = QPixmap()
        self._activity_transition_step = self._activity_transition_steps
        self._mask_cache.clear()
        self._refresh_pixmap()
        self.update()
        label = next((item.name for item in OUTFITS if item.key == outfit_key), "������ë")
        self.show_speech(f"�ѻ��ϣ�{label}��", 3200)

    def open_size_control(self) -> None:
        """�������ߴ绬�鲢ʵʱӦ�ã����ı䲻ͬ����֮��ı�����"""

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
        for outfit in OUTFITS:
            action = menu.addAction(outfit.name)
            available = outfit in unlocked
            action.setEnabled(available)
            action.setCheckable(True)
            action.setChecked(outfit.key == self.settings.equipped_outfit)
            if available:
                action.triggered.connect(
                    lambda _checked=False, key=outfit.key: self.equip_outfit(key)
                )

    def show_outfit_menu(self) -> None:
        """Open the outfit selector from the shared menu without duplicating menus."""

        menu = QMenu(self)
        self._populate_outfit_menu(menu, default_label=f"����{self._pet_name()}")
        menu.exec(QCursor.pos())

    def _populate_pet_companion_menu(self, menu: QMenu) -> None:
        """Keep direct affection actions here; food is a separate scenario entry."""

        for label, action_key in (
            ("����һ������", "love"),
            ("Ϊ�Ҽ���", "encourage"),
            ("��������Ϣ", "rest"),
        ):
            action = menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, key=action_key: self.perform_companion_action(key)
            )

    def _position_floating_panel(self, panel: QWidget) -> None:
        """�ѿ�������ڳ����Ա߲������ڵ�ǰ��Ļ�ɼ�����"""

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
        """Place the icon dock above the pet, then use an edge-safe fallback.

        The anchor is always the pet window rectangle, never an animation mask,
        so breathing and action frames cannot make the dock jump by a few
        pixels.  The preferred gap leaves the red hair visible below the dock.
        """

        panel = self.quick_panel
        panel.adjustSize()
        area = self._screen_geometry()
        gap = 12
        pet_rect = QRect(self.x(), self.y(), self.width(), self.height())
        blocked = [pet_rect]
        for accessory in (self.speech_bubble, self._compact_todo_panel, self.work_controls):
            if accessory is not None and accessory.isVisible():
                blocked.append(accessory.geometry())
        center_x = self.x() + (self.width() - panel.width()) // 2
        upper_y = self.y() - panel.height() - gap
        candidates = [
            (center_x, upper_y),
            (self.x() - panel.width() - gap, self.y() - panel.height() // 3),
            (self.x() + self.width() + gap, self.y() - panel.height() // 3),
            (self.x() - panel.width() - gap, self.y() + (self.height() - panel.height()) // 2),
            (self.x() + self.width() + gap, self.y() + (self.height() - panel.height()) // 2),
            (center_x, self.y() + self.height() + gap),
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
        """Keep the live duration label by the pet's feet with edge fallback."""

        bubble = self.work_duration_bubble
        bubble.adjustSize()
        area = self._screen_geometry()
        gap = 5
        pet_rect = QRect(self.x(), self.y(), self.width(), self.height())
        blocked = [pet_rect]
        for accessory in (
            self._compact_todo_panel,
            self.speech_bubble,
            self.quick_panel,
            self.work_controls,
        ):
            if accessory is not None and accessory.isVisible():
                blocked.append(accessory.geometry())
        center_x = self.x() + (self.width() - bubble.width()) // 2
        candidates = [
            (center_x, self.y() + self.height() + gap),
            (center_x, self.y() - bubble.height() - gap),
            (self.x() - bubble.width() - gap, self.y() + self.height() - bubble.height()),
            (self.x() + self.width() + gap, self.y() + self.height() - bubble.height()),
        ]
        chosen = None
        for candidate_x, candidate_y in candidates:
            candidate = QRect(candidate_x, candidate_y, bubble.width(), bubble.height())
            if area is not None and not area.contains(candidate):
                continue
            if any(candidate.intersects(item) for item in blocked):
                continue
            chosen = candidate
            break
        if chosen is None:
            candidate_x, candidate_y = candidates[0]
            if area is not None:
                candidate_x = min(max(candidate_x, area.left()), area.right() - bubble.width() + 1)
                candidate_y = min(max(candidate_y, area.top()), area.bottom() - bubble.height() + 1)
            chosen = QRect(candidate_x, candidate_y, bubble.width(), bubble.height())
        bubble.move(chosen.topLeft())

    def _update_work_duration_bubble(self, snapshot=None) -> None:
        """Render the shared focus snapshot without creating a second timer."""

        if not hasattr(self, "work_duration_bubble"):
            return
        current = snapshot or self.focus_session.snapshot()
        show_duration = bool(getattr(self.settings, "show_work_duration", True))
        was_visible = self.work_duration_bubble.isVisible()
        self.work_duration_bubble.set_session(
            str(getattr(current, "status", "idle")),
            int(getattr(current, "session_seconds", 0) or 0),
            show_duration,
        )
        if self.work_duration_bubble.isVisible():
            self._position_work_duration_bubble()
        if not was_visible and self.work_duration_bubble.isVisible():
            self._apply_macos_window_behavior(self.work_duration_bubble)
        if self.work_duration_bubble.isVisible():
            self._raise_accessory(self.work_duration_bubble)

    def show_quick_panel(self) -> None:
        """˫���л���ݿڴ����ٴ�˫����������"""

        if self.quick_panel.isVisible():
            self.quick_panel.hide()
            return
        self._refresh_shortcut_state()
        self.quick_panel.set_food_inventory({
            key: self.economy.inventory_count(key)
            for key in ("coffee", "expensive_coffee", "milk_tea", "cake", "tea")
        })
        self._position_quick_panel()
        self.quick_panel.show()
        if sys.platform == "darwin":
            self._apply_macos_window_behavior(self.quick_panel)
        self._raise_accessory(self.quick_panel)

    def show_work_controls(self) -> None:
        """����ë�Ϸ���ʾ��ǰ״̬Ψһ��Ч�Ĺ���������"""

        self._show_work_controls()

    def _show_work_controls(self) -> None:
        """Show the work dock above the pet using the shared focus state."""

        snapshot = self.focus_session.snapshot()
        self._update_work_duration_bubble(snapshot)
        self.work_controls.set_session_status(snapshot.status)
        self.work_controls.set_duration_visible(bool(self.settings.show_work_duration))
        duration = format_work_duration(snapshot.session_seconds)
        self.work_controls.set_session_duration(
            "���� " + duration if snapshot.status in {"focus", "rest"} else "����δ��ʼ"
        )
        self._position_work_controls()
        self.work_controls.show()
        if sys.platform == "darwin":
            self._apply_macos_window_behavior(self.work_controls)
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
        """������ֱ���л���ʼ����ͣ�ͼ��������ٵ�����������ơ�"""

        if self.focus_session.snapshot().status == "focus":
            self.pause_work_timer()
        else:
            self.start_work_timer()

    def _quick_food_action(self, item_key: str) -> None:
        """Use one food directly from the lightweight shortcut pocket."""

        item_key = str(item_key or "").strip()
        if item_key not in {"coffee", "expensive_coffee", "milk_tea", "cake", "tea"}:
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
            "idle": "��ʼ����",
            "focus": "��ͣ����",
            "rest": "��������",
        }
        self.quick_panel.set_work_action_label(
            labels.get(self.focus_session.snapshot().status, "��ʼ����")
        )

    def set_menu_external_callbacks(
        self, callbacks: dict[str, Callable[[bool], object]]
    ) -> None:
        """Add application-level commands used by tray and Dock projections."""

        self._menu_external_callbacks = dict(callbacks)

    def _invoke_menu_external(self, command: str) -> None:
        """Run an application-owned command from the quick settings menu."""

        callback = self._menu_external_callbacks.get(command)
        if callback is not None:
            callback(False)
            return
        self.show_speech("�������Ҫ��Ӧ�÷���׼���ú���ִ�С�", 2600)

    def _menu_state(self) -> dict[str, object]:
        snapshot = self.focus_session.snapshot()
        labels = {"idle": "��ʼ����", "focus": "��ͣ����", "rest": "��������"}
        return {
            "work_action_label": labels.get(snapshot.status, "��ʼ����"),
            "work_status": snapshot.status,
            "visible": self.isVisible(),
            "always_on_top": bool(self.settings.always_on_top),
            "show_work_duration": bool(self.settings.show_work_duration),
            "program_version": __version__,
            "content_version": "��������",
        }

    def _menu_callbacks(self) -> dict[str, Callable[[bool], object]]:
        """Return commands shared by the pet window, tray, and Dock."""

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
            "music_random": lambda _checked=False: self.play_random_song(),
            "companion_love": lambda _checked=False: self.perform_companion_action("love"),
            "companion_encourage": lambda _checked=False: self.perform_companion_action("encourage"),
            "companion_rest": lambda _checked=False: self.perform_companion_action("rest"),
            "companion_status": lambda _checked=False: self.show_companion_status(),
            "outfit": lambda _checked=False: self.show_outfit_menu(),
            "rename": lambda _checked=False: self.rename_pet(),
            "settings": lambda _checked=False: self.open_settings(SETTINGS_SOURCE_USER_ACTION),
            "show_work_duration": lambda checked=False: self.set_work_duration_display(checked),
            "size": lambda _checked=False: self.open_size_control(),
            "show_todos": lambda _checked=False: self.show_compact_todos(),
            "hide_todos": lambda _checked=False: self.hide_compact_todos(),
            "add_todo": lambda _checked=False: self.add_compact_todo(),
            "time_memory": lambda _checked=False: self.show_time_memory(),
            "show_work_time": lambda _checked=False: self.show_work_time(),
            "economy": lambda _checked=False: self.show_economy(),
            "alarms": lambda _checked=False: self.show_alarm_center(),
            "show_growth": lambda _checked=False: self.show_daily_growth(),
            "show_report": lambda _checked=False: self.show_daily_report(),
            "open_album": lambda _checked=False: self.open_daily_album(),
            "topmost": lambda checked=False: self.set_always_on_top(checked),
            "visibility": lambda _checked=False: self.show() if not self.isVisible() else self.hide(),
            "quit": lambda _checked=False: self.quit_requested.emit(),
        }
        callbacks.update(self._menu_external_callbacks)
        return callbacks

    def unified_menu_model(self) -> UnifiedMenuModel:
        """Expose the same model to Qt and the optional native macOS Dock menu."""

        return UnifiedMenuModel(
            pet_name=self._pet_name(),
            state_provider=self._menu_state,
            callbacks=self._menu_callbacks(),
        )

    def build_unified_menu(self, parent=None, context: str = "pet") -> QMenu:
        """Render the unified menu for the requested platform entrance."""

        # Tray/Dock menus must not inherit the pet window's active/enabled
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
        """��������������ë�������֣����ִ��ڸе�����Ƶ�����š�"""

        if not hasattr(self, "ambient_timer"):
            return
        self.ambient_timer.stop()
        if self.settings.automatic_grumbling:
            self.ambient_timer.start(random.randint(8 * 60_000, 18 * 60_000))

    def _night_limited_tick(self) -> None:
        """�ڱ��� 00:30�C06:30 ��ʾ�����޶����ͣ�06:30 ����ָ���ͨ״̬��"""

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

    def _ambient_tick(self) -> None:
        """��ʱ�Ρ�רע������͸��ʲʵ�����ë�������û���"""

        try:
            if night_limited_activity(datetime.now()) is not None:
                self._night_limited_tick()
                return
            busy = self.chat_manager.busy
            if self.isVisible() and not self.dragging and not busy:
                idle_seconds = time.monotonic() - self._last_user_interaction
                if self.work_timer.is_running and self.work_timer.session_seconds() >= 2 * 3600:
                    activity, text = "thermos", "����������Сʱ������ë��ˮ�������ˣ�����Ϣһ�£�"
                elif idle_seconds >= 30 * 60:
                    activity, text = "pointing", "��ܾ�û��������ë͵͵̽ͷ�����㻹�ڲ��ڡ�"
                elif self.work_timer.today_seconds() >= 3 * 3600 and random.random() < 0.06:
                    activity, text = "wild-king", "���͸��ʲʵ�����Ұ����·��������档"
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
                        text = "�������һ�������������"
                    elif activity == "sleepy":
                        text = "��ë�е��������ǵø��Լ���һ����Ϣʱ�䡣"
                    elif activity == "sit":
                        text = "�������������㣬����һС����ɾͺá�"
                    elif activity == "headphones":
                        text = "������������������һ��רע��"
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
        """���û����õ������Ÿ�����ݣ�������͸�����ɧ���ü�ʱ����"""

        if not hasattr(self, "song_timer"):
            return
        self.song_timer.stop()
        if self.settings.lyric_inspiration_enabled:
            base = self.settings.lyric_interval_minutes * 60_000
            self.song_timer.start(max(60_000, round(base * random.uniform(0.85, 1.15))))

    def _song_inspiration_tick(self) -> None:
        """��ʾ������ʶ��У�δѡ���ļ�ʱ��ʾԭ����������̾䡣"""

        try:
            busy = self.chat_manager.busy
            if self.isVisible() and not self.dragging and not busy:
                local_lines = load_local_lines(self.settings.local_lyrics_path)
                if local_lines:
                    self._show_emotion(PetState.SIT, 2400)
                    self.show_speech(f"? {random.choice(local_lines)}", 6800)
                else:
                    reply = self.companion.song_inspiration()
                    self._show_emotion(reply.state, 2400)
                    self.show_speech(f"? {reply.text}", 6800)
        finally:
            self._schedule_song_inspiration()

    def _hourly_tick(self) -> None:
        """���ڼ�����㱨ʱ���ر�ʱ�������κ����ݡ�"""

        self._maybe_announce_hour(datetime.now())

    def _maybe_announce_hour(self, now: datetime) -> bool:
        """��ÿ�����㴰����ֻ����һ�Σ������Ƿ�ʵ�ʲ�����"""

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
        """��������ʾ��ǰ�Ự�����ܡ������ͱ�ʳ״̬��"""

        self._record_user_interaction()
        count = self.work_timer.unlocked_outfit_count()
        next_text = "12 ��Сʱ������ȫ������"
        if count < len(OUTFITS):
            remaining = max(0, (count + 1) * 3600 - self.work_timer.lifetime_seconds())
            next_text = f"����һ������Լ {format_work_duration(remaining)}"
        self.show_speech(
            f"{self.companion.status_text(self.work_timer.today_seconds() // 600)}\n{next_text}",
            6200,
        )

    def trigger_interaction(self) -> None:
        """��ϵ�ǰ������ֵ�����Ѻñ������ַ�����"""

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
            self.show_speech("�Ͳ����ë����������", 3000)

    def _show_emotion(self, state: PetState, duration_ms: int = 1600) -> None:
        """��ʾһ�ζ��ݻ������飬���ڼ�ʱ������ָ��������"""

        self.state_timer.stop()
        self.set_state(state)
        self.interaction_timer.start(max(500, duration_ms))

    def trigger_selfie(self) -> None:
        """��ʽ����һ�ξ������������Ͳ鿴��Ƭ���������С�"""

        if self.dragging:
            return
        self._record_user_interaction()
        self._show_emotion(PetState.SELFIE, 2600)

    def _interaction_zone(self, point: QPoint) -> str:
        """�����������λ�û���ͷ��������������������������"""

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
        """���ݵ���������������ѡ���Ӧ������"""

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
            self.show_speech("��ë����ͷ���㣺���ڽ�����", 3200)
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
            "��������������ë���Ҫ����һ��㡣" if repeated else "��ë�Ͻ���ס���ӣ����ﲻ���Ҵ���",
            3600,
        )

    def play_babuda_voice(self) -> None:
        """˫���Ҽ�ʱ�ֻ������û�������Ƶ��ȱ���ļ�����ϵͳ������΢�����"""

        if not self.settings.voice_enabled:
            self.show_speech("�Ͳ��", 2600)
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
            self._speech_engine.say("�Ͳ���")
        self._show_emotion(random.choice((PetState.HAPPY, PetState.SHY, PetState.SURPRISED)), 1500)
        self.show_speech(random.choice(("�Ͳ��", "�͡������", "�Ͳ����ë���ء�")), 2800)

    def _trigger_long_press(self) -> None:
        """������ë����ԭ��˯�����ͷ����ʱ���ٴ�����ͨ�����"""

        if not self._press_pending or self.dragging:
            return
        self._press_pending = False
        self._long_press_triggered = True
        self.daily_stats.record_sleep()
        self._set_temporary_activity("sleep", 60_000)
        self.show_speech("�����ɹ�����ë�����͵�˯һС�����", 4200)

    def _track_passive_motion(self, point: QPoint) -> None:
        """�����ް�����ͣ��ͣ���������棬ͷ�������ƶ��ж�Ϊ��ͷ��"""

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
            self.show_speech("�����յ�����ë�ĺ�ë�����ĵ�����������", 3400)

    def _trigger_hover_curiosity(self) -> None:
        """����ڳ��︽���ȶ�ͣ��ʱ��ʾ����ע�ӡ�"""

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
        """�ڳ�������ʾ�������ĳ�Ƭ������������Զ����ء�"""

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
        if sys.platform == "darwin":
            self._apply_macos_window_behavior(self.photo_bubble)
        self.photo_timer.start(3800)

    def _scaled_selfie_photo(self, ratio: float) -> QPixmap:
        """���豸���ر�������Ƭ����ͼ������� DPI ��Ļ���ηŴ���ģ����"""

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
        """�����������ָ�����������"""

        if not self.dragging:
            if self.state is PetState.SELFIE:
                self._show_photo_bubble()
            self._schedule(self.behavior.initial_idle())

    def _toggle_walk_from_menu(self) -> None:
        """���Ҽ��˵�ͬʱ�����״ο������ճ���ͣ�����ܶ�������"""

        if not getattr(self.settings, "allow_autonomous_walk", False):
            self.set_allow_autonomous_walk(True)
            return
        self.set_paused(not self.paused)

    def _build_context_menu(self) -> QMenu:
        """Build the small menu for the pet itself, not the full app menu."""

        menu = QMenu(self)

        activity_menu = menu.addMenu("������")
        for group_title, activities in ACTION_GROUPS:
            group_menu = activity_menu.addMenu(group_title)
            for label, activity in activities:
                action = group_menu.addAction(label)
                action.triggered.connect(
                    lambda _checked=False, value=activity: self.set_activity(value)
                )
        activity_menu.addSeparator()
        random_action = activity_menu.addAction("�������")
        random_action.triggered.connect(
            lambda _checked=False: self.set_activity(random.choice(RANDOM_ACTIONS))
        )

        companion_menu = menu.addMenu("��ë����")
        self._populate_pet_companion_menu(companion_menu)

        food_action = menu.addAction("ιʳ��")
        food_action.triggered.connect(lambda _checked=False: self.show_food_scene_dialog())

        outfit_menu = menu.addMenu("������")
        self._populate_outfit_menu(outfit_menu, default_label="Ĭ��װ")

        appearance_menu = menu.addMenu("��װ�����")
        size_action = appearance_menu.addAction("������С")
        size_action.triggered.connect(lambda _checked=False: self.open_size_control())
        topmost_action = appearance_menu.addAction("ʼ���ö����رռ�����ģʽ��")
        topmost_action.setCheckable(True)
        topmost_action.setChecked(bool(self.settings.always_on_top))
        topmost_action.triggered.connect(
            lambda checked=False: self.set_always_on_top(bool(checked))
        )

        menu.addSeparator()
        hide_action = menu.addAction("������ë")
        hide_action.triggered.connect(lambda _checked=False: self.hide())
        return menu

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """�Ժ���ʾ��ë����˵���Ϊ˫���Ҽ����������ж�ʱ�䡣"""

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
        """ȷ�ϲ���˫���󣬴���ë����˵���"""

        if time.monotonic() >= self._suppress_context_until:
            self.work_controls.hide()
            menu = self._build_context_menu()
            if bool(getattr(self.settings, "always_on_top", False)):
                # Keep the popup above the desktop-mode pet without changing
                # the ownership or flags of the real pet window.
                menu.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            menu.ensurePolished()
            point = self._pending_context_global
            screen = QGuiApplication.screenAt(point) or self.screen() or QGuiApplication.primaryScreen()
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
        """������������¼ Lili ���ڽ���仯�������������¼��"""

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
        """��¼������£�ֻ���ƶ�����ϵͳ��ֵ�������������ק��"""

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
        """�϶��ڼ����ȫ�����λ���ƶ������ƴ��ڡ�"""

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
        """����ͷ�ʱ�����϶����ָ�������"""

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
        """����뿪����ʱȡ����δ��������ͣ����ͷ�켣��"""

        self._hover_zone = ""
        self._stroke_points.clear()
        self.hover_timer.stop()
        self.long_press_timer.stop()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """˫������򿪿�ݿڴ���˫���Ҽ�����һ����ͬ�����İͲ��"""

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

