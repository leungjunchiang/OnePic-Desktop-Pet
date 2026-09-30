"""本人一次开放训导范围，搭子直接监督；服务端版本保护跨设备授权。"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QCheckBox, QComboBox, QListWidget, QListWidgetItem, QPushButton

from .buddy_identity import buddy_choice, buddy_name


class SupervisionPolicyWidget(QWidget):
    def __init__(self, engine_provider, buddy_provider, rpc_executor, room_open, parent=None):
        super().__init__(parent)
        self.engine_provider = engine_provider
        self.buddy_provider = buddy_provider
        self.rpc_executor = rpc_executor
        self.room_open = room_open
        self.account_id = engine_provider().store.account_id
        self.revision = None
        self.dirty = False
        self.pending = False
        self.generation = 0
        self._policy = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 10)
        root.addWidget(QLabel("👨‍🏫 谁可以训导我"))
        self.enabled = QCheckBox("允许搭子训导我")
        self.enabled.clicked.connect(self._toggle_enabled)
        self.enabled.setEnabled(False)
        root.addWidget(self.enabled)
        text = QLabel("开启后，范围内的搭子可直接开始普通监督；军官监督需另行开放。关闭后暂停所有搭子监督，保留历史记录。")
        text.setWordWrap(True); root.addWidget(text)
        self.scope = QComboBox()
        for label, value in (("我指定的搭子", "selected"), ("我的所有搭子", "all"), ("只有我邀请的人", "invited")):
            self.scope.addItem(label, value)
        self.scope.activated.connect(self._changed)
        root.addWidget(self.scope)
        self.selected = QListWidget(); self.selected.setMaximumHeight(110)
        self.selected.itemChanged.connect(self._changed); root.addWidget(self.selected)
        self.invited_hint = QLabel(); self.invited_hint.setWordWrap(True); root.addWidget(self.invited_hint)
        self.invitee = QComboBox(); self.invitee.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.invitee.setMinimumContentsLength(24)
        invite_row = QHBoxLayout(); invite_row.addWidget(self.invitee, 1)
        self.invite = QPushButton("邀请 TA 来管我")
        self.invite.clicked.connect(lambda: self._invite(True)); invite_row.addWidget(self.invite)
        self.uninvite = QPushButton("取消邀请")
        self.uninvite.clicked.connect(lambda: self._invite(False)); invite_row.addWidget(self.uninvite)
        root.addLayout(invite_row)
        root.addWidget(QLabel("允许查看与提醒"))
        self.permissions = {}
        permission_grid = QGridLayout()
        for index, (key, label) in enumerate((("remind", "催我开工 / 提醒休息和下班"), ("view_plan", "我的工作计划"),
                           ("view_progress", "今日 / 本周进度"), ("view_lateness", "迟到与开工时间"),
                           ("view_rest", "休息情况"), ("view_reports", "纪律日报"))):
            check = QCheckBox(label); check.clicked.connect(self._changed)
            self.permissions[key] = check; permission_grid.addWidget(check, index // 2, index % 2)
        root.addLayout(permission_grid)
        root.addWidget(QLabel("军官模式资格（仍须符合上方训导范围）"))
        self.officer_scope = QComboBox()
        self.officer_scope.addItem("仅指定搭子", "selected"); self.officer_scope.addItem("所有范围内搭子", "all")
        self.officer_scope.activated.connect(self._changed); root.addWidget(self.officer_scope)
        self.officers = QListWidget(); self.officers.setMaximumHeight(110)
        self.officers.itemChanged.connect(self._changed); root.addWidget(self.officers)
        row = QHBoxLayout()
        self.save = QPushButton("保存训导范围"); self.save.clicked.connect(self._save)
        self.reload = QPushButton("重新读取授权"); self.reload.clicked.connect(self._reload)
        row.addWidget(self.reload); row.addWidget(self.save); root.addLayout(row)
        self.status = QLabel("登录并打开此页后读取授权。")
        self.status.setWordWrap(True); root.addWidget(self.status)
        root.addWidget(QLabel("我正在训导"))
        self.supervising = QListWidget(); self.supervising.setMaximumHeight(120)
        self.supervising.itemDoubleClicked.connect(self._open_room); root.addWidget(self.supervising)
        open_room = QPushButton("进入所选搭子的自习室")
        open_room.clicked.connect(lambda: self._open_room(self.supervising.currentItem()))
        root.addWidget(open_room)
        self._set_controls(False)
        self.timer = QTimer(self); self.timer.setInterval(10000)
        self.timer.timeout.connect(self.refresh); self.timer.start()

    def _set_controls(self, ready):
        for widget in (self.enabled, self.scope, self.selected, self.invitee, self.invite, self.uninvite,
                       self.officer_scope, self.officers, self.save, *self.permissions.values()):
            widget.setEnabled(ready)

    def _changed(self, *_):
        self.dirty = True
        self.selected.setVisible(self.scope.currentData() == "selected")
        self.officers.setVisible(self.officer_scope.currentData() == "selected")
        for widget in (self.invited_hint, self.invitee, self.invite, self.uninvite):
            widget.setVisible(self.scope.currentData() == "invited")

    def _rpc(self, name, body, callback):
        account = self.engine_provider().store.account_id
        generation = self.generation
        self.pending = True
        self._set_controls(False)
        def done(payload):
            if account != self.engine_provider().store.account_id or generation != self.generation:
                return
            self.pending = False
            callback(payload)
        def failed(error):
            if account != self.engine_provider().store.account_id or generation != self.generation:
                return
            self.pending = False
            self._set_controls(self.revision is not None)
            self.enabled.setChecked(bool(self._policy.get("enabled")))
            self.status.setText(str(error)[:300] + "\n可重新读取授权后重试。")
        self.rpc_executor(name, body, done, failed)

    def refresh(self):
        current = self.engine_provider().store.account_id
        if current != self.account_id:
            self.generation += 1
            self.account_id = current
            self.revision = None; self.dirty = False; self.pending = False
            self._policy = {}
            self.selected.clear(); self.officers.clear(); self.supervising.clear()
            self.enabled.setChecked(False); self._set_controls(False)
        if self.isVisible() and not self.pending and not self.dirty:
            self._rpc("lili_supervision_snapshot", {}, self._apply)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def _apply(self, data):
        if not isinstance(data, dict) or not isinstance(data.get("policy"), dict):
            self.status.setText("未能读取授权，请重新读取。")
            return
        p = data["policy"]
        if self.account_id != self.engine_provider().store.account_id:
            return
        self._policy = p
        self.revision = int(p.get("revision", 0))
        self.engine_provider().apply_supervision(data)
        self.enabled.setChecked(bool(p.get("enabled")))
        self.scope.setCurrentIndex(max(0, self.scope.findData(p.get("scope", "selected"))))
        self.officer_scope.setCurrentIndex(max(0, self.officer_scope.findData(p.get("officer_scope", "selected"))))
        buddies = [b for b in self.buddy_provider() if isinstance(b, dict)]
        by_id = {str(b.get("user_id") or b.get("id")): b for b in buddies}
        previous = self.invitee.currentData()
        self.invitee.clear()
        for b in buddies:
            self.invitee.addItem(buddy_choice(b), str(b.get("user_id") or b.get("id")))
        self.invitee.setCurrentIndex(max(0, self.invitee.findData(previous)))
        for listing, field in ((self.selected, "selected_ids"), (self.officers, "officer_ids")):
            listing.blockSignals(True); listing.clear()
            for identifier, buddy in by_id.items():
                item = QListWidgetItem(buddy_choice(buddy)); item.setData(Qt.ItemDataRole.UserRole, identifier)
                item.setToolTip(identifier)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if identifier in p.get(field, []) else Qt.CheckState.Unchecked)
                listing.addItem(item)
            listing.blockSignals(False)
            listing.setFixedHeight(min(110, max(42, listing.count() * 23 + 12)))
        self.invited_hint.setText("已邀请：" + ("、".join(buddy_name(by_id.get(i, {"user_id": i})) for i in p.get("invited_ids", [])) or "暂无"))
        for key, check in self.permissions.items():
            check.setChecked(bool(p.get(key, True)))
        self.supervising.clear()
        for row in data.get("supervising", []):
            identifier = row.get("owner_id")
            item = QListWidgetItem(f"{buddy_name(by_id.get(identifier, {'user_id': identifier}))} · {'军官模式' if row.get('mode') == 'officer' else '正常模式'}")
            item.setData(Qt.ItemDataRole.UserRole, by_id.get(identifier, {"user_id": identifier}))
            self.supervising.addItem(item)
        self._changed(); self.dirty = False
        self._set_controls(True)
        supervisors = data.get("supervisors", [])
        names = "、".join(buddy_name(by_id.get(row.get("supervisor_id"), {"user_id": row.get("supervisor_id")})) for row in supervisors)
        self.status.setText(("已开启，范围内搭子无需再次申请。" + ("\n正在训导我：" + names if names else "\n暂无搭子正在监督我。"))
                            if p.get("enabled") else "搭子训导已关闭。历史记录保留。")

    @staticmethod
    def _checked(listing):
        return [listing.item(i).data(Qt.ItemDataRole.UserRole) for i in range(listing.count())
                if listing.item(i).checkState() == Qt.CheckState.Checked]

    def _save(self):
        if self.revision is None or self.account_id != self.engine_provider().store.account_id:
            self.refresh(); return
        policy = {"enabled": self.enabled.isChecked(), "scope": self.scope.currentData(),
                  "selected_ids": self._checked(self.selected), "officer_scope": self.officer_scope.currentData(),
                  "officer_ids": self._checked(self.officers),
                  **{key: check.isChecked() for key, check in self.permissions.items()}}
        self._rpc("lili_set_supervision_policy", {"p_policy": policy, "p_expected_revision": self.revision}, self._apply)

    def _toggle_enabled(self):
        self._changed()
        self._save()

    def _reload(self):
        self.dirty = False
        self.refresh()

    def _invite(self, enabled):
        identifier = self.invitee.currentData()
        if identifier and not self.dirty:
            self._rpc("lili_invite_supervisor", {"p_buddy_id": identifier, "p_enabled": enabled}, self._apply)
        elif self.dirty:
            self.status.setText("先保存训导范围，再邀请搭子。")

    def _open_room(self, item):
        if item:
            self.room_open(item.data(Qt.ItemDataRole.UserRole), 3)
