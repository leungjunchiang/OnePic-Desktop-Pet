"""搭子自习室界面、后台同步线程和双六毛本地串门窗口。"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QFontDatabase, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
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
        self._tick(); self.show(); self.raise_(); self.activateWindow()

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
    active_visit = Signal(dict)

    def __init__(self, client: SocialClient, outfit_key: str = "", parent=None) -> None:
        super().__init__(parent); self.client = client; self.outfit_key = outfit_key; self.data: dict[str, Any] = {}
        self.setFont(_social_font())
        self.setWindowTitle("六毛搭子自习室"); self.resize(720, 820)
        self.setStyleSheet("QDialog{background:#edf4f7;} QLineEdit,QListWidget{background:rgba(255,255,255,225);border:1px solid #b9c8d0;border-radius:10px;padding:6px;} QWidget#buddyCard{background:#ffffff;border-radius:10px;} QPushButton{padding:8px 12px;border-radius:10px;background:#d7ece8;} QLabel{color:#263746;}")
        root = QVBoxLayout(self); self.stack = QStackedWidget(); root.addWidget(self.stack)
        self.stack.addWidget(self._login_page()); self.stack.addWidget(self._hub_page())
        self.stack.setCurrentIndex(1 if client.signed_in else 0)
        if client.signed_in: QTimer.singleShot(50, self.refresh)

    def _login_page(self) -> QWidget:
        page=QWidget(); layout=QVBoxLayout(page)
        title=QLabel("六毛搭子自习室"); title.setStyleSheet("font-size:22px;font-weight:600;"); layout.addWidget(title)
        layout.addWidget(QLabel("邮箱只用于登录；密码不会保存在 Lili。任务、聊天和文档不会上传。"))
        form=QFormLayout(); self.nickname=QLineEdit("六毛搭子"); self.email=QLineEdit(); self.password=QLineEdit(); self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("昵称",self.nickname); form.addRow("邮箱",self.email); form.addRow("密码",self.password); layout.addLayout(form)
        buttons=QHBoxLayout(); signup=QPushButton("注册"); login=QPushButton("登录"); buttons.addWidget(signup); buttons.addWidget(login); layout.addLayout(buttons)
        signup.clicked.connect(self._signup); login.clicked.connect(self._login); layout.addStretch(); return page

    def _hub_page(self) -> QWidget:
        page=QWidget(); layout=QVBoxLayout(page)
        self.identity=QLabel(); self.identity.setStyleSheet("font-size:18px;font-weight:600;"); layout.addWidget(self.identity)
        privacy=QHBoxLayout(); self.hidden=QCheckBox("隐身"); self.exact=QCheckBox("显示准确时长"); self.visits_allowed=QCheckBox("允许搭子串门"); privacy.addWidget(self.hidden); privacy.addWidget(self.exact); privacy.addWidget(self.visits_allowed); layout.addLayout(privacy)
        save=QPushButton("保存隐私设置"); save.clicked.connect(self._save_profile); layout.addWidget(save)
        self.study_summary = QLabel("正在读取搭子专注时间…"); self.study_summary.setStyleSheet("font-size:16px;font-weight:600;color:#087f74;"); layout.addWidget(self.study_summary)
        layout.addWidget(QLabel("我的搭子（绿色表示两分钟内在线）")); self.buddies=QListWidget(); self.buddies.setSpacing(5); layout.addWidget(self.buddies)
        row=QHBoxLayout(); add=QPushButton("用搭子码添加"); visit=QPushButton("派六毛去串门"); row.addWidget(add); row.addWidget(visit); layout.addLayout(row)
        add.clicked.connect(self._add_buddy); visit.clicked.connect(self._send_visit)
        self.buddies.itemDoubleClicked.connect(lambda _item: self._send_visit())
        layout.addWidget(QLabel("待处理申请 / 串门")); self.inbox=QListWidget(); layout.addWidget(self.inbox)
        accept=QPushButton("接受选中的申请或串门"); accept.clicked.connect(self._accept_inbox); layout.addWidget(accept)
        layout.addWidget(QLabel("私人自习室")); self.rooms=QListWidget(); layout.addWidget(self.rooms)
        roomrow=QHBoxLayout(); create=QPushButton("创建自习室"); join=QPushButton("用房间码加入"); roomrow.addWidget(create); roomrow.addWidget(join); layout.addLayout(roomrow)
        create.clicked.connect(self._create_room); join.clicked.connect(self._join_room)
        bottom=QHBoxLayout(); refresh=QPushButton("刷新"); logout=QPushButton("退出账号"); bottom.addWidget(refresh); bottom.addWidget(logout); layout.addLayout(bottom)
        refresh.clicked.connect(self.refresh); logout.clicked.connect(self._logout); return page

    def _error(self, exc: Exception) -> None: QMessageBox.warning(self,"六毛搭子自习室",str(exc))
    def _signup(self) -> None:
        try:
            signed=self.client.sign_up(self.email.text(),self.password.text(),self.nickname.text())
            if signed: self.stack.setCurrentIndex(1); self.refresh()
            else: QMessageBox.information(self,"请确认邮箱","注册成功。请到邮箱完成确认，然后回到这里登录。")
        except SocialError as exc: self._error(exc)
    def _login(self) -> None:
        try: self.client.sign_in(self.email.text(),self.password.text()); self.stack.setCurrentIndex(1); self.refresh()
        except SocialError as exc: self._error(exc)
    def _logout(self) -> None: self.client.sign_out(); self.stack.setCurrentIndex(0)

    def refresh(self) -> None:
        try: self.data=self.client.dashboard()
        except SocialError as exc: self._error(exc); return
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
        self.rooms.clear()
        for room in self.data.get("rooms") or []:
            self.rooms.addItem(f"{room.get('name')} · {room.get('members')} 人 · 房间码 {room.get('invite_code')}")
        if self.rooms.count() == 0:
            empty_room = QListWidgetItem("还没有私人自习室；创建后可把房间码发给搭子。")
            empty_room.setFlags(Qt.ItemFlag.NoItemFlags); self.rooms.addItem(empty_room)
        active=self.data.get("active_visits") or []
        if active: self.active_visit.emit(active[0])

    def _save_profile(self) -> None:
        try:
            me=self.data.get("me") or {}; self.client.update_profile(nickname=str(me.get("nickname") or "六毛搭子"),visibility="hidden" if self.hidden.isChecked() else "friends",show_exact_time=self.exact.isChecked(),allow_visits=self.visits_allowed.isChecked(),outfit_key=self.outfit_key); self.refresh()
        except SocialError as exc: self._error(exc)
    def _add_buddy(self) -> None:
        code,ok=QInputDialog.getText(self,"添加搭子","输入对方的 8 位搭子码：")
        if ok and code:
            try: self.client.rpc("lili_add_buddy_by_code",{"code":code}); self.refresh()
            except SocialError as exc: self._error(exc)
    def _send_visit(self) -> None:
        item=self.buddies.currentItem()
        if not item: return self._error(SocialError("请先选择一位搭子。"))
        buddy = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(buddy, dict): return self._error(SocialError("请先选择一位搭子。"))
        try: self.client.rpc("lili_send_visit",{"target":buddy["user_id"],"visit_kind":"visit"}); QMessageBox.information(self,"已出发","六毛已经出发，等待对方接受串门。")
        except SocialError as exc: self._error(exc)
    def _accept_inbox(self) -> None:
        item=self.inbox.currentItem()
        if not item: return
        kind,data=item.data(Qt.ItemDataRole.UserRole)
        try:
            if kind=="buddy": self.client.rpc("lili_respond_buddy",{"request_id":data["id"],"accept":True})
            else: self.client.rpc("lili_respond_visit",{"event_id":data["id"],"accept":True})
            self.refresh()
        except SocialError as exc: self._error(exc)
    def _create_room(self) -> None:
        name,ok=QInputDialog.getText(self,"创建自习室","自习室名称：",text="安静工作间")
        if ok and name:
            try: self.client.rpc("lili_create_room",{"room_name":name}); self.refresh()
            except SocialError as exc: self._error(exc)
    def _join_room(self) -> None:
        code,ok=QInputDialog.getText(self,"加入自习室","输入 8 位房间码：")
        if ok and code:
            try: self.client.rpc("lili_join_room",{"code":code}); self.refresh()
            except SocialError as exc: self._error(exc)
