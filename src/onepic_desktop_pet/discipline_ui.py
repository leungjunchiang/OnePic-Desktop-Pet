"""专注导航的计划、训导与记录复用统一按钮反馈；统计刷新不读取授权表单。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable
from .ui_feedback import ACTION_BUTTON_STYLE, decorate_buttons

from PySide6.QtCore import QTime, Qt, Signal, QSize
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QSpinBox, QTabWidget, QTabBar,
    QTableWidget, QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget, QScrollArea,
)

from .discipline import DisciplineEngine, DisciplineSettings, DisciplineStore, WEEKDAYS
from .work_timer import format_work_duration


DAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
EXPLANATION_LABELS = {
    "illness": "身体不适", "sleep": "睡过头", "commute": "外出 / 通勤",
    "forgot": "忘记打开六毛", "urgent": "临时有事", "other": "其他",
}


class EqualFocusTabBar(QTabBar):
    """随内容宽度四等分，不依赖文字长度，也不产生滚动箭头。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("focusSecondaryNav")
        self.setExpanding(True)
        self.setUsesScrollButtons(False)
        self.setStyleSheet("QTabWidget QTabBar#focusSecondaryNav::tab{height:56px;padding:0;border:0;border-bottom:4px solid transparent;color:#465861;background:#fafcfc;}QTabWidget QTabBar#focusSecondaryNav::tab:selected{color:#087f74;font-weight:700;border-bottom-color:#087f74;}QTabWidget QTabBar#focusSecondaryNav::tab:hover{background:#f0f7f6;}")

    def tabSizeHint(self, index):
        count = max(1, self.count())
        available = self.parentWidget().width()
        return QSize(available // count + (1 if index < available % count else 0), 56)

    def paintEvent(self, event):
        super().paintEvent(event)
        index = self.currentIndex()
        if index >= 0:
            rect = self.tabRect(index)
            painter = QPainter(self)
            painter.fillRect(rect.x(), rect.bottom() - 3, rect.width(), 4, QColor("#087f74"))
            painter.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.updateGeometry()


class FocusTabWidget(QTabWidget):
    """在窗口缩放时同步导航宽度，避免 Qt 缓存上一次标签尺寸。"""
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.tabBar().setFixedWidth(self.width())


class FinishReviewDialog(QDialog):
    """Confirm an early finish with a visible, user initiated review."""

    finish_confirmed = Signal()

    def __init__(self, title: str, detail: str, *, confirm_label: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(390)
        layout = QVBoxLayout(self)
        text = QLabel(detail)
        text.setWordWrap(True)
        text.setStyleSheet("font-size:14px;padding:14px;background:white;border-radius:10px;")
        layout.addWidget(text)
        actions = QHBoxLayout()
        continue_button = QPushButton("回去再干一会")
        continue_button.clicked.connect(self.reject)
        confirm_button = QPushButton(confirm_label)
        confirm_button.clicked.connect(self._confirm)
        actions.addWidget(continue_button)
        actions.addWidget(confirm_button)
        layout.addLayout(actions)

    def _confirm(self) -> None:
        self.finish_confirmed.emit()
        self.accept()


class DisciplineWorkspace(QWidget):
    """可直接嵌入专注导航的普通页面，统一管理计划、模式与记录。"""

    def __init__(
        self, store: DisciplineStore, engine: DisciplineEngine,
        progress_provider: Callable[[], tuple[int, int]], *,
        supervisor_open_callback: Callable[[], object] | None = None, parent=None,
        engine_provider=None, policy_factory=None, rest_day_callback=None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.engine = engine
        self.progress_provider = progress_provider
        self.supervisor_open_callback = supervisor_open_callback
        self.engine_provider = engine_provider
        self.policy_factory = policy_factory
        self.rest_day_callback = rest_day_callback
        self.setStyleSheet(
            "QTabWidget::pane{background:white;border:1px solid #d3e0e6;"
            "border-radius:10px;} QLabel{color:#273946;} QPushButton{min-height:30px;padding:5px 12px;"
            "background:#dcefeb;color:#155a52;border:0;border-radius:8px;font-weight:600;}" + ACTION_BUTTON_STYLE
        )
        root = QVBoxLayout(self)
        self.tabs = FocusTabWidget()
        self.tabs.setTabBar(EqualFocusTabBar(self.tabs))
        root.addWidget(self.tabs, 1)
        self.today_page = QWidget()
        self.week_page = QWidget()
        self.ledger_page = QWidget()
        self.settings_page = QWidget()
        self.mode_page = QWidget()
        self.records = QTabWidget()
        self.records.addTab(self.today_page, "今日")
        self.records.addTab(self.week_page, "本周")
        self.records.addTab(self.ledger_page, "历史")
        self.tabs.addTab(self._scroll(self.settings_page), "工作计划")
        self.tabs.addTab(self._scroll(self.mode_page), "训导主任")
        self.tabs.addTab(self.records, "记录")
        self._build_today_page()
        self._build_week_page()
        self._build_ledger_page()
        self._build_settings_page()
        self._build_mode_page()
        self._load_settings()
        self._render_summaries()
        self.tabs.currentChanged.connect(lambda _index: self.refresh())
        self.records.currentChanged.connect(lambda _index: self.refresh())
    @staticmethod
    def _scroll(page):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(page)
        return scroll

    def _summary_label(self) -> QLabel:
        label = QLabel()
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        label.setStyleSheet("font-size:14px;line-height:1.6;padding:18px;background:white;border-radius:10px;")
        return label

    def _build_today_page(self) -> None:
        layout = QVBoxLayout(self.today_page)
        self.today_summary = self._summary_label()
        layout.addWidget(self.today_summary)
        self.snooze_button = QPushButton("🏳️ 高挂免战牌 · 今日休息")
        self.snooze_button.clicked.connect(self._snooze_today)
        layout.addWidget(self.snooze_button, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addStretch()

    def _build_week_page(self) -> None:
        layout = QVBoxLayout(self.week_page)
        self.week_summary = self._summary_label()
        layout.addWidget(self.week_summary)
        self.week_summary.setToolTip("当周周一至周日统计。周缺口留档，新周默认重新开始。")
        layout.addStretch()

    def _build_ledger_page(self) -> None:
        layout = QVBoxLayout(self.ledger_page)
        self.ledger = QTableWidget(0, 4)
        self.ledger.setHorizontalHeaderLabels(("日期", "事项", "记录", "说明状态"))
        self.ledger.horizontalHeader().setStretchLastSection(True)
        self.ledger.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.ledger.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.ledger)
        self.explain_button = QPushButton("说明选中事项")
        self.explain_button.clicked.connect(self._explain_selected)
        layout.addWidget(self.explain_button, alignment=Qt.AlignmentFlag.AlignRight)

    @staticmethod
    def _hours() -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0, 24)
        spin.setDecimals(2)
        spin.setSingleStep(0.25)
        spin.setSuffix(" 小时")
        return spin

    def _build_settings_page(self) -> None:
        layout = QVBoxLayout(self.settings_page)
        form = QFormLayout()
        self.plan_form = form
        self.weekly_target = self._hours()
        self.weekly_target.setRange(0, 168)
        form.addRow("本周专注目标", self.weekly_target)
        self.workdays = {}
        day_row = QHBoxLayout()
        for key, label in zip(WEEKDAYS, DAY_LABELS):
            check = QCheckBox(label)
            self.workdays[key] = check
            day_row.addWidget(check)
        form.addRow("通常工作日", day_row)
        self.usual_start = QTimeEdit(); self.usual_start.setDisplayFormat("HH:mm")
        form.addRow("通常开工时间", self.usual_start)
        self.reference_hint = QLabel(); self.reference_hint.setWordWrap(True)
        form.addRow(self.reference_hint)
        self.weekly_target.valueChanged.connect(self._update_reference)
        for check in self.workdays.values():
            check.toggled.connect(self._update_reference)
        layout.addLayout(form)
        advanced_button = QPushButton("高级设置")
        advanced_button.setCheckable(True)
        layout.addWidget(advanced_button)
        self.advanced = QWidget(); self.advanced.setVisible(False)
        advanced_button.toggled.connect(self.advanced.setVisible)
        layout.addWidget(self.advanced)
        advanced_form = QFormLayout(self.advanced)
        self.late_grace = QSpinBox(); self.late_grace.setRange(0, 240); self.late_grace.setSuffix(" 分钟")
        self.break_limit = QSpinBox(); self.break_limit.setRange(1, 480); self.break_limit.setSuffix(" 分钟")
        self.early_grace = QSpinBox(); self.early_grace.setRange(0, 240); self.early_grace.setSuffix(" 分钟")
        self.recommended_break = QSpinBox(); self.recommended_break.setRange(1, 480); self.recommended_break.setSuffix(" 分钟")
        advanced_form.addRow("开工宽限", self.late_grace)
        advanced_form.addRow("长休息阈值", self.break_limit)
        advanced_form.addRow("建议休息", self.recommended_break)
        self.finish_enabled = QCheckBox("启用计划下班时间")
        self.usual_finish = QTimeEdit(); self.usual_finish.setDisplayFormat("HH:mm")
        advanced_form.addRow(self.finish_enabled)
        advanced_form.addRow("计划下班时间", self.usual_finish)
        advanced_form.addRow("提前下班宽限", self.early_grace)
        self.finish_enabled.toggled.connect(self.usual_finish.setEnabled)
        self.finish_enabled.toggled.connect(self.early_grace.setEnabled)
        self.catchup = QComboBox()
        for label, value in (("均匀补足", "even"), ("优先补足", "frontload"), ("不自动分配", "none")):
            self.catchup.addItem(label, value)
        advanced_form.addRow("补计划方式", self.catchup)
        self.carry = QCheckBox("跨周继续补足（默认关闭）")
        advanced_form.addRow("周结算", self.carry)
        self.plan_summary = self._summary_label()
        layout.addWidget(self.plan_summary)
        privacy = QLabel("登录后，工作计划与纪律记录会同步到本人其他设备。搭子仅能看到你明确授权的内容。")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        save = QPushButton("保存工作计划")
        save.clicked.connect(lambda: self._save_settings("plan"))
        layout.addWidget(save, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addStretch()

    def _build_mode_page(self) -> None:
        layout = QVBoxLayout(self.mode_page)
        if self.policy_factory is not None:
            self.policy_panel = self.policy_factory(self.mode_page)
            layout.addWidget(self.policy_panel)
        layout.addWidget(QLabel("自己的训导规则（独立于分享授权）"))
        self.duty_status = self._summary_label()
        layout.addWidget(self.duty_status)
        form = QFormLayout()
        self.mode = QComboBox()
        for label, value in (("关闭", "off"), ("普通训导", "normal"), ("严格训导", "officer")):
            self.mode.addItem(label, value)
        form.addRow("监督模式", self.mode)
        self.normal_rules = {}
        for key, label in (("start", "到点未开工提醒"), ("break", "长休息提醒"),
                           ("early", "提前下班提醒"), ("daily", "今日目标提醒"), ("weekly", "本周目标提醒")):
            check = QCheckBox(label)
            self.normal_rules[key] = check
            form.addRow(check)
        self.progress_reminders = QCheckBox("提醒今日进度明显落后")
        form.addRow(self.progress_reminders)
        layout.addLayout(form)
        self.mode_hint = QLabel()
        self.mode_hint.setWordWrap(True)
        layout.addWidget(self.mode_hint)
        self.mode.currentIndexChanged.connect(self._update_mode_hint)
        save = QPushButton("保存训导规则")
        save.clicked.connect(lambda: self._save_settings("mode"))
        layout.addWidget(save, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addStretch()

    def _update_mode_hint(self, *_args) -> None:
        officer = self.mode.currentData() == "officer"
        for check in self.normal_rules.values():
            check.setVisible(not officer)
        self.mode_hint.setText(
            "严格训导规则：迟到分级提醒、长休息巡视、严重偏差说明、提前下班审查。日缺口纳入本周剩余目标；未说明事项次日保留，可在记录中补充说明。每日摘要与周结算写入账本，监督者只查看已授权的摘要。"
            if officer else "普通训导以提醒和进度追踪为主，不要求解释行为。关闭后暂停训导，已有记录保留。")

    def refresh(self) -> None:
        if self.engine_provider is not None:
            engine = self.engine_provider()
            if engine.store is not self.store:
                self.engine = engine
                self.store = engine.store
                self._load_settings()
                if hasattr(self, "policy_panel"):
                    self.policy_panel.refresh()
        self._render_summaries()
        decorate_buttons(self)

    def _load_settings(self) -> None:
        settings = self.store.settings
        self.mode.setCurrentIndex(max(0, self.mode.findData(settings.mode)))
        self.weekly_target.setValue(settings.weekly_target_minutes / 60)
        for day, check in self.workdays.items():
            check.setChecked(day in settings.workdays)
        self.usual_start.setTime(QTime.fromString(settings.start_time, "HH:mm"))
        self.usual_finish.setTime(QTime.fromString(settings.finish_time, "HH:mm"))
        self.finish_enabled.setChecked(settings.planned_finish_enabled)
        self.usual_finish.setEnabled(settings.planned_finish_enabled)
        self.early_grace.setEnabled(settings.planned_finish_enabled)
        self._update_reference()
        self.recommended_break.setValue(settings.recommended_break_minutes)
        self.late_grace.setValue(settings.late_grace_minutes)
        self.break_limit.setValue(settings.break_limit_minutes)
        self.early_grace.setValue(settings.early_finish_grace_minutes)
        self.catchup.setCurrentIndex(max(0, self.catchup.findData(settings.catchup_strategy)))
        for key, check in self.normal_rules.items():
            check.setChecked(settings.reminder_rules.get(key, True))
        self.progress_reminders.setChecked(settings.progress_reminders)
        self.carry.setChecked(settings.carry_across_weeks)
        self._update_mode_hint()

    def _save_settings(self, section="plan") -> None:
        # Resolve the active account before writing; an old visible page must
        # never apply another account's form values after login changes.
        if self.engine_provider is not None and self.engine_provider().store is not self.store:
            self.refresh()
            return
        previous = self.store.settings
        if section == "plan":
            changes = {
                "weekly_target_minutes": round(self.weekly_target.value() * 60),
                "workdays": [day for day, check in self.workdays.items() if check.isChecked()],
                "start_time": self.usual_start.time().toString("HH:mm"),
                "finish_time": self.usual_finish.time().toString("HH:mm"),
                "daily_start_times": {}, "daily_finish_times": {},
                "planned_finish_enabled": self.finish_enabled.isChecked(),
                "late_grace_minutes": self.late_grace.value(),
                "break_limit_minutes": self.break_limit.value(),
                "early_finish_grace_minutes": self.early_grace.value(),
                "recommended_break_minutes": self.recommended_break.value(),
                "catchup_strategy": self.catchup.currentData(),
                "carry_across_weeks": self.carry.isChecked(),
            }
        else:
            changes = {
                "mode": self.mode.currentData(),
                "progress_reminders": self.progress_reminders.isChecked(),
                "reminder_rules": {key: check.isChecked() for key, check in self.normal_rules.items()},
            }
        self.store.update_settings(DisciplineSettings.from_dict({**vars(previous), **changes}))
        self._render_summaries()

    def _snooze_today(self) -> None:
        self.refresh()
        if self.rest_day_callback is not None:
            self.rest_day_callback(self.refresh)
        elif self.store.exempt_today():
            self._render_summaries()

    def _update_reference(self, *_args):
        count = sum(check.isChecked() for check in self.workdays.values())
        seconds = round(self.weekly_target.value() * 3600 / count) if count else 0
        self.reference_hint.setText(f"每周 {count} 个工作日 · 建议每天约 {format_work_duration(seconds)}\n本周未完成的时间自动分摊到剩余工作日。" if count else "未选择通常工作日；周目标保留。")

    def _render_summaries(self) -> None:
        today_seconds, week_seconds = self.progress_provider()
        from .discipline import as_beijing
        now_day = as_beijing().date()
        today = self.engine.daily_summary(now_day, today_seconds, week_seconds)
        if not self.snooze_button.property("actionBusy"):
            self.snooze_button.setEnabled(self.store.settings.is_workday(now_day) and not today["exempt"])
            self.snooze_button.setText("🏳️ 今日高挂免战牌 · 暂停训导" if today["exempt"] else "🏳️ 高挂免战牌 · 今日休息")
        decorate_buttons(self)
        target = int(today["daily_target_seconds"])
        actual = int(today["today_seconds"])
        gap = int(today["daily_gap_seconds"])
        mode_text = {"off": "关闭", "normal": "普通训导", "officer": "严格训导"}[today["mode"]]
        self.today_summary.setText(
            f"<h2>我的纪律记录 · {mode_text}</h2>"
            f"通常开工：{today['planned_start']}　实际开工：{today['actual_start'] or '尚未开工'}<br>"
            f"今日专注：{format_work_duration(actual)} / {format_work_duration(target)}<br>"
            f"迟到：{today['lateness_minutes']} 分钟 · 未说明事项：{today['unexplained_count']}<br>"
            f"今日缺口：{format_work_duration(gap)}<br>"
            f"休息累计：{format_work_duration(today['rest_seconds'])}<br>"
            f"长休息：{today['long_break_count']} 次　超时：{format_work_duration(today['break_overtime_seconds'])}<br>"
            f"计划下班：{today['planned_finish']}　实际下班：{today['actual_finish'] or '工作中'}<br>"
            f"本周已完成：{format_work_duration(today['week_seconds'])} / {format_work_duration(today['weekly_target_seconds'])}"
        )
        self.week_summary.setText(
            f"<h2>本周目标与追赶计划</h2>"
            f"周目标：{format_work_duration(today['weekly_target_seconds'])}<br>"
            f"本周累计：{format_work_duration(today['week_seconds'])}<br>"
            f"本周剩余：{format_work_duration(today['weekly_remaining_seconds'])}<br>"
            f"剩余工作日：{today['remaining_workdays']}<br>"
            f"今日参考目标：{format_work_duration(target)}<br>"
            f"今日超额可追回前期缺口：{format_work_duration(today['caught_up_today_seconds'])}"
        )
        week_events = self.store.events_for_week(now_day)
        started_days = {row.get("event_date") for row in week_events if row.get("event_type") == "start_work"}
        late_days = {row.get("event_date") for row in week_events if row.get("event_type") == "late_start" and not self.store.is_exempt(str(row.get("event_date")))}
        long_count = sum(row.get("event_type") == "long_break" and not self.store.is_exempt(str(row.get("event_date"))) for row in week_events)
        early_count = sum(row.get("event_type") == "early_finish" and not self.store.is_exempt(str(row.get("event_date"))) for row in week_events)
        self.week_summary.setText(self.week_summary.text() +
            f"<br>准时开工：{len(started_days - late_days)} / {len(started_days)} 个已开工日"
            f" · 迟到：{len(late_days)} 次 · 长休息：{long_count} 次 · 提前下班：{early_count} 次")
        self.plan_summary.setText(
            f"今日参考目标：{format_work_duration(target)} · 已完成：{format_work_duration(actual)} · 剩余：{format_work_duration(gap)}<br>"
            f"本周计划：{format_work_duration(today['weekly_target_seconds'])} · 已完成：{format_work_duration(today['week_seconds'])} · 剩余：{format_work_duration(today['weekly_remaining_seconds'])}")
        self.duty_status.setText(
            f"<h3>{'训导主任值班中' if today['mode'] != 'off' else '训导主任未值班'} · {mode_text}</h3>"
            f"开工：{today['actual_start'] or '尚未开工'} · 迟到：{today['lateness_minutes']} 分钟<br>"
            f"今日 {format_work_duration(actual)} / {format_work_duration(target)} · 缺口 {format_work_duration(gap)}<br>"
            f"本周 {format_work_duration(today['week_seconds'])} / {format_work_duration(today['weekly_target_seconds'])}<br>"
            f"长休息：{today['long_break_count']} 次 · 未说明事项：{len(self.store.due_explanations())} 项")
        rows = sorted(self.store.events, key=lambda row: str(row.get("occurred_at") or ""), reverse=True)
        signature = (tuple(repr(row) for row in rows), tuple(sorted(self.store.rest_days)))
        if signature == getattr(self, "_ledger_signature", None):
            return
        self._ledger_signature = signature
        self.ledger.setRowCount(len(rows))
        labels = {
            "start_work": "开工", "late_start": "迟到", "late_start_warning": "迟到提醒",
            "start_break": "开始休息", "end_break": "结束休息", "long_break": "长休息",
            "long_break_warning": "休息提醒", "finish_work": "下班", "early_finish": "提前下班",
            "focus_shortfall": "日目标缺口", "behind_schedule": "进度落后",
            "weekly_shortfall": "周目标结算",
            "daily_report": "每日总结", "rest_day": "🏳️ 高挂免战牌",
        }
        for row_index, row in enumerate(rows):
            moment = str(row.get("occurred_at") or "")
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            description = str(metadata.get("detail") or metadata.get("minutes_late") or metadata.get("duration_seconds") or "已记录")
            if isinstance(description, int):
                description = f"{description} 分钟"
            explanation = row.get("explanation")
            state = "免战 · 无需说明" if self.store.is_exempt(str(row.get("event_date"))) else "已说明" if explanation else "待说明" if row.get("requires_explanation") else "已记录"
            values = (str(row.get("event_date") or ""), labels.get(str(row.get("event_type")), str(row.get("event_type"))), f"{moment[11:16]} · {description}", state)
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, str(row.get("id") or ""))
                if row.get("requires_explanation") and not explanation:
                    item.setForeground(QColor("#a33a3a"))
                self.ledger.setItem(row_index, column, item)

    def _explain_selected(self) -> None:
        if self.engine_provider is not None and self.engine_provider().store is not self.store:
            self.refresh()
            return
        row = self.ledger.currentRow()
        if row < 0:
            return
        event_id = str(self.ledger.item(row, 0).data(Qt.ItemDataRole.UserRole) or "")
        event = next((item for item in self.store.events if item.get("id") == event_id), None)
        if not event or self.store.is_exempt(str(event.get("event_date"))) or not event.get("requires_explanation") or event.get("explanation"):
            return
        from PySide6.QtWidgets import QInputDialog
        options = tuple(EXPLANATION_LABELS.values())
        reason, accepted = QInputDialog.getItem(self, "说明这条记录", "请选择原因：", options, 0, False)
        if not accepted:
            return
        key = next((name for name, label in EXPLANATION_LABELS.items() if label == reason), "other")
        self.store.explain(event_id, key)
        self._render_summaries()


class DisciplineDialog(QDialog):
    """兼容独立弹窗入口，页面内容通过普通 QWidget 复用。"""

    def __init__(self, store, engine, progress_provider, *, supervisor_open_callback=None, parent=None, engine_provider=None):
        super().__init__(parent)
        self.setWindowTitle("训导主任 · 工作计划与纪律账本")
        self.resize(650, 640)
        layout = QVBoxLayout(self)
        self.workspace = DisciplineWorkspace(
            store, engine, progress_provider,
            supervisor_open_callback=supervisor_open_callback,
            engine_provider=engine_provider, parent=self,
        )
        layout.addWidget(self.workspace)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        layout.addWidget(close)

    def __getattr__(self, name):
        workspace = self.__dict__.get("workspace")
        if workspace is not None:
            return getattr(workspace, name)
        raise AttributeError(name)
