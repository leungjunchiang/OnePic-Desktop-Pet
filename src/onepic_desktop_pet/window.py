"""
æœ¬æ¨¡å—å®žçŽ°æ¡Œé¢å® ç‰©çš„é€æ˜Žçª—å£ã€è¿žç»­åŠ¨ç”»ã€é¼ æ ‡äº¤äº’ã€å¿«æ·æŽ§åˆ¶å’Œæƒ…å¢ƒé™ªä¼´ã€‚

èŒè´£èŒƒå›´ï¼š
- åˆ›å»ºæ— è¾¹æ¡†ã€é€æ˜Žã€å¯é€‰å§‹ç»ˆç½®é¡¶çš„ QWidgetï¼›
- ä½¿ç”¨ Windows/macOS åŽŸç”Ÿçª—å£å±‚çº§è¡¥å¼ºç½®é¡¶ï¼ŒåŒæ—¶ä¿æŒä¸æ¿€æ´»ã€ä¸å ä»»åŠ¡æ å’Œè½®å»“å¤–ç‚¹å‡»ç©¿é€ï¼›
- æä¾›â€œå§‹ç»ˆç½®é¡¶/æ¡Œé¢æ¨¡å¼â€å³æ—¶åˆ‡æ¢å¹¶æŒä¹…åŒ–ï¼Œåˆ‡æ¢æ—¶ä¸ç ´ååŠ¨ç”»ã€æ‹–åŠ¨å’Œäº’åŠ¨çŠ¶æ€ï¼›
- æ’­æ”¾å¾ªçŽ¯æˆ–å•æ¬¡ PNG åºåˆ—ï¼Œå¹¶æ”¯æŒæ‹–æ‹½ã€åä¸‹ã€åå§¿å…¥ç¡å’Œåå‘èµ·èº«ï¼›
- å¤„ç†å·¦å³ç¿»è½¬ã€è¾¹ç¼˜è½¬èº«åœé¡¿ã€äºšåƒç´ æ—¶é—´é©±åŠ¨ç§»åŠ¨å’ŒåŒæ­¥èº«ä½“èµ·ä¼ï¼›
- ç”¨çª—å£é®ç½©è®©äººç‰©å¤–é€æ˜ŽåŒºåŸŸç©¿é€é¼ æ ‡ç‚¹å‡»ï¼›
- ç¼“å­˜ä¸åŒ DPI ä¸‹çš„ç¼©æ”¾å¸§ï¼Œå¹¶åœ¨çª—å£è·¨æ˜¾ç¤ºå™¨åŽæŒ‰æ–°æ¯”ä¾‹é‡æ–°æ …æ ¼åŒ–ï¼›
- æ”¯æŒå·¦é”®æ‹–åŠ¨ã€å•å‡»è°ƒæˆã€åŒå‡»å¿«æ·å£è¢‹ã€æ— äº’åŠ¨åˆ†çº§ä¼‘æ¯å’Œè¿žç»­å°ºå¯¸æ»‘å—ï¼›
- æ”¯æŒç»™å…­æ¯›å–‚é£Ÿæˆ–é¥®å“ï¼Œå¹¶ç”¨ç‹¬ç«‹åŠé€æ˜Žæ–‡å­—æ°”æ³¡åé¦ˆçŠ¶æ€ï¼›
- æ”¯æŒ Agent çŠ¶æ€ç¼“å­˜ã€å¼‚æ­¥ AIã€æ— ç¼ç¦»çº¿é™çº§ä»¥åŠå·¥ä½œã€çˆ±æ„ã€é¼“åŠ±å’Œå®‰æ…°åŠ¨ä½œï¼›
- åœ¨å†…å­˜ä¸­ä¿ç•™æœ€è¿‘ä¸‰åè½®å®Œæ•´èŠå¤©ï¼Œå¹¶æŠŠæ›´æ—©å†…å®¹æ»šåŠ¨åŽ‹ç¼©ä¸ºé•¿æœŸæ‘˜è¦ï¼›
- å°†è¿žæŽ¥ä¸Žé™ªä¼´è®¾ç½®æ”¶å£åˆ°å”¯ä¸€å…¥å£ï¼Œåªæœ‰æ˜¾å¼ ``user_action`` æ¥æºæ‰å…è®¸åˆ›å»ºè®¾ç½®çª—å£ï¼›
- è‡ªåŠ¨è¯„åˆ†å¹¶ä¾æ¬¡å°è¯•æœ¬æœºéŸ³ä¹ Providerï¼ŒæˆåŠŸåŽæŠŠåŸºç¡€æŽ§åˆ¶é”å®šåˆ°å®žé™…æ’­æ”¾çš„å¹³å°ï¼›
- æ”¯æŒç”µè„‘å›¾å±‚ã€æ‘¸å¤´å·¥ä½œæ°”æ³¡ã€ä»Šæ—¥/ç»ˆèº«è®¡æ—¶ã€æ¯å°æ—¶å¨ƒè¡£è§£é”åŠå¥åº·æé†’ï¼›
- æ ¹æ®å‰å°åº”ç”¨ç²—ç²’åº¦ç±»åˆ«æ˜¾ç¤ºç”µè„‘ã€è€³æœºã€å‰ä»–ã€é¼“ã€é˜…è¯»æˆ–å†™å­—å›¾å±‚ï¼›
- æ”¯æŒå¤´éƒ¨æ‘¸åŠ¨ã€è„¸éƒ¨/èº«ä½“/ç›¸æœºåˆ†åŒºç‚¹å‡»ã€è¿žç»­æˆ³å‡»ã€æ‚¬åœæ³¨è§†å’Œæ‹–æ‹½åŽè¡¨æƒ…ï¼›
- é€šè¿‡ä¸Žè§’è‰²ç´ æè§£è€¦çš„çŸ¢é‡å›¾å±‚å¢žå¼ºå¼€å¿ƒã€å®³ç¾žã€æƒŠè®¶ã€ç”Ÿæ°”ã€å›°å€¦ã€ç–‘æƒ‘ã€è‡ªæ‹å’Œæ‹–æ‹½åé¦ˆï¼›
- ä¼˜å…ˆä»Žç”¨æˆ·ç§æœ‰ç´ æç›®å½•æ˜¾ç¤ºè‡ªæ‹æˆç‰‡æ°”æ³¡ï¼ŒæŒ‰å½“å‰å±å¹• DPI ä¿æŒæ¸…æ™°åº¦ï¼Œå¹¶è´´è¿‘äººç‰©çœŸå®žè½®å»“å®šä½ï¼›
- æ ‡å‡†è§’è‰²ç¡®è®¤åŽåŠ è½½æœ¬åœ°å® ç‰©ä¾›çŽ°åœºéªŒæ”¶ï¼›èµ°è·¯ç¡®è®¤ä»ä½œä¸ºæ‰“åŒ…é—¨ç¦ï¼›
- ç»´æŠ¤äº²å¯†åº¦ã€ç²¾åŠ›ã€æ— èŠåº¦ä¸Žé¥±é£Ÿåº¦çš„ä¼šè¯å†…çŠ¶æ€ï¼›
- ä½¿ç”¨ QTimer é©±åŠ¨çŠ¶æ€åˆ‡æ¢åŠæ°´å¹³ç§»åŠ¨ï¼Œå¹¶é™åˆ¶çª—å£ä¸è„±ç¦»å½“å‰å±å¹•ã€‚

Agent å¿«é€Ÿå®šä½ï¼š
- çª—å£åˆå§‹åŒ–å’Œè®¡æ—¶å™¨è®¾ç½®ä½äºŽ PetWindow.__init__()ï¼›
- çŠ¶æ€æ˜¾ç¤ºå…¥å£ä½äºŽ set_state()ï¼Œé«˜ DPI é‡ç»˜ä½äºŽ _refresh_pixmap()ï¼›
- è‡ªåŠ¨ç§»åŠ¨ä½äºŽ _movement_tick()ï¼›
- é¼ æ ‡äº‹ä»¶ä½äºŽ mousePressEvent() ç­‰ Qt äº‹ä»¶æ–¹æ³•ï¼›
- é€€å‡ºç”± quit_requested ä¿¡å·äº¤ç»™åº”ç”¨ç”Ÿå‘½å‘¨æœŸæ¨¡å—å¤„ç†ã€‚

è¾“å…¥ä¸º PetSettingsã€ç´ ææ¸…å•å’Œå¯é€‰çš„ç”¨æˆ·è‡ªæ‹ç…§ç‰‡èµ„æºï¼Œè¾“å‡ºä¸ºå¯äº¤äº’çš„ Qt çª—å£ã€‚
æœ¬æ¨¡å—å¯åŠ¨åŽåªåœ¨åŽå°ä½Žé¢‘æ£€æµ‹ Agentï¼›æ¯æ¡èŠå¤©ä¸é‡å¤å®Œæ•´æ£€æµ‹ï¼Œæ™®é€šåŠ¨ç”»ã€ç‰¢éªšå’ŒæŠ¥æ—¶å‡ä¸è®¿é—®ç½‘ç»œã€‚
API ä»¤ç‰Œç”±ç³»ç»Ÿå‡­æ®åº“ç®¡ç†ï¼ŒèŠå¤©æ–‡æœ¬ä¸è½ç›˜ï¼›ä½ç½®æŒä¹…åŒ–ç”± app.py åœ¨é€€å‡ºæ—¶å®Œæˆã€‚
`user_assets/` é»˜è®¤ä¸è¿›å…¥ Gitï¼›åªæœ‰ç”¨æˆ·ä¸»åŠ¨æ”¾å…¥çš„è‡ªæ‹å›¾ç‰‡æ‰ä¼šåœ¨æœ¬æœºæ˜¾ç¤ºã€‚
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
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

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
from .chat_memory import ConversationMemory
from .companion import (
    ACTION_BY_KEY,
    APP_DISPLAY_NAME,
    COMPANION_ACTIONS,
    FOOD_OPTIONS,
    CompanionModel,
    CompanionReply,
)
from .config import PetSettings, save_settings
from .controls import QuickControlPanel, SizeControlDialog, WorkControlBubble
from .emotion_effects import draw_emotion_effect, emotion_effect_name
from .daily_report import render_daily_report
from .diary import DailyCompanionStats, album_directory
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
from .resources import resource_path
from .social import SocialClient
from .social_ui import BuddyVisitWindow, SocialHubDialog, SocialSyncThread
from .music_control import MusicControlResult, MusicController, MusicProviderManager
from .music_playback import SongPlaybackResult
from .wellness import WellnessReminderModel
from .work_timer import WorkTimerModel, format_work_duration
from .workflow import WorkflowError, character_is_approved, load_workflow


LOGGER = logging.getLogger(__name__)
SETTINGS_SOURCE_USER_ACTION = "user_action"


DEFAULT_WALK_MOTION_FACTORS = (0.45, 0.7, 1.2, 1.65, 0.45, 0.7, 1.2, 1.65)


class PetWindow(QWidget):
    """æ˜¾ç¤ºå¹¶æŽ§åˆ¶å•ä¸ªæ¡Œé¢å® ç‰©çš„é€æ˜Žé¡¶å±‚çª—å£ã€‚"""

    quit_requested = Signal()
    pause_changed = Signal(bool)
    work_timer_changed = Signal(bool)
    always_on_top_changed = Signal(bool)

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
        self._rewarded_focus_blocks = self.work_timer.today_seconds() // 600
        self.daily_stats = DailyCompanionStats(
            persist=os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1"
        )
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
        self._sleep_after_sit = False
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
        self._buddy_visit_window = BuddyVisitWindow()
        self._seen_visit_ids: set[str] = set()
        self._shown_active_visit_ids: set[str] = set()
        self._chat_dialog: ChatDialog | None = None
        self._chat_memory = ConversationMemory(max_recent_rounds=30)
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
        self.setWindowTitle(APP_DISPLAY_NAME)
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
        self.quick_panel = QuickControlPanel()
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

        self.speech_timer = QTimer(sÛ¾ºæÚ$z{-®éÜj×W6R¢fööEöÖVçRÒÖVçRæFDÖVçR‚.{¹žXZÞjù¾Yh.š9ò"¢f÷"fööB–âdôôEôõD”ôå3 ¢7F–öâÒ7F–öâ†fööBæÆ&VÂÂ6VÆb¢7F–öâçG&–vvW&VBæ6öææV7B†ÆÖ&Fö6†V6¶VCÔfÇ6RÂ¶W“ÖfööBæ¶W“¢6VÆbæfVVE÷WB†¶W’’¢fööEöÖVçRæFD7F–öâ†7F–öâ¢fööEöÖVçRæFE6W&F÷"‚¢ÖööBÒ7F–öâ‚.iú^yÈ¾[ø>h8^Kˆîˆ;Þ˜xò"Â6VÆb¢ÖööBçG&–vvW&VBæ6öææV7B‡6VÆbç6†÷uö6ö×æ–öå÷7FGW2¢fööEöÖVçRæFD7F–öâ†ÖööB¢÷WFf—EöÖVçRÒÖVçRæFDÖVçR‚.hÚ.Š8^KˆîZInŠx""¢6Æ76–2Ò7F–öâ‚.{¸þX[ŽXZÞjù²"Â6VÆb¢6Æ76–2ç6WD6†V6¶&ÆR…G'VR¢6Æ76–2ç6WD6†V6¶VB†æ÷B6VÆbç6WGF–æw2æWV—VEö÷WFf—B¢6Æ76–2çG&–vvW&VBæ6öææV7B†ÆÖ&F¢6VÆbæWV—ö÷WFf—B‚""’¢÷WFf—EöÖVçRæFD7F–öâ†6Æ76–2¢VæÆö6¶VBÒVæÆö6¶VEö÷WFf—G2‡6VÆbçv÷&µ÷F–ÖW"çVæÆö6¶VEö÷WFf—Eö6÷VçB‚’¢f÷"÷WFf—B–âõUDd•E3 ¢7F–öâÒ7F–öâ†÷WFf—BææÖRÂ6VÆb¢f–Æ&ÆRÒ÷WFf—B–âVæÆö6¶V@¢7F–öâç6WDVæ&ÆVB†f–Æ&ÆR¢7F–öâç6WD6†V6¶&ÆR…G'VR¢7F–öâç6WD6†V6¶VB†÷WFf—Bæ¶W’ÓÒ6VÆbç6WGF–æw2æWV—VEö÷WFf—B¢–bf–Æ&ÆS ¢7F–öâçG&–vvW&VBæ6öææV7B†ÆÖ&Fö6†V6¶VCÔfÇ6RÂ¶W“Ö÷WFf—Bæ¶W“¢6VÆbæWV—ö÷WFf—B†¶W’’¢÷WFf—EöÖVçRæFD7F–öâ†7F–öâ¢ÖVçRæFE6W&F÷"‚¢f÷"Æ&VÂÂ6ÆÆ&6²Â6†V6¶VB–â‚‚.Xn[	NXùxš.š©¢"Â6VÆbç6WEöWFöÖF–5öw'VÖ&Æ–ærÂ6VÆbç6WGF–æw2æWFöÖF–5öw'VÖ&Æ–ær’Â‚.i[Nx+žhª^i{b"Â6VÆbç6WEö†÷W&Ç•öææ÷Væ6VÖVçBÂ6VÆbç6WGF–æw2æ†÷W&Ç•öææ÷Væ6VÖVçB’Â‚.Zx¾{¸Ž{Úîšb"Â6VÆbç6WEöÇv—5ööå÷F÷Â6VÆbç6WGF–æw2æÇv—5ööå÷F÷’“ ¢7F–öâÒ7F–öâ†Æ&VÂÂ6VÆb¢7F–öâç6WD6†V6¶&ÆR…G'VR¢7F–öâç6WD6†V6¶VB†6†V6¶VB¢7F–öâçFövvÆVBæ6öææV7B†6ÆÆ&6²¢ÖVçRæFD7F–öâ†7F–öâ¢7—7FVÕöÖVçRÒÖVçRæFDÖVçR‚.{;¾{¹þKˆîŠëî{Úâ"¢•ö7F–öâÒ7F–öâ‚$’Kˆî™š®KËNŠëî{Úâ"Â6VÆb¢•ö7F–öâçG&–vvW&VBæ6öææV7B†ÆÖ&F¢6VÆbæ÷Vå÷6WGF–æw2…4UED”äu5õ4õU$4UõU4U%ô5D”ôâ’¢7—7FVÕöÖVçRæFD7F–öâ†•ö7F–öâ¢6—¦Uö7F–öâÒ7F–öâ‚.‹>i[NjÎZêZJ~[ò"Â6VÆb¢6—¦Uö7F–öâçG&–vvW&VBæ6öææV7B‡6VÆbæ÷Vå÷6—¦Uö6öçG&öÂ¢7—7FVÕöÖVçRæFD7F–öâ‡6—¦Uö7F–öâ¢&WGW&åö7F–öâÒ7F–öâ‚.Y¹îX‹K‹¾[þ[™R"Â6VÆb¢&WGW&åö7F–öâçG&–vvW&VBæ6öææV7B‡6VÆbç&WGW&å÷Fõ÷&–Ö'•÷67&VVâ¢7—7FVÕöÖVçRæFD7F–öâ‡&WGW&åö7F–öâ¢†–FUö7F–öâÒ7F–öâ‚.™©‰xò"Â6VÆb¢†–FUö7F–öâçG&–vvW&VBæ6öææV7B‡6VÆbæ†–FR¢ÖVçRæFD7F–öâ††–FUö7F–öâ¢V—Eö7F–öâÒ7F–öâ‚.˜X{¢"Â6VÆb¢V—Eö7F–öâçG&–vvW&VBæ6öææV7B‡6VÆbçV—E÷&WVW7FVBæVÖ—B¢ÖVçRæFD7F–öâ‡V—Eö7F–öâ¢&WGW&âÖVçP ¢FVbö'V–ÆEö6öçFW‡EöÖVçUöÆVv7’‡6VÆb’ÓâÖVçS ¢"".hÈžK©NKŠ®z‹>Zé®Xˆn{¸NièN[»®Xû>™JîˆùÎXÙ^ûÈÎ˜þXXÞX©þˆ;Þ[›>™;®Y(ÎXZ^Xú>[.{ª~k{~K›8"""  ¢ÖVçRÒÖVçR‡6VÆb¢6†Eöw&÷WÒÖVçRæFDÖVçR‚.ˆ®ZJžKˆî™š®KËB"¢7F–öåöw&÷WÒÖVçRæFDÖVçR‚.XªŽKÙÎKˆîZInŠx""¢×W6–5öw&÷WÒÖVçRæFDÖVçR‚.™û>K™KˆîZ‹K™"¢fö7W5öw&÷WÒÖVçRæFDÖVçR‚.K‰>k:ŽKˆîˆz®Kš"¢7—7FVÕöw&÷WÒÖVçRæFDÖVçR‚.{;¾{¹þKˆîi‹îzK¢" ¢F–ÆöwVUö7F–öâÒ7F–öâ‚.Y(ÎXZÞjù¾ˆ®ˆ®(
b"Â6VÆb¢F–ÆöwVUö7F–öâçG&–vvW&VBæ6öææV7B‡6VÆbç&ö×EöF–ÆöwVR¢6†Eöw&÷WæFD7F–öâ†F–ÆöwVUö7F–öâ¢6ö6–Åö7F–öâÒ7F–öâ‚.XZÞjù¾i
ÞZÙˆz®KšZêN(
b"Â6VÆb¢6ö6–Åö7F–öâçG&–vvW&VBæ6öææV7B‡6VÆbæ÷Vå÷6ö6–Åö‡V"¢6†Eöw&÷WæFD7F–öâ‡6ö6–Åö7F–öâ¢7F–öåöÖVçRÒ6†Eöw&÷WæFDÖVçR‚.™š®KËNXªŽKÙÂ"¢f÷"÷F–öâ–â4ôÕä”ôåô5D”ôå3 ¢7F–öâÒ7F–öâ†÷F–öâæÆ&VÂÂ6VÆb¢7F–öâçG&–vvW&VBæ6öææV7B€¢ÆÖ&Fö6†V6¶VCÔfÇ6RÂ¶W“Ö÷F–öâæ¶W“¢6VÆbçW&f÷&Õö6ö×æ–öåö7F–öâ€¢¶W¢¢¢7F–öåöÖVçRæFD7F–öâ†7F–öâ¢fööEöÖVçRÒ6†Eöw&÷WæFDÖVçR‚.Yh.š9þ8šZîY8Kˆîx«nh"¢f÷"fööB–âdôôEôõD”ôå3 ¢fööEö7F–öâÒ7F–öâ†fööBæÆ&VÂÂ6VÆb¢fööEö7F–öâçG&–vvW&VBæ6öææV7B€¢ÆÖ&Fö6†V6¶VCÔfÇ6RÂ¶W“ÖfööBæ¶W“¢6VÆbæfVVE÷WB†¶W’¢¢fööEöÖVçRæFD7F–öâ†fööEö7F–öâ¢fööEöÖVçRæFE6W&F÷"‚¢ÖööEö7F–öâÒ7F–öâ‚.iú^yÈ¾XZÞjù¾[ø>h8^Kˆîˆ;Þ˜xò"Â6VÆb¢ÖööEö7F–öâçG&–vvW&VBæ6öææV7B‡6VÆbç6†÷uö6ö×æ–öå÷7FGW2¢fööEöÖVçRæFD7F–öâ†ÖööEö7F–öâ ¢W6Uö7F–öâÒ7F–öâ‚.h.ZHÞ‹yXª‚"–b6VÆbçW6VBVÇ6R.i¨.XÎ‹yXª‚"Â6VÆb¢W6Uö7F–öâçG&–vvW&VBæ6öææV7B†ÆÖ&F¢6VÆbç6WE÷W6VB†æ÷B6VÆbçW6VB’¢7F–öåöw&÷WæFD7F–öâ‡W6Uö7F–öâ¢–7GW&Uö7F–öç2Ò7F–öåöw&÷WæFDÖVçR‚.ZèÎi[NY»îx˜~XªŽKÙÂ"¢f÷"w&÷WöæÖRÂVçG&–W2–â5D”ôåôu$õU3 ¢w&÷WöÖVçRÒ–7GW&Uö7F–öç2æFDÖVçR†w&÷WöæÖR¢f÷"Æ&VÂÂ¶W’–âVçG&–W3 ¢7F–öâÒ7F–öâ†Æ&VÂÂ6VÆb¢7F–öâçG&–vvW&VBæ6öææV7B†ÆÖ&Fö6†V6¶VCÔfÇ6RÂfÇVSÖ¶W“¢6VÆbç6WEö7F—f—G’‡fÇVR’¢w&÷WöÖVçRæFD7F–öâ†7F–öâ¢6VÆf–Uö7F–öâÒ7F–öâ‚.ˆz®h¸ÞKˆKˆ²"Â6VÆb¢6VÆf–Uö7F–öâçG&–vvW&VBæ6öææV7B‡6VÆbçG&–vvW%÷6VÆf–R¢7F–öåöw&÷WæFD7F–öâ‡6VÆf–Uö7F–öâ¢÷WFf—EöÖVçRÒ7F–öåöw&÷WæFDÖVçR‚.[z^KÙÎi{n™[þZˆ>Š2"¢6Æ76–2Ò7F–öâ‚.{¸þX[ŽXZÞjù²"Â6VÆb¢6Æ76–2ç6WD6†V6¶&ÆR…G'VR¢6Æ76–2ç6WD6†V6¶VB†æ÷B6VÆbç6WGF–æw2æWV—VEö÷WFf—B¢6Æ76–2çG&–vvW&VBæ6öææV7B†ÆÖ&F¢6VÆbæWV—ö÷WFf—B‚""’¢÷WFf—EöÖVçRæFD7F–öâ†6Æ76–2¢VæÆö6¶VBÒVæÆö6¶VEö÷WFf—G2‡6VÆbçv÷&µ÷F–ÖW"çVæÆö6¶VEö÷WFf—Eö6÷VçB‚’¢f÷"†÷W"Â÷WFf—B–âVçVÖW&FR„õUDd•E2Â7F'CÓ“ ¢f–Æ&ÆRÒ÷WFf—B–âVæÆö6¶V@¢Æ&VÂÒ€¢b'¶†÷W'Ò[þi{b+r¶÷WFf—BææÖWÒ ¢–bf–Æ&ÆP¢VÇ6Rb/	ùI"¶†÷W'Ò[þi{b+r¶÷WFf—BææÖWÒ ¢¢7F–öâÒ7F–öâ†Æ&VÂÂ6VÆb¢7F–öâç6WD6†V6¶&ÆR†f–Æ&ÆR¢7F–öâç6WD6†V6¶VB†÷WFf—Bæ¶W’ÓÒ6VÆbç6WGF–æw2æWV—VEö÷WFf—B¢7F–öâç6WDVæ&ÆVB†f–Æ&ÆR¢–bf–Æ&ÆS ¢7F–öâçG&–vvW&VBæ6öææV7B€¢ÆÖ&Fö6†V6¶VCÔfÇ6RÂ¶W“Ö÷WFf—Bæ¶W“¢6VÆbæWV—ö÷WFf—B†¶W’¢¢÷WFf—EöÖVçRæFD7F–öâ†7F–öâ ¢×W6–5ö6öçG&öÅöÖVçRÒ×W6–5öw&÷WæFDÖVçR‚.hê~X‹njÚ>YÊŽ‹ùŠÎy¨Ni*ÞiKîYš‚"¢f÷"Æ&VÂÂ6öÖÖæB–â€¢‚.i*ÞiKâòi¨.XÂ"Â'FövvÆR"’À¢‚.Kˆ®Kˆšib"Â'&Wf–÷W2"’À¢‚.Kˆ¾Kˆšib"Â&æW‡B"’À¢‚.iú^yÈ¾jÚ>YÊŽi*ÞiKâ"Â'7FGW2"’À¢“ ¢6öçG&öÅö7F–öâÒ7F–öâ†Æ&VÂÂ6VÆb¢6öçG&öÅö7F–öâçG&–vvW&VBæ6öææV7B€¢ÆÖ&Fö6†V6¶VCÔfÇ6RÂfÇVSÖ6öÖÖæC¢6VÆbæ6öçG&öÅö×W6–2‡fÇVR¢¢×W6–5ö6öçG&öÅöÖVçRæFD7F–öâ†6öçG&öÅö7F–öâ¢×W6–5÷6V&6…ö7F–öâÒ7F–öâ‚.i	Î{J.Kˆšin™˜ŽjY®yIò"Â6VÆb¢×W6–5÷6V&6…ö7F–öâçG&–vvW&VBæ6öææV7B‡6VÆbçÆ•÷&æFöÕ÷6öær¢×W6–5öw&÷WæFD7F–öâ†×W6–5÷6V&6…ö7F–öâ¢×W6–5öÖ÷fRÒ×W6–5öw&÷WæFDÖVçR‚.™û>K™XªŽKÙÂ"¢f÷"Æ&VÂÂ¶W’–â‚‚.h‹Nˆ>iË¢"Â&†VG†öæW2"’Â‚.[ËžYžK¹b"Â&wV—F""’Â‚.h™>›É2"Â&G'V×2"’“ ¢7F–öâÒ7F–öâ†Æ&VÂÂ6VÆb¢7F–öâçG&–vvW&VBæ6öææV7B†ÆÖ&Fö6†V6¶VCÔfÇ6RÂfÇVSÖ¶W“¢6VÆbç6WEö7F—f—G’‡fÇVR’¢×W6–5öÖ÷fRæFD7F–öâ†7F–öâ ¢v÷&µöÖVçRÒfö7W5öw&÷WæFDÖVçR€¢b.[z^KÙÎŠêi{nûÉ§¶f÷&ÖE÷v÷&µöGW&F–öâ‡6VÆbçv÷&µ÷F–ÖW"çFöF•÷6V6öæG2‚’—Ò ¢¢7F'E÷v÷&µö7F–öâÒ7F–öâ‚.[ÈZx²þ{º~{ºÞ[z^KÙÂ"Â6VÆb¢7F'E÷v÷&µö7F–öâç6WDVæ&ÆVB†æ÷B6VÆbçv÷&µ÷F–ÖW"æ—5÷'Vææ–ær¢7F'E÷v÷&µö7F–öâçG&–vvW&VBæ6öææV7B‡6VÆbç7F'E÷v÷&µ÷F–ÖW"¢v÷&µöÖVçRæFD7F–öâ‡7F'E÷v÷&µö7F–öâ¢–b6VÆbçv÷&µ÷F–ÖW"æ—5÷'Vææ–æs ¢6öçG&öÇ5ö7F–öâÒ7F–öâ‚.iŽZKNh‰nx+žyK^ˆI‹ù¾ŠÎi¨.XÂþ{¹>iÙò"Â6VÆb¢6öçG&öÇ5ö7F–öâçG&–vvW&VBæ6öææV7B‡6VÆbç6†÷u÷v÷&µö6öçG&öÇ2¢v÷&µöÖVçRæFD7F–öâ†6öçG&öÇ5ö7F–öâ¢6†÷u÷v÷&µö7F–öâÒ7F–öâ‚.iú^yÈ¾K¸®iz^{JþŠê"Â6VÆb¢6†÷u÷v÷&µö7F–öâçG&–vvW&VBæ6öææV7B‡6VÆbç6†÷u÷v÷&µ÷F–ÖR¢v÷&µöÖVçRæFD7F–öâ‡6†÷u÷v÷&µö7F–öâ¢w&÷wF…ö7F–öâÒ7F–öâ‚.iú^yÈ¾K¸®izR(	3‚[þi{nh‰™[þ{«ò"Â6VÆb¢w&÷wF…ö7F–öâçG&–vvW&VBæ6öææV7B‡6VÆbç6†÷uöF–Ç•öw&÷wF‚¢v÷&µöÖVçRæFD7F–öâ†w&÷wF…ö7F–öâ¢&W÷'Eö7F–öâÒ7F–öâ‚.K¸®ZJžXZÞjù¾™š®KÚX®K¨nK¸K˜‚"Â6VÆb¢&W÷'Eö7F–öâçG&–vvW&VBæ6öææV7B‡6VÆbç6†÷uöF–Ç•÷&W÷'B¢v÷&µöÖVçRæFD7F–öâ‡&W÷'Eö7F–öâ¢Æ'VÕö7F–öâÒ7F–öâ‚.h™>[ÈXZÞjù¾y»ŽXhÂ"Â6VÆb¢Æ'VÕö7F–öâçG&–vvW&VBæ6öææV7B‡6VÆbæ÷VåöF–Ç•öÆ'VÒ¢v÷&µöÖVçRæFD7F–öâ†Æ'VÕö7F–öâ¢fö7W5÷6ö6–ÂÒ7F–öâ‚.h™>[Èi
ÞZÙˆz®KšZêN(
b"Â6VÆb¢fö7W5÷6ö6–ÂçG&–vvW&VBæ6öææV7B‡6VÆbæ÷Vå÷6ö6–Åö‡V"¢fö7W5öw&÷WæFD7F–öâ†fö7W5÷6ö6–Â ¢•ö7F–öâÒ7F–öâ‚$’Kˆî™š®KËNŠëî{Úî(
b"Â6VÆb¢•ö7F–öâçG&–vvW&VBæ6öææV7B€¢ÆÖ&Fö6†V6¶VCÔfÇ6S¢6VÆbæ÷Vå÷6WGF–æw2…4UED”äu5õ4õU$4UõU4U%ô5D”ôâ¢¢7—7FVÕöw&÷WæFD7F–öâ†•ö7F–öâ¢w'VÖ&ÆUö7F–öâÒ7F–öâ‚.Xn[	NXùxš.š©¢"Â6VÆb¢w'VÖ&ÆUö7F–öâç6WD6†V6¶&ÆR…G'VR¢w'VÖ&ÆUö7F–öâç6WD6†V6¶VB‡6VÆbç6WGF–æw2æWFöÖF–5öw'VÖ&Æ–ær¢w'VÖ&ÆUö7F–öâçFövvÆVBæ6öææV7B‡6VÆbç6WEöWFöÖF–5öw'VÖ&Æ–ær¢7—7FVÕöw&÷WæFD7F–öâ†w'VÖ&ÆUö7F–öâ¢†÷W&Ç•ö7F–öâÒ7F–öâ‚.i[Nx+žhª^i{b"Â6VÆb¢†÷W&Ç•ö7F–öâç6WD6†V6¶&ÆR…G'VR¢†÷W&Ç•ö7F–öâç6WD6†V6¶VB‡6VÆbç6WGF–æw2æ†÷W&Ç•öææ÷Væ6VÖVçB¢†÷W&Ç•ö7F–öâçFövvÆVBæ6öææV7B‡6VÆbç6WEö†÷W&Ç•öææ÷Væ6VÖVçB¢7—7FVÕöw&÷WæFD7F–öâ††÷W&Ç•ö7F–öâ¢F÷Ö÷7Eö7F–öâÒ7F–öâ‚.Zx¾{¸Ž{ÚîšnûÈŽX[>™zÞXÛ>jÎ™Ú.jŠ[ÈþûÈ’"Â6VÆb¢F÷Ö÷7Eö7F–öâç6WD6†V6¶&ÆR…G'VR¢F÷Ö÷7Eö7F–öâç6WD6†V6¶VB‡6VÆbç6WGF–æw2æÇv—5ööå÷F÷¢F÷Ö÷7Eö7F–öâçFövvÆVBæ6öææV7B‡6VÆbç6WEöÇv—5ööå÷F÷¢7—7FVÕöw&÷WæFD7F–öâ‡F÷Ö÷7Eö7F–öâ¢6—¦Uö7F–öâÒ7F–öâ‚.‹ùî{ºÞ‹>ˆ¨.ZêxšžZJ~[þ(
b"Â6VÆb¢6—¦Uö7F–öâçG&–vvW&VBæ6öææV7B‡6VÆbæ÷Vå÷6—¦Uö6öçG&öÂ¢7—7FVÕöw&÷WæFD7F–öâ‡6—¦Uö7F–öâ¢7—7FVÕöw&÷WæFE6W&F÷"‚¢&WGW&åö7F–öâÒ7F–öâ‚.Y¹îX‹K‹¾[þ[™R"Â6VÆb¢&WGW&åö7F–öâçG&–vvW&VBæ6öææV7B‡6VÆbç&WGW&å÷Fõ÷&–Ö'•÷67&VVâ¢7—7FVÕöw&÷WæFD7F–öâ‡&WGW&åö7F–öâ¢†–FUö7F–öâÒ7F–öâ‚.™©‰xò"Â6VÆb¢†–FUö7F–öâçG&–vvW&VBæ6öææV7B‡6VÆbæ†–FR¢7—7FVÕöw&÷WæFD7F–öâ††–FUö7F–öâ¢7—7FVÕöw&÷WæFE6W&F÷"‚¢V—Eö7F–öâÒ7F–öâ‚.˜X{¢"Â6VÆb¢V—Eö7F–öâçG&–vvW&VBæ6öææV7B‡6VÆbçV—E÷&WVW7FVBæVÖ—B¢7—7FVÕöw&÷WæFD7F–öâ‡V—Eö7F–öâ¢&WGW&âÖVçP ¢FVb6öçFW‡DÖVçTWfVçB‡6VÆbÂWfVçC¢6öçFW‡DÖVçTWfVçB’ÓâæöæS ¢"".zˆÞX	ži‹îzK®XÙ^jÊXû>™JîˆùÎXÙ^ûÈÎK‹®XøÎX{¾Xû>™Jîy¨N[{N[ˆ>‹ëîŠúÞ™û>yYžX{®XŠNZé®i{n™{N8"""  ¢6VÆbå÷&V6÷&E÷W6W%ö–çFW&7F–öâ‚¢–bF–ÖRæÖöæ÷Föæ–2‚’Â6VÆbå÷7W&W75ö6öçFW‡E÷VçF–Ã ¢WfVçBæ66WB‚¢&WGW&à¢6VÆbå÷VæF–æuö6öçFW‡EövÆö&ÂÒWfVçBævÆö&Å÷2‚¢6VÆbæ6öçFW‡EöÖVçU÷F–ÖW"ç7F'B…Æ–6F–öâæF÷V&ÆT6Æ–6´–çFW'fÂ‚’²c¢WfVçBæ66WB‚ ¢FVb÷6†÷uöFVfW'&VEö6öçFW‡EöÖVçR‡6VÆb’ÓâæöæS ¢"".zîŠêNKˆÞiŠþXøÎX{¾YîûÈÎYÊŽXéþ›Êj~KØÞ{Úîh™>[Èišî˜	®Xû>™JîˆùÎXÙ^8"""  ¢–bF–ÖRæÖöæ÷Föæ–2‚’ãÒ6VÆbå÷7W&W75ö6öçFW‡E÷VçF–Ã ¢6VÆbåö'V–ÆEö6öçFW‡EöÖVçR‚’æW†V2‡6VÆbå÷VæF–æuö6öçFW‡EövÆö&Â ¢FVbÖ÷W6U&W74WfVçB‡6VÆbÂWfVçC¢Ö÷W6TWfVçB’ÓâæöæS ¢"".Šë[Ù^[zn™JîhÈžKˆ¾ûÉ¾Xú®iÈžz{¾XªŽ‹h^‹ø~{;¾{¹þ™ˆŽXÎYîh˜ÞyÉþjÚ>‹ù¾XZ^h¹nh»Þ8"""  ¢–bWfVçBæ'WGFöâ‚’ÓÒBäÖ÷W6T'WGFöâäÆVgD'WGFöã ¢6VÆbå÷&V6÷&E÷W6W%ö–çFW&7F–öâ‚¢6VÆbå÷&W75÷VæF–ærÒG'VP¢6VÆbåöÆöæu÷&W75÷G&–vvW&VBÒfÇ6P¢6VÆbæÆöæu÷&W75÷F–ÖW"ç7F'BƒƒS¢6VÆbæG&vv–ærÒfÇ6P¢6VÆbç7FFU÷F–ÖW"ç7F÷‚¢6VÆbæ–çFW&7F–öå÷F–ÖW"ç7F÷‚¢6VÆbæ†÷fW%÷F–ÖW"ç7F÷‚¢6VÆbå÷&W75öÆö6ÂÒWfVçBç÷6—F–öâ‚’çFõö–çB‚¢6VÆbå÷&W75övÆö&ÂÒWfVçBævÆö&Å÷6—F–öâ‚’çFõö–çB‚¢6VÆbåöG&uööfg6WBÒWfVçBævÆö&Å÷6—F–öâ‚’çFõö–çB‚’Ò6VÆbæg&ÖTvVöÖWG'’‚’çF÷ÆVgB‚¢WfVçBæ66WB‚¢&WGW&à¢7WW"‚’æÖ÷W6U&W74WfVçB†WfVçB ¢FVbÖ÷W6TÖ÷fTWfVçB‡6VÆbÂWfVçC¢Ö÷W6TWfVçB’ÓâæöæS ¢"".h¹nXªŽiÉþ™{NjžhÚîXZŽ[›Êj~KØÞ{Úîz{¾XªŽ[›n™™X‹nz©~Xú>8"""  ¢–bWfVçBæ'WGFöç2‚’bBäÖ÷W6T'WGFöâäÆVgD'WGFöã ¢7W'&VçEövÆö&ÂÒWfVçBævÆö&Å÷6—F–öâ‚’çFõö–çB‚¢–b€¢6VÆbå÷&W75÷VæF–æp¢æB†7W'&VçEövÆö&ÂÒ6VÆbå÷&W75övÆö&Â’æÖæ†GFäÆVæwF‚‚¢ãÒÆ–6F–öâç7F'DG&tF—7Fæ6R‚¢“ ¢6VÆbæÆöæu÷&W75÷F–ÖW"ç7F÷‚¢6VÆbå÷&W75÷VæF–ærÒfÇ6P¢6VÆbæG&vv–ærÒG'VP¢6VÆbæÖööBç&V6V—fUöG&r‚¢6VÆbç6WE÷7FFR…WE7FFRäE$r¢–bæ÷B6VÆbæG&vv–æs ¢WfVçBæ66WB‚¢&WGW&à¢F&vWBÒWfVçBævÆö&Å÷6—F–öâ‚’çFõö–çB‚’Ò6VÆbåöG&uööfg6W@¢6VÆbæÖ÷fR‡6VÆbåö6öç7G&–æVE÷÷6—F–öâ‡F&vWB’¢WfVçBæ66WB‚¢&WGW&à¢6VÆbå÷G&6µ÷76—fUöÖ÷F–öâ†WfVçBç÷6—F–öâ‚’çFõö–çB‚’¢7WW"‚’æÖ÷W6TÖ÷fTWfVçB†WfVçB ¢FVbÖ÷W6U&VÆV6TWfVçB‡6VÆbÂWfVçC¢Ö÷W6TWfVçB’ÓâæöæS ¢"".[zn™Jî˜x®iKîi{n{¹>iÙþh¹nXªŽ[›nh.ZHÞ[è^iË®8"""  ¢–bWfVçBæ'WGFöâ‚’ÓÒBäÖ÷W6T'WGFöâäÆVgD'WGFöã ¢6VÆbæÆöæu÷&W75÷F–ÖW"ç7F÷‚¢–b6VÆbæG&vv–æs ¢6VÆbæG&vv–ærÒfÇ6P¢6VÆbå÷&W75÷VæF–ærÒfÇ6P¢6VÆbå÷6†÷uöVÖ÷F–öâ…WE7FFRå5U%$•4TBÂ¢VÆ–b6VÆbåöÆöæu÷&W75÷G&–vvW&VC ¢6VÆbåöÆöæu÷&W75÷G&–vvW&VBÒfÇ6P¢VÆ–b6VÆbå÷&W75÷VæF–æs ¢6VÆbå÷&W75÷VæF–ærÒfÇ6P¢6VÆbåö†æFÆUö6Æ–6²‡6VÆbå÷&W75öÆö6Â¢WfVçBæ66WB‚¢&WGW&à¢7WW"‚’æÖ÷W6U&VÆV6TWfVçB†WfVçB ¢FVbÆVfTWfVçB‡6VÆbÂWfVçB’ÓâæöæS ¢"".›Êj~zk¾[ÈZêxšži{nXùnkhŽ[	®iÊ®ŠznXùy¨Nh*ÎXÎY(ÎiŽZKN‹ÚŽ‹ûž8"""  ¢6VÆbåö†÷fW%÷¦öæRÒ" ¢6VÆbå÷7G&ö¶U÷ö–çG2æ6ÆV"‚¢6VÆbæ†÷fW%÷F–ÖW"ç7F÷‚¢6VÆbæÆöæu÷&W75÷F–ÖW"ç7F÷‚¢7WW"‚’æÆVfTWfVçB†WfVçB ¢FVbÖ÷W6TF÷V&ÆT6Æ–6´WfVçB‡6VÆbÂWfVçC¢Ö÷W6TWfVçB’ÓâæöæS ¢"".XøÎX{¾[zn™Jîh™>[È[ú¾hÛ~Xú>Š(¾ûÉ¾XøÎX{¾Xû>™Jîi*ÞiKîKˆZ;KˆÞYÎŠúÞk	Ny¨N[{N[ˆ>‹ëî8"""  ¢–bWfVçBæ'WGFöâ‚’ÓÒBäÖ÷W6T'WGFöâäÆVgD'WGFöã ¢6VÆbæG&vv–ærÒfÇ6P¢6VÆbå÷&W75÷VæF–ærÒfÇ6P¢6VÆbå÷&V6÷&E÷W6W%ö–çFW&7F–öâ‚¢6VÆbç6†÷u÷V–6µ÷æVÂ‚¢WfVçBæ66WB‚¢&WGW&à¢–bWfVçBæ'WGFöâ‚’ÓÒBäÖ÷W6T'WGFöâå&–v‡D'WGFöã ¢6VÆbæ6öçFW‡EöÖVçU÷F–ÖW"ç7F÷‚¢6VÆbå÷7W&W75ö6öçFW‡E÷VçF–ÂÒF–ÖRæÖöæ÷Föæ–2‚’²ã€¢6VÆbå÷&V6÷&E÷W6W%ö–çFW&7F–öâ‚¢6VÆbçÆ•ö&'VF÷fö–6R‚¢WfVçBæ66WB‚¢&WGW&à¢7WW"‚’æÖ÷W6TF÷V&ÆT6Æ–6´WfVçB†WfVçB 