"""复用一张用户回应卡和一个进度牌；监督者处置与用户输入严格分开。"""

from __future__ import annotations

from uuid import uuid4
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QDialog

from .coaching import projection, case_detail, closed_case_lines, TERMINAL
from .resources import resource_path
from .ui_feedback import ACTION_BUTTON_STYLE, begin_button_work, end_button_work, decorate_buttons
from .work_timer import format_work_duration


class CoachingPanel(QFrame):
    """无轮询的账号投影；普通 UI 刷新只读本地事实，不发送 RPC。"""

    def __init__(self, engine_provider, progress_provider, rpc, identity, records, parent=None):
        super().__init__(parent)
        self.engine_provider, self.progress_provider, self.rpc = engine_provider, progress_provider, rpc
        self.identity, self.records = identity, records
        self._primary = None
        self._selected_case_id = None
        self._badge_case = None
        self._account = engine_provider().store.account_id
        self._busy = False
        self._generation = 0
        self._known = None
        self._notice_active = False
        self._debt_observed = None
        self.setStyleSheet(ACTION_BUTTON_STYLE + "QFrame#coachHeadCard{background:#fff6e3;border:1px solid #cdbc86;border-radius:12px;} QLineEdit{padding:8px;color:#243b44;background:#fff;border:1px solid #a8bcbc;}")
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame(); self.card.setObjectName("coachHeadCard")
        card_layout = QVBoxLayout(self.card)
        self.summary = QLabel(); self.summary.setWordWrap(True); self.summary.setTextFormat(Qt.TextFormat.PlainText)
        card_layout.addWidget(self.summary)
        self.actions = QHBoxLayout()
        self.explain = QPushButton("写说明"); self.explain.clicked.connect(self._edit)
        self.accept = QPushButton("接受补时"); self.accept.clicked.connect(lambda: self._send("accept", self.accept))
        self.more = QPushButton(); self.more.clicked.connect(self._show_pending)
        for button in (self.explain, self.accept, self.more): self.actions.addWidget(button)
        card_layout.addLayout(self.actions)
        self.input = QLineEdit(); self.input.setMaxLength(500); self.input.setPlaceholderText("发生什么了？一两句话即可。")
        self.submit = QPushButton("提交说明"); self.submit.clicked.connect(lambda: self._send("explain", self.submit, self.input.text()))
        card_layout.addWidget(self.input); card_layout.addWidget(self.submit)
        self.input.hide(); self.submit.hide()
        layout.addWidget(self.card)
        self.pet = QLabel(); self.pet.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        pix = QPixmap(str(resource_path("assets/pet/daily-actions/02-office.png")))
        if not pix.isNull(): self.pet.setPixmap(pix.scaled(86, 86, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else: self.pet.setText("▼ 六毛")
        layout.addWidget(self.pet)
        self.message = QLabel(); self.message.setWordWrap(True); self.message.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.message)
        # Inserted by the caller to the LEFT of today's focus total.
        self.badge = QPushButton(); self.badge.clicked.connect(self._details)
        self.badge.setStyleSheet(ACTION_BUTTON_STYLE)
        self.badge.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh()

    def _name(self, row):
        return self.identity(str(row.get("supervisor_id") or ""))

    def refresh(self):
        engine = self.engine_provider()
        if engine.store.account_id != self._account:
            self._account = engine.store.account_id
            self._generation += 1; self._known = None; self._primary = None; self._busy = False
            self._selected_case_id = None
            for button in (self.accept, self.submit):
                if button.property("actionBusy"): end_button_work(button)
            self.input.clear(); self.input.hide(); self.submit.hide(); self.message.clear()
        known = {row["id"]: row.get("state") for row in engine.store.coaching_cases if row.get("id")}
        if self._known is not None:
            for row in engine.store.coaching_cases:
                if row.get("state") in TERMINAL and self._known.get(row.get("id")) not in TERMINAL | {None}:
                    self.flash("✓ " + (self._name(row) + " 放过了这件事" if row["state"] == "forgiven" else "事项已结案"))
        self._known = known
        today, _week = self.progress_provider()
        view = projection(engine, today)
        primary = view["card"]
        selected = next((row for row in engine.store.coaching_cases if row.get("id") == self._selected_case_id
                         and row.get("state") in {"pending", "rejected"} and not row.get("paused")), None)
        if primary is not None and selected is not None:
            primary = selected
        changed = (primary or {}).get("id") != (self._primary or {}).get("id")
        self._primary = primary
        if changed:
            self.input.clear(); self.input.hide(); self.submit.hide()
        self.card.setVisible(primary is not None)
        self.pet.setVisible(primary is not None)
        self.setVisible(primary is not None or bool(self.message.text()))
        if primary:
            self.summary.setText(case_detail(primary, self._name(primary)))
            self.explain.setText("重新说明" if primary.get("state") == "rejected" else "写说明")
            self.accept.setVisible(primary.get("state") == "pending" and int(primary.get("required_seconds") or 0) > 0)
            if not self.accept.property("actionBusy"):
                self.accept.setText("接受补时 " + format_work_duration(int(primary.get("required_seconds") or 0)))
            self.more.setVisible(view["card_count"] > 1)
            self.more.setText(f"还有 {view['card_count'] - 1} 项")
            for button in (self.explain, self.accept, self.submit):
                if not button.property("actionBusy"): button.setEnabled(not self._busy)
        self._badge_case = view["badge"]
        if not self._notice_active:
            self.badge.setVisible(self._badge_case is not None)
            if self._badge_case:
                self.badge.setText(self._badge_case["badge_text"] + (f" · +{view['badge_count'] - 1}" if view["badge_count"] > 1 else ""))
        # Observation completion is local and intentionally not a new discipline event.
        if self._badge_case and str(self._badge_case.get("id", "")).startswith("observe-debt:"):
            self._debt_observed = self._badge_case["id"]
        elif self._debt_observed and today >= 3600:
            self._debt_observed = None; self.flash("✓ 今天表现还行")
        decorate_buttons(self)

    def flash(self, text):
        self._notice_active = True
        self.badge.setText(text); self.badge.show()
        QTimer.singleShot(1800, self, self._clear_flash)

    def _clear_flash(self):
        self._notice_active = False; self.refresh()

    def _edit(self):
        self.input.show(); self.submit.show(); self.input.setFocus()

    def _send(self, action, button, text=""):
        if not self._primary or self._busy or not begin_button_work(button, "正在提交…"): return
        case = dict(self._primary); engine = self.engine_provider()
        if engine.store.account_id != self._account:
            end_button_work(button); self.refresh(); return
        if action == "explain" and not text.strip():
            end_button_work(button); self.message.setText("请写一两句话说明。"); self.show(); return
        self._busy = True; self._generation += 1; generation = self._generation; account = self._account
        self.message.setText("")
        def finish():
            if generation != self._generation: return
            self._busy = False; end_button_work(button); self.refresh()
        def done(payload):
            if generation != self._generation or account != self.engine_provider().store.account_id: return
            try:
                self.engine_provider().store.merge_coaching(payload)
                self.input.clear(); self.input.hide(); self.submit.hide()
                self.flash("✓ " + str(payload.get("message") or "已回应"))
            finally: finish()
        def failed(error):
            if generation != self._generation: return
            try: self.message.setText(str(error)[:300]); self.show()
            finally: finish()
        def timeout():
            if self._busy and generation == self._generation:
                failed("提交超时，请刷新后重试。"); self._generation += 1
        QTimer.singleShot(30000, self, timeout)
        try:
            self.rpc("lili_coaching_case_action", {"p_owner_id": account, "p_case_id": case["id"],
                     "p_expected_revision": case["revision"], "p_action": action,
                     "p_action_id": str(uuid4()), "p_text": text}, done, failed)
        except Exception as error: failed(error)

    def _show_pending(self):
        engine = self.engine_provider()
        dialog = QDialog(self); dialog.setWindowTitle("待回应的训导事项"); layout = QVBoxLayout(dialog)
        for row in engine.store.coaching_cases:
            if row.get("state") not in {"pending", "rejected"} or row.get("paused"): continue
            button = QPushButton(str(row.get("title") or "训导事项"))
            def select(_checked=False, chosen=row):
                self._selected_case_id = chosen["id"]
                self._primary = chosen; self.summary.setText(case_detail(chosen, self._name(chosen)))
                self.accept.setVisible(chosen.get("state") == "pending" and int(chosen.get("required_seconds") or 0) > 0)
                self.accept.setText("接受补时 " + format_work_duration(int(chosen.get("required_seconds") or 0)))
                self.explain.setText("重新说明" if chosen.get("state") == "rejected" else "写说明")
                self.input.clear(); self.input.hide(); self.submit.hide(); dialog.accept()
            button.clicked.connect(select); layout.addWidget(button)
        dialog.exec()

    def _details(self):
        if not self._badge_case: return
        row = self._badge_case
        dialog = QDialog(self); dialog.setWindowTitle("训导状态"); layout = QVBoxLayout(dialog)
        text = case_detail(row, self._name(row)) if row.get("source_event_id") else str(row.get("detail") or "")
        if row.get("state") == "active":
            remaining = int(row.get("remaining_seconds") or 0)
            text += "\n已完成：" + format_work_duration(max(0, int(row.get("required_seconds") or 0) - remaining))
        label = QLabel(text); label.setTextFormat(Qt.TextFormat.PlainText); label.setWordWrap(True); layout.addWidget(label)
        records = QPushButton("查看纪律记录"); records.clicked.connect(lambda: (dialog.accept(), self.records()))
        layout.addWidget(records); dialog.resize(390, 210); dialog.exec()


class DesktopCoachingSurface(QFrame):
    """六毛头顶/进度左侧的持续状态；被动展示，用户点击后才打开回应页。"""

    def __init__(self, parent, open_today, compact=False):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                 Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(ACTION_BUTTON_STYLE + "QFrame{background:#fff5de;border:1px solid #b5a67e;border-radius:10px;color:#273d3c;} QLabel{border:0;padding:4px;}")
        layout = QVBoxLayout(self); layout.setContentsMargins(8, 6, 8, 6)
        self.label = QLabel(); self.label.setWordWrap(not compact); self.label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.label)
        self.open_button = QPushButton("回应事项" if not compact else "查看")
        self.open_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.open_button.clicked.connect(open_today); layout.addWidget(self.open_button)
        self.setFixedWidth(285 if not compact else 215)
        self._compact = compact
        if compact:
            layout.removeWidget(self.label); self.label.hide()
        decorate_buttons(self)

    def passive_show(self):
        if self._compact:
            from PySide6.QtGui import QFontMetrics
            self.open_button.setText(QFontMetrics(self.open_button.font()).elidedText(
                self.label.text(), Qt.TextElideMode.ElideRight, self.width()-32))
            self.open_button.setToolTip(self.label.text())
            self.adjustSize()
        self.show()
        parent = self.parentWidget()
        if hasattr(parent, "_show_nonactivating"):
            parent._show_nonactivating(self, always_on_top=True)
