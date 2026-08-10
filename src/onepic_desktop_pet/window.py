"""
本模块实现桌面宠物的透明窗口、连续动画、鼠标交互、快捷控制和情境陪伴。

职责范围：
- 创建无边框、透明、可选始终置顶的 QWidget；
- 播放循环或单次 PNG 序列，并支持拖拽、坐下、坐姿入睡和反向起身；
- 处理左右翻转、边缘转身停顿、亚像素时间驱动移动和同步身体起伏；
- 用窗口遮罩让人物外透明区域穿透鼠标点击；
- 缓存不同 DPI 下的缩放帧，并在窗口跨显示器后按新比例重新栅格化；
- 支持左键拖动、单击调戏、双击快捷口袋、无互动分级休息和连续尺寸滑块；
- 支持给六毛喂食或饮品，并用独立半透明文字气泡反馈状态；
- 支持离线优先的聊天面板、可选 AI 后端以及工作、爱意、鼓励和安慰动作；
- 支持电脑图层、摸头工作气泡、今日/终身计时、八小时娃衣解锁及健康提醒；
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
本模块只在用户主动发送在线消息时启动后台请求线程；普通动画、牢骚和报时均不访问网络。
API 令牌由系统凭据库管理，聊天文本不落盘；位置持久化由 app.py 在退出时完成。
`user_assets/` 默认不进入 Git；只有用户主动放入的自拍图片才会在本机显示。
"""

from __future__ import annotations

import json
import os
import random
import time
from collections import OrderedDict, deque
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal, QUrl
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QContextMenuEvent,
    QFont,
    QHideEvent,
    QMouseEvent,
    QMoveEvent,
    QPixmap,
    QRegion,
    QScreen,
    QShowEvent,
    QTransform,
    QDesktopServices,
)
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMenu, QWidget

try:
    from PySide6.QtTextToSpeech import QTextToSpeech
except ImportError:
    QTextToSpeech = None

from .ai import AIChatService, CredentialStore, PROVIDER_PRESETS
from .accessories import OUTFITS, draw_activity_overlay, unlocked_outfits
from .activity import active_application_category
from .behavior import BehaviorModel, PetMood, PetState, StateDecision
from .chat import AIReplyThread, AISettingsDialog, ChatDialog
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
from .resources import resource_path
from .music import choose_song, music_search_url
from .wellness import WellnessReminderModel
from .work_timer import WorkTimerModel, format_work_duration
from .workflow import WorkflowError, character_is_approved, load_workflow


DEFAULT_WALK_MOTION_FACTORS = (0.45, 0.7, 1.2, 1.65, 0.45, 0.7, 1.2, 1.65)


class PetWindow(QWidget):
    """显示并控制单个桌面宠物的透明顶层窗口。"""

    quit_requested = Signal()
    pause_changed = Signal(bool)
    work_timer_changed = Signal(bool)

    def __init__(
        self,
        settings: PetSettings,
        work_timer: WorkTimerModel | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.behavior = BehaviorModel(settings)
        self.mood = PetMood()
        self.companion = CompanionModel(self.mood)
        self.work_timer = work_timer or WorkTimerModel()
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
        self._chat_dialog: ChatDialog | None = None
        self._ai_thread: AIReplyThread | None = None
        self._chat_history: list[tuple[str, str]] = []
        self._action_sequence_id = 0
        self._last_announced_hour = ""
        self._ambient_activity = "none"
        self._last_app_category = "other"
        self._late_wakeup_shown = False
        self._speech_engine = QTextToSpeech(self) if QTextToSpeech is not None else None

        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if settings.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
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
        self.photo_bubble.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.photo_bubble.setAttribute(
            Qt.WidgetAttribute.WA_ShowWithoutActivating,
            True,
        )
        self.photo_bubble.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_bubble.setStyleSheet("background: transparent;")

        self.speech_bubble = QLabel()
        self.speech_bubble.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
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
        self.quick_panel.music_requested.connect(self.play_random_song)
        self.quick_panel.size_requested.connect(self.open_size_control)
        self.quick_panel.settings_requested.connect(self.open_ai_settings)

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

        self.set_state(PetState.IDLE)
        self._schedule(self.behavior.initial_idle())

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
        activity = "computer" if self.work_timer.is_running else self._ambient_activity
        composed = draw_activity_overlay(
            composed,
            activity,
            self.settings.equipped_outfit,
            self._effect_phase,
        )
        self.label.setPixmap(composed)
        effect_key = self._effect_phase if emotion_effect_name(display_state) else -1
        overlay_key = hash((activity, self.settings.equipped_outfit, self._effect_phase % 2))
        self._refresh_window_mask(display_state, composed, direction_key, effect_key ^ overlay_key)

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
        QTimer.singleShot(0, self._ensure_on_top)

    def _ensure_on_top(self) -> None:
        """周期恢复顶层层级；Windows 使用不抢焦点的原生置顶作为补强。"""

        if not self.settings.always_on_top or not self.isVisible():
            return
        if os.name == "nt":
            try:
                import ctypes

                ctypes.windll.user32.SetWindowPos(
                    int(self.winId()), -1, 0, 0, 0, 0,
                    0x0001 | 0x0002 | 0x0010 | 0x0040,
                )
                return
            except (AttributeError, OSError, ValueError):
                pass
        self.raise_()

    def moveEvent(self, event: QMoveEvent) -> None:
        """人物移动时让仍在显示的文字气泡跟随可见轮廓。"""

        super().moveEvent(event)
        if hasattr(self, "speech_bubble") and self.speech_bubble.isVisible():
            self._position_speech_bubble()

    def hideEvent(self, event: QHideEvent) -> None:
        """隐藏宠物时同步隐藏照片和文字气泡。"""

        self.photo_bubble.hide()
        self.speech_bubble.hide()
        self.work_controls.hide()
        self.quick_panel.hide()
        super().hideEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭宠物时保存运行中的工作计时并释放两个独立气泡窗口。"""

        self.shutdown_work_timer()
        self.photo_bubble.close()
        self.speech_bubble.close()
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

    def _on_dpi_changed(self, _dpi: float) -> None:
        """显示器缩放发生变化时刷新当前帧。"""

        self._render_cache.clear()
        QTimer.singleShot(0, self._refresh_pixmap)

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
            allow_walk=not self.paused,
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
            self._play_action_sequence(
                (PetState.SIT, PetState.HAPPY, PetState.SIT),
                3000,
            )
        else:
            self._show_emotion(reply.state, 2200)
        self.show_speech(reply.text)
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
        started = self.work_timer.start()
        if started:
            self.set_paused(True)
        reply = self.companion.work_started(resumed=not started)
        self._show_emotion(reply.state, 3600)
        self.show_speech(reply.text, 5600)
        self.work_timer_changed.emit(self.work_timer.is_running)
        self._refresh_pixmap()
        return reply

    def pause_work_timer(self) -> CompanionReply:
        """暂停工作计时并显示当天累计与休息建议。"""

        self._record_user_interaction()
        was_running = self.work_timer.pause()
        duration = format_work_duration(self.work_timer.today_seconds())
        if was_running:
            reply = self.companion.work_paused(duration)
        else:
            reply = CompanionReply(
                f"计时现在是暂停状态，今天累计工作 {duration}。",
                PetState.CURIOUS,
            )
        self._show_emotion(reply.state, 3200)
        self.show_speech(reply.text, 5600)
        self.work_timer_changed.emit(False)
        self.work_controls.hide()
        self._refresh_pixmap()
        return reply

    def finish_work_timer(self) -> CompanionReply:
        """完成本次工作、保留今日累计并播放庆祝动作。"""

        self._record_user_interaction()
        total = self.work_timer.finish()
        self.set_paused(False)
        reply = self.companion.work_finished(format_work_duration(total))
        self._show_emotion(reply.state, 3400)
        self.show_speech(reply.text, 6200)
        self.work_timer_changed.emit(False)
        self.work_controls.hide()
        self._refresh_pixmap()
        self._show_new_outfit_unlock()
        return reply

    def show_work_time(self) -> None:
        """显示今日累计工作时长和当前计时状态。"""

        self._record_user_interaction()
        state = PetState.SIT if self.work_timer.is_running else PetState.CURIOUS
        self._show_emotion(state, 2600)
        self.show_speech(self.work_timer.status_text(), 4800)

    def _work_timer_tick(self) -> None:
        """定期保存工作进度，并显示一次到期的鼓励或休息提醒。"""

        self.work_timer.checkpoint()
        self._show_new_outfit_unlock()
        wellness_kind = self.wellness.take_due(
            self.settings.water_reminder_enabled,
            self.settings.stand_reminder_enabled,
            self.settings.water_interval_minutes,
            self.settings.stand_interval_minutes,
        )
        if wellness_kind == "water":
            self.show_speech("喝口水吧。六毛替你把这一小会儿守住。", 6200)
        elif wellness_kind == "stand":
            self.show_speech("站起来走两步、松松肩膀吧。身体也在陪你完成今天。", 6500)
        reminder_kind = self.work_timer.take_due_reminder()
        if reminder_kind is None:
            return
        duration = format_work_duration(self.work_timer.session_seconds())
        reply = self.companion.work_reminder(reminder_kind, duration)
        self._show_emotion(reply.state, 3600)
        self.show_speech(reply.text, 7200)

    def _show_new_outfit_unlock(self) -> None:
        """在跨过新的八小时门槛时提示并自动装备新娃衣。"""

        index = self.work_timer.take_new_outfit_unlock()
        if index is None or not 1 <= index <= len(OUTFITS):
            return
        outfit = OUTFITS[index - 1]
        self.settings.equipped_outfit = outfit.key
        save_settings(self.settings)
        self._refresh_pixmap()
        self.show_speech(f"八小时成就解锁：{outfit.name}。{outfit.message}", 7600)

    def shutdown_work_timer(self) -> None:
        """自然退出前暂停并保存计时，避免把关机时间计入工作。"""

        if hasattr(self, "work_timer"):
            self.work_timer.pause()

    def prompt_dialogue(self) -> None:
        """打开新版聊天面板；离线和在线模式共用同一个入口。"""

        self._record_user_interaction()
        if self._chat_dialog is None:
            self._chat_dialog = ChatDialog(self)
            self._chat_dialog.message_submitted.connect(self._submit_chat_message)
            self._chat_dialog.settings_requested.connect(self.open_ai_settings)
            self._chat_dialog.append_message(
                "六毛",
                "巴布达！没网也可以聊天；也能在设置里连接 Codex、Claude Code、DeepSeek 或 Kimi。",
            )
        self._chat_dialog.set_provider(self.settings.ai_provider)
        self._chat_dialog.show()
        self._chat_dialog.raise_()
        self._chat_dialog.activateWindow()

    def _submit_chat_message(self, message: str) -> None:
        """显示用户消息，并按设置选择本地回答或后台 AI 请求。"""

        if self._chat_dialog is None:
            return
        self._record_user_interaction()
        self._chat_dialog.append_message("你", message)
        history_before = list(self._chat_history)
        self._chat_history.append(("user", message))
        self._chat_history = self._chat_history[-10:]
        if self.settings.ai_provider == "offline":
            reply = self.talk_to_pet(message)
            self._chat_history.append(("assistant", reply.text))
            self._chat_dialog.append_message("六毛", reply.text)
            return
        if self._ai_thread is not None and self._ai_thread.isRunning():
            self._chat_dialog.append_message("六毛", "上一句话还在路上，稍等我一下。")
            return
        self._chat_dialog.set_busy(True)
        self._ai_thread = AIReplyThread(
            self.ai_service,
            self.settings.ai_provider,
            message,
            history_before,
            self.settings.ai_base_url,
            self.settings.ai_model,
            self,
        )
        self._ai_thread.succeeded.connect(self._ai_reply_succeeded)
        self._ai_thread.failed.connect(
            lambda error, original=message: self._ai_reply_failed(error, original)
        )
        self._ai_thread.finished.connect(self._ai_thread_finished)
        self._ai_thread.start()

    def _ai_reply_succeeded(self, answer: str) -> None:
        """显示联网后端的成功回复。"""

        self._chat_history.append(("assistant", answer))
        self._chat_history = self._chat_history[-10:]
        if self._chat_dialog is not None:
            self._chat_dialog.append_message("六毛", answer)
            self._chat_dialog.set_busy(False)
        state = PetState.SHY if any(word in answer for word in ("抱抱", "爱", "陪你")) else PetState.CURIOUS
        self._show_emotion(state, 3000)
        self.show_speech(answer, 6500)

    def _ai_reply_failed(self, error: str, original: str) -> None:
        """明确提示连接问题，并无缝使用本地规则回答。"""

        offline = self.companion.reply_to(original)
        combined = f"{error}\n\n离线六毛：{offline.text}"
        self._chat_history.append(("assistant", offline.text))
        self._chat_history = self._chat_history[-10:]
        if self._chat_dialog is not None:
            self._chat_dialog.append_message("六毛", combined)
            self._chat_dialog.set_busy(False)
        self._show_emotion(offline.state, 2800)
        self.show_speech(offline.text, 6200)

    def _ai_thread_finished(self) -> None:
        """释放已完成的后台请求对象。"""

        if self._ai_thread is not None:
            self._ai_thread.deleteLater()
            self._ai_thread = None

    def open_ai_settings(self) -> None:
        """打开 AI、自动牢骚与报时设置，保存时不持久化任何明文令牌。"""

        dialog = AISettingsDialog(self.settings, self.credentials, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            dialog.apply()
        except Exception as exc:
            self.show_speech(f"设置没有保存：{exc}", 6000)
            return
        save_settings(self.settings)
        self._schedule_ambient()
        if self._chat_dialog is not None:
            self._chat_dialog.set_provider(self.settings.ai_provider)
        preset = PROVIDER_PRESETS[self.settings.ai_provider]
        self.show_speech(f"已切换为：{preset.label}", 4200)

    def set_automatic_grumbling(self, enabled: bool) -> None:
        """启用或停用只在本机生成的间歇牢骚。"""

        self.settings.automatic_grumbling = bool(enabled)
        save_settings(self.settings)
        self._schedule_ambient()

    def set_hourly_announcement(self, enabled: bool) -> None:
        """启用或停用整点报时。"""

        self.settings.hourly_announcement = bool(enabled)
        self._last_announced_hour = ""
        save_settings(self.settings)
        self.show_speech("整点报时已开启。" if enabled else "整点报时已关闭。", 3200)

    def _app_awareness_tick(self) -> None:
        """只根据前台应用类别切换配饰动作，不读取标题或文档内容。"""

        if not self.settings.app_awareness or self.work_timer.is_running:
            return
        category = active_application_category()
        if category == self._last_app_category:
            return
        self._last_app_category = category
        mapping = {"music": "headphones", "office": "writing", "coding": "computer", "reading": "reading"}
        self._ambient_activity = mapping.get(category, "none")
        self._mask_cache.clear()
        self._refresh_pixmap()

    def play_random_song(self) -> str:
        """由用户主动打开一首陈楚生歌曲的正版平台搜索页。"""

        title = choose_song()
        QDesktopServices.openUrl(QUrl(music_search_url(self.settings.music_service, title)))
        self._ambient_activity = random.choice(("headphones", "guitar", "drums"))
        self._refresh_pixmap()
        self.show_speech(f"六毛挑了《{title}》。我打开正版搜索页啦，点一下就能播放。", 6500)
        return title

    def set_activity(self, activity: str) -> None:
        """手动选择电脑、耳机、吉他、鼓或阅读叠加动作。"""

        self._ambient_activity = activity if activity in {"computer", "headphones", "guitar", "drums", "reading", "writing"} else "none"
        self._mask_cache.clear()
        self._refresh_pixmap()

    def equip_outfit(self, outfit_key: str) -> None:
        """装备已解锁娃衣；空字符串恢复经典外观。"""

        allowed = {item.key for item in unlocked_outfits(self.work_timer.unlocked_outfit_count())}
        if outfit_key and outfit_key not in allowed:
            self.show_speech("这套娃衣还在秘密王国里，再累计工作八小时就更近一点。", 5200)
            return
        self.settings.equipped_outfit = outfit_key
        save_settings(self.settings)
        self._mask_cache.clear(); self._refresh_pixmap()

    def open_size_control(self) -> None:
        """打开连续尺寸滑块并实时应用，不改变不同动作之间的比例。"""

        dialog = SizeControlDialog(self.settings.display_height, self)
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
        """显示无需系统托盘即可访问的快捷入口。"""

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
        """用随机间隔安排下一句牢骚，避免固定频率造成打扰。"""

        if not hasattr(self, "ambient_timer"):
            return
        self.ambient_timer.stop()
        if self.settings.automatic_grumbling:
            self.ambient_timer.start(random.randint(12 * 60_000, 28 * 60_000))

    def _ambient_tick(self) -> None:
        """在宠物可见且当前没有聊天请求时显示一条本地牢骚。"""

        try:
            busy = self._ai_thread is not None and self._ai_thread.isRunning()
            if self.isVisible() and not self.dragging and not busy:
                if self.settings.lyric_inspiration_enabled and random.random() < 0.28:
                    reply = self.companion.song_inspiration()
                else:
                    reply = self.companion.ambient_grumble(self.work_timer.is_running)
                self._show_emotion(reply.state, 2600)
                self.show_speech(reply.text, 6200)
        finally:
            self._schedule_ambient()

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
        self.show_speech(self.companion.status_text(), 4200)

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
        if self.settings.voice_enabled and self._speech_engine is not None:
            self._speech_engine.say("巴布达")

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
        if zone == "work_device" or (zone == "head" and self.work_timer.is_running):
            self.show_work_controls()
            return
        if zone == "camera":
            self.trigger_selfie()
            return
        if zone == "head":
            self.mood.receive_affection()
            state = PetState.SHY if self.mood.affinity >= 70 else PetState.HAPPY
            self._show_emotion(state, 1700)
            self.show_speech("巴布达。六毛被你摸到脑袋啦。", 3000)
            if self.settings.voice_enabled and self._speech_engine is not None:
                self._speech_engine.say("巴布达")
            return
        if zone == "face":
            self.mood.receive_poke(False)
            self._show_emotion(PetState.SURPRISED, 1300)
            return

        now = time.monotonic()
        self._poke_times.append(now)
        while self._poke_times and now - self._poke_times[0] > 2.5:
            self._poke_times.popleft()
        repeated = len(self._poke_times) >= 3
        self.mood.receive_poke(repeated)
        self._show_emotion(
            PetState.ANNOYED if repeated else PetState.SURPRISED,
            1800 if repeated else 1200,
        )

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
            state = PetState.SHY if self.mood.affinity >= 70 else PetState.HAPPY
            self._show_emotion(state, 1600)

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

    def _build_context_menu(self) -> QMenu:
        """构建宠物窗口的右键菜单。"""

        menu = QMenu(self)
        pause_action = QAction("恢复跑动" if self.paused else "暂停跑动", self)
        pause_action.triggered.connect(lambda: self.set_paused(not self.paused))
        menu.addAction(pause_action)
        dialogue_action = QAction("和六毛聊聊…", self)
        dialogue_action.triggered.connect(self.prompt_dialogue)
        menu.addAction(dialogue_action)
        action_menu = menu.addMenu("六毛陪伴动作")
        for option in COMPANION_ACTIONS:
            action = QAction(option.label, self)
            action.triggered.connect(
                lambda _checked=False, key=option.key: self.perform_companion_action(
                    key
                )
            )
            action_menu.addAction(action)
        work_menu = menu.addMenu(
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
        food_menu = menu.addMenu("给六毛喂食/饮品")
        for food in FOOD_OPTIONS:
            food_action = QAction(food.label, self)
            food_action.triggered.connect(
                lambda _checked=False, key=food.key: self.feed_pet(key)
            )
            food_menu.addAction(food_action)
        selfie_action = QAction("自拍一下", self)
        selfie_action.triggered.connect(self.trigger_selfie)
        menu.addAction(selfie_action)
        music_action = QAction("随机听一首陈楚生", self)
        music_action.triggered.connect(self.play_random_song)
        menu.addAction(music_action)
        music_move = menu.addMenu("音乐动作")
        for label, key in (("戴耳机", "headphones"), ("弹吉他", "guitar"), ("打鼓", "drums")):
            action = QAction(label, self)
            action.triggered.connect(lambda _checked=False, value=key: self.set_activity(value))
            music_move.addAction(action)
        ai_action = QAction("AI 与陪伴设置…", self)
        ai_action.triggered.connect(self.open_ai_settings)
        menu.addAction(ai_action)
        grumble_action = QAction("偶尔发牢骚", self)
        grumble_action.setCheckable(True)
        grumble_action.setChecked(self.settings.automatic_grumbling)
        grumble_action.toggled.connect(self.set_automatic_grumbling)
        menu.addAction(grumble_action)
        hourly_action = QAction("整点报时", self)
        hourly_action.setCheckable(True)
        hourly_action.setChecked(self.settings.hourly_announcement)
        hourly_action.toggled.connect(self.set_hourly_announcement)
        menu.addAction(hourly_action)
        size_action = QAction("连续调节宠物大小…", self)
        size_action.triggered.connect(self.open_size_control)
        menu.addAction(size_action)
        outfit_menu = menu.addMenu("八小时成就娃衣")
        classic = QAction("经典六毛", self); classic.triggered.connect(lambda: self.equip_outfit("")); outfit_menu.addAction(classic)
        unlocked = unlocked_outfits(self.work_timer.unlocked_outfit_count())
        for outfit in OUTFITS:
            label = outfit.name if outfit in unlocked else f"🔒 {outfit.name}"
            action = QAction(label, self); action.setEnabled(outfit in unlocked)
            action.triggered.connect(lambda _checked=False, key=outfit.key: self.equip_outfit(key))
            outfit_menu.addAction(action)
        return_action = QAction("回到主屏幕", self)
        return_action.triggered.connect(self.return_to_primary_screen)
        menu.addAction(return_action)
        hide_action = QAction("隐藏", self)
        hide_action.triggered.connect(self.hide)
        menu.addAction(hide_action)
        menu.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit_requested.emit)
        menu.addAction(quit_action)
        return menu

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """在鼠标位置显示窗口菜单。"""

        self._record_user_interaction()
        self._build_context_menu().exec(event.globalPos())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """记录左键按下；只有移动超过系统阈值后才真正进入拖拽。"""

        if event.button() == Qt.MouseButton.LeftButton:
            self._record_user_interaction()
            self._press_pending = True
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
            if self.dragging:
                self.dragging = False
                self._press_pending = False
                self._show_emotion(PetState.SURPRISED, 1100)
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
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """双击左键打开快捷口袋，避免依赖系统托盘。"""

        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self._press_pending = False
            self._record_user_interaction()
            self.show_quick_panel()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
