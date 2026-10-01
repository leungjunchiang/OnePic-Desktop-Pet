"""双向训导使用正式事项审核，日常互动独立，勾选配置使用统一矢量绘制。
搭子详情仅展示 TA 的授权信息与双方监督关系，免战日暂停所有训导操作。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QTabWidget, QVBoxLayout, QWidget, QScrollArea, QMessageBox, QComboBox, QSpinBox, QLineEdit, QGridLayout, QFrame,
)
from .check_controls import AppCheckBox as QCheckBox

from .buddy_identity import buddy_name, public_name
from .discipline import DisciplineSettings, as_beijing
from .work_timer import format_work_duration
from .ui_feedback import ACTION_BUTTON_STYLE, begin_button_work, end_button_work, decorate_buttons
from .coaching import case_detail, TERMINAL
from uuid import uuid4


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
        self._action_busy = False
        self.setStyleSheet(ACTION_BUTTON_STYLE)
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
        modes = QHBoxLayout()
        for button in (self.start_normal, self.start_officer, self.stop_supervising):
            button.setEnabled(False); modes.addWidget(button)
        pages[3].addLayout(modes)
        self.nudges = {}
        nudge_row = QGridLayout()
        self.nudge_layout = nudge_row
        for index, (kind, label) in enumerate((("start", "催 TA 开工"), ("rest", "提醒休息太久"), ("progress", "提醒今日进度"), ("finish", "提醒 TA 下班"), ("cheer", "加油一下"), ("take_break", "提醒休息"), ("return", "该回来了"), ("rest_more", "再歇会儿"), ("knock", "👊 敲桌子"), ("ask", "问问怎么回事"), ("praise", "👏 夸一下"), ("flower", "🌸 发小红花"), ("approve_finish", "✅ 批准下班"))):
            button = QPushButton(label); button.setEnabled(False)
            button.clicked.connect(lambda _checked=False, k=kind: self._nudge(k))
            self.nudges[kind] = button; nudge_row.addWidget(button, index//3, index%3)
        pages[3].addLayout(nudge_row)
        feed_row = QHBoxLayout()
        self.feed = QPushButton("🎁 投喂")
        from PySide6.QtWidgets import QMenu
        from .social_ui import BUDDY_FEED_ITEMS
        from .ui_feedback import readable_milk_tea_label
        menu = QMenu(self.feed)
        for kind, label in BUDDY_FEED_ITEMS:
            action = menu.addAction(readable_milk_tea_label(label, self.font()))
            action.triggered.connect(lambda _checked=False, event=kind: hub._send_food_interaction(self.buddy, event))
        self.feed.setMenu(menu); feed_row.addWidget(self.feed)
        join = QPushButton("一起专注"); join.clicked.connect(self._open_together); feed_row.addWidget(join)
        pages[3].addLayout(feed_row)
        self.case_box = QFrame(); case_layout = QVBoxLayout(self.case_box)
        case_layout.addWidget(QLabel("正式训导事项 · 严格训导"))
        self.case_selector = QComboBox(); self.case_selector.currentIndexChanged.connect(self._render_case)
        case_layout.addWidget(self.case_selector)
        self.case_summary = self._label(case_layout)
        self.makeup_minutes = QSpinBox(); self.makeup_minutes.setRange(1, 1440); self.makeup_minutes.setValue(30)
        self.makeup_minutes.setSuffix(" 分钟")
        case_layout.addWidget(self.makeup_minutes)
        self.review_note = QLineEdit(); self.review_note.setMaxLength(300); self.review_note.setPlaceholderText("可写一句话；退回时请说明需要补充什么")
        case_layout.addWidget(self.review_note)
        self.case_actions = {}
        actions_layout = QGridLayout()
        self.case_action_layout = actions_layout
        for index, (kind, title) in enumerate((("request_explanation", "要求说明"), ("request_makeup", "要求补时"),
                ("forgive", "放过"), ("approve", "通过说明"), ("approve_makeup", "通过 + 补时"),
                ("reject", "退回说明"), ("week_makeup", "本周补回"), ("tomorrow_makeup", "明天优先补"))):
            button = QPushButton(title); button.clicked.connect(lambda _checked=False, k=kind: self._case_action(k))
            self.case_actions[kind] = button; actions_layout.addWidget(button, index//3, index%3)
        case_layout.addLayout(actions_layout)
        self.case_box.hide(); pages[3].addWidget(self.case_box)
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
        self.timer = QTimer(self); self.timer.setInterval(60000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self._render_public()
        self.relationship.setText("正在读取双方监督关系与授权范围…")
        self.peer_plan.setText("正在读取 TA 的计划授权…")
        self.records_hint.setText("纪律日报需要 TA 明确授权后才能查看。")
        decorate_buttons(self)
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
                        self.case_box.hide()
                        self.case_selector.clear()
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
        if self._request_pending or self._action_busy:
            return
        self._request_pending = True
        self._rpc("lili_buddy_study_overview", {"p_buddy_id": self.buddy_id}, self._apply_overview)

    def _render_public(self):
        from .social_ui import _buddy_identifier, _buddy_focus_totals_text, _presence_status, _format_last_confirmed_age_seconds, _presence_last_seen_age_seconds
        fresh = next((row for row in self.hub.data.get("buddies", [])
                      if isinstance(row, dict) and _buddy_identifier(row) == self.buddy_id), None)
        if fresh is not None:
            self.buddy = dict(fresh)
        status = {"focus": "🟢 正在专注", "rest": "正在休息", "online": "在线", "offline": "已离线", "unknown": "状态同步中", "exempt": "🏳️ 高挂免战牌 · 今日休息"}.get(_presence_status(self.buddy), "状态同步中")
        if self._overview.get("exempt") and self._overview.get("exemption_date") == as_beijing().date().isoformat():
            status = "🏳️ 高挂免战牌 · 今日休息"
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
        self._render_cases()
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
        self._compact_actions(self.nudge_layout, [button for kind, button in self.nudges.items() if kind in actions])
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
        self._render_public()
        self.explain_nudge.setVisible(False)
        self.records.clear()
        if self._overview.get("can_read_reports"):
            self.records_hint.setText("TA 已授权分享纪律日报，仅显示允许查看的摘要。")
            if self.tabs.currentIndex() == 4:
                self._rpc("lili_discipline_supervisor_report", {"p_owner_id": self.buddy_id}, self._apply_reports)
        else:
            self.records_hint.setText("TA 尚未授权分享纪律日报。授权关闭后，已有摘要会从本窗口清除。")
        self.message.setText(self._action_message)

    def _render_cases(self):
        current = (self.case_selector.currentData() or {}).get("id")
        self.case_selector.blockSignals(True)
        try:
            self.case_selector.clear()
            if not self._overview.get("exempt"):
                for row in self._overview.get("coaching_cases", []):
                    if row.get("state") not in TERMINAL and not row.get("paused"):
                        self.case_selector.addItem(str(row.get("title") or "训导事项"), row)
                for row in self._overview.get("coaching_candidates", []):
                    self.case_selector.addItem(str(row.get("title") or "已结算事项") + " · 尚未处理", row)
            for index in range(self.case_selector.count()):
                if self.case_selector.itemData(index).get("id") == current:
                    self.case_selector.setCurrentIndex(index); break
        finally:
            self.case_selector.blockSignals(False)
        self._render_case()

    def _render_case(self, *_args):
        row = self.case_selector.currentData() or {}
        permission = self._overview.get("peer_permission") or {}
        allowed = bool(permission.get("officer") and self._overview.get("active_mode") == "officer")
        self.case_box.setVisible(bool(row) and allowed)
        state = row.get("state", "candidate")
        if row:
            self.case_summary.setText(case_detail(row, buddy_name(self.buddy)).replace("我的说明：", "TA的说明：")
                                      if row.get("source_event_id") else str(row.get("title", "")))
        choices = {"forgive"} if row else set()
        if state in {"candidate", "pending", "rejected", "acknowledged"}:
            choices |= {"request_explanation", "request_makeup"}
            if row.get("kind") in {"focus_shortfall", "weekly_shortfall"}: choices |= {"week_makeup", "tomorrow_makeup"}
        elif state == "explained": choices |= {"approve", "approve_makeup", "reject"}
        for kind, button in self.case_actions.items():
            button.setVisible(kind in choices and allowed)
        self._compact_actions(self.case_action_layout,
                              [button for kind, button in self.case_actions.items() if kind in choices and allowed])
        self.makeup_minutes.setVisible(bool(choices & {"request_makeup", "approve_makeup", "week_makeup", "tomorrow_makeup"}) and allowed)
        if row.get("id") != getattr(self, "_review_draft_case", None):
            self.review_note.clear()
        self._review_draft_case = row.get("id")

    @staticmethod
    def _compact_actions(layout, buttons):
        """当前状态的操作连续排布，隐藏的旧动作不保留空行或空列。"""
        while layout.count():
            layout.takeAt(0)
        for index, button in enumerate(buttons):
            layout.addWidget(button, index//3, index%3)

    def _case_action(self, kind):
        row = self.case_selector.currentData() or {}
        if not row or self._action_busy: return
        button = self.case_actions[kind]
        if not button.isVisible() or not button.isEnabled(): return
        body = {"p_owner_id": self.buddy_id, "p_action": kind, "p_action_id": str(uuid4()),
                "p_expected_revision": int(row.get("revision") or 0), "p_minutes": self.makeup_minutes.value(),
                "p_text": self.review_note.text()}
        if row.get("source_event_id"): body["p_case_id"] = row["id"]
        else: body["p_source_event_id"] = row["id"]
        self._action("lili_coaching_case_action", body, button)

    def _nudge(self, kind):
        if kind == "approve_finish" and int(self._overview.get("unhandled_case_count") or 0) > 0:
            from PySide6.QtWidgets import QMessageBox
            box = QMessageBox(self); box.setWindowTitle("批准下班")
            box.setText(f"TA 还有 {self._overview['unhandled_case_count']} 项未处理训导事项。下班不会删除这些记录。")
            proceed = box.addButton("仍然放行", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("先不放行", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is not proceed: return
        self._action("lili_supervision_nudge", {"p_owner_id": self.buddy_id, "p_kind": kind}, self.nudges[kind])

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
                line += f" · 迟到 {int(data['lateness_minutes'])} 分钟" if data["lateness_minutes"] is not None else " · 缺少历史计划或开工基准"
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

    def _action(self, name, body, button=None):
        if self._action_busy: return
        button = button or self.sender()
        if isinstance(button, QPushButton) and not begin_button_work(button, "正在处理…"): return
        self._action_busy = True
        self._generation += 1
        self._request_pending = False
        self.message.setText("正在同步…")
        generation = self._generation
        account = self.account_id
        def finish():
            self._action_busy = False
            if isinstance(button, QPushButton): end_button_work(button)
        def completed(payload):
            if generation != self._generation or not self._active(): return
            try:
                self._action_message = str(payload.get("message") or "设置已更新。") if isinstance(payload, dict) else "设置已更新。"
                self.message.setText(self._action_message)
            finally: finish()
            self.refresh()
            stamp = self._action_message
            QTimer.singleShot(2200, self, lambda: self._clear_action_message(stamp))
        def failed(error):
            if generation != self._generation: return
            try: self._action_message = str(error)[:300]; self.message.setText(self._action_message)
            finally: finish()
        def timeout():
            if self._action_busy and generation == self._generation:
                failed("处理超时，请刷新后重试。"); self._generation += 1
        QTimer.singleShot(30000, self, timeout)
        try: self.hub.study_rpc(name, body, completed, failed)
        except Exception as error: failed(error)

    def _clear_action_message(self, stamp):
        if self._action_message == stamp:
            self._action_message = ""; self.message.clear()

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
        self._action_busy = False
        for button in self.findChildren(QPushButton):
            if button.property("actionBusy"): end_button_work(button)
        super().hideEvent(event)
