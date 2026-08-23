"""按需计算并展示六毛工作报告，不生成或保存报告图片。

报告只读取当前登录账号的本地专注历史、当前计时器和最近一次自习室同步
快照。日度、本周和月度页签在窗口打开时计算，并在窗口保持打开时定时刷新；
不会为每一天创建 PNG，也不会因为打开报告额外请求 Supabase。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable

from PySide6.QtCore import QRect, QTimer, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QPainter, QPen
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
    QToolTip,
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
QFrame#reportChart { background: #ffffff; border: 1px solid #d6e4e8; border-radius: 18px; }
QLabel#reportTitle { color: #173d55; font-size: 23px; font-weight: 700; }
QLabel#reportSubtitle, QLabel#reportHint { color: #647b88; }
QLabel#reportHeroValue { color: #008b83; font-size: 30px; font-weight: 700; }
QLabel#reportMetricLabel { color: #6c7f89; font-size: 11px; }
QLabel#reportMetricValue { color: #21475d; font-size: 16px; font-weight: 650; }
QLabel#reportSection { color: #21475d; font-size: 15px; font-weight: 650; }
QLabel#reportNote { background: #e0f2ee; color: #28665b; border-radius: 12px; padding: 10px; }
QLabel#reportWarning { background: #fff3df; color: #855d2c; border: 1px solid #edd2a7; border-radius: 12px; padding: 10px; }
QProgressBar#reportBar { background: #e7eff1; border: none; border-radius: 5px; height: 10px; text-align: right; }
QProgressBar#reportBar::chunk { background: #51b8aa; border-radius: 5px; }
QLabel#reportTodayLabel { color: #008b83; font-weight: 700; }
QPushButton#reportClose { background: #cfece7; color: #1f5d57; border: none; border-radius: 10px; padding: 8px 20px; }
QPushButton#reportClose:hover { background: #bce3dc; }
QPushButton#reportFinish { background: #fff0d7; color: #8a5b25; border: 1px solid #edcf9c; border-radius: 10px; padding: 8px 16px; }
QPushButton#reportFinish:hover { background: #ffe4b4; }
QPushButton#reportFinish:disabled { background: #edf1f2; color: #91a0a6; border-color: #dbe3e5; }
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


def _rest_state(summary: Any, current_status: str, now: datetime) -> str:
    """Return a clearly labelled local rest-state inference."""

    if current_status == "focus":
        return "工作中"
    if current_status == "rest":
        return "休息中"
    if now.hour >= 23 or now.hour < 6:
        return "可能已入睡（本地推断）"
    if int(getattr(summary, "today_seconds", 0) or 0) > 0:
        return "清醒 / 暂未工作"
    return "暂无足够数据"


def _signed_delta(seconds: int | None) -> str:
    if seconds is None:
        return "暂无可比数据"
    value = int(seconds or 0)
    sign = "+" if value >= 0 else "−"
    return f"较昨日 {sign}{format_work_duration(abs(value))}"


def build_work_report(
    analytics: FocusAnalyticsStore,
    timer: WorkTimerModel,
    daily_stats: DailyCompanionStats,
    *,
    best_buddy: str = "暂无自习室排行榜数据",
    focus_snapshot: Any | None = None,
    task_stats: dict[str, dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build an account-scoped report snapshot without writing a file."""

    moment = now or datetime.now(BEIJING_TIMEZONE)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=BEIJING_TIMEZONE)
    summary = analytics.summary(moment)
    daily_stats_snapshot = daily_stats.snapshot()
    snapshot_status = ""
    snapshot_today = 0
    snapshot_session = 0
    snapshot_room = ""
    if focus_snapshot is not None:
        if isinstance(focus_snapshot, dict):
            snapshot_status = str(focus_snapshot.get("status") or "")
            snapshot_today = max(0, int(focus_snapshot.get("today_seconds") or 0))
            snapshot_session = max(0, int(focus_snapshot.get("session_seconds") or 0))
            snapshot_room = str(focus_snapshot.get("room_id") or "")
        else:
            snapshot_status = str(getattr(focus_snapshot, "status", "") or "")
            snapshot_today = max(0, int(getattr(focus_snapshot, "today_seconds", 0) or 0))
            snapshot_session = max(0, int(getattr(focus_snapshot, "session_seconds", 0) or 0))
            snapshot_room = str(getattr(focus_snapshot, "room_id", "") or "")
    live_today = max(0, int(timer.today_seconds()), snapshot_today)
    current_status = snapshot_status if snapshot_status in {"focus", "rest", "idle"} else (
        "focus" if bool(timer.is_running)
        else "rest" if bool(timer.has_active_session)
        else "idle"
    )
    report: dict[str, Any] = {
        "generated_at": moment.astimezone(BEIJING_TIMEZONE).strftime("%H:%M:%S"),
        "best_buddy": str(best_buddy or "暂无自习室排行榜数据"),
        "sleep_note": _sleep_inference(summary, moment.astimezone(BEIJING_TIMEZONE)),
        "current_status": current_status,
        "current_status_label": {"focus": "工作中", "rest": "休息中", "idle": "未开始工作"}[current_status],
        "rest_state": _rest_state(summary, current_status, moment.astimezone(BEIJING_TIMEZONE)),
        "current_streak_days": int(summary.current_streak_days),
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
    day["week_total_seconds"] = int(report["week"]["total_seconds"])
    day["yesterday_seconds"] = summary.yesterday_seconds
    day["difference_vs_yesterday_seconds"] = summary.difference_vs_yesterday_seconds
    day["current_streak_days"] = int(summary.current_streak_days)
    day["rest_state"] = report["rest_state"]
    day["current_status_label"] = report["current_status_label"]
    day["focus_session_seconds"] = snapshot_session
    day["focus_room_id"] = snapshot_room
    if day.get("daily"):
        day["daily"][-1]["seconds"] = max(int(day["daily"][-1].get("seconds", 0) or 0), live_today)

    def overlay_live_today(item: dict[str, Any]) -> None:
        """Overlay the open timer once, without double-counting a period."""

        rows = item.get("daily") or []
        today_row = next((row for row in rows if row.get("is_today")), None)
        if not isinstance(today_row, dict):
            return
        stored_day = max(0, int(today_row.get("seconds", 0) or 0))
        visible_today = max(stored_day, live_today)
        if visible_today > stored_day:
            item["total_seconds"] = int(item.get("total_seconds", 0) or 0) + visible_today - stored_day
            today_row["seconds"] = visible_today

    for key in ("day", "week", "month"):
        item = report[key]
        if isinstance(task_stats, dict) and isinstance(task_stats.get(key), dict):
            item["completed_task_count"] = max(
                0,
                int(task_stats[key].get("completed_tasks", 0) or 0),
            )
        if key != "day":
            overlay_live_today(item)
        item["total_seconds"] = max(
            int(item.get("total_seconds", 0) or 0),
            sum(max(0, int(row.get("seconds", 0) or 0)) for row in item.get("daily") or []),
        )
        longest = max(0, int(item.get("longest_focus_seconds", 0) or 0))
        item["average_session_seconds"] = min(
            max(0, int(item.get("average_session_seconds", 0) or 0)),
            longest,
        )
        item["deep_focus_seconds"] = min(
            max(0, int(item.get("deep_focus_seconds", item.get("high_quality_seconds", 0)) or 0)),
            int(item["total_seconds"]),
        )
        item["quality_label"] = _quality_label(int(item.get("average_quality", 0) or 0))

    day["week_total_seconds"] = int(report["week"]["total_seconds"])
    report["data_quality"] = {
        "average_not_above_longest": all(
            int(report[key].get("average_session_seconds", 0) or 0)
            <= int(report[key].get("longest_focus_seconds", 0) or 0)
            for key in ("day", "week", "month")
        ),
        "deep_focus_not_above_total": all(
            int(report[key].get("deep_focus_seconds", 0) or 0)
            <= int(report[key].get("total_seconds", 0) or 0)
            for key in ("day", "week", "month")
        ),
    }
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


class ReportBarChart(QWidget):
    """Draw a compact bar chart without creating image files."""

    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        hourly: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._rows = [row for row in rows if isinstance(row, dict)]
        self._hourly = bool(hourly)
        self._bar_rects: list[QRect] = []
        self._hover_index = -1
        # Leave enough room for a real x-axis: daily charts show date +
        # weekday, hourly charts show the hour tick.  The old 178px height
        # clipped those labels and left only the misleading caption visible.
        self.setMinimumHeight(220)
        self.setMinimumWidth(360)
        self.setMouseTracking(True)

    def _tooltip_for(self, index: int) -> str:
        if not (0 <= index < len(self._rows)):
            return ""
        row = self._rows[index]
        seconds = max(0, int(row.get("seconds", 0) or 0))
        if self._hourly:
            hour = max(0, min(23, int(row.get("hour", index) or 0)))
            end_hour = (hour + 1) % 24
            title = f"{hour:02d}:00–{end_hour:02d}:00"
            detail = f"工作时长：{format_work_duration(seconds)}"
        else:
            date = str(row.get("date") or row.get("label") or "未知日期")
            weekday = str(row.get("weekday") or "")
            title = f"{date} {weekday}".strip()
            detail = (
                f"工作时长：{format_work_duration(seconds)}\n"
                f"完成专注段：{int(row.get('rounds', 0) or 0)} 段"
            )
        if row.get("trusted") is False:
            detail += "\n该日期数据已剔除（旧版异常记录）"
        return f"{title}\n{detail}"

    def mouseMoveEvent(self, event) -> None:  # pragma: no cover - rendered by Qt
        position = event.position().toPoint()
        index = next(
            (item for item, rect in enumerate(self._bar_rects) if rect.contains(position)),
            -1,
        )
        if index != self._hover_index:
            self._hover_index = index
            self.update()
        if index >= 0:
            QToolTip.showText(QCursor.pos(), self._tooltip_for(index), self)
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # pragma: no cover - rendered by Qt
        self._hover_index = -1
        QToolTip.hideText()
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # pragma: no cover - rendered by Qt
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        plot = self.rect().adjusted(78, 12, 12, 70)
        self._bar_rects = []
        values = [max(0, int(row.get("seconds", 0) or 0)) for row in self._rows]
        maximum = max(values or [1])

        painter.setPen(QPen(QColor("#e3edef"), 1))
        for ratio in (0.0, 0.5, 1.0):
            y = plot.bottom() - int(plot.height() * ratio)
            painter.drawLine(plot.left(), y, plot.right(), y)
        painter.setPen(QColor("#7b8d96"))
        tick_values = (maximum, maximum // 2, 0) if maximum else (0,)
        tick_ratios = (1.0, 0.5, 0.0) if maximum else (0.0,)
        for tick_value, ratio in zip(tick_values, tick_ratios):
            y = plot.bottom() - int(plot.height() * ratio)
            painter.drawText(
                0,
                y - 9,
                plot.left() - 8,
                18,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                format_work_duration(tick_value),
            )

        count = max(1, len(self._rows))
        gap = 4 if count <= 12 else 2
        bar_width = max(3, int((plot.width() - gap * (count - 1)) / count))
        # Keep all daily labels readable, while still showing a useful set of
        # hour ticks instead of squeezing 24 labels into a narrow card.
        if self._hourly:
            label_step = 3
        else:
            # The report only uses daily bars for a week at a time.  Show
            # every day so the x-axis is self-explanatory; the month view uses
            # a calendar heatmap instead of squeezing 31 dates into bars.
            label_step = 1
        for index, (row, value) in enumerate(zip(self._rows, values)):
            x = plot.left() + index * (bar_width + gap)
            height = int(plot.height() * value / maximum) if maximum else 0
            y = plot.bottom() - height
            self._bar_rects.append(QRect(x, plot.top(), bar_width, plot.height()))
            color = QColor("#36a99d") if row.get("is_today") else QColor("#75c8bd")
            if self._hourly:
                color = QColor("#4389ad") if value else QColor("#dfecef")
            if index == self._hover_index:
                color = QColor("#e19a62")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            if height > 0:
                painter.drawRoundedRect(x, y, bar_width, max(3, height), 4, 4)
            if index % label_step == 0 or index == count - 1:
                painter.setPen(QColor("#647b88"))
                if self._hourly:
                    hour = max(0, min(23, int(row.get("hour", index) or 0)))
                    label = f"{hour:02d}:00"
                else:
                    # Keep date and weekday on separate lines so the x-axis
                    # remains readable at the normal report width.
                    label = str(row.get("label") or row.get("display_label") or "")
                if self._hourly:
                    label_text = label
                    label_height = 20
                else:
                    weekday = str(row.get("weekday") or "")
                    label_text = f"{label}\n{weekday}" if weekday else label
                    label_height = 38
                painter.drawText(
                    x - 8,
                    plot.bottom() + 6,
                    bar_width + 16,
                    label_height,
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                    label_text,
                )
        painter.end()


class ReportCalendarHeatmap(QWidget):
    """Small month calendar showing one reliable daily duration per cell."""

    _WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")

    def __init__(self, rows: list[dict[str, Any]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows = [row for row in rows if isinstance(row, dict)]
        self._cells: list[tuple[QRect, dict[str, Any]]] = []
        self.setMinimumHeight(178)
        self.setMinimumWidth(360)
        self.setMouseTracking(True)

    def paintEvent(self, event) -> None:  # pragma: no cover - rendered by Qt
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bounds = self.rect().adjusted(8, 4, 8, 8)
        header_height = 22
        left = bounds.left() + 28
        top = bounds.top() + header_height
        columns = 7
        weeks = max(1, (len(self._rows) + 6) // 7 + 1)
        cell_width = max(22, (bounds.width() - 28) // columns)
        cell_height = max(22, (bounds.height() - header_height) // weeks)
        self._cells = []
        painter.setPen(QColor("#6c7f89"))
        for column, label in enumerate(self._WEEKDAYS):
            painter.drawText(
                left + column * cell_width,
                bounds.top(),
                cell_width,
                header_height,
                Qt.AlignmentFlag.AlignCenter,
                label,
            )
        values = [max(0, int(row.get("seconds", 0) or 0)) for row in self._rows]
        maximum = max(values or [1])
        for index, row in enumerate(self._rows):
            try:
                focus_date = date.fromisoformat(str(row.get("date") or ""))
            except ValueError:
                continue
            slot = focus_date.day + date(focus_date.year, focus_date.month, 1).weekday()
            column = slot % 7
            week = slot // 7
            cell = QRect(
                left + column * cell_width + 2,
                top + week * cell_height + 2,
                max(16, cell_width - 4),
                max(16, cell_height - 4),
            )
            self._cells.append((cell, row))
            value = values[index] if index < len(values) else 0
            ratio = value / maximum if maximum else 0
            if value <= 0:
                color = QColor("#edf3f4")
            elif ratio < 0.25:
                color = QColor("#cfe9e4")
            elif ratio < 0.6:
                color = QColor("#86cec2")
            else:
                color = QColor("#36a99d")
            painter.setPen(QPen(QColor("#d6e4e8"), 1))
            painter.setBrush(QBrush(color))
            painter.drawRoundedRect(cell, 5, 5)
            painter.setPen(QColor("#21475d"))
            painter.drawText(cell, Qt.AlignmentFlag.AlignCenter, str(focus_date.day))
            if row.get("is_today"):
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor("#e19a62"), 2))
                painter.drawRoundedRect(cell.adjusted(1, 1, -1, -1), 5, 5)
        painter.end()

    def mouseMoveEvent(self, event) -> None:  # pragma: no cover - rendered by Qt
        position = event.position().toPoint()
        for cell, row in self._cells:
            if cell.contains(position):
                focus_date = str(row.get("date") or "未知日期")
                weekday = str(row.get("weekday") or "")
                QToolTip.showText(
                    QCursor.pos(),
                    f"{focus_date} {weekday}\n"
                    f"工作时长：{format_work_duration(int(row.get('seconds', 0) or 0))}\n"
                    f"专注段：{int(row.get('rounds', 0) or 0)} 段",
                    self,
                )
                return
        QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # pragma: no cover - rendered by Qt
        QToolTip.hideText()
        super().leaveEvent(event)


class WorkReportDialog(QDialog):
    """Live day/week/month report window with no image-generation side effect."""

    finish_requested = Signal()
    closed = Signal()

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
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.setMinimumSize(620, 620)
        self.resize(720, 760)
        self.setStyleSheet(REPORT_STYLE)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(12)
        title = QLabel(f"{self._pet_name}工作报告")
        title.setObjectName("reportTitle")
        subtitle = QLabel("今天六毛陪你又往前一点 · 与搭子自习室共用 FocusSession · 实时生成")
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
        self.finish_button = QPushButton("结束本轮工作")
        self.finish_button.setObjectName("reportFinish")
        self.finish_button.clicked.connect(self.finish_requested.emit)
        footer.addWidget(self.finish_button)
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

    def closeEvent(self, event) -> None:  # pragma: no cover - native window event
        self._refresh_timer.stop()
        self.closed.emit()
        super().closeEvent(event)

    def refresh(self) -> None:
        try:
            report = self._snapshot_provider()
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            report = {"error": f"报告暂时无法读取：{exc}"}
        self.finish_button.setEnabled(
            report.get("current_status") in {"focus", "rest"}
            if not report.get("error")
            else False
        )
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

    @staticmethod
    def _chart_card(
        title: str,
        subtitle: str,
        rows: list[dict[str, Any]],
        *,
        hourly: bool = False,
    ) -> QFrame:
        frame = QFrame()
        frame.setObjectName("reportChart")
        box = QVBoxLayout(frame)
        box.setContentsMargins(14, 12, 14, 10)
        box.setSpacing(4)
        heading = QLabel(title)
        heading.setObjectName("reportSection")
        box.addWidget(heading)
        if subtitle:
            note = QLabel(subtitle)
            note.setObjectName("reportHint")
            note.setWordWrap(True)
            box.addWidget(note)
        box.addWidget(ReportBarChart(rows, hourly=hourly), 1)
        return frame

    @staticmethod
    def _heatmap_card(title: str, subtitle: str, rows: list[dict[str, Any]]) -> QFrame:
        frame = QFrame()
        frame.setObjectName("reportChart")
        box = QVBoxLayout(frame)
        box.setContentsMargins(14, 12, 14, 10)
        box.setSpacing(4)
        heading = QLabel(title)
        heading.setObjectName("reportSection")
        box.addWidget(heading)
        note = QLabel(subtitle)
        note.setObjectName("reportHint")
        note.setWordWrap(True)
        box.addWidget(note)
        box.addWidget(ReportCalendarHeatmap(rows), 1)
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
        if key == "day":
            overview = QLabel(
                f"{_signed_delta(data.get('difference_vs_yesterday_seconds'))}  ·  "
                f"当前：{data.get('current_status_label') or report.get('current_status_label', '未开始工作')}"
            )
            overview.setObjectName("reportHint")
            hero_layout.addWidget(overview)
        elif key == "week":
            overview = QLabel(
                f"工作 {int(data.get('active_days', 0) or 0)} / 7 天  ·  "
                f"日均 {format_work_duration(total // max(1, int(data.get('active_days', 0) or 0)))}"
            )
            overview.setObjectName("reportHint")
            hero_layout.addWidget(overview)
        else:
            overview = QLabel(
                f"工作 {int(data.get('active_days', 0) or 0)} 天  ·  "
                f"日均 {format_work_duration(total // max(1, int(data.get('active_days', 0) or 0)))}"
            )
            overview.setObjectName("reportHint")
            hero_layout.addWidget(overview)
        generated = QLabel(f"最后更新 {report.get('generated_at', '--:--:--')}")
        generated.setObjectName("reportHint")
        hero_layout.addWidget(generated)
        layout.addWidget(hero)

        quality = data.get("data_quality") or {}
        if not bool(quality.get("trusted", True)):
            days = len(quality.get("untrusted_days") or [])
            warning = QLabel(f"ⓘ 已排除 {days} 天旧版异常计时记录，未纳入本页统计。")
            warning.setObjectName("reportWarning")
            warning.setWordWrap(True)
            layout.addWidget(warning)

        daily_rows = data.get("daily") or []
        if key == "day":
            daily_rows = (report.get("week") or {}).get("daily") or daily_rows
        if key == "month":
            layout.addWidget(
                self._heatmap_card(
                    "本月工作日历",
                    "颜色越深表示当天有效工作时间越长；悬停日期可查看具体数值。",
                    daily_rows,
                )
            )
        else:
            chart_period = "本周" if key == "week" else "本周（今日高亮）"
            layout.addWidget(
                self._chart_card(
                    f"{chart_period}工作时长",
                    "横轴显示日期和星期，纵轴显示有效工作时长；悬停柱子可查看明细。",
                    daily_rows,
                )
            )
        rhythm = {"day": "今天工作节律", "week": "本周工作节律", "month": "本月工作节律"}[key]
        layout.addWidget(
            self._chart_card(
                rhythm,
                "横轴为开始时间（每小时），纵轴为有效工作时长；悬停柱子可查看小时区间。",
                data.get("hourly") or [],
                hourly=True,
            )
        )

        core_grid = QGridLayout()
        core_grid.setSpacing(10)
        core_metrics = [
            ("有效专注段", f"{int(data.get('started_rounds', 0) or 0)} 段"),
            ("最长连续专注", format_work_duration(int(data.get("longest_focus_seconds", 0) or 0))),
            ("中间打断次数", f"{int(data.get('interruptions', 0) or 0)} 次"),
            ("平均专注段时长", format_work_duration(int(data.get("average_session_seconds", 0) or 0))),
            ("深度专注时间", format_work_duration(int(data.get("deep_focus_seconds", 0) or 0))),
            (
                "完成任务" if data.get("completed_task_count") is not None else "完成专注段",
                f"{int(data.get('completed_task_count', data.get('completed_rounds', 0)) or 0)} "
                f"{'项' if data.get('completed_task_count') is not None else '段'}",
            ),
        ]
        for index, (label, value) in enumerate(core_metrics):
            core_grid.addWidget(self._metric(label, value), index // 2, index % 2)
        layout.addLayout(core_grid)

        detail_title = QLabel("工作节奏")
        detail_title.setObjectName("reportSection")
        layout.addWidget(detail_title)
        grid = QGridLayout()
        grid.setSpacing(10)
        if key == "day":
            metrics = [
                (
                    "今日节奏",
                    f"{data.get('first_started_at', '暂无记录')} 开始 · "
                    f"{data.get('last_ended_at', '暂无记录')} 结束",
                ),
                ("本周工作时间", format_work_duration(int(data.get("week_total_seconds", 0) or 0))),
                ("当前连续工作天数", f"{int(report.get('current_streak_days', 0) or 0)} 天"),
                ("最强时段", str(data.get("strongest_window") or "暂无足够数据")),
            ]
        elif key == "week":
            metrics = [
                ("工作天数", f"{int(data.get('active_days', 0) or 0)} / 7 天"),
                ("日均工作", format_work_duration(total // max(1, int(data.get("active_days", 0) or 0)))),
                ("最强时段", str(data.get("strongest_window") or "暂无足够数据")),
                ("当前连续工作天数", f"{int(report.get('current_streak_days', 0) or 0)} 天"),
                ("本周最佳搭子", str(report.get("best_buddy") or "暂无可用排行榜数据")),
            ]
        else:
            metrics = [
                ("工作天数", f"{int(data.get('active_days', 0) or 0)} 天"),
                ("日均工作", format_work_duration(total // max(1, int(data.get("active_days", 0) or 0)))),
                ("最长连续工作", f"{int(report.get('current_streak_days', 0) or 0)} 天"),
                ("最强时段", str(data.get("strongest_window") or "暂无足够数据")),
            ]
        for index, (label, value) in enumerate(metrics):
            grid.addWidget(self._metric(label, value), index // 2, index % 2)
        layout.addLayout(grid)

        if key == "day":
            room_label = "已连接搭子自习室" if data.get("focus_room_id") else "个人自习室"
            session_card = self._metric(
                "当前工作状态",
                f"{report.get('current_status_label', '未开始工作')} · "
                f"本轮 {format_work_duration(int(data.get('focus_session_seconds', 0) or 0))} · {room_label}",
            )
            layout.addWidget(session_card)
            current_task = data.get("current_task") or {}
            task_text = str(current_task.get("title") or "当前没有绑定专注任务") if isinstance(current_task, dict) else "当前没有绑定专注任务"
            layout.addWidget(self._metric("当前任务", task_text))

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
            if bool(row.get("is_today")):
                label.setObjectName("reportTodayLabel")
            bar = QProgressBar()
            bar.setObjectName("reportBar")
            bar.setRange(0, maximum)
            bar.setValue(max(0, int(row.get("seconds", 0) or 0)))
            bar.setTextVisible(False)
            bar.setToolTip(
                f"{row.get('label', '--')} · {format_work_duration(int(row.get('seconds', 0) or 0))} · "
                f"{int(row.get('rounds', 0) or 0)} 段"
            )
            value = QLabel(format_work_duration(int(row.get("seconds", 0) or 0)))
            value.setMinimumWidth(70)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            line.addWidget(label)
            line.addWidget(bar, 1)
            line.addWidget(value)
            layout.addLayout(line)
