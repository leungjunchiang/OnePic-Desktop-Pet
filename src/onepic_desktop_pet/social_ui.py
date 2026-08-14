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
        self.setModal(False…10532 tokens truncated…def _end_action() -> None:
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
