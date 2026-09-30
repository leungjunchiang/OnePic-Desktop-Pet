"""搭子详情仅展示 TA 的授权信息与双方监督关系，免战日暂停所有训导操作。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QTabWidget, QVBoxLayout, QWidget, QScrollArea, QMessageBox,
)

from .buddy_identity import buddy_name, public_name
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
        self._action_message = ""
        self.setWindowTitle(f"{buddy_name(buddy)} · 搭子详情")
        self.resize(570, 610)
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        pages = []
        for title in ("状态", "一起专注", "TA的计划", "监督关系", "纪律记录"):
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
        self.peer_plan = self._label(pages[2])
        self.relationship = self._label(pages[3])
        self.permissions = {}
        self.start_normal = QPushButton("普通训导")
        self.start_normal.clicked.connect(lambda: self._start_supervision("normal"))
        self.start_officer = QPushButton("严格训导")
        self.start_officer.clicked.connect(lambda: self._start_supervision("officer"))
        self.stop_supervising = QPushButton("停止我对 TA 的监督")
        self.stop_supervising.clicked.connect(lambda: self._start_supervision("off"))
        for button in (self.start_normal, self.start_officer, self.stop_supervising):
            button.setEnabled(False); pages[3].addWidget(button)
        self.nudges = {}
        nudge_row = QHBoxLayout()
        for kind, label in (("start", "催 TA 开工"), ("rest", "提醒休息太久"), ("progress", "提醒今日进度"), ("finish", "提醒 TA 下班"), ("cheer", "加油一下"), ("take_break", "提醒休息"), ("return", "该回来了"), ("rest_more", "再歇会儿")):
            button = QPushButton(label); button.setEnabled(False)
            button.clicked.connect(lambda _checked=False, k=kind: self._action("lili_supervision_nudge", {"p_owner_id": self.buddy_id, "p_kind": k}))
            self.nudges[kind] = button; pages[3].addWidget(button)
        pages[3].addLayout(nudge_row)
        self.progress_button = QPushButton("查看今日进度")
        self.progress_button.clicked.connect(lambda: self.tabs.setCurrentIndex(0))
        pages[3].addWidget(self.progress_button)
        self.rest_hint = self._label(pages[3])
        self.inverse_relationship = self._label(pages[3])
        self.stop_peer = QPushButton("停止 TA 对我的监督")
        self.stop_peer.clicked.connect(lambda: self._action("lili_pause_peer_supervision", {"p_supervisor_id": self.buddy_id, "p_paused": True}))
        self.resume_peer = QPushButton("恢复 TA 的监督资格")
        self.resume_peer.clicked.connect(lambda: self._action("lili_pause_peer_supervision", {"p_supervisor_id": self.buddy_id, "p_paused": False}))
        for button in (self.stop_peer, self.resume_peer):
            button.setEnabled(False); pages[3].addWidget(button)
        self.records_hint = self._label(pages[4])
        self.records = QListWidget()
        self.records.itemClicked.connect(self._mark_report_read)
        pages[4].addWidget(self.records, 1)
        self.explain_nudge = QPushButton("提醒 TA 处理未说明事项")
        self.explain_nudge.setVisible(False)
        self.explain_nudge.clicked.connect(lambda: self._action("lili_supervision_nudge", {"p_owner_id": self.buddy_id, "p_kind": "explain"}))
        pages[4].addWidget(self.explain_nudge)
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
                if name in {"lili_buddy_study_overview", "lili_discipline_supervisor_report"}:
                    self.records.clear()
                    if name == "lili_buddy_study_overview":
                        self._overview = {}
                        self.peer_plan.setText("授权未能确认，已清除缓存计划。")
                        self.relationship.setText("暂未确认 TA 的训导授权，请重试。")
                        self.inverse_relationship.setText("双方监督关系暂未确认。")
                        self.progress_button.setVisible(False)
                        self.rest_hint.setText("")
                        self.explain_nudge.setVisible(False)
                        for button in (self.start_normal, self.start_officer, self.stop_supervising, self.stop_peer, self.resume_peer, self.explain_nudge, *self.nudges.values()):
                            button.setEnabled(False)
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
        status = {"focus": "🟢 正在专注", "rest": "正在休息", "offline": "已离线", "unknown": "状态同步中", "exempt": "🏳️ 高挂免战牌 · 今日休息"}.get(_presence_status(self.buddy), "状态同步中")
        session = max(0, int(self.buddy.get("session_seconds", 0) or 0))
        session_text = format_work_duration(session) if self.buddy.get("session_seconds") is not None else "时长未公开"
        self.setWindowTitle(f"{buddy_name(self.buddy)} · 搭子详情")
        identity = buddy_name(self.buddy)
        secondary = public_name(self.buddy)
        if identity != secondary:
            identity += "\n" + secondary
        self.status_summary.setText(f"{identity}\n{status} · 本轮 {session_text}\n{_buddy_focus_totals_text(self.buddy)}\n{_format_last_confirmed_age_seconds(_presence_last_seen_age_seconds(self.buddy))}")
        for kind, field in (("start_work", "on_focus_start"), ("finish_work", "on_focus_end")):
            check = self.subscriptions[kind]
            check.setChecked(bool(self.buddy.get(field, self.buddy.get("subscribed", False))))
            check.setEnabled(kind not in self.buddy.get("_reminder_pending_types", ()))
        same_room = any(_buddy_identifier(row) == self.buddy_id for row in getattr(self.hub, "_room_people", []))
        self.together_summary.setText(
            self.hub.room_summary.text() + "\n" + self.hub.room_goal.text()
            if same_room and hasattr(self.hub, "room_summary") else
            "你们尚未进入同一个共同专注房间。打开共同专注房间，创建房间并分享房间码，或用搭子的房间码加入。")

    def _apply_overview(self, payload):
        self._generation += 1
        self._request_pending = False
        self._overview = payload if isinstance(payload, dict) else {}
        permission = self._overview.get("peer_permission") or {}
        own_permission = self._overview.get("own_permission") or {}
        eligible = bool(permission.get("eligible"))
        mode = self._overview.get("active_mode")
        exempt = bool(self._overview.get("exempt"))
        mode_label = lambda value: "严格训导" if value == "officer" else "普通训导" if value == "normal" else "尚未开始"
        self.relationship.setText(
            f"我监督 {buddy_name(self.buddy)}\n" +
            ("TA 已允许你普通训导。" if eligible else
             "TA 已暂停你对 TA 的监督。" if permission.get("paused") else
             "TA 仅向指定搭子开放训导。" if permission.get("enabled") else "TA 尚未开启训导功能。") +
            ("\n严格训导：已开放" if permission.get("officer") else "\n严格训导：未开放") +
            "\n当前：" + mode_label(mode))
        self.start_normal.setEnabled(eligible and not exempt)
        self.start_officer.setEnabled(bool(permission.get("officer")) and not exempt)
        self.stop_supervising.setEnabled(bool(mode) and not exempt)
        actions = set(self._overview.get("actions", [])) if not exempt else set()
        for kind, button in self.nudges.items():
            button.setVisible(kind in actions)
            button.setEnabled(kind in actions)
        self.progress_button.setVisible(bool(permission.get("view_progress")) and not exempt)
        self.rest_hint.setText("🏳️ TA 今天挂了免战牌，暂停训导。\n今日休息，本周目标仍会继续累计。" if exempt else
                               "看看休息多久了：" + format_work_duration(int(self._overview["rest_seconds"])) if self._overview.get("rest_seconds") is not None else "")
        self.rest_hint.setVisible(bool(self.rest_hint.text()))
        inverse = self._overview.get("peer_active_mode")
        self.inverse_relationship.setText(f"{buddy_name(self.buddy)} 监督我\n" +
            ("我已允许 TA 普通训导" if own_permission.get("eligible") else "TA 当前不在我的训导范围内") +
            ("\n严格训导：已允许" if own_permission.get("officer") else "\n严格训导：未开放") +
            "\n当前：" + ("已由我暂停" if own_permission.get("paused") else mode_label(inverse)))
        self.stop_peer.setEnabled(bool(inverse))
        self.resume_peer.setVisible(bool(own_permission.get("paused")))
        self.resume_peer.setEnabled(bool(own_permission.get("paused")))
        plan = self._overview.get("peer_plan")
        if isinstance(plan, dict):
            settings = DisciplineSettings.from_dict(plan)
            labels = dict(zip(("mon", "tue", "wed", "thu", "fri", "sat", "sun"), ("周一", "周二", "周三", "周四", "周五", "周六", "周日")))
            text = f"TA 的工作计划\n本周目标：{format_work_duration(settings.weekly_target_minutes * 60)}\n通常工作日：" + ("、".join(labels[day] for day in settings.workdays) or "未设置") + f"\n通常开工：{settings.start_time}"
            if settings.planned_finish_enabled:
                text += f"\n计划下班：{settings.finish_time}"
            self.peer_plan.setText(text + "\n" + self._progress_text())
        else:
            self.peer_plan.setText("TA 暂未向你公开工作计划。\n" + self._progress_text())
        self.explain_nudge.setVisible("explain" in actions)
        self.explain_nudge.setEnabled("explain" in actions)
        self.records.clear()
        if self._overview.get("can_read_reports"):
            self.records_hint.setText("TA 已授权分享纪律日报，仅显示允许查看的摘要。")
            if self.tabs.currentIndex() == 4:
                self._rpc("lili_discipline_supervisor_report", {"p_owner_id": self.buddy_id}, self._apply_reports)
        else:
            self.records_hint.setText("TA 尚未授权分享纪律日报。授权关闭后，已有摘要会从本窗口清除。")
        self.message.setText(self._action_message)

    def _progress_text(self):
        from .social_ui import _buddy_focus_totals_text
        data = self._overview.get("progress") or {}
        if data:
            parts = []
            for field, title in (("today_seconds", "今日已完成"), ("week_seconds", "本周已完成"), ("daily_target_seconds", "今日参考目标")):
                if field in data:
                    parts.append(title + "：" + format_work_duration(int(data[field])))
            return "\n".join(parts)
        return _buddy_focus_totals_text(self.buddy)

    def _apply_reports(self, payload):
        if not self._overview.get("can_read_reports"):
            return
        self.records.clear()
        for row in payload.get("reports", []) if isinstance(payload, dict) else []:
            data = row.get("metadata") or {}
            line = str(row.get("event_date", ""))
            if "today_seconds" in data:
                line += f" · 专注 {format_work_duration(int(data['today_seconds']))}"
            if "daily_target_seconds" in data:
                line += f" / {format_work_duration(int(data['daily_target_seconds']))}"
            if "long_break_count" in data:
                line += f" · 长休息 {int(data['long_break_count'])} 次"
            if "lateness_minutes" in data:
                line += f" · 迟到 {int(data['lateness_minutes'])} 分钟"
            line += " · 已阅" if row.get("read_at") else " · 未阅"
            item = QListWidgetItem(line)
            item.setData(Qt.ItemDataRole.UserRole, str(row.get("event_date") or ""))
            self.records.addItem(item)
        if not self.records.count():
            self.records.addItem("暂无已同步的纪律日报；下班后生成当天摘要。")
        elif self.isVisible() and self.tabs.currentIndex() == 4:
            self._mark_report_read(self.records.item(0))

    def _mark_report_read(self, item):
        day = item.data(Qt.ItemDataRole.UserRole)
        if day and self._overview.get("can_read_reports"):
            self._rpc("lili_mark_discipline_report_read", {"p_owner_id": self.buddy_id, "p_report_date": day},
                      lambda _payload: item.setText(item.text().replace("未阅", "已阅")))

    def _action(self, name, body):
        self._generation += 1
        self._request_pending = False
        self.message.setText("正在同步…")
        def completed(payload):
            self._action_message = str(payload.get("message") or "设置已更新。") if isinstance(payload, dict) else "设置已更新。"
            self.refresh()
        self._rpc(name, body, completed)

    def _start_supervision(self, mode):
        if mode == "officer" and self._overview.get("active_mode") != "officer":
            answer = QMessageBox.question(self, "开始严格训导",
                f"你将以严格训导监督 {buddy_name(self.buddy)}。\n"
                "TA 已开放严格训导资格，允许查看的信息以 TA 的当前设置为准。\n"
                "严格训导规则包括严重偏差说明、下班审查及纪律记录。是否开始？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._action("lili_start_supervision", {"p_owner_id": self.buddy_id, "p_mode": mode})

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
        self.peer_plan.setText("TA 暂未向你公开工作计划。")
        self.records.clear()
        super().closeEvent(event)

    def hideEvent(self, event):
        self._generation += 1
        self._request_pending = False
        super().hideEvent(event)
