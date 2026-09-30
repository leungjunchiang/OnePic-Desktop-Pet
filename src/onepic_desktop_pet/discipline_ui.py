"""训导主任日计划、周进度、纪律账本和监督强度设置界面。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable

from PySide6.QtCore import QTime, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QGridLayout, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QSpinBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget,
)

from .discipline import DisciplineEngine, DisciplineSettings, DisciplineStore, WEEKDAYS
from .work_timer import format_work_duration


DAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
EXPLANATION_LABELS = {
    "illness": "身体不适", "sleep": "睡过头", "commute": "外出 / 通勤",
    "forgot": "忘记打开六毛", "urgent": "临时有事", "other": "其他",
}


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


class DisciplineSupervisorDialog(QDialog):
    """Manage explicit two-party supervisor consent and read authorized reports."""

    def __init__(self, buddy_provider, rpc_executor, parent=None) -> None:
        super().__init__(parent)
        self.buddy_provider = buddy_provider
        self.rpc_executor = rpc_executor
        self.setWindowTitle("训导主任授权与报告")
        self.resize(520, 540)
        layout = QVBoxLayout(self)
        privacy = QLabel(
            "监督者只能在你主动授权后查看纪律摘要。摘要包含计划、开工/下班、专注时长和纪律事件；"
            "不包含应用名称、窗口标题、文件或聊天内容。你可以随时撤销授权。"
        )
        privacy.setWordWrap(True)
        layout.addWidget(privacy)

        layout.addWidget(QLabel("邀请一位已确认搭子担任你的训导主任"))
        request_row = QHBoxLayout()
        self.buddies = QComboBox()
        self.buddies.setMinimumContentsLength(20)
        request_row.addWidget(self.buddies, 1)
        request = QPushButton("发送授权申请")
        request.clicked.connect(self._request_supervisor)
        request_row.addWidget(request)
        layout.addLayout(request_row)
        self.owned_status = QLabel("正在读取授权状态…")
        layout.addWidget(self.owned_status)
        revoke = QPushButton("撤销当前训导主任授权")
        revoke.clicked.connect(self._revoke)
        layout.addWidget(revoke, alignment=Qt.AlignmentFlag.AlignRight)

        layout.addWidget(QLabel("待处理的授权申请"))
        self.incoming = QListWidget()
        layout.addWidget(self.incoming, 1)
        response_row = QHBoxLayout()
        accept = QPushButton("允许对方查看我的纪律摘要")
        accept.clicked.connect(lambda: self._respond(True))
        reject = QPushButton("拒绝")
        reject.clicked.connect(lambda: self._respond(False))
        response_row.addWidget(accept)
        response_row.addWidget(reject)
        layout.addLayout(response_row)

        layout.addWidget(QLabel("我监督的用户"))
        report_row = QHBoxLayout()
        self.supervised = QComboBox()
        report_row.addWidget(self.supervised, 1)
        report = QPushButton("查看近 30 天摘要")
        report.clicked.connect(self._load_report)
        report_row.addWidget(report)
        layout.addLayout(report_row)
        self.report = QListWidget()
        self.report.itemClicked.connect(self._mark_read)
        layout.addWidget(self.report, 2)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self._request_ids: dict[str, dict[str, Any]] = {}
        self._supervised_ids: dict[str, dict[str, Any]] = {}
        self.refresh_buddies()
        self._rpc("lili_discipline_supervisor_snapshot", {}, self._apply_snapshot)

    def refresh_buddies(self) -> None:
        self.buddies.clear()
        count = 0
        for buddy in self.buddy_provider() or []:
            if not isinstance(buddy, dict):
                continue
            buddy_id = str(buddy.get("user_id") or buddy.get("id") or "").strip()
            if not buddy_id:
                continue
            name = str(buddy.get("owner_nickname") or buddy.get("nickname") or "搭子").strip()
            self.buddies.addItem(name, buddy_id)
            count += 1
        if not count:
            self.buddies.addItem("暂无可选搭子（先刷新自习室好友）", "")

    def _rpc(self, name: str, body: dict[str, Any], callback) -> None:
        self.status.setText("正在安全地同步授权状态…")
        self.rpc_executor(name, body, callback, self._failed)

    def _request_supervisor(self) -> None:
        target = str(self.buddies.currentData() or "")
        if target:
            self._rpc("lili_request_discipline_supervisor", {"p_supervisor_id": target}, self._action_done)
        else:
            self.status.setText("请先打开搭子自习室并刷新好友列表，再回来发送授权申请。")

    def _respond(self, accepted: bool) -> None:
        item = self.incoming.currentItem()
        record = self._request_ids.get(str(item.data(Qt.ItemDataRole.UserRole) or "")) if item else None
        if record:
            self._rpc("lili_respond_discipline_supervisor", {
                "p_request_id": record["request_id"], "p_accept": accepted,
            }, self._action_done)

    def _revoke(self) -> None:
        self._rpc("lili_revoke_discipline_supervisor", {}, self._action_done)

    def _load_report(self) -> None:
        owner_id = str(self.supervised.currentData() or "")
        if owner_id:
            self.report.clear()
            self._rpc("lili_discipline_supervisor_report", {"p_owner_id": owner_id}, self._apply_report)

    def _apply_report(self, payload: object) -> None:
        rows = payload.get("reports", []) if isinstance(payload, dict) else []
        self.report.clear()
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            day = str(row.get("event_date") or "")
            data = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            actual = int(data.get("today_seconds", 0) or 0)
            target = int(data.get("daily_target_seconds", 0) or 0)
            line = (
                f"{day}  专注 {format_work_duration(actual)} / {format_work_duration(target)}"
                f"  · 迟到 {int(data.get('lateness_minutes', 0) or 0)} 分"
                f"  · 长休息 {int(data.get('long_break_count', 0) or 0)} 次"
                f"  · 缺口 {format_work_duration(int(data.get('daily_gap_seconds', 0) or 0))}"
                f"  · {'已阅' if row.get('read_at') else '未阅'}"
            )
            item = QListWidgetItem(line)
            item.setData(Qt.ItemDataRole.UserRole, {"date": day, "owner_id": self.supervised.currentData()})
            self.report.addItem(item)
        self.status.setText("报告仅由已授权的监督者读取；打开摘要会记录已阅状态。")
        if self.report.count():
            self.report.setCurrentRow(0)
            self._mark_read(self.report.item(0))

    def _mark_read(self, item: QListWidgetItem) -> None:
        row = item.data(Qt.ItemDataRole.UserRole) or {}
        if row.get("date") and row.get("owner_id"):
            self.rpc_executor(
                "lili_mark_discipline_report_read",
                {"p_owner_id": row["owner_id"], "p_report_date": row["date"]},
                lambda _result, current=item: current.setText(current.text().replace("未阅", "已阅")),
                self._failed,
            )

    def _action_done(self, payload: object) -> None:
        message = payload.get("message") if isinstance(payload, dict) else None
        self.status.setText(str(message or "操作已更新；双方授权状态已同步。"))
        self._rpc("lili_discipline_supervisor_snapshot", {}, self._apply_snapshot)

    def _apply_snapshot(self, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        owned = data.get("owned") if isinstance(data.get("owned"), dict) else None
        if owned:
            name = str(owned.get("supervisor_nickname") or "训导主任")
            self.owned_status.setText(f"当前训导主任：{name}（已授权）")
        else:
            self.owned_status.setText("当前没有已授权的训导主任")
        self.incoming.clear()
        self._request_ids.clear()
        for row in data.get("incoming", []) if isinstance(data.get("incoming"), list) else []:
            if not isinstance(row, dict):
                continue
            request_id = str(row.get("request_id") or "")
            label = f"{row.get('owner_nickname') or '搭子'} 希望查看你的纪律摘要"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, request_id)
            self._request_ids[request_id] = row
            self.incoming.addItem(item)
        self.supervised.clear()
        self._supervised_ids.clear()
        for row in data.get("supervising", []) if isinstance(data.get("supervising"), list) else []:
            if not isinstance(row, dict):
                continue
            owner_id = str(row.get("owner_id") or "")
            name = str(row.get("owner_nickname") or "用户")
            self.supervised.addItem(name, owner_id)
            self._supervised_ids[owner_id] = row
        self.status.setText(str(data.get("outgoing_status") or "授权状态已刷新。"))

    def _failed(self, error: object) -> None:
        self.status.setText(str(error)[:300])


class DisciplineDialog(QDialog):
    """展示训导状态与账本，并编辑按账号保存的工作计划。"""

    def __init__(
        self, store: DisciplineStore, engine: DisciplineEngine,
        progress_provider: Callable[[], tuple[int, int]], *,
        supervisor_open_callback: Callable[[], object] | None = None, parent=None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.engine = engine
        self.progress_provider = progress_provider
        self.supervisor_open_callback = supervisor_open_callback
        self.setWindowTitle("训导主任 · 工作计划与纪律账本")
        self.resize(650, 590)
        self.setStyleSheet(
            "QDialog{background:#edf3f6;} QTabWidget::pane{background:white;border:1px solid #d3e0e6;"
            "border-radius:10px;} QLabel{color:#273946;} QPushButton{min-height:30px;padding:5px 12px;"
            "background:#dcefeb;color:#155a52;border:0;border-radius:8px;font-weight:600;}"
        )
        root = QVBoxLayout(self)
        intro = QLabel("正常模式负责提醒你；军官模式会持续记录偏差，并在严重事项时要求说明。")
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.today_page = QWidget()
        self.week_page = QWidget()
        self.ledger_page = QWidget()
        self.settings_page = QWidget()
        self.tabs.addTab(self.today_page, "今日")
        self.tabs.addTab(self.week_page, "本周")
        self.tabs.addTab(self.ledger_page, "纪律账本")
        self.tabs.addTab(self.settings_page, "计划与模式")
        self._build_today_page()
        self._build_week_page()
        self._build_ledger_page()
        self._build_settings_page()
        self._render_summaries()
        buttons = QHBoxLayout()
        buttons.addStretch()
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        root.addLayout(buttons)

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
        self.snooze_button = QPushButton("今日休息 / 暂停训导")
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

    def _build_settings_page(self) -> None:
        layout = QVBoxLayout(self.settings_page)
        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItem("关闭训导", "off")
        self.mode.addItem("正常模式", "normal")
        self.mode.addItem("军官模式", "officer")
        form.addRow("监督模式", self.mode)
        self.weekly_target = QSpinBox()
        self.weekly_target.setRange(0, 168)
        self.weekly_target.setSuffix(" 小时 / 周")
        form.addRow("周目标", self.weekly_target)
        self.daily_targets: dict[str, QSpinBox] = {}
        day_grid = QGridLayout()
        for index, (key, label) in enumerate(zip(WEEKDAYS, DAY_LABELS)):
            spin = QSpinBox()
            spin.setRange(0, 24)
            spin.setSuffix(" 小时")
            self.daily_targets[key] = spin
            day_grid.addWidget(QLabel(label), 0, index)
            day_grid.addWidget(spin, 1, index)
        form.addRow("基础日计划", day_grid)
        self.start_time = QTimeEdit()
        self.start_time.setDisplayFormat("HH:mm")
        form.addRow("计划开工", self.start_time)
        self.finish_time = QTimeEdit()
        self.finish_time.setDisplayFormat("HH:mm")
        form.addRow("计划下班", self.finish_time)
        self.late_grace = QSpinBox(); self.late_grace.setRange(0, 240); self.late_grace.setSuffix(" 分钟")
        form.addRow("迟到宽限", self.late_grace)
        self.break_limit = QSpinBox(); self.break_limit.setRange(1, 480); self.break_limit.setSuffix(" 分钟")
        form.addRow("计划休息", self.break_limit)
        self.early_grace = QSpinBox(); self.early_grace.setRange(0, 240); self.early_grace.setSuffix(" 分钟")
        form.addRow("提前下班宽限", self.early_grace)
        self.catchup = QComboBox()
        self.catchup.addItem("均匀补账", "even")
        self.catchup.addItem("前置补账", "frontload")
        self.catchup.addItem("自定义每日额外时长", "custom")
        form.addRow("周内补账方式", self.catchup)
        self.custom_catchup: dict[str, QSpinBox] = {}
        custom_grid = QGridLayout()
        for index, (key, label) in enumerate(zip(WEEKDAYS, DAY_LABELS)):
            spin = QSpinBox()
            spin.setRange(0, 24)
            spin.setSuffix(" 小时")
            self.custom_catchup[key] = spin
            custom_grid.addWidget(QLabel(label), 0, index)
            custom_grid.addWidget(spin, 1, index)
        form.addRow("每天额外追赶", custom_grid)
        self.progress_reminders = QCheckBox("提醒明显落后的日进度（每天最多按固定时段触发）")
        form.addRow("进度提醒", self.progress_reminders)
        self.carry = QCheckBox("跨周追账（默认关闭）")
        self.carry.setToolTip("开启后上周缺口会增加到新周；关闭时历史留档，新周重新计算。")
        form.addRow("周结算", self.carry)
        layout.addLayout(form)
        privacy = QLabel("登录后，计划和纪律账本会按账号同步到六毛服务端与本人其他设备；未登录时仅保存在本机。训导主任授权流程尚未开放，其他用户不会看到你的账本。")
        privacy.setWordWrap(True)
        privacy.setStyleSheet("color:#5c6d76;padding:8px;")
        layout.addWidget(privacy)
        save = QPushButton("保存计划")
        save.clicked.connect(self._save_settings)
        layout.addWidget(save, alignment=Qt.AlignmentFlag.AlignRight)
        supervisor = QPushButton("训导主任授权与监督者报告…")
        supervisor.setToolTip("设置好友互相授权，或查看已授权给你的纪律摘要。")
        supervisor.setEnabled(self.supervisor_open_callback is not None)
        if self.supervisor_open_callback is not None:
            supervisor.clicked.connect(self.supervisor_open_callback)
        layout.addWidget(supervisor, alignment=Qt.AlignmentFlag.AlignRight)
        self._load_settings()

    def _load_settings(self) -> None:
        settings = self.store.settings
        self.mode.setCurrentIndex(max(0, self.mode.findData(settings.mode)))
        self.weekly_target.setValue(settings.weekly_target_minutes // 60)
        for day, spin in self.daily_targets.items():
            spin.setValue(settings.daily_target_minutes.get(day, 0) // 60)
        hour, minute = (int(part) for part in settings.start_time.split(":"))
        self.start_time.setTime(QTime.fromString(f"{hour:02d}:{minute:02d}", "HH:mm"))
        hour, minute = (int(part) for part in settings.finish_time.split(":"))
        self.finish_time.setTime(QTime.fromString(f"{hour:02d}:{minute:02d}", "HH:mm"))
        self.late_grace.setValue(settings.late_grace_minutes)
        self.break_limit.setValue(settings.break_limit_minutes)
        self.early_grace.setValue(settings.early_finish_grace_minutes)
        self.catchup.setCurrentIndex(max(0, self.catchup.findData(settings.catchup_strategy)))
        for day, spin in self.custom_catchup.items():
            spin.setValue(settings.custom_catchup_minutes.get(day, 0) // 60)
        self.progress_reminders.setChecked(settings.progress_reminders)
        self.carry.setChecked(settings.carry_across_weeks)

    def _save_settings(self) -> None:
        previous = self.store.settings
        targets = {day: spin.value() * 60 for day, spin in self.daily_targets.items()}
        settings = DisciplineSettings.from_dict({
            **vars(previous), "mode": self.mode.currentData(),
            "weekly_target_minutes": self.weekly_target.value() * 60,
            "daily_target_minutes": targets,
            "start_time": self.start_time.time().toString("HH:mm"),
            "finish_time": self.finish_time.time().toString("HH:mm"),
            "late_grace_minutes": self.late_grace.value(),
            "break_limit_minutes": self.break_limit.value(),
            "early_finish_grace_minutes": self.early_grace.value(),
            "catchup_strategy": self.catchup.currentData(),
            "custom_catchup_minutes": {day: spin.value() * 60 for day, spin in self.custom_catchup.items()},
            "progress_reminders": self.progress_reminders.isChecked(),
            "carry_across_weeks": self.carry.isChecked(),
        })
        self.store.update_settings(settings)
        self._render_summaries()

    def _snooze_today(self) -> None:
        settings = self.store.settings
        from .discipline import as_beijing
        next_day = as_beijing().date() + timedelta(days=1)
        settings.snooze_until = f"{next_day.isoformat()}T00:00:00+08:00"
        self.store.update_settings(settings)
        self.snooze_button.setText("训导已暂停至明天")

    def _render_summaries(self) -> None:
        today_seconds, week_seconds = self.progress_provider()
        from .discipline import as_beijing
        now_day = as_beijing().date()
        today = self.engine.daily_summary(now_day, today_seconds, week_seconds)
        target = int(today["daily_target_seconds"])
        actual = int(today["today_seconds"])
        gap = int(today["daily_gap_seconds"])
        mode_text = {"off": "关闭", "normal": "正常模式", "officer": "军官模式"}[today["mode"]]
        self.today_summary.setText(
            f"<h2>今日纪律 · {mode_text}</h2>"
            f"计划开工：{today['planned_start']}　实际开工：{today['actual_start'] or '尚未开工'}<br>"
            f"今日专注：{format_work_duration(actual)} / {format_work_duration(target)}<br>"
            f"今日缺口：{format_work_duration(gap)}<br>"
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
            f"今日基础计划：{format_work_duration(target)}<br>"
            f"今日追赶目标：{format_work_duration(self.engine.progress(today_seconds, week_seconds).catchup_target_seconds)}<br>"
            f"今日超额可追回前期缺口：{format_work_duration(today['caught_up_today_seconds'])}"
        )
        rows = self.store.events_for_week(now_day)
        self.ledger.setRowCount(len(rows))
        labels = {
            "start_work": "开工", "late_start": "迟到", "late_start_warning": "迟到提醒",
            "start_break": "开始休息", "end_break": "结束休息", "long_break": "长休息",
            "long_break_warning": "休息提醒", "finish_work": "下班", "early_finish": "提前下班",
            "focus_shortfall": "日目标缺口", "behind_schedule": "进度落后",
            "weekly_shortfall": "周目标结算",
            "daily_report": "每日总结",
        }
        for row_index, row in enumerate(rows):
            moment = str(row.get("occurred_at") or "")
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            description = str(metadata.get("detail") or metadata.get("minutes_late") or metadata.get("duration_seconds") or "已记录")
            if isinstance(description, int):
                description = f"{description} 分钟"
            explanation = row.get("explanation")
            state = "已说明" if explanation else "待说明" if row.get("requires_explanation") else "已记录"
            values = (str(row.get("event_date") or ""), labels.get(str(row.get("event_type")), str(row.get("event_type"))), f"{moment[11:16]} · {description}", state)
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, str(row.get("id") or ""))
                if row.get("requires_explanation") and not explanation:
                    item.setForeground(QColor("#a33a3a"))
                self.ledger.setItem(row_index, column, item)

    def _explain_selected(self) -> None:
        row = self.ledger.currentRow()
        if row < 0:
            return
        event_id = str(self.ledger.item(row, 0).data(Qt.ItemDataRole.UserRole) or "")
        event = next((item for item in self.store.events if item.get("id") == event_id), None)
        if not event or not event.get("requires_explanation") or event.get("explanation"):
            return
        from PySide6.QtWidgets import QInputDialog
        options = tuple(EXPLANATION_LABELS.values())
        reason, accepted = QInputDialog.getItem(self, "说明这条记录", "请选择原因：", options, 0, False)
        if not accepted:
            return
        key = next((name for name, label in EXPLANATION_LABELS.items() if label == reason), "other")
        self.store.explain(event_id, key)
        self._render_summaries()
