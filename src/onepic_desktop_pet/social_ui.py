"""搭子自习室界面、后台同步线程和双六毛本地串门窗口。"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QFontDatabase, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFormLayout, QFrame, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
)

from .resources import resource_path
from .accessories import SPECIAL_OUTFIT_SPRITES
from .social import SocialClient, SocialError
from .work_timer import format_work_duration


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
            self.completed.emit(self.client.dashboard())
        except SocialError as exc:
            self.failed.emit(str(exc))


class BuddyCardWidget(QWidget):
    """把搭子的在线、工作和今日时长显示成一眼能看清的卡片。"""

    def __init__(self, buddy: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("buddyCard")
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(3)
        online = bool(buddy.get("online"))
        working = bool(buddy.get("working"))
        nickname = str(buddy.get("nickname") or "搭子")
        headline = QLabel(
            f"{'🟢' if online else '⚪'}  {nickname} 的六毛"
            f"{'正在工作' if working else '正在休息'}"
        )
        headline.setStyleSheet("font-size:15px;font-weight:600;color:#203847;")
        root.addWidget(headline)
        duration = buddy.get("today_seconds")
        time_text = "今日专注时长已隐藏" if duration is None else f"已专注 {format_work_duration(duration)}"
        focus = QLabel(time_text)
        focus.setStyleSheet("font-size:18px;font-weight:700;color:#087f74;")
        root.addWidget(focus)
        outfit = str(buddy.get("outfit_key") or "经典六毛")
        footer = QLabel(f"当前娃衣：{outfit}　·　双击或选中后可派六毛串门")
        footer.setStyleSheet("color:#61727d;font-size:11px;")
        root.addWidget(footer)


class BuddyVisitWindow(QWidget):
    """完全由本地素材绘制的双六毛陪伴窗口。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setFont(_social_font())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
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
        close = QPushButton("结束这次串门"); close.clicked.connect(self.hide); layout.addWidget(close)
        self.elapsed = 0
        self._phase = 0
        self._mine_outfit = ""
        self._peer_outfit = ""
        self._mine_actions = ("02-office.png", "22-thermos.png", "04-guitar.png")
        self._peer_actions = ("09-night-reading.png", "03-headphones.png", "19-tea.png")
        self.timer = QTimer(self); self.timer.timeout.connect(self._tick); self.timer.start(1000)
        self.resize(520, 430)

    def show_peer(self, peer: dict[str, Any], mine_outfit: str = "", mine_today: int = 0) -> None:
        nickname = str(peer.get("nickname") or "搭子")
        self.title.setText(f"{nickname} 的六毛来串门了")
        self.subtitle.setText(f"💻 你的六毛　　{nickname} 的六毛 📖\n一起工作中")
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

    def __init__(self, client: SocialClient, outfit_key: str = "", parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.outfit_key = outfit_key
        self.data: dict[str, Any] = {}
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
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowTitle("六毛搭子自习室")
        self.resize(760, 760)
        self.setMinimumSize(680, 660)
        self.setStyleSheet("""
            QDialog { background:#edf4f7; }
            QLabel { color:#263746; }
            QLabel#pageTitle { font-size:24px; font-weight:700; }
            QLabel#sectionTitle { font-size:17px; font-weight:650; }
            QLabel#muted { color:#667984; }
            QLabel#status { background:#e1efec; color:#087f74; border-radius:9px; padding:7px 10px; }
            QFrame#card, QWidget#buddyCard { background:#ffffff; border:1px solid #d6e1e6; border-radius:14px; }
            QLineEdit, QListWidget { background:#ffffff; border:1px solid #b9c8d0; border-radius:10px; padding:7px; }
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
        self.tabs.addTab(self._home_page(), "首页")
        self.tabs.addTab(self._chat_page(), "聊天")
        self.tabs.addTab(self._focus_page(), "专注")
        self.tabs.addTab(self._mine_page(), "我的")
        root.addWidget(self.tabs, 1)
        self._update_account_state()
        if client.signed_in:
            QTimer.singleShot(50, self.refresh)

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
        layout.addWidget(welcome)
        buddies_card, buddies_layout = self._card("我的搭子", "绿色表示两分钟内在线；选择后可到“聊天”页派六毛串门。")
        self.buddies = QListWidget(); self.buddies.setSpacing(5)
        self.buddies.itemDoubleClicked.connect(lambda _item: self._send_visit())
        buddies_layout.addWidget(self.buddies, 1)
        layout.addWidget(buddies_card, 1)
        return page

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
        self.inbox = QListWidget(); inbox_layout.addWidget(self.inbox, 1)
        accept = QPushButton("接受选中的项目"); accept.clicked.connect(self._accept_inbox); inbox_layout.addWidget(accept)
        layout.addWidget(inbox_card, 1)
        return page

    def _focus_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(12)
        focus_card, focus_layout = self._card("共同专注", "私人房间只同步最小在线和时长状态，不同步任务名称或桌面内容。")
        preview = QLabel("功能预览：创建房间 · 分享 8 位房间码 · 查看共同专注人数 · 六毛串门")
        preview.setWordWrap(True); preview.setObjectName("muted"); focus_layout.addWidget(preview)
        layout.addWidget(focus_card)
        room_card, room_layout = self._card("私人自习室")
        self.rooms = QListWidget(); room_layout.addWidget(self.rooms, 1)
        row = QHBoxLayout(); create = QPushButton("创建自习室"); join = QPushButton("使用房间码加入")
        create.clicked.connect(self._create_room); join.clicked.connect(self._join_room)
        row.addWidget(create); row.addWidget(join); room_layout.addLayout(row)
        layout.addWidget(room_card, 1)
        return page

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
        return page

    def _auth_card(self) -> QWidget:
        card, layout = self._card("账号", "邮箱只用于登录；密码不会保存在 Lili。")
        auth_tabs = QTabWidget()
        login = QWidget(); login_layout = QVBoxLayout(login); login_form = QFormLayout()
        self.login_email = QLineEdit(); self.login_password = QLineEdit(); self.login_password.setEchoMode(QLineEdit.EchoMode.Password)
        login_form.addRow("邮箱", self.login_email); login_form.addRow("密码", self.login_password)
        login_layout.addLayout(login_form); login_button = QPushButton("登录")
        login_button.clicked.connect(self._login); login_layout.addWidget(login_button); login_layout.addStretch()
        register = QWidget(); register_layout = QVBoxLayout(register); register_form = QFormLayout()
        self.signup_nickname = QLineEdit("六毛搭子"); self.signup_email = QLineEdit(); self.signup_password = QLineEdit(); self.signup_password.setEchoMode(QLineEdit.EchoMode.Password)
        register_form.addRow("昵称", self.signup_nickname); register_form.addRow("邮箱", self.signup_email); register_form.addRow("密码", self.signup_password)
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

    def _error(self, exc: Exception) -> None:
        self._end_action()
        self._set_status(str(exc), error=True)
        QMessageBox.warning(self, "六毛搭子自习室", str(exc))

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
        self._begin_action("正在刷新搭子与专注状态…")
        try: self.data=self.client.dashboard()
        except SocialError as exc: self._error(exc); return
        self._end_action()
        me=self.data.get("me") or {}; self.identity.setText(f"{me.get('nickname','六毛搭子')} · 我的搭子码：{me.get('invite_code','--------')}")
        self.hidden.setChecked(me.get("visibility") == "hidden"); self.exact.setChecked(bool(me.get("show_exact_time",True))); self.visits_allowed.setChecked(bool(me.get("allow_visits",True)))
        self.buddies.clear()
        people=(self.data.get("buddies") or [])+(self.data.get("room_people") or [])
        seen=set()
        working_count = 0
        visible_total = 0
        for buddy in people:
            if buddy.get("user_id") in seen: continue
            seen.add(buddy.get("user_id"))
            working_count += int(bool(buddy.get("working")))
            duration = buddy.get("today_seconds")
            if duration is not None: visible_total += max(0, int(duration))
            item=QListWidgetItem(); item.setSizeHint(QSize(0, 92)); item.setData(Qt.ItemDataRole.UserRole,buddy); self.buddies.addItem(item)
            self.buddies.setItemWidget(item, BuddyCardWidget(buddy, self.buddies))
        self.study_summary.setText(f"现在 {working_count} 位搭子正在专注　·　可见今日合计 {format_work_duration(visible_total)}")
        if not seen:
            empty = QListWidgetItem("还没有搭子。点击下方“用搭子码添加”，一起工作时这里会显示清楚的专注时长。")
            empty.setFlags(Qt.ItemFlag.NoItemFlags); self.buddies.addItem(empty)
        self.inbox.clear()
        for request in self.data.get("requests") or []:
            item=QListWidgetItem(f"搭子申请：{request.get('nickname')}"); item.setData(Qt.ItemDataRole.UserRole,("buddy",request)); self.inbox.addItem(item)
        for visit in self.data.get("visits") or []:
            item=QListWidgetItem(f"串门邀请：{visit.get('nickname')}"); item.setData(Qt.ItemDataRole.UserRole,("visit",visit)); self.inbox.addItem(item)
        if self.inbox.count() == 0:
            empty = QListWidgetItem("当前没有待处理申请或串门，新的邀请会显示在这里。")
            empty.setFlags(Qt.ItemFlag.NoItemFlags); self.inbox.addItem(empty)
        self.rooms.clear()
        for room in self.data.get("rooms") or []:
            self.rooms.addItem(f"{room.get('name')} · {room.get('members')} 人 · 房间码 {room.get('invite_code')}")
        if self.rooms.count() == 0:
            empty_room = QListWidgetItem("还没有私人自习室；创建后可把房间码发给搭子。")
            empty_room.setFlags(Qt.ItemFlag.NoItemFlags); self.rooms.addItem(empty_room)
        active=self.data.get("active_visits") or []
        if active: self.active_visit.emit(active[0])
        self._set_status("已刷新，页面内容是最新的。")

    def _save_profile(self) -> None:
        if not self._require_login(): return
        self._begin_action("正在保存隐私设置…")
        try:
            me=self.data.get("me") or {}; self.client.update_profile(nickname=str(me.get("nickname") or "六毛搭子"),visibility="hidden" if self.hidden.isChecked() else "friends",show_exact_time=self.exact.isChecked(),allow_visits=self.visits_allowed.isChecked(),outfit_key=self.outfit_key); self.refresh()
        except SocialError as exc: self._error(exc)
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
