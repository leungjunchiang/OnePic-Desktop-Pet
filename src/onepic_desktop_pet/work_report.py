"""按需计算并展示六毛工作报告，不生成或保存报告图片。

报告只读取当前登录账号的本地专注历史、当前计时器和最近一次自习室同步
快照。日度、本周和月度页签在窗口打开时计算，并在窗口保持打开时定时刷新；
不会为每一天创建 PNG，也不会因为打开报告额外请求 Supabase。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .diary import DailyCompanionStats
from .focus_analytics import BEIJING_TIMEZONE, FocusAnalyticsStore
from .work_timer import WorkTimerModel, format_work_duration


REPORT_STYLE = """
QDialog#workReportDialog { background: #eef5f7; color: #243b4b; }
QFrame#reportHero { background: #ffffff; border: 1px solid #d6e4e8; border-radius: 20px; }
QFrame#reportMetric { background: #ffffff; border: 1px solid #d6e4e8; border-radius: 14px; }
QLabel#reportTitle { color: #173d55; font-size: 23px; font-weight: 700; }
QLabel#reportSubtitle, QLabel#reportHint { color: #647b88; }
QLabel#reportHeroValue { color: #008b83; font-size: 30px; font-weight: 700; }
QLabel#reportMetricLabel { color: #6c7f89; font-size: 11px; }
QLabel#reportMetricValue { color: #21475d; font-size: 16px; font-weight: 650; }
QLabel#reportSection { color: #21475d; font-size: 15px; font-weight: 650; }
QLabel#reportNote { background: #e0f2ee; color: #28665b; border-radius: 12px; padding: 10px; }
QProgressBar#reportBar { background: #e7eff1; border: none; border-radius: 5px; height: 10px; text-align: right; }
QProgressBar#reportBar::chunk { background: #51b8aa; border-radius: 5px; }
QPushButton#reportClose { background: #cfece7; color: #1f5d57; border: none; border-radius: 10px; padding: 8px 20px; }
QPushButton#reportClose:hover { background: #bce3dc; }
"""


def _quality_label(score: int) -> str:
    value = max(0, int(score))
    if not value:
        return "暂无足够数据"
    if value >= 82:
        return "专注质量较高"
    if value >= 62:
        return "状态比较稳定"
    return "容易被打断"


def _sleep_inference(summary: Any, now: datetime) -> str:
    """Explain the conservative sleep inference instead of pretending to measure sleep."""

    late_average = max(0, int(getattr(summary, "late_night_average_seconds", 0) or 0))
    if late_average:
        return (
            "作息线索：最近 7 天有 23:00 后的专注记录。六毛只能看到专注、暂停、锁屏或系统睡眠等事件，"
            "不能测量心率、睡眠阶段或真实睡眠质量。"
        )
    if now.hour >= 22 or now.hour < 6:
        return "作息线索：当前处在夜间时段，暂未发现晚间专注记录；这不等于已经入睡。"
    return "睡眠状态：暂无足够数据。六毛不能测量真实睡眠质量，不会把“没有操作”直接当作睡着，只会在系统锁屏/睡眠时暂停计时。"


def build_work_report(
    analytics: FocusAnalyticsStore,
    timer: WorkTimerModel,
    daily_stats: DailyCompanionStats,
    *,
    best_buddy: str = "暂无自习室排行榜数据",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build an account-scoped report snapshot without writing a file."""

    moment = now or datetime.now(BEIJING_TIMEZONE)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=BEIJING_TIMEZONE)
    summary = analytics.summary(moment)
    daily_stats_snapshot = daily_stats.snapshot()
    live_today = max(0, int(timer.today_seconds()))
    stored_today = max(0, int(summary.today_seconds or 0))
    live_delta = max(0, live_today - stored_today)
    report: dict[str, Any] = {
        "generated_at": moment.astimezone(BEIJING_TIMEZONE).strftime("%H:%M:%S"),
        "best_buddy": str(best_buddy or "暂无自习室排行榜数据"),
        "sleep_note": _sleep_inference(summary, moment.astimezone(BEIJING_TIMEZONE)),
        "day": analytics.period_summary("day", moment),
        "week": analytics.period_summary("week", moment),
        "month": analytics.period_summary("month", moment),
    }
    day = report["day"]
    day["total_seconds"] = max(int(day["total_seconds"]), live_today)
    day["completed_rounds"] = max(
        int(day["completed_rounds"]),
        int(daily_stats_snapshot.get("completed_tasks", 0) or 0),
    )
    day["longest_focus_seconds"] = max(
        int(day["longest_focus_seconds"]),
        int(daily_stats_snapshot.get("longest_focus_seconds", 0) or 0),
        int(summary.current_continuous_seconds or 0),
    )
    day["interruptions"] = max(int(day["interruptions"]), int(summary.today_interruptions or 0))
    day["quality_label"] = _quality_label(int(day["average_quality"]))
    day["touches"] = int(daily_stats_snapshot.get("touches", 0) or 0)
    day["pet_sleeps"] = int(daily_stats_snapshot.get("sleeps", 0) or 0)
    day["current_task"] = analytics.current_task()
    day["sleep_note"] = report["sleep_note"]
    if day.get("daily"):
        day["daily"][-1]["seconds"] = max(int(day["daily"][-1]["seconds"]), live_today)

    for key in ("week", "month"):
        item = report[key]
        item["total_seconds"] = int(item["total_seconds"]) + live_delta
        item["quality_label"] = _quality_label(int(item["average_quality"]))
        if item.get("daily"):
            item["daily"][-1]["seconds"] = max(int(item["daily"][-1]["seconds"]), live_today)
    return report


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        child = item.layout()
        if child is not None:
            _clear_layout(child)  # type: ignore[arg-type]


class WorkReportDialog(QDialog):
    """Live day/week/month report window with no image-generation side effect."""

    def __init__(
        self,
        snapshot_provider: Callable[[], dict[str, Any]],
        *,
        pet_name: str = "六毛",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._snapshot_provider = snapshot_provider
        self._pet_name = pet_name.strip() or "六毛"
        self.setObjectName("workReportDialog")
        self.setWindowTitle(f"{self._pet_name}工作报告")
        self.setMinimumSize(620, 620)
        self.resize(720, 760)
        self.setStyleSheet(REPORT_STYLE)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(12)
        title = QLabel(f"{self._pet_name}工作报告")
        title.setObjectName("reportTitle")
        subtitle = QLabel("按需实时生成 · 不保存每日图片 · 只读取当前账号的数据")
        subtitle.setObjectName("reportSubtitle")
        root.addWidget(title)
        root.addWidget(subtitle)
        self.tabs = QTabWidget(self)
        self._pages: dict[str, QVBoxLayout] = {}
        for key, label in (("day", "日度"), ("week", "本周"), ("month", "月度")):
            scroll = QScrollArea(self)
            scroll.setWidgetResizable(True)
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(4, 8, 10, 8)
            page_layout.setSpacing(12)
            scroll.setWidget(page)
            self.tabs.addTab(scroll, label)
            self._pages[key] = page_layout
        root.addWidget(self.tabs, 1)
        footer = QHBoxLayout()
        footer.addStretch(1)
        close = QPushButton("关闭")
        close.setObjectName("reportClose")
        close.clicked.connect(self.close)
        footer.addWidget(close)
        root.addLayout(footer)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(5000)
        self._refresh_timer.timeout.connect(self.refresh)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
        self._refresh_timer.start()

    def hideEvent(self, event) -> None:
        self._refresh_timer.stop()
        super().hideEvent(event)

    def refresh(self) -> None:
        try:
            report = self._snapshot_provider()
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            report = {"error": f"报告暂时无法读取：{exc}"}
        for key, layout in self._pages.items():
            _clear_layout(layout)
            if report.get("error"):
                label = QLabel(str(report["error"]))
                label.setWordWrap(True)
                layout.addWidget(label)
                continue
            self._render_period(layout, key, report)
            layout.addStretch(1)

    @staticmethod
    def _metric(label: str, value: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("reportMetric")
        box = QVBoxLayout(frame)
        box.setContentsMargins(12, 10, 12, 10)
        caption = QLabel(label)
        caption.setObjectName("reportMetricLabel")
        amount = QLabel(value)
        amount.setObjectName("reportMetricValue")
        amount.setWordWrap(True)
        box.addWidget(caption)
        box.addWidget(amount)
        return frame

    def _render_period(self, layout: QVBoxLayout, key: str, report: dict[str, Any]) -> None:
        data = report.get(key) or {}
        total = max(0, int(data.get("total_seconds", 0) or 0))
        title = {"day": "今天陪你工作", "week": "这周陪你工作", "month": "这个月陪你工作"}[key]
        hero = QFrame()
        hero.setObjectName("reportHero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(18, 16, 18, 16)
        hero_title = QLabel(title)
        hero_title.setObjectName("reportSection")
        hero_value = QLabel(format_work_duration(total))
        hero_value.setObjectName("reportHeroValue")
        hero_layout.addWidget(hero_title)
        hero_layout.addWidget(hero_value)
        generated = QLabel(f"最后更新 {report.get('generated_at', '--:--:--')}")
        generated.setObjectName("reportHint")
        hero_layout.addWidget(generated)
        layout.addWidget(hero)

        grid = QGridLayout()
        grid.setSpacing(10)
        metrics = [
            ("完成专注段", f"{int(data.get('completed_rounds', 0) or 0)} 段"),
            ("最长连续专注", format_work_duration(int(data.get("longest_focus_seconds", 0) or 0))),
            ("中间打断次数", f"{int(data.get('interruptions', 0) or 0)} 次"),
            ("专注质量", f"{data.get('average_quality', 0) or '暂无'} · {data.get('quality_label', '暂无足够数据')}"),
            ("活跃天数", f"{int(data.get('active_days', 0) or 0)} 天"),
            ("最佳搭子", str(report.get("best_buddy") or "暂无自习室排行榜数据")),
        ]
        for index, (label, value) in enumerate(metrics):
            grid.addWidget(self._metric(label, value), index // 2, index % 2)
        layout.addLayout(grid)

        if key == "day":
            extras = self._metric(
                f"{self._pet_name()}陪伴",
                f"摸摸 {int(data.get('touches', 0) or 0)} 次 · 六毛入睡动作 {int(data.get('pet_sleeps', 0) or 0)} 次",
            )
            layout.addWidget(extras)
            current_task = data.get("current_task") or {}
            task_text = str(current_task.get("title") or "当前没有绑定专注任务") if isinstance(current_task, dict) else "当前没有绑定专注任务"
            layout.addWidget(self._metric("当前任务", task_text))

        section = QLabel("作息与数据说明")
        section.setObjectName("reportSection")
        layout.addWidget(section)
        note = QLabel(str(data.get("sleep_note") or report.get("sleep_note") or ""))
        note.setObjectName("reportNote")
        note.setWordWrap(True)
        layout.addWidget(note)

        if key in {"week", "month"}:
            chart_title = QLabel("每日专注分布")
            chart_title.setObjectName("reportSection")
            layout.addWidget(chart_title)
            self._render_daily_bars(layout, data.get("daily") or [])

        hint = QLabel("报告在窗口打开时实时刷新；关闭窗口不会生成 PNG，也不会增加服务器报告数据。")
        hint.setObjectName("reportHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    @staticmethod
    def _render_daily_bars(layout: QVBoxLayout, rows: list[dict[str, Any]]) -> None:
        values = [max(0, int(row.get("seconds", 0) or 0)) for row in rows if isinstance(row, dict)]
        maximum = max(values or [1])
        for row in rows:
            if not isinstance(row, dict):
                continue
            line = QHBoxLayout()
            label = QLabel(str(row.get("label") or "--"))
            label.setMinimumWidth(48)
            bar = QProgressBar()
            bar.setObjectName("reportBar")
            bar.setRange(0, maximum)
            bar.setValue(max(0, int(row.get("seconds", 0) or 0)))
            bar.setTextVisible(False)
            value = QLabel(format_work_duration(int(row.get("seconds", 0) or 0)))
            value.setMinimumWidth(70)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            line.addWidget(label)
            line.addWidget(bar, 1)
            line.addWidget(value)
            layout.addLayout(line)
