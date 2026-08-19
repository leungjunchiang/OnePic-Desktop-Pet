"""Non-modal UI for managing and handling Lili alarms."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import QDateTime, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .alarm_manager import Alarm, AlarmManager, REPEAT_DAILY, REPEAT_ONCE, REPEAT_WEEKDAYS


ALARM_STYLE = """
QDialog#alarmCenter, QDialog#alarmEditor { background: #eef5f8; color: #24475b; }
QFrame#alarmCard { background: rgba(255, 249, 230, 248); color: #27313d;
    border: 2px solid #e7a84d; border-radius: 14px; }
QFrame#alarmCard QLabel { background: transparent; border: none; }
QFrame#alarmCard QPushButton { background: #e7f3f6; color: #24475b;
    border: 1px solid #8dbbc7; border-radius: 9px; padding: 5px 9px; }
QFrame#alarmCard QPushButton:hover { background: #fff0c8; border-color: #e74a4f; }
QListWidget { background: rgba(255,255,255,210); border: 1px solid #b4ccd5; border-radius: 10px; }
"""


def _no_focus(widget: QWidget) -> None:
    widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)


class AlarmCard(QFrame):
    """A persistent, non-modal alarm surface that never activates Lili."""

    start_requested = Signal(str)
    snooze_requested = Signal(str, int)
    dismiss_requested = Signal(str)

    def __init__(self, alarm: Alarm, parent=None) -> None:
        super().__init__(None)
        self.alarm = alarm
        self.setObjectName("alarmCard")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setStyleSheet(ALARM_STYLE)
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(7)
        heading = QLabel("⏰ 六毛闹钟")
        heading.setStyleSheet("font-size: 16px; font-weight: 700;")
        title = QLabel(str(alarm.title))
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(heading)
        layout.addWidget(title)
        sound_hint = QLabel(
            f"系统提示音 · 最长{max(0, int(alarm.max_ring_seconds or 0))}秒"
            if alarm.sound_enabled else "静音闹钟 · 只显示六毛提醒"
        )
        sound_hint.setStyleSheet("color:#607985;font-size:11px;")
        layout.addWidget(sound_hint)
        actions = QHBoxLayout()
        actions.setSpacing(5)
        start = QPushButton("开始工作" if alarm.linked_todo_id else "开始30分钟")
        start.clicked.connect(lambda: self.start_requested.emit(alarm.id))
        actions.addWidget(start)
        for minutes in (5, 10, 30):
            button = QPushButton(f"后{minutes}分")
            button.clicked.connect(lambda _checked=False, value=minutes: self.snooze_requested.emit(alarm.id, value))
            actions.addWidget(button)
        close = QPushButton("关闭")
        close.clicked.connect(lambda: self.dismiss_requested.emit(alarm.id))
        actions.addWidget(close)
        layout.addLayout(actions)
        for widget in (start, close):
            _no_focus(widget)
        if alarm.sound_enabled:
            self._sound_timer = QTimer(self)
            # QApplication.beep uses the platform's own short alert sound.
            # Repeat it gently instead of hammering the system speaker.
            self._sound_timer.setInterval(3_000)
            self._sound_timer.timeout.connect(QApplication.beep)
            self._sound_timer.start()
            QTimer.singleShot(0, QApplication.beep)
            self._sound_stop_timer = QTimer(self)
            self._sound_stop_timer.setSingleShot(True)
            self._sound_stop_timer.setInterval(max(0, int(alarm.max_ring_seconds or 60)) * 1_000)
            self._sound_stop_timer.timeout.connect(self._stop_sound)
            self._sound_stop_timer.start()
        else:
            self._sound_timer = None
            self._sound_stop_timer = None

    def _stop_sound(self) -> None:
        if self._sound_timer is not None:
            self._sound_timer.stop()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._sound_timer is not None:
            self._sound_timer.stop()
        if self._sound_stop_timer is not None:
            self._sound_stop_timer.stop()
        super().closeEvent(event)


class AlarmEditDialog(QDialog):
    def __init__(self, todos: list[Any], alarm: Alarm | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("alarmEditor")
        self.setWindowTitle("六毛闹钟")
        self.setStyleSheet(ALARM_STYLE)
        self.alarm = alarm
        form = QFormLayout(self)
        self.title = QLineEdit(alarm.title if alarm else "叫我开工", self)
        self.trigger = QDateTimeEdit(self)
        self.trigger.setCalendarPopup(True)
        self.trigger.setDisplayFormat("yyyy-MM-dd HH:mm")
        if alarm:
            self.trigger.setDateTime(QDateTime.fromString(alarm.trigger_at[:19], Qt.DateFormat.ISODate))
        else:
            self.trigger.setDateTime(QDateTime.currentDateTime().addSecs(60))
        self.repeat = QComboBox(self)
        self.repeat.addItem("一次性", REPEAT_ONCE)
        self.repeat.addItem("每天", REPEAT_DAILY)
        self.repeat.addItem("工作日", REPEAT_WEEKDAYS)
        self.repeat.addItem("指定星期", "weekly")
        self.weekdays = QWidget(self)
        weekday_layout = QHBoxLayout(self.weekdays)
        weekday_layout.setContentsMargins(0, 0, 0, 0)
        weekday_layout.setSpacing(4)
        self.weekday_checks: list[QCheckBox] = []
        for index, label in enumerate(("一", "二", "三", "四", "五", "六", "日")):
            check = QCheckBox(label, self.weekdays)
            check.setProperty("weekday_index", index)
            self.weekday_checks.append(check)
            weekday_layout.addWidget(check)
        self.repeat.currentIndexChanged.connect(self._toggle_weekdays)
        if alarm:
            repeat_value = "weekly" if alarm.repeat_rule.startswith("weekly:") else alarm.repeat_rule
            index = self.repeat.findData(repeat_value)
            self.repeat.setCurrentIndex(index if index >= 0 else 0)
            if alarm.repeat_rule.startswith("weekly:"):
                try:
                    selected_days = {
                        int(value) for value in alarm.repeat_rule.split(":", 1)[1].split(",")
                    }
                except ValueError:
                    selected_days = set()
                for check in self.weekday_checks:
                    check.setChecked(check.property("weekday_index") in selected_days)
        else:
            self.weekday_checks[datetime.now().astimezone().weekday()].setChecked(True)
        self.sound = QCheckBox("到点播放提示音", self)
        self.sound.setChecked(bool(alarm.sound_enabled) if alarm else True)
        self.sound_id = QComboBox(self)
        self.sound_id.addItem("系统提示音", "system")
        if alarm:
            sound_index = self.sound_id.findData(alarm.sound_id)
            if sound_index >= 0:
                self.sound_id.setCurrentIndex(sound_index)
        self.volume = QSpinBox(self)
        self.volume.setRange(0, 100)
        self.volume.setSuffix("%（系统提示音音量由系统设置控制）")
        self.volume.setValue(int(alarm.volume) if alarm else 60)
        self.allow_dnd = QCheckBox("允许穿透免打扰", self)
        self.allow_dnd.setChecked(bool(alarm.allow_during_dnd) if alarm else False)
        self.snooze = QSpinBox(self)
        self.snooze.setRange(1, 120)
        self.snooze.setSuffix(" 分钟")
        self.snooze.setValue(int(alarm.snooze_minutes) if alarm else 10)
        self.linked_todo = QComboBox(self)
        self.linked_todo.addItem("不绑定待办", "")
        for todo in todos:
            if not getattr(todo, "completed", False):
                self.linked_todo.addItem(str(todo.title), str(todo.id))
        if alarm and alarm.linked_todo_id:
            index = self.linked_todo.findData(alarm.linked_todo_id)
            if index >= 0:
                self.linked_todo.setCurrentIndex(index)
        form.addRow("内容", self.title)
        form.addRow("时间", self.trigger)
        form.addRow("重复", self.repeat)
        form.addRow("星期", self.weekdays)
        form.addRow("稍后默认", self.snooze)
        form.addRow("关联待办", self.linked_todo)
        form.addRow("", self.sound)
        form.addRow("铃声", self.sound_id)
        form.addRow("音量", self.volume)
        form.addRow("", self.allow_dnd)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._toggle_weekdays()

    def _toggle_weekdays(self) -> None:
        self.weekdays.setEnabled(self.repeat.currentData() == "weekly")

    def values(self) -> dict[str, Any]:
        repeat_rule = self.repeat.currentData()
        if repeat_rule == "weekly":
            days = [
                str(check.property("weekday_index"))
                for check in self.weekday_checks
                if check.isChecked()
            ]
            repeat_rule = "weekly:" + ",".join(days) if days else REPEAT_ONCE
        return {
            "title": self.title.text().strip() or "六毛闹钟",
            "trigger_at": self.trigger.dateTime().toString("yyyy-MM-ddTHH:mm:ss"),
            "repeat_rule": repeat_rule,
            "sound_enabled": self.sound.isChecked(),
            "sound_id": self.sound_id.currentData() or "system",
            "volume": self.volume.value(),
            "max_ring_seconds": 60,
            "allow_during_dnd": self.allow_dnd.isChecked(),
            "snooze_minutes": self.snooze.value(),
            "linked_todo_id": self.linked_todo.currentData() or None,
        }


class AlarmCenterDialog(QDialog):
    """A small local alarm manager; it is intentionally not modal."""

    changed = Signal()

    def __init__(self, alarms: AlarmManager, todos: list[Any], parent=None) -> None:
        super().__init__(parent)
        self.alarms = alarms
        self.todos = todos
        self.setObjectName("alarmCenter")
        self.setWindowTitle("六毛闹钟")
        self.setMinimumSize(520, 390)
        self.setStyleSheet(ALARM_STYLE)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("到点后六毛会来找你；可以开始工作、稍后提醒或关闭。闹钟不会抢其它软件焦点。"))
        self.list = QListWidget(self)
        self.list.itemDoubleClicked.connect(self._edit_selected)
        layout.addWidget(self.list)
        buttons = QHBoxLayout()
        add = QPushButton("+ 新建闹钟")
        edit = QPushButton("编辑")
        remove = QPushButton("删除")
        add.clicked.connect(self._add)
        edit.clicked.connect(self._edit_selected)
        remove.clicked.connect(self._delete_selected)
        buttons.addWidget(add)
        buttons.addStretch(1)
        buttons.addWidget(edit)
        buttons.addWidget(remove)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for alarm in self.alarms.items:
            repeat = {REPEAT_ONCE: "一次性", REPEAT_DAILY: "每天", REPEAT_WEEKDAYS: "工作日"}.get(alarm.repeat_rule, "每周")
            status = "已关闭" if not alarm.enabled else ("响铃中" if alarm.active else "已启用")
            item = QListWidgetItem(f"{alarm.title} · {alarm.trigger_at[:16].replace('T', ' ')} · {repeat} · {status}")
            item.setData(Qt.ItemDataRole.UserRole, alarm.id)
            self.list.addItem(item)

    def _selected(self) -> Alarm | None:
        item = self.list.currentItem()
        return self.alarms.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _add(self) -> None:
        dialog = AlarmEditDialog(list(self.todos), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.alarms.add(**dialog.values())
        self.refresh()
        self.changed.emit()

    def _edit_selected(self, *_args) -> None:
        alarm = self._selected()
        if alarm is None:
            return
        dialog = AlarmEditDialog(list(self.todos), alarm=alarm, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.alarms.update(alarm.id, **dialog.values())
        self.refresh()
        self.changed.emit()

    def _delete_selected(self) -> None:
        alarm = self._selected()
        if alarm is None:
            return
        self.alarms.delete(alarm.id)
        self.refresh()
        self.changed.emit()

