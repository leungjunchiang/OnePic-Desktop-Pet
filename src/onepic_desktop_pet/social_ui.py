"""搭子自习室界面、后台同步线程和双六毛本地串门窗口。"""

from __future__ import annotations

import sys
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QFontDatabase, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QStackedWidget, QTabWidget,
    QVBoxLayout, QWidget, QSizePolicy,
)

from .resources import resource_path
from .accessories import SPECIAL_OUTFIT_SPRITES
from .social import SocialClient, SocialError
from .config import PET_NAME, social_pet_label
from .work_timer import format_work_duration

LOGGER = logging.getLogger(__name__)


def _presence_working(presence: dict[str, Any]) -> bool:
    """Read both the legacy boolean and the new explicit presence status.

    Older dashboard functions only returned ``working`` while the repaired
    function also returns ``status``.  Keeping this normalization in the UI
    prevents a mixed-version pair of clients from showing a false rest state.
    """

    status = str(presence.get("status") or "").strip().casefold()
    if status in {"focus", "working", "专注", "工作", "专注中", "正在工作"}:
        return True
    if status in {"rest", "idle", "offline", "休息", "休息中", "离线"}:
        return False
    value = presence.get("working")
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "focus", "working", "专注", "工作", "专注中", "正在工作"}
    return bool(value)


def _presence_status(presence: dict[str, Any]) -> str:
    """Return a stable user-facing status for old and new API payloads."""

    status = str(presence.get("status") or "").strip().casefold()
    if status in {"offline", "离线"}:
        return "offline"
    if _presence_working(presence):
        return "focus"
    return "rest"


def _social_font() -> QFont:
    candidates = (
        (Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/simhei.ttf"))
        if sys.platform == "win32"
        else (Path("/System/Library/Fonts/PingFang.ttc"), Path("/System/Library/Fonts/Hiragino Sans GB.ttc"))
    )
    family = ""
    for path in candidates:
        if path.is_file():
            font_id = QFontDatabase.addApplicationFont(str(path))
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families: family = families[0]; break
    return QFont(family or "sans-serif", 10)


class SocialSyncThread(QThread):
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, client: SocialClient, presence: dict[str, Any], parent=None) -> None:
        super().__init__(parent); self.client = client; self.presence = presence

    def run(self) -> None:
        try:
            self.client.heartbeat(**self.presence)
            room_id = self.presence.get("room_id")
            try:
                data = self.client.dashboard(room_id=room_id)
            except TypeError:
                # Keep third-party/test backends compatible while they adopt
                # the room-scoped dashboard argument.
                data = self.client.dashboard()
            self.completed.emit(data)
        except SocialError as exc:
            cached_loader = getattr(self.client, "cached_dashboard", None)
            cached = cached_loader(self.presence.get("room_id")) if callable(cached_loader) else None
            if cached is not None:
                self.completed.emit(cached)
            else:
                self.failed.emit(str(exc))


class SocialDashboardThread(QThread):
    """Fetch one dashboard without blocking the Qt GUI thread."""

    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, client: SocialClient, room_id: str | None, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.room_id = room_id

    def run(self) -> None:
        try:
            try:
                data = self.client.dashboard(room_id=self.room_id)
            except TypeError:
                # Keep small offline/test backends compatible with the room
                # scoped dashboard while the real request stays off the GUI.
                data = self.client.dashboard()
            self.completed.emit(dict(data or {}))
        except SocialError as exc:
            cached_loader = getattr(self.client, "cached_dashboard", None)
            cached = cached_loader(self.room_id) if callable(cached_loader) else None
            if cached is not None:
                self.completed.emit(cached)
            else:
                self.failed.emit(str(exc))


class SocialHealthThread(QThread):
    """Probe the configured social endpoint without blocking the UI."""

    completed = Signal(dict)
    failed = Signal(object)

    def __init__(self, client: SocialClient, parent=None) -> None:
        super().__init__(parent)
        self.client = client

    def run(self) -> None:
        try:
            checker = getattr(self.client, "health", None)
            if not callable(checker):
                raise SocialError("当前自习室后端未提供健康检查。", kind="config")
            self.completed.emit(dict(checker() or {}))
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception as exc:
            self.failed.emit(SocialError(f"健康检查失败：{exc}", kind="network"))


class SocialEventThread(QThread):
    """Send a room event without freezing pet animation or the study window."""

    completed = Signal()
    failed = Signal(str)

    def __init__(self, client: SocialClient, event: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.event = event

    def run(self) -> None:
        try:
            self.client.record_room_event(**self.event)
            self.completed.emit()
        except (SocialError, AttributeError) as exc:
            self.failed.emit(str(exc))


class SocialProfileThread(QThread):
    """Persist an owner nickname away from the Qt GUI thread."""

    failed = Signal(str)

    def __init__(self, client: SocialClient, nickname: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.nickname = nickname

    def run(self) -> None:
        try:
            self.client.update_owner_nickname(self.nickname)
        except (SocialError, AttributeError) as exc:
            self.failed.emit(str(exc))


class BuddyCardWidget(QWidget):
    """把搭子的在线、工作和今日时长显示成一眼能看清的卡片。"""

    interaction_requested = Signal(dict, str)
    interaction_blocked = Signal(str)
    subscription_requested = Signal(dict, bool)

    def __init__(self, buddy: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.buddy = buddy
        self._cooldown_seconds = 15
        self._cooldown_until: dict[str, float] = {}
        self._buttons: dict[str, QPushButton] = {}
        self.setObjectName("buddyCard")
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(5)
        online = bool(buddy.get("online"))
        working = _presence_working(buddy)
        nickname = str(buddy.get("nickname") or "搭子")
        is_self = bool(buddy.get("is_self"))
        status = _presence_status(buddy)
        status_text = {"focus": "正在工作", "rest": "正在休息", "offline": "已离线"}[status]
        headline = QLabel(
            f"{'🟢' if online else '⚪'}  {social_pet_label(nickname)}"
            f"{status_text}{'（我）' if is_self else ''}"
        )
        headline.setWordWrap(True)
        headline.setStyleSheet("font-size:15px;font-weight:600;color:#203847;")
        root.addWidget(headline)
        duration = buddy.get("today_seconds")
        time_text = "今日专注时长已隐藏" if duration is None else f"已专注 {format_work_duration(duration)}"
        session_seconds = buddy.get("session_seconds")
        if session_seconds is not None and status == "focus":
            time_text = f"本轮专注 {format_work_duration(session_seconds)}　·　{time_text}"
        focus = QLabel(time_text)
        focus.setStyleSheet("font-size:18px;font-weight:700;color:#087f74;")
        root.addWidget(focus)
        quick_status = str(buddy.get("quick_status") or "").strip()
        expires = str(buddy.get("quick_status_expires_at") or "")
        if quick_status and (not expires or expires > datetime.now().astimezone().isoformat()):
            quick = QLabel(f"状态：{quick_status[:40]}")
            quick.setStyleSheet("color:#b36b2c;font-size:12px;font-weight:600;")
            root.addWidget(quick)
        outfit = str(buddy.get("outfit_key") or "经典六毛")
        footer = QLabel(f"当前娃衣：{outfit}　·　双击或选中后可派六毛串门")
        footer.setStyleSheet("color:#61727d;font-size:11px;")
        footer.setWordWrap(True)
        root.addWidget(footer)
        actions = QHBoxLayout()
        for kind, label in (("poke", "戳一下"), ("cheer", "加油"), ("drink", "递奶茶")):
            button = QPushButton(label)
            button.setMinimumHeight(32)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            button.clicked.connect(lambda _checked=False, action=kind: self._request_interaction(action))
            if is_self:
                button.setEnabled(False)
                button.setToolTip("互动按钮只对房间里的其他搭子开放")
            self._buttons[kind] = button
            actions.addWidget(button)
        root.addLayout(actions)
        if not is_self:
            subscribe = QCheckBox("订阅开工/下班提醒")
            subscribe.setChecked(bool(buddy.get("subscribed")))
            subscribe.stateChanged.connect(lambda state: self.subscription_requested.emit(self.buddy, bool(state)))
            root.addWidget(subscribe)

    def _request_interaction(self, kind: str) -> None:
        now = time.monotonic()
        remaining = self._cooldown_until.get(kind, 0.0) - now
        if remaining > 0:
            self.interaction_blocked.emit(f"互动冷却中，请 {int(remaining) + 1} 秒后再试。")
            return
        self._cooldown_until[kind] = now + self._cooldown_seconds
        button = self._buttons.get(kind)
        if button is not None:
            button.setEnabled(False)
            button.setText(f"已发送 ({self._cooldown_seconds}s)")
            QTimer.singleShot(self._cooldown_seconds * 1000, lambda: self._restore_button(kind))
        self.interaction_requested.emit(self.buddy, kind)

    def _restore_button(self, kind: str) -> None:
        button = self._buttons.get(kind)
        if button is None:
            return
        labels = {"poke": "戳一下", "cheer": "加油", "drink": "递奶茶"}
        button.setText(labels.get(kind, "互动"))
        if not bool(self.buddy.get("is_self")):
            button.setEnabled(True)


class BuddyVisitWindow(QWidget):
    """完全由本地素材绘制的双六毛陪伴窗口。"""

    def __init__(self, parent=None) -> None:
        # Keep visits as ordinary top-level application windows.  The pet may
        # be pinned, but a visit must have a taskbar entry and never force
        # itself above the user's current application.
        super().__init__(
            parent,
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint,
        )
        self.setFont(_social_font())
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setStyleSheet("QWidget#card{background:rgba(238,246,249,235);border:1px solid rgba(90,110,120,80);border-radius:20px;} QLabel{color:#263746;font-family:'Microsoft YaHei UI','PingFang SC';} QPushButton{padding:8px;border-radius:10px;background:#d7ece8;}")
        card = QWidget(self); card.setObjectName("card")
        root = QVBoxLayout(self); root.addWidget(card); layout = QVBoxLayout(card)
        pets = QHBoxLayout(); self.mine = QLabel(); self.peer = QLabel()
        for label in (self.mine, self.peer):
            label.setFixedSize(220, 220)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setScaledContents(True)
            pets.addWidget(label)
        layout.addLayout(pets)
        self.title = QLabel("两只六毛一起工作中"); self.title.setAlignment(Qt.AlignmentFlag.AlignCenter); self.title.setStyleSheet("font-size:18px;font-weight:700;")
        self.subtitle = QLabel("💻 六毛　　六毛 📖\n一起工作中"); self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clock = QLabel("00:00:00"); self.clock.setAlignment(Qt.AlignmentFlag.AlignCenter); self.clock.setStyleSheet("font-size:30px;font-weight:700;color:#087f74;")
        self.today = QLabel(); self.today.setAlignment(Qt.AlignmentFlag.AlignCenter); self.today.setStyleSheet("color:#61727d;")
        layout.addWidget(self.title); layout.addWidget(self.subtitle); layout.addWidget(self.clock); layout.addWidget(self.today)
        close = QPushButton("结束这次串门"); close.clicked.connect(self.hide_visit); layout.addWidget(close)
        self.elapsed = 0
        self._phase = 0
        self._mine_outfit = ""
        self._peer_outfit = ""
        self._mine_actions = ("02-office.png", "22-thermos.png", "04-guitar.png")
        self._peer_actions = ("09-night-reading.png", "03-headphones.png", "19-tea.png")
        self.timer = QTimer(self); self.timer.timeout.connect(self._tick); self.timer.start(1000)
        self.resize(520, 430)
        self.active_visit_id = ""
        self.visible_requested = False
        self.user_minimized = False
        self._presented_visit_id = ""

    def show_peer(self, peer: dict[str, Any], mine_outfit: str = "", mine_today: int = 0) -> None:
        visit_id = str(peer.get("id") or peer.get("visit_id") or "")
        # Heartbeat/dashboard refreshes are idempotent.  Never resurrect a
        # visit the user has minimized or hidden; only a new visit id may ask
        # the window to become visible.
        if visit_id and visit_id == self._presented_visit_id:
            return
        self.active_visit_id = visit_id
        self._presented_visit_id = visit_id
        self.visible_requested = True
        self.user_minimized = False
        nickname = social_pet_label(peer.get("nickname"))
        self.title.setText(f"{nickname}来串门了")
        self.subtitle.setText(f"💻 {PET_NAME}　　{nickname} 📖\n一起工作中")
        peer_today = peer.get("today_seconds")
        peer_text = "时长隐藏" if peer_today is None else format_work_duration(peer_today)
        self.today.setText(f"你今日 {format_work_duration(mine_today)}　·　{nickname} 今日 {peer_text}")
        self._mine_outfit = mine_outfit
        self._peer_outfit = str(peer.get("outfit_key") or "")
        self._phase = 0
        self.elapsed = 0
        started = peer.get("visit_started_at")
        if started:
            try:
                self.elapsed = max(0, int((datetime.now().astimezone() - datetime.fromisoformat(str(started))).total_seconds()))
            except ValueError:
                self.elapsed = 0
        self._refresh_pets()
        self._tick()
        self.show()

    def hide_visit(self) -> None:
        """Hide this visit without allowing a background refresh to reopen it."""
        self.visible_requested = False
        self.user_minimized = False
        self.hide()

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.WindowStateChange:
            self.user_minimized = bool(self.windowState() & Qt.WindowState.WindowMinimized)
        super().changeEvent(event)

    def _load_pet(self, label: QLabel, outfit_key: str, fallback_name: str) -> None:
        relative = SPECIAL_OUTFIT_SPRITES.get(outfit_key, f"assets/pet/daily-actions/{fallback_name}")
        pix = QPixmap(str(resource_path(relative)))
        label.setPixmap(pix)

    def _refresh_pets(self) -> None:
        mine_action = self._mine_actions[self._phase % len(self._mine_actions)]
        peer_action = self._peer_actions[self._phase % len(self._peer_actions)]
        # 第一幕展示双方当前娃衣，后续动作均由本地轮换，不同步动画帧。
        self._load_pet(self.mine, self._mine_outfit if self._phase == 0 else "", mine_action)
        self._load_pet(self.peer, self._peer_outfit if self._phase == 0 else "", peer_action)

    def _tick(self) -> None:
        if self.isVisible():
            self.elapsed += 1
            if self.elapsed % 15 == 0:
                self._phase += 1
                self._refresh_pets()
        h, rest = divmod(self.elapsed, 3600); m, s = divmod(rest, 60)
        self.clock.setText(f"{h:02d}:{m:02d}:{s:02d}")


class SocialHubDialog(QDialog):
    """提供首页、聊天、专注、我的四个清晰页面及统一操作反馈。"""

    active_visit = Signal(dict)
    focus_start_requested = Signal()
    focus_pause_requested = Signal()
    focus_finish_requested = Signal()
    focus_task_requested = Signal(str, int)
    tomorrow_review_requested = Signal(str)
    room_changed = Signal(object)
    room_event_received = Signal(dict)
    room_ritual_due = Signal(str)
    buddy_subscription_notice = Signal(str)
    quick_action_requested = Signal(str)

    def __init__(self, client: SocialClient, outfit_key: str = "", owner_nickname: str = "", parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.outfit_key = outfit_key
        self.owner_nickname = owner_nickname.strip()[:24]
        self.data: dict[str, Any] = {}
        self.current_room_id: str | None = None
        self._focus_snapshot: Any = None
        self._applying_dashboard = False
        self._room_goal_state: dict[str, Any] = {}
        self._room_schedule_state: dict[str, Any] = {}
        self._room_challenge_state: dict[str, Any] = {}
        self._seen_room_event_ids: set[str] = set()
        self._focus_analytics: dict[str, Any] = {}
        self._last_ritual_notice = ""
        self._initial_refresh_timer = QTimer(self)
        self._initial_refresh_timer.setSingleShot(True)
        self._initial_refresh_timer.timeout.connect(self.refresh)
        self._room_refresh_timer = QTimer(self)
        self._room_refresh_timer.setSingleShot(True)
        self._room_refresh_timer.timeout.connect(self._refresh_selected_room)
        self._dashboard_thread: SocialDashboardThread | None = None
        self._health_thread: SocialHealthThread | None = None
        self._event_threads: list[SocialEventThread] = []
        self.setFont(_social_font())
        # Make this a normal independent utility window.  QDialog's default
        # flags differ by platform and can omit the minimize button when a
        # parent is supplied, which made the study room feel like a modal
        # sheet on Windows.  It deliberately does not include Tool or
        # WindowStaysOnTopHint: minimizing it must only hide this window and
        # never affect the desktop pet or its timers.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowTitle("六毛搭子自习室")
        self.resize(760, 760)
        self.setMinimumSize(520, 480)
        self.setSizeGripEnabled(True)
        self.setStyleSheet("""
            QDialog { background:#edf4f7; }
            QLabel { color:#263746; }
            QLabel#pageTitle { font-size:24px; font-weight:700; }
            QLabel#sectionTitle { font-size:17px; font-weight:650; }
            QLabel#muted { color:#667984; }
            QLabel#status { background:#e1efec; color:#087f74; border-radius:9px; padding:7px 10px; }
            QFrame#card, QWidget#buddyCard { background:#ffffff; border:1px solid #d6e1e6; border-radius:14px; }
            QLineEdit, QListWidget { background:#ffffff; border:1px solid #b9c8d0; border-radius:10px; padding:7px; }
            QScrollArea#pageScroll { background:transparent; border:0; }
            QTabWidget::pane { border:0; }
            QTabBar::tab { min-width:105px; padding:10px 16px; color:#526872; }
            QTabBar::tab:selected { color:#087f74; font-weight:700; border-bottom:3px solid #38a397; }
            QPushButton { min-height:20px; padding:8px 14px; border:0; border-radius:9px; background:#d7ece8; color:#204c4a; font-weight:600; }
            QPushButton:hover { background:#c2e2dd; }
            QPushButton:disabled { color:#91a1a8; background:#e8eef0; }
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 20)
        root.setSpacing(9)
        title = QLabel("六毛搭子自习室")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        subtitle = QLabel("一起聊天、专注和串门；未登录时，六毛仍可完整离线陪伴。")
        subtitle.setObjectName("muted")
        root.addWidget(subtitle)
        self.status_label = QLabel("页面已准备好")
        self.status_label.setObjectName("status")
        root.addWidget(self.status_label)
        self.tabs = QTabWidget()
        self.tabs.tabBar().setExpanding(True)
        self.tabs.tabBar().setUsesScrollButtons(False)
        self.tabs.addTab(self._home_page(), "首页")
        self.tabs.addTab(self._chat_page(), "聊天")
        self.tabs.addTab(self._focus_page(), "专注")
        self.tabs.addTab(self._mine_page(), "我的")
        root.addWidget(self.tabs, 1)
        self._update_account_state()
        if client.signed_in:
            self._initial_refresh_timer.start(50)

    def set_focus_snapshot(self, snapshot: Any) -> None:
        """Render the desktop timer state without creating a second timer."""

        self._focus_snapshot = snapshot
        if not hasattr(self, "focus_status"):
            return
        status = getattr(snapshot, "status", None)
        if isinstance(snapshot, dict):
            status = snapshot.get("status")
            session_seconds = snapshot.get("session_seconds", 0)
            today_seconds = snapshot.get("today_seconds", 0)
        else:
            session_seconds = getattr(snapshot, "session_seconds", 0)
            today_seconds = getattr(snapshot, "today_seconds", 0)
        labels = {"focus": "专注中", "rest": "休息中", "idle": "尚未开始"}
        self.focus_status.setText(labels.get(str(status), "等待同步"))
        self.focus_clock.setText(format_work_duration(int(session_seconds)))
        self.focus_today.setText(f"今日累计 {format_work_duration(int(today_seconds))}")
        self.focus_start.setEnabled(str(status) != "focus")
        self.focus_pause.setEnabled(str(status) == "focus")
        self.focus_finish.setEnabled(int(session_seconds) > 0 or int(today_seconds) > 0)

    def set_room_quick_status(self, status: str, expires_at: datetime | None = None) -> None:
        """Render the local room action immediately, before the next heartbeat."""

        clean = str(status or "").strip()[:40]
        expiry = expires_at.isoformat() if expires_at is not None else None
        for person in getattr(self, "_room_people", []):
            if person.get("is_self"):
                person["quick_status"] = clean
                person["quick_status_expires_at"] = expiry
        if hasattr(self, "room_members") and getattr(self, "_room_people", None):
            self._render_room_people(self._room_people)

    def set_owner_nickname(self, nickname: str) -> None:
        self.owner_nickname = str(nickname or "").strip()[:24]
        if self.data:
            me = self.data.get("me") or {}
            own_label = social_pet_label(self.owner_nickname or me.get("nickname"))
            self.identity.setText(f"{own_label} · 我的搭子码：{me.get('invite_code','--------')}")

    def set_focus_analytics(self, snapshot: dict[str, Any] | None) -> None:
        """Render local continuity metrics and the one-task countdown."""

        self._focus_analytics = dict(snapshot or {})
        if not hasattr(self, "focus_insights"):
            return
        summary = self._focus_analytics
        task = summary.get("current_task") or {}
        task_text = "当前任务：未设置"
        if isinstance(task, dict) and task.get("title"):
            task_text = f"当前任务：{task['title']}"
            due = str(task.get("due_at") or "")
            if due:
                try:
                    deadline = datetime.fromisoformat(due.replace("Z", "+00:00"))
                    if deadline.tzinfo is None:
                        deadline = deadline.astimezone()
                    remaining = max(0, int((deadline - datetime.now().astimezone()).total_seconds()))
                    task_text += f" · 剩余 {format_work_duration(remaining)}"
                except ValueError:
                    pass
        first_task = str(summary.get("first_task_today") or "")
        if first_task:
            task_text += f"\n今天第一件事：{first_task}"
        self.focus_task.setText(task_text)
        self.focus_insights.setText(
            f"今天第 {int(summary.get('today_rounds') or 0)} 轮 · 连续 {int(summary.get('current_streak_days') or 0)} 天 · "
            f"本周 {format_work_duration(int(summary.get('weekly_total_seconds') or 0))}\n"
            f"最长连续 {int(summary.get('longest_streak_days') or 0)} 天 · "
            f"较昨天 {'多' if int(summary.get('difference_vs_yesterday_seconds') or 0) >= 0 else '少'} "
            f"{format_work_duration(abs(int(summary.get('difference_vs_yesterday_seconds') or 0)))} · "
            f"{summary.get('quality_label') or '暂无质量数据'}"
        )

    @staticmethod
    def _card(title: str, description: str = "") -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        if description:
            detail = QLabel(description)
            detail.setObjectName("muted")
            detail.setWordWrap(True)
            layout.addWidget(detail)
        return card, layout

    @staticmethod
    def _scroll_page(page: QWidget) -> QScrollArea:
        """Keep dense pages usable when the utility window is made smaller."""

        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll

    @staticmethod
    def _fit_list_height(widget: QListWidget, minimum: int, maximum: int) -> None:
        """Size short lists to their contents while retaining scrolling for long lists."""

        widget.setMinimumHeight(minimum)
        widget.setMaximumHeight(maximum)
        widget.ensurePolished()
        total = max(0, widget.frameWidth() * 2)
        for index in range(widget.count()):
            row_height = widget.sizeHintForRow(index)
            total += row_height if row_height > 0 else widget.fontMetrics().lineSpacing() + 14
        desired = min(maximum, max(minimum, total + 8))
        widget.setFixedHeight(desired)

    @staticmethod
    def _set_buddy_item_height(item: QListWidgetItem, widget: BuddyCardWidget) -> None:
        widget.ensurePolished()
        # Leave room for Windows font/DPI metrics and the checkbox below the
        # interaction row. The old fixed 125px rows clipped this content.
        item.setSizeHint(QSize(0, max(132, widget.sizeHint().height() + 16)))

    def _home_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(12)
        welcome, welcome_layout = self._card("今天也一起往前一点", "查看搭子动态、待处理邀请和当前专注状态。")
        self.study_summary = QLabel("登录后可查看搭子专注时间；本地工作计时不受影响。")
        self.study_summary.setStyleSheet("font-size:18px;font-weight:700;color:#087f74;")
        self.study_summary.setWordWrap(True)
        welcome_layout.addWidget(self.study_summary)
        refresh = QPushButton("刷新首页")
        refresh.clicked.connect(self.refresh)
        welcome_layout.addWidget(refresh)
        network_row = QHBoxLayout()
        self.network_hint = QLabel(self._backend_hint())
        self.network_hint.setObjectName("muted")
        self.network_hint.setWordWrap(True)
        network_row.addWidget(self.network_hint, 1)
        network_check = QPushButton("检测自习室网络")
        network_check.clicked.connect(self._check_network)
        network_row.addWidget(network_check)
        welcome_layout.addLayout(network_row)
        layout.addWidget(welcome)
        buddies_card, buddies_layout = self._card("我的搭子", "绿色表示两分钟内在线；选择后可到“聊天”页派六毛串门。")
        self.buddies = QListWidget(); self.buddies.setSpacing(5)
        self.buddies.setMinimumHeight(46); self.buddies.setMaximumHeight(360)
        self.buddies.itemDoubleClicked.connect(lambda _item: self._send_visit())
        buddies_layout.addWidget(self.buddies)
        layout.addWidget(buddies_card)
        layout.addStretch()
        return self._scroll_page(page)

    def _chat_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(12)
        actions, action_layout = self._card("搭子互动", "添加搭子、派六毛串门；聊天正文不会上传到自习室服务。")
        row = QHBoxLayout()
        add = QPushButton("用搭子码添加")
        visit = QPushButton("派六毛去串门")
        add.clicked.connect(self._add_buddy); visit.clicked.connect(self._send_visit)
        row.addWidget(add); row.addWidget(visit); action_layout.addLayout(row)
        layout.addWidget(actions)
        inbox_card, inbox_layout = self._card("待处理申请与串门", "选择一项后接受，操作结果会显示在页面顶部。")
        self.inbox = QListWidget(); self.inbox.setMinimumHeight(125); self.inbox.setMaximumHeight(360)
        inbox_layout.addWidget(self.inbox)
        accept = QPushButton("接受选中的项目"); accept.clicked.connect(self._accept_inbox); inbox_layout.addWidget(accept)
        layout.addWidget(inbox_card)
        layout.addStretch()
        return self._scroll_page(page)

    def _focus_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(12)
        focus_card, focus_layout = self._card(
            "我的专注",
            "桌面六毛与自习室共用同一个 FocusSession；这里不会再启动第二套计时器。",
        )
        self.focus_status = QLabel("等待同步")
        self.focus_status.setStyleSheet("font-size:18px;font-weight:700;color:#087f74;")
        self.focus_clock = QLabel("0分钟")
        self.focus_clock.setStyleSheet("font-size:28px;font-weight:700;color:#203847;")
        self.focus_today = QLabel("今日累计 0分钟")
        self.focus_today.setObjectName("muted")
        focus_layout.addWidget(self.focus_status)
        focus_layout.addWidget(self.focus_clock)
        focus_layout.addWidget(self.focus_today)
        self.focus_task = QLabel("当前任务：未设置")
        self.focus_task.setObjectName("muted")
        self.focus_task.setWordWrap(True)
        focus_layout.addWidget(self.focus_task)
        self.focus_insights = QLabel("今天第 0 轮 · 连续 0 天 · 本周 0分钟")
        self.focus_insights.setObjectName("muted")
        self.focus_insights.setWordWrap(True)
        focus_layout.addWidget(self.focus_insights)
        controls = QGridLayout()
        self.focus_start = QPushButton("开始专注")
        self.focus_pause = QPushButton("暂停休息")
        self.focus_finish = QPushButton("结束本轮")
        self.focus_start.clicked.connect(self.focus_start_requested.emit)
        self.focus_pause.clicked.connect(self.focus_pause_requested.emit)
        self.focus_finish.clicked.connect(self.focus_finish_requested.emit)
        for column, button in enumerate((self.focus_start, self.focus_pause, self.focus_finish)):
            controls.addWidget(button, 0, column)
            controls.setColumnStretch(column, 1)
        focus_layout.addLayout(controls)
        task_button = QPushButton("设置一次只盯一件事")
        task_button.clicked.connect(self._set_focus_task)
        review_button = QPushButton("写下明天第一件事")
        review_button.clicked.connect(self._set_tomorrow_review)
        task_row = QGridLayout(); task_row.addWidget(task_button, 0, 0); task_row.addWidget(review_button, 0, 1)
        task_row.setColumnStretch(0, 1); task_row.setColumnStretch(1, 1)
        focus_layout.addLayout(task_row)
        layout.addWidget(focus_card)

        room_card, room_layout = self._card(
            "共同专注房间",
            "只显示专注/休息和累计时长；不会上传正在使用的软件、窗口标题或任务内容。",
        )
        self.room_goal = QLabel("尚未选择房间目标")
        self.room_goal.setObjectName("muted")
        room_layout.addWidget(self.room_goal)
        self.room_summary = QLabel("选择一个房间后，这里会显示共同专注人数和累计时长。")
        self.room_summary.setObjectName("muted")
        self.room_summary.setWordWrap(True)
        room_layout.addWidget(self.room_summary)
        self.room_members = QListWidget(); self.room_members.setSpacing(5)
        self.room_members.setMinimumHeight(46); self.room_members.setMaximumHeight(310)
        room_layout.addWidget(self.room_members)
        self.room_activity = QListWidget(); self.room_activity.setMinimumHeight(90); self.room_activity.setMaximumHeight(180)
        room_layout.addWidget(self.room_activity)
        self.room_ritual = QLabel("共同开工/收工：未设置")
        self.room_ritual.setObjectName("muted")
        self.room_ritual.setWordWrap(True)
        room_layout.addWidget(self.room_ritual)
        self.room_challenge = QLabel("共同挑战：未设置")
        self.room_challenge.setObjectName("muted")
        self.room_challenge.setWordWrap(True)
        room_layout.addWidget(self.room_challenge)
        self.rooms = QListWidget(); self.rooms.setMinimumHeight(52); self.rooms.setMaximumHeight(140)
        self.rooms.currentItemChanged.connect(self._room_selected)
        room_layout.addWidget(self.rooms)
        row = QGridLayout(); create = QPushButton("创建自习室"); join = QPushButton("使用房间码加入")
        create.clicked.connect(self._create_room); join.clicked.connect(self._join_room)
        row.addWidget(create, 0, 0); row.addWidget(join, 0, 1)
        row.setColumnStretch(0, 1); row.setColumnStretch(1, 1); room_layout.addLayout(row)
        room_actions = QGridLayout()
        self.room_goal_button = QPushButton("设置共同目标")
        self.room_schedule_button = QPushButton("一起开工/收工")
        self.room_challenge_button = QPushButton("设置共同挑战")
        self.room_leave_button = QPushButton("离开当前房间")
        self.room_goal_button.clicked.connect(self._set_room_goal)
        self.room_schedule_button.clicked.connect(self._set_room_schedule)
        self.room_challenge_button.clicked.connect(self._set_room_challenge)
        self.room_leave_button.clicked.connect(self._leave_room)
        for index, button in enumerate((self.room_goal_button, self.room_schedule_button, self.room_challenge_button, self.room_leave_button)):
            room_actions.addWidget(button, index // 2, index % 2)
            room_actions.setColumnStretch(index % 2, 1)
        room_layout.addLayout(room_actions)
        phrase_row = QGridLayout()
        for index, phrase in enumerate(("我也开工了", "再卷 30 分钟", "去喝水", "下班没？")):
            button = QPushButton(phrase)
            button.setToolTip("发送给当前房间成员，短时间内不会重复骚扰同一人")
            button.clicked.connect(lambda _checked=False, value=phrase: self._quick_action_clicked(value))
            phrase_row.addWidget(button, index // 2, index % 2)
            phrase_row.setColumnStretch(index % 2, 1)
        room_layout.addLayout(phrase_row)
        self.room_goal_timer = QTimer(self)
        self.room_goal_timer.setInterval(1000)
        self.room_goal_timer.timeout.connect(self._refresh_room_goal_text)
        self.room_goal_timer.start()
        layout.addWidget(room_card)
        layout.addStretch()
        self.set_focus_snapshot(self._focus_snapshot or {"status": "idle", "session_seconds": 0, "today_seconds": 0})
        self.set_focus_analytics(self._focus_analytics)
        return self._scroll_page(page)

    def _quick_action_clicked(self, action: str) -> None:
        if action == "下班没？":
            self._send_phrase(action)
            return
        if not self._require_login() or not self.current_room_id:
            self._set_status("请先加入一个共同房间，再改变房间状态。", error=True)
            return
        self.quick_action_requested.emit(action)
        self._set_status(f"正在把“{action}”同步给当前房间…")

    def _room_id_from_payload(self, room: dict[str, Any]) -> str | None:
        room_id = str(room.get("id") or room.get("room_id") or "")
        if not room_id and not isinstance(self.client, SocialClient):
            # Lightweight offline clients often only expose the invite code.
            # Real Supabase room payloads must carry the UUID used by the RPCs.
            room_id = str(room.get("invite_code") or "")
        return room_id or None

    def _room_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None = None) -> None:
        room = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        self.current_room_id = self._room_id_from_payload(room) if isinstance(room, dict) else None
        if self.current_room_id:
            self.room_changed.emit(self.current_room_id)
            self._set_status("已切换房间；正在同步这个房间的成员、目标和动态。")
            if not self._applying_dashboard:
                self._room_refresh_timer.start(0)

    def _backend_hint(self) -> str:
        backend = str(getattr(self.client, "backend_name", "unknown") or "unknown")
        endpoint = str(getattr(self.client, "backend_endpoint", "") or "")
        if endpoint:
            return f"当前自习室后端：{backend} · {endpoint}"
        return f"当前自习室后端：{backend} · 未配置独立中转服务"

    def _check_network(self) -> None:
        if self._health_thread is not None and self._health_thread.isRunning():
            return
        self._begin_action("正在检测自习室网络…")
        if not isinstance(self.client, SocialClient):
            try:
                checker = getattr(self.client, "health", None)
                if not callable(checker):
                    raise SocialError("当前测试后端未提供健康检查。", kind="config")
                self._network_check_succeeded(dict(checker() or {}))
            except Exception as exc:
                self._network_check_failed(exc)
            return
        thread = SocialHealthThread(self.client, self)
        self._health_thread = thread
        thread.completed.connect(self._network_check_succeeded)
        thread.failed.connect(self._network_check_failed)
        thread.finished.connect(lambda: self._health_thread_finished(thread))
        thread.start()

    def _network_check_succeeded(self, data: dict[str, Any]) -> None:
        self._end_action()
        backend = str(data.get("backend") or getattr(self.client, "backend_name", "social"))
        service = str(data.get("service") or "服务可达")
        self.network_hint.setText(f"当前自习室后端：{backend} · {service}")
        self._set_status("自习室网络检查通过，可以同步房间状态。")

    def _network_check_failed(self, error: object) -> None:
        self._end_action()
        exc = error if isinstance(error, SocialError) else SocialError(str(error), kind="network")
        LOGGER.warning(
            "social health check failed kind=%s endpoint=%s status=%s: %s",
            exc.kind,
            exc.endpoint,
            exc.status,
            exc,
        )
        self._set_status(f"网络检查失败：{exc}", error=True)

    def _health_thread_finished(self, thread: SocialHealthThread) -> None:
        if self._health_thread is thread:
            self._health_thread = None
        thread.deleteLater()

    def _start_dashboard_refresh(self, room_id: str | None, message: str) -> None:
        """Start one coalesced dashboard request away from the GUI thread."""

        if not self.client.signed_in:
            return
        if self._dashboard_thread is not None and self._dashboard_thread.isRunning():
            return

        # The application uses SocialClient, whose dashboard call performs
        # network I/O and therefore must stay off the GUI thread.  The small
        # in-memory clients used by the desktop smoke tests and offline demo
        # are deliberately kept synchronous so a refresh remains immediately
        # observable to callers without needing a second event-loop turn.
        if not isinstance(self.client, SocialClient):
            self._begin_action(message)
            try:
                try:
                    data = self.client.dashboard(room_id=room_id)
                except TypeError:
                    data = self.client.dashboard()
            except SocialError as exc:
                self._dashboard_failed(str(exc))
                return
            self._end_action()
            self.apply_dashboard(data)
            return

        self._begin_action(message)
        thread = SocialDashboardThread(self.client, room_id, self)
        self._dashboard_thread = thread
        thread.completed.connect(
            lambda data, requested_room=room_id: self._dashboard_received(data, requested_room)
        )
        thread.failed.connect(self._dashboard_failed)
        thread.finished.connect(lambda: self._dashboard_thread_finished(thread))
        thread.start()

    def _dashboard_received(self, data: dict[str, Any], requested_room: str | None) -> None:
        self._end_action()
        # If the user changed rooms while an older request was in flight,
        # render the base snapshot but queue one request for the new room.
        if requested_room and requested_room != self.current_room_id:
            self._room_refresh_timer.start(0)
            return
        self.apply_dashboard(data)

    def _dashboard_failed(self, message: str) -> None:
        self._end_action()
        self._set_status(f"同步失败：{message}", error=True)

    def _dashboard_thread_finished(self, thread: SocialDashboardThread) -> None:
        if self._dashboard_thread is thread:
            self._dashboard_thread = None
        thread.deleteLater()

    def _refresh_selected_room(self) -> None:
        if not self.current_room_id or not self.client.signed_in:
            return
        self._start_dashboard_refresh(self.current_room_id, "正在同步当前自习室…")

    def _send_interaction(self, buddy: dict[str, Any], kind: str) -> None:
        if not self._require_login():
            return
        target = str(buddy.get("user_id") or buddy.get("id") or "")
        nickname = social_pet_label(buddy.get("nickname"))
        labels = {"poke": "戳了一下", "cheer": "送上加油", "drink": "递了一杯奶茶"}
        if not self.current_room_id:
            self._set_status("请先选择一个共同房间，再向房间成员互动。", error=True)
            return
        event = {"room_id": self.current_room_id, "kind": kind, "target_id": target, "message": ""}
        thread = SocialEventThread(self.client, event, self)
        self._event_threads.append(thread)
        thread.completed.connect(lambda: self._interaction_sent(nickname, kind))
        thread.failed.connect(lambda message: self._set_status(f"互动没有送出：{message}", error=True))
        thread.finished.connect(lambda: self._event_thread_finished(thread))
        self._set_status(f"正在向 {nickname} {labels.get(kind, '送出互动')}…")
        thread.start()

    def _send_phrase(self, phrase: str) -> None:
        """Send one short room phrase to a selected/first peer."""

        if not self._require_login() or not self.current_room_id:
            self._set_status("请先加入一个共同房间，再发送房间短语。", error=True)
            return
        people = getattr(self, "_room_people", [])
        target = next((person for person in people if not person.get("is_self")), None)
        if target is None:
            self._set_status("当前房间还没有可接收短语的搭子。", error=True)
            return
        nickname = str(target.get("nickname") or "搭子")
        event = {"room_id": self.current_room_id, "kind": "phrase", "target_id": str(target.get("user_id") or ""), "message": phrase[:80]}
        thread = SocialEventThread(self.client, event, self)
        self._event_threads.append(thread)
        thread.completed.connect(lambda: self._interaction_sent(nickname, "phrase"))
        thread.failed.connect(lambda message: self._set_status(f"短语没有送出：{message}", error=True))
        thread.finished.connect(lambda: self._event_thread_finished(thread))
        self._set_status(f"正在向 {nickname} 发送“{phrase}”…")
        thread.start()

    def _set_focus_task(self) -> None:
        title, ok = QInputDialog.getText(self, "一次只盯一件事", "目标：", text="完成当前最重要的一件事")
        if not ok or not title.strip():
            return
        minutes, ok = QInputDialog.getInt(self, "任务倒计时", "距离截止还有多少分钟（0 表示不倒计时）：", 60, 0, 7 * 24 * 60, 5)
        if not ok:
            return
        self.focus_task_requested.emit(title.strip()[:120], minutes)
        self._set_status("本轮任务已保存到本机，计时和倒计时会共用这一项目标。")

    def _set_tomorrow_review(self) -> None:
        title, ok = QInputDialog.getText(self, "轻量复盘", "明天打开时最先做什么？")
        if not ok:
            return
        self.tomorrow_review_requested.emit(title.strip()[:160])
        self._set_status("明天第一件事已记在本机。")

    def _set_room_schedule(self) -> None:
        if not self._require_login() or not self.current_room_id:
            return
        start, ok = QInputDialog.getText(self, "一起开工/收工", "开工时间（HH:MM）：", text="21:00")
        if not ok:
            return
        end, ok = QInputDialog.getText(self, "一起开工/收工", "收工时间（HH:MM）：", text="23:00")
        if not ok:
            return
        try:
            datetime.strptime(start.strip(), "%H:%M")
            datetime.strptime(end.strip(), "%H:%M")
        except ValueError:
            self._set_status("时间请填写成 HH:MM，例如 21:00。", error=True)
            return
        try:
            setter = getattr(self.client, "set_room_schedule", None)
            if callable(setter):
                setter(room_id=self.current_room_id, start_at=start.strip(), end_at=end.strip(), enabled=True)
            else:
                self.client.rpc("lili_set_room_schedule", {"p_room_id": self.current_room_id, "p_start_at": start.strip(), "p_end_at": end.strip(), "p_enabled": True})
            self._set_status(f"已设定 {start.strip()} 一起开工，{end.strip()} 一起收工。")
            self._refresh_selected_room()
        except SocialError as exc:
            self._error(exc)

    def _set_room_challenge(self) -> None:
        if not self._require_login() or not self.current_room_id:
            return
        title, ok = QInputDialog.getText(self, "共同挑战", "挑战名称：", text="今晚一起完成 4 小时")
        if not ok or not title.strip():
            return
        hours, ok = QInputDialog.getInt(self, "共同挑战", "共同专注小时数：", 4, 1, 72, 1)
        if not ok:
            return
        rounds, ok = QInputDialog.getInt(self, "共同挑战", "每位成员至少完成几轮：", 3, 1, 30, 1)
        if not ok:
            return
        try:
            setter = getattr(self.client, "set_room_challenge", None)
            if callable(setter):
                setter(room_id=self.current_room_id, title=title.strip()[:80], target_seconds=hours * 3600, target_rounds=rounds)
            else:
                self.client.rpc("lili_set_room_challenge", {"p_room_id": self.current_room_id, "p_title": title.strip()[:80], "p_target_seconds": hours * 3600, "p_target_rounds": rounds})
            self._set_status("共同挑战已保存，完成时会写入房间动态。")
            self._refresh_selected_room()
        except SocialError as exc:
            self._error(exc)

    def _interaction_sent(self, nickname: str, kind: str) -> None:
        labels = {"poke": "戳了一下", "cheer": "送上加油", "drink": "递了一杯奶茶", "phrase": "发送了快速短语"}
        self._set_status(f"{PET_NAME}已向 {nickname} {labels.get(kind, '送出互动')}；对方房间动态会显示这次互动。")
        QTimer.singleShot(0, self._refresh_selected_room)

    def _event_thread_finished(self, thread: SocialEventThread) -> None:
        if thread in self._event_threads:
            self._event_threads.remove(thread)
        thread.deleteLater()

    def _render_room_people(self, people: list[dict[str, Any]]) -> None:
        if not hasattr(self, "room_members"):
            return
        self._room_people = list(people)
        self.room_members.clear()
        for buddy in people:
            item = QListWidgetItem()
            widget = BuddyCardWidget(buddy, self.room_members)
            widget.interaction_requested.connect(self._send_interaction)
            widget.interaction_blocked.connect(lambda message: self._set_status(message, error=True))
            widget.subscription_requested.connect(self._set_subscription)
            item.setData(Qt.ItemDataRole.UserRole, buddy)
            self.room_members.addItem(item)
            self.room_members.setItemWidget(item, widget)
            self._set_buddy_item_height(item, widget)
        if not people:
            empty = QListWidgetItem("加入房间后，这里会显示一起专注的六毛和累计时长。")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.room_members.addItem(empty)
        self._fit_list_height(self.room_members, 46, 310)

    def _render_room_activity(self, entries: list[Any]) -> None:
        if not hasattr(self, "room_activity"):
            return
        self.room_activity.clear()
        for entry in entries[-8:]:
            if isinstance(entry, dict):
                created = str(entry.get("created_at") or "")
                stamp = created[11:16] if len(created) >= 16 and "T" in created else ""
                text = str(entry.get("text") or entry.get("message") or "")
                if not text:
                    actor = social_pet_label(entry.get("nickname") or entry.get("actor_nickname"))
                    target = entry.get("target_nickname")
                    target_text = f" → {social_pet_label(target)}" if target else ""
                    kind_text = {
                        "join": "进入房间", "leave": "离开房间", "focus_start": "开始专注",
                        "focus_pause": "暂停休息", "focus_finish": "完成一轮",
                        "poke": "戳了一下", "cheer": "送上加油", "drink": "递了一杯奶茶",
                        "phrase": "发送了快速短语", "challenge_complete": "完成了共同挑战",
                        "schedule_start": "一起开工", "schedule_end": "一起收工",
                        "goal_set": "设置了共同目标",
                    }.get(str(entry.get("kind")), "更新了状态")
                    text = f"{actor}{target_text} {kind_text}"
                if stamp:
                    text = f"{stamp}  {text}"
            else:
                text = str(entry)
            if text:
                self.room_activity.addItem(text)
        if self.room_activity.count() == 0:
            self.room_activity.addItem("房间动态会显示开始专注、完成一轮和六毛互动。")

    def _refresh_room_goal_text(self) -> None:
        if not hasattr(self, "room_goal"):
            return
        schedule = self._room_schedule_state
        if schedule:
            now_text = datetime.now().strftime("%H:%M")
            for key, label in (("start_at", "一起开工"), ("end_at", "一起收工")):
                marker = f"{key}:{now_text}"
                if str(schedule.get(key) or "") == now_text and marker != self._last_ritual_notice:
                    self._last_ritual_notice = marker
                    self.room_ritual_due.emit(label)
        goal = self._room_goal_state
        if not goal:
            self.room_goal.setText("尚未设置共同目标；房间成员可以在这里设定任务和倒计时。")
            return
        title = str(goal.get("title") or "一起专注")
        target = int(goal.get("target_seconds") or goal.get("target_minutes", 0) * 60)
        completed = int(goal.get("completed_seconds") or goal.get("current_seconds") or 0)
        due = str(goal.get("due_at") or "")
        remaining = ""
        if due:
            try:
                due_dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
                if due_dt.tzinfo is None:
                    due_dt = due_dt.astimezone()
                seconds = max(0, int((due_dt - datetime.now().astimezone()).total_seconds()))
                remaining = f" · 倒计时 {format_work_duration(seconds)}"
            except ValueError:
                pass
        progress = f"{format_work_duration(completed)} / {format_work_duration(target)}" if target else "共同进行中"
        self.room_goal.setText(f"共同任务：{title} · {progress}{remaining}")

    def _set_room_goal(self) -> None:
        if not self._require_login() or not self.current_room_id:
            return
        title, ok = QInputDialog.getText(self, "设置共同目标", "共同任务名称：", text="完成这一轮专注")
        if not ok or not title.strip():
            return
        minutes, ok = QInputDialog.getInt(self, "设置倒计时", "共同专注分钟数：", 50, 1, 24 * 60, 5)
        if not ok:
            return
        self._begin_action("正在保存共同任务…")
        try:
            due = datetime.now().astimezone().timestamp() + minutes * 60
            due_at = datetime.fromtimestamp(due).astimezone().isoformat()
            setter = getattr(self.client, "set_room_goal", None)
            if callable(setter):
                setter(room_id=self.current_room_id, title=title.strip()[:80], target_seconds=minutes * 60, due_at=due_at)
            else:
                self.client.rpc("lili_set_room_goal", {"p_room_id": self.current_room_id, "p_title": title.strip()[:80], "p_target_seconds": minutes * 60, "p_due_at": due_at})
            self._end_action()
            self._set_status("共同任务已更新，房间成员会看到同一个倒计时。")
            self._refresh_selected_room()
        except SocialError as exc:
            self._error(exc)

    def _leave_room(self) -> None:
        if not self._require_login() or not self.current_room_id:
            return
        room_id = self.current_room_id
        summary = self.data.get("room_summary") or (self.data.get("current_room") or {}).get("room_summary") or {}
        room_name = str((self.data.get("current_room") or {}).get("name") or "当前自习室")
        try:
            leaver = getattr(self.client, "leave_room", None)
            if callable(leaver):
                leaver(room_id=room_id)
            else:
                self.client.rpc("lili_leave_room", {"p_room_id": room_id})
            self.current_room_id = None
            self.room_changed.emit(None)
            self._set_status("已离开当前自习室，本次共同专注已保留在房间动态中。")
            if isinstance(summary, dict) and summary:
                QMessageBox.information(
                    self,
                    "本次自习室总结",
                    f"{room_name}\n\n"
                    f"共同专注：{format_work_duration(int(summary.get('shared_focus_seconds') or 0))}\n"
                    f"参与成员：{int(summary.get('member_count') or 0)} 人\n"
                    f"离开后可再次用房间码加入。",
                )
            self.refresh()
        except SocialError as exc:
            self._error(exc)

    def _mine_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(12)
        self.account_stack = QStackedWidget()
        self.account_stack.addWidget(self._auth_card())
        self.account_stack.addWidget(self._profile_card())
        layout.addWidget(self.account_stack)
        preview_card, preview_layout = self._card(
            "登录后可以做什么",
            "账号只用于搭子与私人自习室；聊天、计时、动作和离线陪伴不登录也能使用。",
        )
        preview_layout.addWidget(QLabel("• 添加搭子并查看在线状态\n• 创建私人专注房间\n• 接收串门邀请并一起计时"))
        layout.addWidget(preview_card)
        layout.addStretch()
        return self._scroll_page(page)

    def _auth_card(self) -> QWidget:
        card, layout = self._card(
            "账号",
            "邮箱只用于登录；密码不会保存在 Lili。网络暂时不可达时会显示最近状态，恢复后自动同步。",
        )
        auth_tabs = QTabWidget()
        login = QWidget(); login_layout = QVBoxLayout(login); login_form = QFormLayout()
        self.login_email = QLineEdit(); self.login_password = QLineEdit(); self.login_password.setEchoMode(QLineEdit.EchoMode.Password)
        login_form.addRow("邮箱", self.login_email); login_form.addRow("密码", self.login_password)
        login_layout.addLayout(login_form); login_button = QPushButton("登录")
        login_button.clicked.connect(self._login); login_layout.addWidget(login_button); login_layout.addStretch()
        register = QWidget(); register_layout = QVBoxLayout(register); register_form = QFormLayout()
        self.signup_nickname = QLineEdit(self.owner_nickname or "搭子"); self.signup_email = QLineEdit(); self.signup_password = QLineEdit(); self.signup_password.setEchoMode(QLineEdit.EchoMode.Password)
        register_form.addRow("主人称呼", self.signup_nickname); register_form.addRow("邮箱", self.signup_email); register_form.addRow("密码", self.signup_password)
        register_layout.addLayout(register_form); signup_button = QPushButton("注册")
        signup_button.clicked.connect(self._signup); register_layout.addWidget(signup_button); register_layout.addStretch()
        auth_tabs.addTab(login, "登录"); auth_tabs.addTab(register, "注册")
        layout.addWidget(auth_tabs)
        return card

    def _profile_card(self) -> QWidget:
        card, layout = self._card("我的账号", "管理搭子码、可见性和串门权限。")
        self.identity = QLabel(); self.identity.setStyleSheet("font-size:18px;font-weight:650;"); self.identity.setWordWrap(True)
        layout.addWidget(self.identity)
        self.hidden = QCheckBox("隐身")
        self.exact = QCheckBox("显示准确时长")
        self.visits_allowed = QCheckBox("允许搭子串门")
        layout.addWidget(self.hidden); layout.addWidget(self.exact); layout.addWidget(self.visits_allowed)
        save = QPushButton("保存隐私设置"); save.clicked.connect(self._save_profile); layout.addWidget(save)
        logout = QPushButton("退出账号"); logout.clicked.connect(self._logout); layout.addWidget(logout)
        layout.addStretch()
        return card

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self.status_label.setText(message)
        color = "#a33a3a" if error else "#087f74"
        background = "#f7e5e5" if error else "#e1efec"
        self.status_label.setStyleSheet(f"background:{background};color:{color};border-radius:9px;padding:7px 10px;")

    def _begin_action(self, message: str) -> None:
        self._set_status(message)
        if QApplication.overrideCursor() is None:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()

    @staticmethod
    def _end_action() -> None:
        if QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()

    def _require_login(self) -> bool:
        if self.client.signed_in:
            return True
        self.tabs.setCurrentIndex(3)
        self._set_status("请先在“我的”页面登录；其他离线功能仍可正常使用。", error=True)
        return False

    def _update_account_state(self) -> None:
        self.account_stack.setCurrentIndex(1 if self.client.signed_in else 0)
        if not self.client.signed_in:
            self._fill_signed_out_placeholders()

    def _fill_signed_out_placeholders(self) -> None:
        self.buddies.clear(); self.buddies.addItem("登录后，这里会显示搭子的在线与专注状态。")
        self.inbox.clear(); self.inbox.addItem("登录后可接收搭子申请与串门邀请。")
        self.rooms.clear(); self.rooms.addItem("登录后可创建或加入私人自习室。")
        self._fit_list_height(self.buddies, 46, 360)
        self._fit_list_height(self.rooms, 52, 140)
        if hasattr(self, "room_members"):
            self._render_room_people([])
        if hasattr(self, "room_activity"):
            self._render_room_activity([])

    def _error(self, exc: Exception) -> None:
        self._end_action()
        raw = str(exc)
        LOGGER.warning(
            "social room operation failed kind=%s endpoint=%s status=%s: %s",
            getattr(exc, "kind", "unknown"),
            getattr(exc, "endpoint", ""),
            getattr(exc, "status", None),
            raw,
        )
        message = "共同房间状态保存失败，请稍后重试。" if "ambiguous" in raw.lower() or "room_id" in raw.lower() else raw
        self._set_status(message, error=True)
        QMessageBox.warning(self, "六毛搭子自习室", message)

    def _signup(self) -> None:
        self._begin_action("正在创建账号…")
        try:
            signed = self.client.sign_up(self.signup_email.text(), self.signup_password.text(), self.signup_nickname.text())
            self._end_action()
            if signed:
                self._update_account_state(); self.refresh()
            else:
                self._set_status("注册成功，请到邮箱确认后回来登录。")
                QMessageBox.information(
                    self,
                    "请确认邮箱",
                    "注册成功。请到邮箱完成确认，然后回到这里登录。\n\n"
                    "确认页会打开六毛项目页面，不需要启动 localhost 服务。",
                )
        except SocialError as exc:
            self._error(exc)

    def _login(self) -> None:
        self._begin_action("正在登录搭子自习室…")
        try:
            self.client.sign_in(self.login_email.text(), self.login_password.text())
            self._end_action(); self._update_account_state(); self.tabs.setCurrentIndex(0); self.refresh()
        except SocialError as exc:
            self._error(exc)

    def _logout(self) -> None:
        self.client.sign_out(); self.data = {}; self._update_account_state(); self._set_status("已退出账号，六毛继续离线陪伴。")

    def refresh(self) -> None:
        if not self._require_login(): return
        self._start_dashboard_refresh(self.current_room_id, "正在刷新搭子与专注状态…")

    def apply_dashboard(self, data: dict[str, Any] | None) -> None:
        """Render a dashboard already fetched by the background sync thread.

        Heartbeats run off the UI thread.  Previously the completed payload was
        only consumed for visit notifications, leaving the visible room cards
        on the previous (often resting) state until the user clicked refresh.
        """

        previous_data = self.data
        self.data = dict(data or {})
        me=self.data.get("me") or {}
        me_presence = self.data.get("me_presence") or {}
        own_label = social_pet_label(self.owner_nickname or me.get("nickname"))
        self.identity.setText(f"{own_label} · 我的搭子码：{me.get('invite_code','--------')}")
        self.hidden.setChecked(me.get("visibility") == "hidden"); self.exact.setChecked(bool(me.get("show_exact_time",True))); self.visits_allowed.setChecked(bool(me.get("allow_visits",True)))
        self.buddies.clear()
        people=(self.data.get("buddies") or [])+(self.data.get("room_people") or [])
        seen=set()
        working_count = 0
        visible_total = 0
        for buddy in people:
            if buddy.get("user_id") in seen: continue
            seen.add(buddy.get("user_id"))
            if buddy.get("subscribed"):
                previous_buddies = {
                    str(item.get("user_id")): item
                    for item in (previous_data.get("buddies") or [])
                    if isinstance(item, dict)
                }
                previous = previous_buddies.get(str(buddy.get("user_id")))
                if previous is not None and bool(previous.get("working")) != bool(buddy.get("working")):
                    state_text = "开始专注" if buddy.get("working") else "结束专注"
                    self.buddy_subscription_notice.emit(f"{social_pet_label(buddy.get('nickname'))} {state_text}了。")
            working_count += int(bool(buddy.get("working")))
            duration = buddy.get("today_seconds")
            if duration is not None: visible_total += max(0, int(duration))
            item=QListWidgetItem(); item.setData(Qt.ItemDataRole.UserRole,buddy); self.buddies.addItem(item)
            buddy_widget = BuddyCardWidget(buddy, self.buddies)
            buddy_widget.interaction_requested.connect(self._send_interaction)
            buddy_widget.interaction_blocked.connect(lambda message: self._set_status(message, error=True))
            buddy_widget.subscription_requested.connect(self._set_subscription)
            self.buddies.setItemWidget(item, buddy_widget)
            self._set_buddy_item_height(item, buddy_widget)
        me_seconds = int(me_presence.get("today_seconds") or me.get("today_seconds") or 0)
        self.study_summary.setText(
            f"现在 {working_count} 位搭子正在专注　·　"
            f"我的今日专注 {format_work_duration(me_seconds)}　·　"
            f"房间可见合计 {format_work_duration(visible_total)}"
        )
        if not seen:
            empty = QListWidgetItem("还没有搭子。点击下方“用搭子码添加”，一起工作时这里会显示清楚的专注时长。")
            empty.setFlags(Qt.ItemFlag.NoItemFlags); self.buddies.addItem(empty)
        self._fit_list_height(self.buddies, 46, 360)
        self.inbox.clear()
        for request in self.data.get("requests") or []:
            item=QListWidgetItem(f"搭子申请：{request.get('nickname')}"); item.setData(Qt.ItemDataRole.UserRole,("buddy",request)); self.inbox.addItem(item)
        for visit in self.data.get("visits") or []:
            item=QListWidgetItem(f"串门邀请：{visit.get('nickname')}"); item.setData(Qt.ItemDataRole.UserRole,("visit",visit)); self.inbox.addItem(item)
        if self.inbox.count() == 0:
            empty = QListWidgetItem("当前没有待处理申请或串门，新的邀请会显示在这里。")
            empty.setFlags(Qt.ItemFlag.NoItemFlags); self.inbox.addItem(empty)
        rooms = list(self.data.get("rooms") or [])
        previous_room_id = self.current_room_id
        self._applying_dashboard = True
        # Rebuilding the list is an internal render operation.  Suppress the
        # transient "selection cleared" and "selection restored" signals;
        # otherwise each dashboard response schedules another network sync.
        self.rooms.blockSignals(True)
        self.rooms.clear()
        for room in rooms:
            room_item = QListWidgetItem(
                f"{room.get('name')} · {room.get('members')} 人 · 房间码 {room.get('invite_code')}"
            )
            room_item.setData(Qt.ItemDataRole.UserRole, room)
            self.rooms.addItem(room_item)
        if self.rooms.count() == 0:
            empty_room = QListWidgetItem("还没有私人自习室；创建后可把房间码发给搭子。")
            empty_room.setFlags(Qt.ItemFlag.NoItemFlags); self.rooms.addItem(empty_room)
            self.current_room_id = None
        else:
            # QCombo/List widgets do not consistently select the first item
            # after a clear() across Qt platforms.  Without a selected room
            # the next heartbeat used to send room_id=NULL, so the server had
            # no reliable way to associate this user's focus with a room.
            selected = -1
            for index, room in enumerate(rooms):
                room_id = self._room_id_from_payload(room)
                if room_id and room_id == previous_room_id:
                    selected = index
                    break
            if selected < 0:
                # On first open prefer the room with the most members.  This
                # avoids silently landing in an old one-person room when the
                # user has just joined a shared workroom.
                selected = max(
                    range(len(rooms)),
                    key=lambda index: int(rooms[index].get("members") or 0),
                )
            self.rooms.setCurrentRow(selected)
            selected_room = rooms[selected]
            self.current_room_id = self._room_id_from_payload(selected_room)
        self.rooms.blockSignals(False)
        self._fit_list_height(self.rooms, 52, 140)
        self._applying_dashboard = False
        if self.current_room_id != previous_room_id:
            self.room_changed.emit(self.current_room_id)
        # The room-scoped endpoint is authoritative for members and events.
        # Keep the legacy top-level fields as a compatibility fallback for
        # older proxy deployments and the offline UI tests.
        room_detail = self.data.get("current_room") or {}
        if not isinstance(room_detail, dict):
            room_detail = {}
        if self.current_room_id and self.current_room_id != previous_room_id and not room_detail:
            self._room_refresh_timer.start(0)
        room_people = list(room_detail.get("room_people") or self.data.get("room_people") or []) if self.current_room_id else []
        # Always render the local member as well.  The old SQL function only
        # returned peers, which made the room look like everybody was resting
        # when the local timer was the only state visible in the UI.
        local_status = self._focus_snapshot
        local_presence = dict(me_presence)
        if isinstance(local_status, dict):
            local_presence = {**local_presence, **local_status}
        elif local_status is not None:
            local_presence = {
                **local_presence,
                "status": getattr(local_status, "status", "idle"),
                "working": bool(getattr(local_status, "is_running", False)),
                "session_seconds": int(getattr(local_status, "session_seconds", 0)),
                "today_seconds": int(getattr(local_status, "today_seconds", 0)),
            }
        local_presence.update({
            "user_id": str(me.get("user_id") or me.get("id") or "me"),
            "nickname": self.owner_nickname or str(me.get("nickname") or "搭子"),
            "outfit_key": str(me_presence.get("outfit_key") or self.outfit_key or me.get("outfit_key") or ""),
            "online": True,
            "is_self": True,
        })
        if self.current_room_id:
            room_people = [local_presence] + [p for p in room_people if str(p.get("user_id")) != str(local_presence.get("user_id"))]
        else:
            room_people = []
        self._render_room_people(room_people)
        goal = room_detail.get("room_goal") or self.data.get("room_goal") or {}
        summary = room_detail.get("room_summary") or self.data.get("room_summary") or {}
        if isinstance(summary, dict) and summary:
            self.room_summary.setText(
                f"本房间 {int(summary.get('member_count') or len(room_people))} 人 · "
                f"{int(summary.get('focus_count') or 0)} 人正在专注 · "
                f"共同专注 {format_work_duration(int(summary.get('shared_focus_seconds') or 0))}"
            )
        elif hasattr(self, "room_summary"):
            self.room_summary.setText("你当前没有加入工作间。创建工作间或输入房间码加入后，这里才会显示共同状态。")
        self._room_goal_state = dict(goal) if isinstance(goal, dict) else {}
        schedule = room_detail.get("room_schedule") or self.data.get("room_schedule") or {}
        challenge = room_detail.get("room_challenge") or self.data.get("room_challenge") or {}
        self._room_schedule_state = dict(schedule) if isinstance(schedule, dict) else {}
        self._room_challenge_state = dict(challenge) if isinstance(challenge, dict) else {}
        self.room_goal_button.setEnabled(bool(self.current_room_id))
        if hasattr(self, "room_schedule_button"):
            self.room_schedule_button.setEnabled(bool(self.current_room_id))
        if hasattr(self, "room_challenge_button"):
            self.room_challenge_button.setEnabled(bool(self.current_room_id))
        self.room_leave_button.setEnabled(bool(self.current_room_id))
        self._refresh_room_goal_text()
        if hasattr(self, "room_ritual"):
            if self._room_schedule_state:
                self.room_ritual.setText(
                    f"共同开工/收工：{self._room_schedule_state.get('start_at', '--:--')} 开工 · "
                    f"{self._room_schedule_state.get('end_at', '--:--')} 收工"
                )
            else:
                self.room_ritual.setText("共同开工/收工：未设置")
        if hasattr(self, "room_challenge"):
            if self._room_challenge_state:
                self.room_challenge.setText(
                    f"共同挑战：{self._room_challenge_state.get('title', '一起完成')} · "
                    f"{format_work_duration(int(self._room_challenge_state.get('target_seconds') or 0))} · "
                    f"每人 {int(self._room_challenge_state.get('target_rounds') or 0)} 轮"
                )
            else:
                self.room_challenge.setText("共同挑战：未设置")
        activity = list(room_detail.get("room_activity") or self.data.get("room_activity") or self.data.get("activity") or [])
        me_id = str(me.get("user_id") or me.get("id") or "")
        for event in activity:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("id") or "")
            target_id = str(event.get("target_id") or "")
            if event_id and event_id not in self._seen_room_event_ids:
                self._seen_room_event_ids.add(event_id)
                is_target = target_id == me_id or (
                    not target_id and str(event.get("target_nickname") or "") == str(me.get("nickname") or "")
                )
                if me_id and is_target and str(event.get("actor_id") or "") != me_id:
                    self.room_event_received.emit(dict(event))
        self._render_room_activity(activity)
        active=self.data.get("active_visits") or []
        if active: self.active_visit.emit(active[0])
        if self.data.get("_sync_offline"):
            age = int(self.data.get("_sync_age_minutes") or 0)
            age_text = f"约 {age} 分钟前" if age else "刚才"
            self._set_status(
                f"当前无法连接自习室，已显示{age_text}的本地状态；网络恢复后会自动同步。"
            )
        else:
            self._set_status("已刷新，页面内容是最新的。")

    def _save_profile(self) -> None:
        if not self._require_login(): return
        self._begin_action("正在保存隐私设置…")
        try:
            me=self.data.get("me") or {}; self.client.update_profile(nickname=str(self.owner_nickname or me.get("nickname") or "搭子"),visibility="hidden" if self.hidden.isChecked() else "friends",show_exact_time=self.exact.isChecked(),allow_visits=self.visits_allowed.isChecked(),outfit_key=self.outfit_key); self.refresh()
        except SocialError as exc: self._error(exc)

    def _set_subscription(self, buddy: dict[str, Any], enabled: bool) -> None:
        if not self._require_login():
            return
        buddy_id = str(buddy.get("user_id") or buddy.get("id") or "")
        if not buddy_id:
            return
        try:
            setter = getattr(self.client, "set_buddy_subscription", None)
            if callable(setter):
                setter(buddy_id=buddy_id, on_focus_start=enabled, on_focus_end=enabled, muted=not enabled)
            else:
                self.client.rpc("lili_set_buddy_subscription", {"p_buddy_id": buddy_id, "p_on_focus_start": enabled, "p_on_focus_end": enabled, "p_muted": not enabled})
            self._set_status("搭子状态订阅已开启。" if enabled else "搭子状态订阅已关闭。")
        except SocialError as exc:
            self._error(exc)

    def _add_buddy(self) -> None:
        if not self._require_login(): return
        code,ok=QInputDialog.getText(self,"添加搭子","输入对方的 8 位搭子码：")
        if ok and code:
            self._begin_action("正在发送搭子申请…")
            try: self.client.rpc("lili_add_buddy_by_code",{"code":code}); self.refresh(); self._set_status("搭子申请已发送。")
            except SocialError as exc: self._error(exc)
    def _send_visit(self) -> None:
        if not self._require_login(): return
        item=self.buddies.currentItem()
        if not item: return self._error(SocialError("请先选择一位搭子。"))
        buddy = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(buddy, dict): return self._error(SocialError("请先选择一位搭子。"))
        self._begin_action("六毛正在准备出发…")
        try:
            self.client.rpc("lili_send_visit",{"target":buddy["user_id"],"visit_kind":"visit"}); self._end_action(); self._set_status("六毛已经出发，等待对方接受串门。"); QMessageBox.information(self,"已出发","六毛已经出发，等待对方接受串门。")
        except SocialError as exc: self._error(exc)
    def _accept_inbox(self) -> None:
        if not self._require_login(): return
        item=self.inbox.currentItem()
        if not item: return self._error(SocialError("请先选择一项申请或串门。"))
        kind,data=item.data(Qt.ItemDataRole.UserRole)
        self._begin_action("正在处理选中的申请…")
        try:
            if kind=="buddy": self.client.rpc("lili_respond_buddy",{"request_id":data["id"],"accept":True})
            else: self.client.rpc("lili_respond_visit",{"event_id":data["id"],"accept":True})
            self.refresh()
        except SocialError as exc: self._error(exc)
    def _create_room(self) -> None:
        if not self._require_login(): return
        name,ok=QInputDialog.getText(self,"创建自习室","自习室名称：",text="安静工作间")
        if ok and name:
            self._begin_action("正在创建自习室…")
            try: self.client.rpc("lili_create_room",{"room_name":name}); self.refresh(); self._set_status("自习室已创建，可以分享房间码了。")
            except SocialError as exc: self._error(exc)
    def _join_room(self) -> None:
        if not self._require_login(): return
        code,ok=QInputDialog.getText(self,"加入自习室","输入 8 位房间码：")
        if ok and code:
            self._begin_action("正在加入自习室…")
            try: self.client.rpc("lili_join_room",{"code":code}); self.refresh(); self._set_status("已加入自习室。")
            except SocialError as exc: self._error(exc)
