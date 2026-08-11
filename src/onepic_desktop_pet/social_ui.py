"""搭子自习室界面、后台同步线程和双六毛本地串门窗口。"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QFontDatabase, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from .resources import resource_path
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


class BuddyVisitWindow(QWidget):
    """完全由本地素材绘制的双六毛陪伴窗口。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setFont(_social_font())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("QWidget#card{background:rgba(238,246,249,220);border:1px solid rgba(90,110,120,80);border-radius:20px;} QLabel{color:#263746;font-family:'Microsoft YaHei UI','PingFang SC';}")
        card = QWidget(self); card.setObjectName("card")
        root = QVBoxLayout(self); root.addWidget(card); layout = QVBoxLayout(card)
        pets = QHBoxLayout(); self.mine = QLabel(); self.peer = QLabel()
        for label, name in ((self.mine,"02-office.png"),(self.peer,"09-night-reading.png")):
            pix = QPixmap(str(resource_path(f"assets/pet/daily-actions/{name}"))).scaled(190,190,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
            label.setPixmap(pix); pets.addWidget(label)
        layout.addLayout(pets)
        self.title = QLabel("两只六毛一起工作中"); self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clock = QLabel("00:00:00"); self.clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title); layout.addWidget(self.clock)
        close = QPushButton("结束这次串门"); close.clicked.connect(self.hide); layout.addWidget(close)
        self.elapsed = 0; self.timer = QTimer(self); self.timer.timeout.connect(self._tick); self.timer.start(1000)
        self.resize(440, 290)

    def show_peer(self, peer: dict[str, Any]) -> None:
        self.title.setText(f"你和 {peer.get('nickname','搭子')} 的六毛一起工作中")
        self.elapsed = 0
        started = peer.get("session_started_at")
        if started:
            try:
                self.elapsed = max(0, int((datetime.now().astimezone() - datetime.fromisoformat(str(started))).total_seconds()))
            except ValueError:
                self.elapsed = 0
        self._tick(); self.show(); self.raise_()

    def _tick(self) -> None:
        if self.isVisible(): self.elapsed += 1
        h, rest = divmod(self.elapsed, 3600); m, s = divmod(rest, 60)
        self.clock.setText(f"{h:02d}:{m:02d}:{s:02d}")


class SocialHubDialog(QDialog):
    active_visit = Signal(dict)

    def __init__(self, client: SocialClient, outfit_key: str = "", parent=None) -> None:
        super().__init__(parent); self.client = client; self.outfit_key = outfit_key; self.data: dict[str, Any] = {}
        self.setFont(_social_font())
        self.setWindowTitle("六毛搭子自习室"); self.resize(610, 680)
        self.setStyleSheet("QDialog{background:#edf4f7;} QLineEdit,QListWidget{background:rgba(255,255,255,220);border:1px solid #b9c8d0;border-radius:8px;padding:6px;} QPushButton{padding:7px 12px;border-radius:10px;background:#d7ece8;} QLabel{color:#263746;}")
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
        layout.addWidget(QLabel("我的搭子（绿色表示两分钟内在线）")); self.buddies=QListWidget(); layout.addWidget(self.buddies)
        row=QHBoxLayout(); add=QPushButton("用搭子码添加"); visit=QPushButton("派六毛去串门"); row.addWidget(add); row.addWidget(visit); layout.addLayout(row)
        add.clicked.connect(self._add_buddy); visit.clicked.connect(self._send_visit)
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
        for buddy in people:
            if buddy.get("user_id") in seen: continue
            seen.add(buddy.get("user_id")); dot="🟢" if buddy.get("online") else "⚪"
            status="正在工作" if buddy.get("working") else "休息中"; duration=buddy.get("today_seconds")
            text=f"{dot} {buddy.get('nickname')} · {status}" + (f" · 今日 {format_work_duration(duration)}" if duration is not None else "")
            item=QListWidgetItem(text); item.setData(Qt.ItemDataRole.UserRole,buddy); self.buddies.addItem(item)
        self.inbox.clear()
        for request in self.data.get("requests") or []:
            item=QListWidgetItem(f"搭子申请：{request.get('nickname')}"); item.setData(Qt.ItemDataRole.UserRole,("buddy",request)); self.inbox.addItem(item)
        for visit in self.data.get("visits") or []:
            item=QListWidgetItem(f"串门邀请：{visit.get('nickname')}"); item.setData(Qt.ItemDataRole.UserRole,("visit",visit)); self.inbox.addItem(item)
        self.rooms.clear()
        for room in self.data.get("rooms") or []:
            self.rooms.addItem(f"{room.get('name')} · {room.get('members')} 人 · 房间码 {room.get('invite_code')}")
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
        try: self.client.rpc("lili_send_visit",{"target":item.data(Qt.ItemDataRole.UserRole)["user_id"],"visit_kind":"visit"}); QMessageBox.information(self,"已出发","六毛已经出发，等待对方接受串门。")
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
