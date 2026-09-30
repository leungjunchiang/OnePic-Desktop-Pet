"""具体搭子的自习室：公开状态、共同专注、授权计划、监督关系与纪律摘要。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QPushButton,
    QTabWidget, QVBoxLayout, QWidget, QScrollArea,
)

from .discipline import DisciplineSettings, as_beijing
from .work_timer import format_work_duration


class BuddyStudyDialog(QDialog):
    """授权数据每次向服务端读取，旧账号、关闭后的回调均不显示。"""

    def __init__(self, hub, buddy):
        super().__init__(hub)
        from .social import _session_user_id
        from .social_ui import _buddy_identifier, _owner_label
        self.hub = hub
        self.buddy = dict(buddy)
        self.buddy_id = _buddy_identifier(buddy)
        self.account_id = _session_user_id(hub.client)
        self._generation = 0
        self._request_pending = False
        self._permissions_dirty = False
        self._overview = {}
        self.setWindowTitle(f"{_owner_label(buddy)} · 搭子自习室")
        self.resize(570, 610)
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        pages = []
        for title in ("状态", "一起专注", "计划概览", "训导关系", "纪律记录"):
            page = QWidget(); layout = QVBoxLayout(page)
            scroll = QScrollArea(); scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.tabs.addTab(scroll, title)
            pages.append(layout)
        self.status_summary = self._label(pages[0])
        pages[0].addWidget(QLabel("持续提醒订阅"))
        self.subscriptions = {}
        for kind, title, hint in (
            ("start_work", "🔔 开工提醒", "TA 每次开始专注时提醒我"),
            ("finish_work", "🔔 下班提醒", "TA 每次结束当天工作时提醒我"),
        ):
            check = QCheckBox(title); check.setToolTip(hint)
            check.clicked.connect(lambda checked, event=kind: hub._set_subscription(self.buddy, event, checked))
            self.subscriptions[kind] = check
            pages[0].addWidget(check)
            pages[0].addWidget(QLabel(hint))
        self.together_summary = self._label(pages[1])
        together = QPushButton("打开共同专注房间")
        together.clicked.connect(self._open_together)
        pages[1].addWidget(together)
        self.my_plan = self._label(pages[2])
        self.peer_plan = self._label(pages[2])
        edit_plan = QPushButton("编辑我的工作计划")
        edit_plan.clicked.connect(lambda: self._open_own_section(1))
        pages[2].addWidget(edit_plan)
        self.relationship = self._label(pages[3])
        self.permissions = {}
        for key, title in (("view_plan", "允许 TA 查看我的计划"),
                           ("view_reports", "向 TA 分享纪律日报"),
                           ("view_lateness", "允许 TA 查看迟到和开工时间")):
            check = QCheckBox(title)
            check.clicked.connect(lambda: setattr(self, "_permissions_dirty", True))
            self.permissions[key] = check
            pages[3].addWidget(check)
        save = QPushButton("保存对这位搭子的授权范围")
        save.clicked.connect(self._save_permissions)
        self.save_permissions_button = save
        pages[3].addWidget(save)
        self.invite = QPushButton("邀请 TA 担任我的训导主任")
        self.invite.clicked.connect(lambda: self._action("lili_request_discipline_supervisor", {"p_supervisor_id": self.buddy_id}))
        pages[3].addWidget(self.invite)
        self.accept_invite = QPushButton("接受邀请，担任 TA 的训导主任")
        self.accept_invite.clicked.connect(lambda: self._respond(True))
        self.reject_invite = QPushButton("拒绝 TA 的监督邀请")
        self.reject_invite.clicked.connect(lambda: self._respond(False))
        pages[3].addWidget(self.accept_invite)
        pages[3].addWidget(self.reject_invite)
        self.revoke = QPushButton("撤销 TA 查看我的计划和纪律摘要的权限")
        self.revoke.clicked.connect(lambda: self._action("lili_revoke_discipline_supervisor", {}))
        pages[3].addWidget(self.revoke)
        mode = QPushButton("设置我的监督模式与规则")
        mode.clicked.connect(lambda: self._open_own_section(2))
        pages[3].addWidget(mode)
        self.records_hint = self._label(pages[4])
        self.records = QListWidget()
        pages[4].addWidget(self.records, 1)
        for layout in pages[:4]:
            layout.addStretch()
        self.message = QLabel(); self.message.setWordWrap(True)
        root.addWidget(self.message)
        self.tabs.currentChanged.connect(lambda _index: self.refresh())
        self.timer = QTimer(self); self.timer.setInterval(10000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self._render_public()
        self.relationship.setText("正在读取双方监督关系与授权范围…")
        self.peer_plan.setText("正在读取 TA 的计划授权…")
        self.records_hint.setText("纪律日报需要 TA 明确授权后才能查看。")
        self.save_permissions_button.setEnabled(False)
        self.invite.setEnabled(False)
        for check in self.permissions.values():
            check.setEnabled(False)
        for button in (self.accept_invite, self.reject_invite, self.revoke):
            button.hide()

    @staticmethod
    def _label(layout):
        label = QLabel(); label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setStyleSheet("font-size:14px;padding:12px;background:#f1f7f7;border-radius:8px;")
        layout.addWidget(label)
        return label

    def _active(self):
        from .social import _session_user_id
        return self.account_id == _session_user_id(self.hub.client)

    def _rpc(self, name, body, callback):
        generation = self._generation
        def completed(payload):
            if self._active() and generation == self._generation and self.isVisible():
                callback(payload)
        def failed(error):
            if self._active() and generation == self._generation:
                self._request_pending = False
                self.message.setText(str(error)[:300])
        self.hub.study_rpc(name, body, completed, failed)

    def refresh(self):
        if not self.isVisible():
            return
        if not self._active():
            self.close()
            return
        self._render_public()
        if self._request_pending:
            return
        self._request_pending = True
        self._rpc("lili_buddy_study_overview", {"p_buddy_id": self.buddy_id}, self._apply_overview)

    def _render_public(self):
        from .social_ui import _buddy_identifier, _buddy_focus_totals_text, _presence_status, _format_last_confirmed_age_seconds, _presence_last_seen_age_seconds
        fresh = next((row for row in self.hub.data.get("buddies", [])
                      if isinstance(row, dict) and _buddy_identifier(row) == self.buddy_id), None)
        if fresh is not None:
            self.buddy = dict(fresh)
        status = {"focus": "🟢 正在专注", "rest": "正在休息", "offline": "已离线", "unknown": "状态同步中"}.get(_presence_status(self.buddy), "状态同步中")
        session = max(0, int(self.buddy.get("session_seconds", 0) or 0))
        session_text = format_work_duration(session) if self.buddy.get("session_seconds") is not None else "时长未公开"
        self.status_summary.setText(f"{status} · 本轮 {session_text}\n{_buddy_focus_totals_text(self.buddy)}\n{_format_last_confirmed_age_seconds(_presence_last_seen_age_seconds(self.buddy))}")
        for kind, field in (("start_work", "on_focus_start"), ("finish_work", "on_focus_end")):
            check = self.subscriptions[kind]
            check.setChecked(bool(self.buddy.get(field, self.buddy.get("subscribed", False))))
            check.setEnabled(kind not in self.buddy.get("_reminder_pending_types", ()))
        settings = self.hub._focus_engine().store.settings
        today, week = self.hub._focus_progress()
        self.my_plan.setText(f"我的计划\n今日 {format_work_duration(today)} / {format_work_duration(settings.for_weekday(as_beijing().date()) * 60)}\n本周 {format_work_duration(week)} / {format_work_duration(settings.weekly_target_minutes * 60)}")
        same_room = any(_buddy_identifier(row) == self.buddy_id for row in getattr(self.hub, "_room_people", []))
        self.together_summary.setText(
            self.hub.room_summary.text() + "\n" + self.hub.room_goal.text()
            if same_room and hasattr(self.hub, "room_summary") else
            "你们尚未进入同一个共同专注房间。打开共同专注房间，创建房间并分享房间码，或用搭子的房间码加入。")

    def _apply_overview(self, payload):
        self._generation += 1
        self._request_pending = False
        self._overview = payload if isinstance(payload, dict) else {}
        owned = self._overview.get("owned_access") or {}
        supervising = self._overview.get("supervising_access") or {}
        self.relationship.setText(
            ("TA 正在担任我的训导主任。" if owned else "TA 尚未获得我的监督授权。") + "\n" +
            ("我已获准监督 TA。" if supervising else "我尚未获得 TA 的监督授权。") + "\n" +
            str(self._overview.get("outgoing_status") or "双方同意后才生效，可随时撤销。"))
        for key, check in self.permissions.items():
            if not self._permissions_dirty:
                check.setChecked(bool(owned.get(key, True)))
            check.setEnabled(bool(owned))
        self.save_permissions_button.setEnabled(bool(owned))
        self.revoke.setVisible(bool(owned))
        self.invite.setEnabled(not bool(self._overview.get("has_supervisor")) and self._overview.get("outgoing_status") != "pending")
        request = self._overview.get("incoming_request") or {}
        self.accept_invite.setVisible(bool(request))
        self.reject_invite.setVisible(bool(request))
        plan = self._overview.get("peer_plan")
        if isinstance(plan, dict):
            settings = DisciplineSettings.from_dict(plan)
            target = settings.for_weekday(as_beijing().date()) * 60
            self.peer_plan.setText(f"TA 的计划（已授权）\n今日目标 {format_work_duration(target)} · 本周目标 {format_work_duration(settings.weekly_target_minutes * 60)}\n今日 {settings.start_at(as_beijing().date()).strftime('%H:%M')}–{settings.finish_at(as_beijing().date()).strftime('%H:%M')}")
            self.status_summary.setText(self.status_summary.text() + f"\n计划目标：今日 {format_work_duration(target)} · 本周 {format_work_duration(settings.weekly_target_minutes * 60)}")
        else:
            self.peer_plan.setText("TA 的计划未授权，或尚未同步；公开累计时长可在状态页查看。")
        self.records.clear()
        if self._overview.get("can_read_reports"):
            self.records_hint.setText("TA 已授权分享纪律日报，仅显示允许查看的摘要。")
            if self.tabs.currentIndex() == 4:
                self._rpc("lili_discipline_supervisor_report", {"p_owner_id": self.buddy_id}, self._apply_reports)
        else:
            self.records_hint.setText("TA 尚未授权分享纪律日报。授权关闭后，已有摘要会从本窗口清除。")
        self.message.setText("")

    def _apply_reports(self, payload):
        if not self._overview.get("can_read_reports"):
            return
        self.records.clear()
        for row in payload.get("reports", []) if isinstance(payload, dict) else []:
            data = row.get("metadata") or {}
            line = f"{row.get('event_date', '')} · 专注 {format_work_duration(int(data.get('today_seconds', 0)))}"
            if "daily_target_seconds" in data:
                line += f" / {format_work_duration(int(data['daily_target_seconds']))}"
            line += f" · 长休息 {int(data.get('long_break_count', 0))} 次"
            if "lateness_minutes" in data:
                line += f" · 迟到 {int(data['lateness_minutes'])} 分钟"
            self.records.addItem(line)
        if not self.records.count():
            self.records.addItem("暂无已同步的纪律日报；下班后生成当天摘要。")

    def _action(self, name, body):
        self._generation += 1
        self._request_pending = False
        self.message.setText("正在同步…")
        self._rpc(name, body, lambda _payload: self.refresh())

    def _respond(self, accepted):
        request = self._overview.get("incoming_request") or {}
        if request.get("request_id"):
            self._action("lili_respond_discipline_supervisor", {"p_request_id": request["request_id"], "p_accept": accepted})

    def _save_permissions(self):
        self._permissions_dirty = False
        self._action("lili_set_discipline_permissions", {
            "p_supervisor_id": self.buddy_id,
            **{f"p_{key}": check.isChecked() for key, check in self.permissions.items()},
        })

    def _open_own_section(self, index):
        self.hide()
        self.hub.open_focus_section(index)

    def _open_together(self):
        self._open_own_section(0)
        self.hub.rooms.setFocus()

    def closeEvent(self, event):
        self._generation += 1
        self._request_pending = False
        self._overview = {}
        self._permissions_dirty = False
        self.peer_plan.setText("TA 的计划未授权，或尚未同步。")
        self.records.clear()
        super().closeEvent(event)

    def hideEvent(self, event):
        self._generation += 1
        self._request_pending = False
        super().hideEvent(event)
