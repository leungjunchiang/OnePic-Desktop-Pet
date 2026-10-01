"""北京时间业务时间与显示统一；UTC 事实和持续时长不作手工偏移。

双向训导结案汇总进入纪律记录，正式说明通过今日回应卡，勾选配置统一绘制。
专注导航的计划、训导与记录复用统一按钮反馈；统计刷新不读取授权表单；记录只呈现紧凑纪律摘要和已结算事项，分析留在工作报告。"""

from __future__ import annotations

from .time_service import format_clock

from datetime import date, datetime, time, timedelta
from copy import deepcopy
import time as monotonic_time
from html import escape
from typing import Any, Callable
from .ui_feedback import ACTION_BUTTON_STYLE, decorate_buttons

from PySide6.QtCore import QTime, Qt, Signal, QSize, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QSpinBox, QTabWidget, QTabBar,
    QTableWidget, QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget, QScrollArea,
)
from .check_controls import AppCheckBox as QCheckBox

from .discipline import DisciplineEngine, DisciplineSettings, DisciplineStore, WEEKDAYS, discipline_events, get_actual_work_start, local_work_time
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
        height = 48 if self.objectName() == "disciplineRecordNav" else 56
        return QSize(available // count + (1 if index < available % count else 0), height)

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
        self.records = FocusTabWidget()
        self.records.setTabBar(EqualFocusTabBar(self.records))
        self.records.tabBar().setObjectName("disciplineRecordNav")
        self.records.tabBar().setStyleSheet("QTabBar::tab{height:48px;padding:0;color:#465861;border-bottom:4px solid transparent;}QTabBar::tab:selected{font-weight:700;color:#087f74;border-bottom-color:#087f74;}QTabBar::tab:hover{background:#e6f2ef;}")
        self.records.addTab(self._scroll(self.today_page), "今日")
        self.records.addTab(self._scroll(self.week_page), "本周")
        self.records.addTab(self.ledger_page, "历史")
        plan_scroll = self._scroll(self.settings_page)
        plan_scroll.setProperty("disciplinePlanPage", True)
        self.tabs.addTab(plan_scroll, "工作计划")
        self.tabs.addTab(self._scroll(self.mode_page), "训导主任")
        self.tabs.addTab(self.records, "记录")
        self._build_today_page()
        self._build_week_page()
        self._build_ledger_page()
        self._build_settings_page()
        self._build_mode_page()
        self._load_settings()
        self._render_summaries()
        self.tabs.currentChanged.connect(self._tab_entered)
        self.records.currentChanged.connect(lambda _index: self.refresh())
    def _tab_entered(self, index):
        self.refresh()
        if self.isVisible() and (self.tabs.widget(index) is self.records or self.tabs.widget(index).property("disciplinePlanPage")):
            self._read_records_once()

    def showEvent(self, event):
        super().showEvent(event)
        current = self.tabs.currentWidget()
        if current is self.records or current.property("disciplinePlanPage"):
            self._read_records_once()

    def _read_records_once(self, *, include_config=True):
        owner = getattr(self.engine_provider, "__self__", None)
        provider = getattr(owner, "_discipline_engine_provider", None)
        owner = getattr(provider, "__self__", owner)
        callback = getattr(owner, "_sync_discipline_state", None)
        if callable(callback):
            callback(include_config=include_config)

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
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        heading = QHBoxLayout()
        self.today_title = QLabel()
        self.today_title.setStyleSheet("font-size:18px;font-weight:700;")
        heading.addWidget(self.today_title, 1)
        self.today_badge = QLabel()
        self.today_badge.setStyleSheet("background:#e7f3ee;color:#26584e;border-radius:8px;padding:5px 9px;")
        heading.addWidget(self.today_badge)
        self.snooze_button = QPushButton("🏳 高挂免战牌")
        self.snooze_button.clicked.connect(self._snooze_today)
        heading.addWidget(self.snooze_button)
        layout.addLayout(heading)
        metrics = QHBoxLayout()
        metrics.setSpacing(8)
        self.discipline_metrics = []
        for title in ("开工", "今日缺口", "长休", "待说明"):
            label = QLabel(title)
            label.setMinimumWidth(0)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignTop)
            label.setStyleSheet("background:#f0f6f5;color:#274b47;border:1px solid #d0e2de;border-radius:9px;padding:10px;font-size:12px;")
            metrics.addWidget(label, 1)
            self.discipline_metrics.append(label)
        layout.addLayout(metrics)
        self.today_summary = self._summary_label()
        layout.addWidget(self.today_summary)
        self.today_events = self._summary_label()
        layout.addWidget(self.today_events)
        self.today_explanation_row = QWidget()
        explanation_layout = QHBoxLayout(self.today_explanation_row)
        explanation_layout.setContentsMargins(0,0,0,0)
        self.today_pending = QComboBox()
        explanation_layout.addWidget(self.today_pending, 1)
        explain_today = QPushButton("说明事项")
        explain_today.clicked.connect(lambda: self._explain_event(str(self.today_pending.currentData() or "")))
        explanation_layout.addWidget(explain_today)
        layout.addWidget(self.today_explanation_row)

    def _build_week_page(self) -> None:
        layout = QVBoxLayout(self.week_page)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.week_summary = self._summary_label()
        layout.addWidget(self.week_summary)
        self.week_days = self._summary_label()
        layout.addWidget(self.week_days)

    def _build_ledger_page(self) -> None:
        layout = QVBoxLayout(self.ledger_page)
        self.ledger = QTableWidget(0, 4)
        self.ledger.setHorizontalHeaderLabels(("日期", "纪律结果", "完成 / 缺口", "待说明"))
        self.ledger.horizontalHeader().setStretchLastSection(True)
        self.ledger.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.ledger.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.ledger.itemSelectionChanged.connect(self._show_history_day)
        layout.addWidget(self.ledger)
        self.history_detail = self._summary_label()
        self.history_detail.setText("选择一天查看已结算的纪律事项。")
        layout.addWidget(self.history_detail)
        self.history_events = QComboBox()
        layout.addWidget(self.history_events)
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
        self._form_loaded_stamp = self.store.settings_updated_at
        self._form_loaded_values = self._form_values()
        self._settings_form_baseline = deepcopy(vars(settings))

    def _form_values(self):
        return (self.weekly_target.value(), tuple(check.isChecked() for check in self.workdays.values()),
                self.usual_start.time(), self.usual_finish.time(), self.finish_enabled.isChecked(),
                self.late_grace.value(), self.break_limit.value(), self.early_grace.value(), self.recommended_break.value(),
                self.catchup.currentData(), self.carry.isChecked(), self.mode.currentData(),
                tuple(check.isChecked() for check in self.normal_rules.values()), self.progress_reminders.isChecked())

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
        changes = {key:value for key,value in changes.items() if value != self._settings_form_baseline.get(key)}
        merged = DisciplineSettings.from_dict({**vars(previous), **changes})
        if vars(merged) == vars(previous): return
        draft = self._form_values()
        self.store.update_settings(merged)
        self._load_settings()
        # 保存一页不能丢掉另一页尚未保存的草稿；基线保留已确认设置。
        if section == "plan":
            self.mode.setCurrentIndex(max(0, self.mode.findData(draft[11])))
            for check, value in zip(self.normal_rules.values(), draft[12]): check.setChecked(value)
            self.progress_reminders.setChecked(draft[13])
            self._update_mode_hint()
        else:
            self.weekly_target.setValue(draft[0])
            for check, value in zip(self.workdays.values(), draft[1]): check.setChecked(value)
            self.usual_start.setTime(draft[2]); self.usual_finish.setTime(draft[3])
            self.finish_enabled.setChecked(draft[4])
            for widget, value in zip((self.late_grace,self.break_limit,self.early_grace,self.recommended_break),draft[5:9]): widget.setValue(value)
            self.catchup.setCurrentIndex(max(0,self.catchup.findData(draft[9])))
            self.carry.setChecked(draft[10])
        self._read_records_once()
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

    def _completed_day_seconds(self, day, fallback):
        """历史完成时长读取同一账号区间账本；不再以纪律日报副本为唯一来源。"""
        owner = getattr(self.engine_provider, "__self__", None)
        owner = getattr(getattr(owner, "_discipline_engine_provider", None), "__self__", owner)
        analytics = getattr(owner, "focus_analytics", None)
        reader = getattr(analytics, "account_today_seconds", None)
        if not callable(reader): return fallback
        cache = getattr(self, "_day_totals_cache", {})
        key = (self.store.account_id, day.isoformat())
        now = monotonic_time.monotonic()
        if key not in cache or now - cache[key][0] >= 300:
            from .discipline import BEIJING_TIMEZONE
            cache[key] = (now, int(reader(datetime.combine(day, time(23,59,59), BEIJING_TIMEZONE))))
        self._day_totals_cache = cache
        return cache[key][1]

    @staticmethod
    def _event_description(row):
        kind = row.get("event_type")
        meta = row.get("metadata") or {}
        labels = {"start_work":"实际开工", "late_start":"最终迟到", "long_break":"长休超时",
                  "finish_work":"实际下班", "early_finish":"下班异常", "focus_shortfall":"最终目标缺口",
                  "weekly_shortfall":"周内补账", "daily_report":"当天结算", "rest_day":"高挂免战牌", "cancel_rest_day":"取消免战牌"}
        detail = str(meta.get("detail") or "")
        if kind == "late_start": detail = "晚于计划 " + format_work_duration(int(meta.get("minutes_late", 0)) * 60)
        elif kind == "long_break": detail = "超出 " + format_work_duration(int(meta.get("overtime_seconds", 0)))
        elif kind == "daily_report": detail = "缺口 " + format_work_duration(int(meta.get("daily_gap_seconds", 0)))
        elif kind == "weekly_shortfall": detail = "剩余 " + format_work_duration(int(meta.get("remaining_seconds", 0)))
        return labels.get(kind, "纪律事项"), detail

    def _render_summaries(self) -> None:
        if self.store.settings_updated_at != getattr(self, "_form_loaded_stamp", "") and self._form_values() == getattr(self, "_form_loaded_values", None):
            self._load_settings()
        today_seconds, week_seconds = self.progress_provider()
        now_day = local_work_time().date()
        starts = self.engine.work_start_index()
        today = self.engine.daily_summary(now_day, today_seconds, week_seconds, sessions=starts)
        exempt = today["exempt"]
        if not self.snooze_button.property("actionBusy"):
            self.snooze_button.setEnabled(self.store.settings.is_workday(now_day) and not exempt)
            self.snooze_button.setVisible(not exempt)
        decorate_buttons(self)
        target, actual, gap = (int(today[key]) for key in ("daily_target_seconds", "today_seconds", "daily_gap_seconds"))
        mode = {"off":"未启用训导", "normal":"普通训导", "officer":"严格训导"}[today["mode"]]
        self.today_title.setText(f"今日纪律 · {now_day:%m/%d}")
        self.today_badge.setText("🏳 今日免战" if exempt else mode)
        late_text = "免战 · 不记迟到" if exempt else "尚无开工记录" if not today["actual_start"] else "迟到 " + format_work_duration(today["lateness_minutes"] * 60) if today["lateness_minutes"] else "准时开工"
        cards = (
            f"<b>开工</b><h2>{today['actual_start'] or '尚无开工记录'}</h2>计划 {today['planned_start']}<br>{late_text}",
            f"<b>今日缺口</b><h2>{'免战' if exempt else format_work_duration(gap)}</h2>已完成 {format_work_duration(actual)}<br>参考 {format_work_duration(target)}",
            f"<b>长休</b><h2>{today['long_break_count']} 次</h2>超时 {format_work_duration(today['break_overtime_seconds'])}",
            f"<b>待说明</b><h2>{today['unexplained_count']} 项</h2>{'今日无需说明' if exempt else '仅记录重要事项'}",
        )
        for label, text in zip(self.discipline_metrics, cards): label.setText(text)
        self.today_summary.setText(f"计划下班：{today['planned_finish']}　实际下班：{today['actual_finish'] or '尚未下班'}" +
            ("<br>今日免战；不记迟到、长休或今日缺口纪律，本周目标继续保留。" if exempt else ""))
        events = [row for row in today["events"] if row.get("event_type") not in {"daily_report", "finish_work", "late_start"}]
        lines = []
        for row in events:
            title, detail = self._event_description(row)
            if row.get("event_type") == "start_work": detail = late_text
            if exempt and row.get("event_type") not in {"start_work", "rest_day", "cancel_rest_day"}: continue
            lines.append(f"<p><b>{escape(format_clock(row.get('occurred_at')))}　{title}</b><br>{escape(detail)}</p>")
        coach_lines = [f"<p><b>{escape(format_clock(row.get('occurred_at')))} {escape(str(row.get('title', '')))}</b><br>{escape(str(row.get('detail', '')))}</p>"
                       for row in today["coach_messages"][-20:]]
        from .coaching import closed_case_lines
        closed_lines = ["<p>" + escape(line) + "</p>" for line in closed_case_lines(self.store, now_day)]
        self.today_events.setText("<h3>今日事件</h3>" + ("".join(lines) or "今天没有需要特别记录的纪律事项。")
                                 + ("<h3>训导结案</h3>" + "".join(closed_lines) if closed_lines else "")
                                 + ("<h3>搭子互动</h3>" + "".join(coach_lines) if coach_lines else ""))
        previous_pending = self.today_pending.currentData()
        self.today_pending.clear()
        if not exempt:
            for row in today["events"]:
                if (row.get("requires_explanation") and not row.get("explanation")
                        and not any(str(case.get(key)) == str(row.get("id"))
                                    for case in self.store.coaching_cases
                                    for key in ("source_event_id", "local_source_event_id"))):
                    title, _ = self._event_description(row)
                    self.today_pending.addItem(title + " · " + format_clock(row.get("occurred_at")), row.get("id"))
        if self.today_pending.findData(previous_pending) >= 0:
            self.today_pending.setCurrentIndex(self.today_pending.findData(previous_pending))
        self.today_explanation_row.setVisible(self.today_pending.count() > 0)
        monday = now_day - timedelta(days=now_day.weekday())
        week_events = list(discipline_events(self.store.events_for_week(now_day)))
        late_days, long_count, due_count, exemptions = set(), 0, 0, 0
        days = []
        for offset in range(7):
            day = monday + timedelta(days=offset)
            if day > now_day: continue
            rows = self.store.events_for_day(day)
            summary_row = next((row for row in reversed(rows) if row.get("event_type") == "daily_report"), None)
            meta = (summary_row or {}).get("metadata", {})
            summary = self.engine.daily_summary(day, today_seconds if day == now_day else self._completed_day_seconds(day, int(meta.get("today_seconds", 0))), week_seconds, sessions=starts)
            if summary["exempt"]:
                exemptions += 1
                result = "🏳 免战"
            else:
                if summary["lateness_minutes"]: late_days.add(day)
                long_count += summary["long_break_count"]
                due_count += summary["unexplained_count"]
                result = (summary['actual_start'] or "尚无开工记录") + "　" + ("迟到 " + format_work_duration(summary["lateness_minutes"] * 60) if summary["lateness_minutes"] else "缺少历史计划基准" if not summary.get("historical_plan_known",True) else "正常" if summary["actual_start"] else "")
            days.append(f"<p><b>{DAY_LABELS[offset]} {day:%m/%d}</b>　{result}　完成 {format_work_duration(summary['today_seconds'])}</p>")
        self.week_summary.setText(f"<h2>本周纪律</h2>本周目标 {format_work_duration(today['weekly_target_seconds'])} · 已完成 {format_work_duration(week_seconds)} · 剩余 {format_work_duration(today['weekly_remaining_seconds'])}<br>迟到 {len(late_days)} 次 · 长休超时 {long_count} 次 · 待说明 {due_count} 项 · 免战 {exemptions} 天<br>剩余工作日 {today['remaining_workdays']} · 周目标继续分摊")
        self.week_days.setText("".join(days))
        self.plan_summary.setText(f"今日参考目标：{format_work_duration(target)} · 已完成：{format_work_duration(actual)} · 剩余：{format_work_duration(gap)}<br>本周计划：{format_work_duration(today['weekly_target_seconds'])} · 已完成：{format_work_duration(week_seconds)} · 剩余：{format_work_duration(today['weekly_remaining_seconds'])}")
        self.duty_status.setText(f"<h3>{mode}</h3>开工：{today['actual_start'] or '尚无开工记录'} · {late_text}<br>今日缺口：{format_work_duration(gap)} · 长休：{today['long_break_count']} 次 · 待说明总计：{len(self.store.due_explanations())} 项")
        if self.records.currentIndex() != 2:
            return  # 历史仅进入时展开；不随今日每次刷新扫描历史。
        rows = list(discipline_events(self.store.events))
        session_days = {stamp.date().isoformat() for stamp in starts.starts if stamp.date() < now_day} if starts else set()
        signature = (tuple(repr(row) for row in rows), tuple(sorted(self.store.rest_days)), starts,
                     tuple((row.get("id"), row.get("revision")) for row in self.store.coaching_cases))
        if signature == getattr(self, "_ledger_signature", None): return
        self._ledger_signature = signature
        settled_days = sorted(session_days | {str(row.get("event_date")) for row in rows if row.get("event_type") in {"daily_report", "finish_work", "rest_day"} or str(row.get("event_date")) < now_day.isoformat()}, reverse=True)
        settled_days = sorted(set(settled_days) | {str(row.get("event_date")) for row in self.store.coaching_cases
                                                 if row.get("state") in {"completed", "forgiven"}}, reverse=True)
        history_totals = {}
        owner = getattr(self.engine_provider, "__self__", None)
        owner = getattr(getattr(owner, "_discipline_engine_provider", None), "__self__", owner)
        analytics = getattr(owner, "focus_analytics", None)
        if settled_days and callable(getattr(analytics, "range_aggregate", None)):
            # 一次本地范围汇总，避免为历史每一行重复遍历全部区间；绝不请求服务器。
            from .discipline import BEIJING_TIMEZONE
            history_totals = analytics.range_aggregate(
                datetime.combine(date.fromisoformat(settled_days[-1]), time(), BEIJING_TIMEZONE),
                datetime.combine(now_day+timedelta(days=1), time(), BEIJING_TIMEZONE)).daily
            self._day_totals_cache = {(self.store.account_id,key):(monotonic_time.monotonic(),value) for key,value in history_totals.items()}
        selected = self.ledger.item(self.ledger.currentRow(), 0)
        selected_day = selected.text() if selected else ""
        self.ledger.blockSignals(True)
        self.ledger.setRowCount(len(settled_days))
        for index, day_key in enumerate(settled_days):
            day_rows = [row for row in rows if row.get("event_date") == day_key]
            final = next((row for row in reversed(day_rows) if row.get("event_type") == "daily_report"), {})
            meta = final.get("metadata") or {}
            completed = history_totals.get(day_key, int(meta.get("today_seconds", 0)))
            summary = self.engine.daily_summary(date.fromisoformat(day_key), completed, week_seconds, sessions=starts)
            result = "🏳 免战" if summary["exempt"] else " · ".join(filter(None, ("严格训导" if meta.get("mode") == "officer" else "正常", "迟到 " + format_work_duration(summary["lateness_minutes"] * 60) if summary["lateness_minutes"] else "", f"长休 {summary['long_break_count']} 次" if summary["long_break_count"] else "")))
            result = (summary["actual_start"] + "开工 · " if summary["actual_start"] else "尚无开工记录 · ") + result
            if not summary.get("historical_plan_known", True) and not summary["exempt"]: result += " · 缺少历史计划基准"
            values = (day_key, result, "完成 " + format_work_duration(summary["today_seconds"]) + (" · 缺口 " + format_work_duration(int(meta.get("daily_gap_seconds", 0))) if meta.get("daily_gap_seconds") else ""), str(summary["unexplained_count"]))
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, day_key)
                self.ledger.setItem(index, column, item)
            if day_key == selected_day: self.ledger.selectRow(index)
        self.ledger.resizeColumnsToContents()
        self.ledger.blockSignals(False)
        self._show_history_day()

    def _show_history_day(self):
        item = self.ledger.item(self.ledger.currentRow(), 0)
        self.history_events.clear()
        if item is None:
            self.explain_button.setEnabled(False)
            return
        day = date.fromisoformat(item.text())
        summary = self.engine.daily_summary(day, self._completed_day_seconds(day, 0), 0)
        rows = summary["events"]
        lines = []
        for row in rows:
            title, detail = self._event_description(row)
            lines.append(f"<p>{escape(format_clock(row.get('occurred_at')))}　<b>{title}</b>　{escape(detail)}</p>")
            if row.get("requires_explanation") and not row.get("explanation") and not self.store.is_exempt(day):
                self.history_events.addItem(title + " · " + format_clock(row.get("occurred_at")), row.get("id"))
        baseline = "缺少历史计划基准；不按当前计划重新计算迟到。" if not summary.get("historical_plan_known", True) else "计划开工 " + summary["planned_start"]
        from .coaching import closed_case_lines
        lines.extend("<p>" + escape(line) + "</p>" for line in closed_case_lines(self.store, day))
        self.history_detail.setText(f"<h3>{day:%m/%d} 纪律记录</h3>" + escape(baseline) + "".join(lines))
        self.history_events.setVisible(self.history_events.count() > 0)
        self.explain_button.setEnabled(self.history_events.count() > 0)

    def _explain_selected(self) -> None:
        self._explain_event(str(self.history_events.currentData() or ""))

    def _explain_event(self, event_id):
        if any(str(row.get(key)) == event_id for row in self.store.coaching_cases
               for key in ("source_event_id", "local_source_event_id")):
            self.history_detail.setText("这是一条搭子正式训导事项，请到专注 → 今日回应；说明会交给搭子审核。")
            return
        if self.engine_provider is not None and self.engine_provider().store is not self.store:
            self.refresh()
            return
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
        self._read_records_once(include_config=False)
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
