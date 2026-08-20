"""Unified Todo Center for tasks, reminders, countdowns and anniversaries.

The desktop CompactTodo remains intentionally small. This window reads the
same TimeMemory stores; projected countdown/anniversary rows never create a
second Todo record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .time_memory import TimeMemory
from .alarm_ui import AlarmCenterDialog, AlarmSoundSelector
from .todo_manager import REMINDER_ALARM, REMINDER_NONE, REMINDER_PET
from .todo_view import todo_event_parts


TODO_CENTER_STYLE = """
QDialog#todoCenter { background: #eef5f8; color: #24475b; }
QDialog#todoCenter QLabel#subtitle { color: #607985; }
QListWidget { background: rgba(255,255,255,235); border: 1px solid #b4c9d2;
  border-radius: 12px; padding: 6px; }
QListWidget::item { padding: 8px 10px; border-radius: 8px; }
QListWidget::item:selected { background: #d8edf1; color: #183c4c; }
QPushButton { background: #d7ebf1; color: #24475b; border: 0;
  border-radius: 9px; padding: 8px 14px; font-weight: 600; }
QPushButton:hover { background: #c2e2e9; }
QPushButton#primary { background: #4a7e97; color: white; }
QPushButton#primary:hover { background: #376a82; }
QTabBar::tab { padding: 9px 16px; color: #607985; }
QTabBar::tab:selected { color: #008c85; font-weight: 700; }
"""


@dataclass(frozen=True)
class CenterItem:
    id: str
    source_type: str
    source_id: str
    title: str
    date_text: str
    time_text: str = ""
    completed: bool = False
    detail: str = ""
    priority: int | None = None
    queue_position: int | None = None
    read: bool = False
    due_at: str | None = None
    reminder: bool = False
    reminder_minutes_before: int = 10
    reminder_mode: str = REMINDER_NONE
    alarm_sound_id: str = "system"
    alarm_volume: int = 60
    alarm_snooze_minutes: int = 10
    reminder_suppressed: bool = False


class QueueListWidget(QListWidget):
    """The unified, reorderable sticky-note list shown on the main tab."""

    reordered = Signal(list)
    external_dropped = Signal(str, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QListWidget.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt API
        source = event.source()
        if source is not self and isinstance(source, QListWidget):
            source_item = source.currentItem()
            if source_item is not None:
                target_row = self.indexAt(event.position().toPoint()).row()
                if target_row < 0:
                    target_row = self.count()
                item_id = source_item.data(Qt.ItemDataRole.UserRole)
                if item_id:
                    self.external_dropped.emit(
                        str(item_id), min(target_row + 1, 10)
                    )
                    event.acceptProposedAction()
                    return
        super().dropEvent(event)
        self.reordered.emit(
            [
                str(self.item(index).data(Qt.ItemDataRole.UserRole))
                for index in range(self.count())
                if self.item(index).data(Qt.ItemDataRole.UserRole)
            ]
        )


def _remaining_label(days: int) -> str:
    if days < 0:
        return f"已逾期{-days}天"
    if days == 0:
        return "今天"
    if days == 1:
        return "明天"
    return f"还有{days}天"


class _ItemEditor(QDialog):
    """Type-aware editor used by TodoCenter."""

    def __init__(
        self,
        memory: TimeMemory,
        item: CenterItem | None = None,
        *,
        forced_type: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.memory = memory
        self.item = item
        self.setWindowTitle("编辑待办" if item else "新建事项")
        self.setModal(True)
        form = QFormLayout(self)
        self._form = form
        self.kind = QComboBox(self)
        self.kind.addItem("待办", "todo")
        self.kind.addItem("提醒", "reminder")
        self.kind.addItem("倒计时", "countdown")
        self.kind.addItem("纪念日", "anniversary")
        self.title = QLineEdit(self)
        self.date = QLineEdit(self)
        self.date.setPlaceholderText("YYYY-MM-DD")
        self.time = QLineEdit(self)
        self.time.setPlaceholderText("可选，例如 20:00")
        self.repeat = QComboBox(self)
        self.repeat.addItem("一次", "none")
        self.repeat.addItem("每年", "yearly")
        self.reminder_mode = QComboBox(self)
        self.reminder_mode.addItem("不提醒", REMINDER_NONE)
        self.reminder_mode.addItem("六毛提醒（无声音）", REMINDER_PET)
        self.reminder_mode.addItem("六毛闹钟（播放系统提示音）", REMINDER_ALARM)
        self.reminder_mode.setCurrentIndex(self.reminder_mode.findData(REMINDER_PET))
        self.reminder_minutes_before = QSpinBox(self)
        self.reminder_minutes_before.setRange(0, 24 * 60)
        self.reminder_minutes_before.setSuffix(" 分钟前")
        self.reminder_minutes_before.setValue(10)
        self.alarm_volume = QSpinBox(self)
        self.alarm_volume.setRange(0, 100)
        self.alarm_volume.setSuffix("%（系统提示音音量由系统设置控制）")
        self.alarm_volume.setValue(60)
        self.alarm_snooze_minutes = QSpinBox(self)
        self.alarm_snooze_minutes.setRange(1, 120)
        self.alarm_snooze_minutes.setSuffix(" 分钟")
        self.alarm_snooze_minutes.setValue(10)
        self.alarm_sound = AlarmSoundSelector(
            getattr(memory, "alarm_sounds", None),
            "system",
            self,
        )
        self.show_before = QLineEdit(self)
        self.show_before.setPlaceholderText("默认 7 天")
        self.note = QLineEdit(self)
        self.note.setPlaceholderText("可选备注")
        form.addRow("类型", self.kind)
        form.addRow("标题", self.title)
        form.addRow("日期", self.date)
        form.addRow("时间", self.time)
        form.addRow("重复", self.repeat)
        form.addRow("提醒方式", self.reminder_mode)
        form.addRow("提前提醒", self.reminder_minutes_before)
        form.addRow("闹钟音量", self.alarm_volume)
        form.addRow("闹钟铃声", self.alarm_sound)
        form.addRow("稍后默认", self.alarm_snooze_minutes)
        form.addRow("提前天数", self.show_before)
        form.addRow("备注", self.note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.kind.currentIndexChanged.connect(self._refresh_fields)
        self.reminder_mode.currentIndexChanged.connect(self._refresh_fields)
        if item:
            self.kind.setEnabled(False)
            self._load_item(item)
        elif forced_type:
            index = self.kind.findData(forced_type)
            if index >= 0:
                self.kind.setCurrentIndex(index)
                if forced_type == "reminder":
                    self.reminder_mode.setCurrentIndex(self.reminder_mode.findData(REMINDER_PET))
        self._refresh_fields()

    def _set_row_visible(self, widget: QWidget, visible: bool) -> None:
        label = self._form.labelForField(widget)
        widget.setVisible(visible)
        if label is not None:
            label.setVisible(visible)

    def _load_item(self, item: CenterItem) -> None:
        index = self.kind.findData(item.source_type)
        if index >= 0:
            self.kind.setCurrentIndex(index)
        self.title.setText(item.title)
        self.date.setText(item.date_text)
        self.time.setText(item.time_text)
        source = self._source()
        if source is not None:
            self.note.setText(str(getattr(source, "note", "") or ""))
            self.show_before.setText(str(getattr(source, "show_before_days", 7)))
            if item.source_type == "anniversary":
                repeat_index = self.repeat.findData(
                    str(getattr(source, "repeat", "none"))
                )
                if repeat_index >= 0:
                    self.repeat.setCurrentIndex(repeat_index)
            elif item.source_type in {"todo", "reminder"}:
                mode = str(getattr(source, "reminder_mode", "") or "")
                if not mode:
                    mode = REMINDER_PET if bool(getattr(source, "reminder", False)) else REMINDER_NONE
                mode_index = self.reminder_mode.findData(mode)
                self.reminder_mode.setCurrentIndex(mode_index if mode_index >= 0 else 0)
                self.reminder_minutes_before.setValue(
                    max(0, min(24 * 60, int(getattr(source, "reminder_minutes_before", 10) or 0)))
                )
                self.alarm_volume.setValue(max(0, min(100, int(getattr(source, "alarm_volume", 60) or 0))))
                self.alarm_snooze_minutes.setValue(max(1, min(120, int(getattr(source, "alarm_snooze_minutes", 10) or 10))))
                self.alarm_sound.refresh(str(getattr(source, "alarm_sound_id", "system") or "system"))

    def _source(self):
        if not self.item:
            return None
        if self.item.source_type in {"todo", "reminder"}:
            return self.memory.todos.get(self.item.source_id)
        if self.item.source_type == "countdown":
            return self.memory.countdowns.get(self.item.source_id)
        return self.memory.anniversaries.get(self.item.source_id)

    def _refresh_fields(self) -> None:
        kind = str(self.kind.currentData())
        is_event = kind in {"countdown", "anniversary"}
        self._set_row_visible(self.time, not is_event)
        self._set_row_visible(self.repeat, kind == "anniversary")
        self._set_row_visible(self.reminder_mode, not is_event)
        self._set_row_visible(self.reminder_minutes_before, not is_event)
        self._set_row_visible(self.alarm_volume, not is_event and self.reminder_mode.currentData() == REMINDER_ALARM)
        self._set_row_visible(self.alarm_sound, not is_event and self.reminder_mode.currentData() == REMINDER_ALARM)
        self._set_row_visible(self.alarm_snooze_minutes, not is_event and self.reminder_mode.currentData() == REMINDER_ALARM)
        self._set_row_visible(self.show_before, is_event)
        self._set_row_visible(self.note, is_event)
        mode = self.reminder_mode.currentData()
        self.reminder_minutes_before.setEnabled(mode != REMINDER_NONE and not is_event)

    def save(self) -> None:
        kind = str(self.kind.currentData())
        title = self.title.text().strip()
        if not title:
            raise ValueError("标题不能为空")
        # An empty date means “no scheduled date” for a Todo.  TodoManager
        # keeps its legacy compatibility date internally, but the semantic
        # ``date_explicit`` flag prevents that placeholder reaching the UI.
        day = self.date.text().strip()
        if kind in {"todo", "reminder"}:
            values = {
                "title": title,
                "date": day,
                "time": self.time.text().strip() or None,
                # ``提醒`` is a record type, not a reminder level.  Preserve
                # the user's selected mode here; previously every save of a
                # reminder record forced it back to the quiet PET mode, so a
                # selected audible alarm disappeared after reopening and was
                # never scheduled by AlarmManager.
                "reminder_mode": self.reminder_mode.currentData(),
                "reminder": self.reminder_mode.currentData() != REMINDER_NONE,
                "reminder_minutes_before": self.reminder_minutes_before.value(),
                "alarm_volume": self.alarm_volume.value(),
                "alarm_snooze_minutes": self.alarm_snooze_minutes.value(),
                "alarm_sound_id": self.alarm_sound.current_sound_id(),
                "reminder_suppressed": False,
            }
            if self.item and self.item.source_type in {"todo", "reminder"}:
                saved = self.memory.todos.update(self.item.source_id, **values)
            else:
                saved = self.memory.todos.add(source="todo_center", **values)
            self.memory.sync_todo_reminder(saved)
            return
        if not day:
            raise ValueError("请填写日期")
        try:
            show_before = max(
                0, min(365, int(self.show_before.text().strip() or "7"))
            )
        except ValueError as exc:
            raise ValueError("提前天数必须是数字") from exc
        if kind == "countdown":
            if self.item and self.item.source_type == "countdown":
                self.memory.countdowns.update(
                    self.item.source_id,
                    title=title,
                    target_datetime=day,
                    show_before_days=show_before,
                    note=self.note.text().strip(),
                )
            else:
                self.memory.countdowns.add(
                    title,
                    day,
                    show_before_days=show_before,
                    note=self.note.text().strip(),
                )
            return
        repeat = str(self.repeat.currentData())
        if self.item and self.item.source_type == "anniversary":
            self.memory.anniversaries.update(
                self.item.source_id,
                title=title,
                date=day,
                repeat=repeat,
                show_before_days=show_before,
                note=self.note.text().strip(),
            )
        else:
            self.memory.anniversaries.add(
                title,
                day,
                repeat=repeat,
                show_before_days=show_before,
                note=self.note.text().strip(),
            )

    def accept(self) -> None:
        try:
            self.save()
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
            return
        super().accept()


class TodoCenterWindow(QDialog):
    """Full management view backed by the same TimeMemory as CompactTodo."""

    changed = Signal()

    def __init__(self, memory: TimeMemory, parent=None) -> None:
        super().__init__(None)
        self.memory = memory
        self.setObjectName("todoCenter")
        self.setWindowTitle("待办 · 六毛")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.resize(700, 540)
        self.setStyleSheet(TODO_CENTER_STYLE)
        root = QVBoxLayout(self)
        heading = QLabel("待办中心")
        heading.setStyleSheet("font-size:24px;font-weight:700;color:#183c4c;")
        root.addWidget(heading)
        subtitle = QLabel(
            "待办像便利贴：有时间的事项到点后保留24小时；没有具体时间的事项不会自动消失，需手动标为已读。"
            " 可设置任务顺位和提前提醒。"
        )
        subtitle.setObjectName("subtitle")
        root.addWidget(subtitle)
        self.tabs = QTabWidget(self)
        self._lists: list[QListWidget] = []
        self._queue_list: QueueListWidget | None = None
        self._alarm_center: AlarmCenterDialog | None = None
        for label, key in (
            ("今天", "today"),
            ("即将到来", "upcoming"),
            ("重要日期", "events"),
            ("已完成", "completed"),
            ("已读", "read"),
        ):
            listing = QListWidget(self)
            listing.setProperty("view_key", key)
            listing.itemChanged.connect(self._item_changed)
            listing.itemDoubleClicked.connect(self._edit_row)
            listing.setDragEnabled(True)
            listing.setDragDropMode(QListWidget.DragDropMode.DragOnly)
            self._lists.append(listing)
            page = QWidget(self)
            layout = QVBoxLayout(page)
            if key == "today":
                queue_title = QLabel("当前待办（最多10项） · 拖动调整顺序")
                queue_title.setObjectName("subtitle")
                layout.addWidget(queue_title)
                self._queue_list = QueueListWidget(page)
                self._queue_list.itemDoubleClicked.connect(self._edit_row)
                self._queue_list.reordered.connect(self._reorder_queue)
                self._queue_list.external_dropped.connect(self._add_to_queue)
                self._queue_list.currentItemChanged.connect(
                    lambda *_args: self._update_buttons()
                )
                layout.addWidget(self._queue_list)
                # Keep the date-based QListWidget in the model list for the
                # other view code, but do not render a second "other tasks"
                # section on the main page.
                listing.setVisible(False)
            else:
                layout.addWidget(listing, 1)
            hint = QLabel("双击编辑；勾选可完成或恢复。")
            hint.setObjectName("subtitle")
            layout.addWidget(hint)
            self.tabs.addTab(page, label)
        # The alarm tab uses the same AlarmManager as the system-menu alarm
        # entry.  It is embedded here for discoverability, not duplicated.
        alarm_page = QWidget(self)
        alarm_layout = QVBoxLayout(alarm_page)
        self._alarm_center = AlarmCenterDialog(
            self.memory.alarms,
            list(self.memory.todos.items),
            parent=alarm_page,
            sound_library=self.memory.alarm_sounds,
        )
        self._alarm_center.setWindowFlags(Qt.WindowType.Widget)
        self._alarm_center.changed.connect(self.changed.emit)
        alarm_layout.addWidget(self._alarm_center)
        self._alarm_tab_index = self.tabs.addTab(alarm_page, "闹钟")
        root.addWidget(self.tabs, 1)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("＋ 新建", self)
        self.add_button.setObjectName("primary")
        self.add_button.clicked.connect(self._new_item)
        self.edit_button = QPushButton("编辑", self)
        self.edit_button.clicked.connect(self._edit_current)
        self.restore_button = QPushButton("恢复待办", self)
        self.restore_button.clicked.connect(self._restore_current)
        self.delete_button = QPushButton("删除", self)
        self.delete_button.clicked.connect(self._delete_current)
        self.read_button = QPushButton("标为已读", self)
        self.read_button.clicked.connect(self._toggle_read_current)
        buttons.addWidget(self.add_button)
        buttons.addStretch(1)
        buttons.addWidget(self.restore_button)
        buttons.addWidget(self.read_button)
        buttons.addWidget(self.edit_button)
        buttons.addWidget(self.delete_button)
        root.addLayout(buttons)
        for listing in self._lists:
            listing.currentItemChanged.connect(
                lambda *_args: self._update_buttons()
            )
        self.refresh()

    def _all_items(self) -> list[CenterItem]:
        result: list[CenterItem] = []
        for item in self.memory.todos.items:
            event_date, event_time = todo_event_parts(item)
            detail = "提醒" if item.reminder else "待办"
            result.append(
                CenterItem(
                    item.id,
                    "reminder" if item.reminder else "todo",
                    item.id,
                    item.title,
                    event_date,
                    event_time or "",
                    item.completed,
                    detail,
                    getattr(item, "priority", None),
                    getattr(item, "queue_position", None),
                    bool(getattr(item, "read", False)),
                    getattr(item, "due_at", None),
                    bool(getattr(item, "reminder_mode", "") not in {"", REMINDER_NONE}),
                    max(0, int(getattr(item, "reminder_minutes_before", 10) or 0)),
                    str(getattr(item, "reminder_mode", "") or (REMINDER_PET if getattr(item, "reminder", False) else REMINDER_NONE)),
                    str(getattr(item, "alarm_sound_id", "system") or "system"),
                    max(0, min(100, int(getattr(item, "alarm_volume", 60) or 0))),
                    max(1, min(120, int(getattr(item, "alarm_snooze_minutes", 10) or 10))),
                    bool(getattr(item, "reminder_suppressed", False)),
                )
            )
        for item in self.memory.countdowns.items:
            result.append(
                CenterItem(
                    f"countdown:{item.id}",
                    "countdown",
                    item.id,
                    item.title,
                    item.target_datetime[:10],
                    "",
                    item.completed,
                    f"倒计时 · {_remaining_label(self.memory.countdowns.remaining_days(item))}",
                )
            )
        for item in self.memory.anniversaries.items:
            next_day = self.memory.anniversaries.next_date(item)
            remaining = self.memory.anniversaries.remaining_days(item)
            result.append(
                CenterItem(
                    f"anniversary:{item.id}",
                    "anniversary",
                    item.id,
                    item.title,
                    next_day.isoformat(),
                    "",
                    item.acknowledged_date == next_day.isoformat(),
                    f"纪念日 · {_remaining_label(remaining)}",
                )
            )
        return result

    def _event_in_reminder_window(self, item: CenterItem) -> bool:
        """Return whether an event is close enough for the time-line view."""
        if item.source_type == "countdown":
            source = self.memory.countdowns.get(item.source_id)
            if source is None:
                return False
            remaining = self.memory.countdowns.remaining_days(source)
        elif item.source_type == "anniversary":
            source = self.memory.anniversaries.get(item.source_id)
            if source is None:
                return False
            remaining = self.memory.anniversaries.remaining_days(source)
        else:
            return False
        window = max(0, int(getattr(source, "show_before_days", 7) or 0))
        return 0 < remaining <= window

    def _partition(self, item: CenterItem, view: str) -> bool:
        try:
            item_day = date.fromisoformat(item.date_text[:10])
        except ValueError:
            item_day = self.memory.now().date()
        today = self.memory.now().date()
        is_event = item.source_type in {"countdown", "anniversary"}

        # Important dates is a management view: keep every saved event here,
        # including one whose one-off occurrence has already been acknowledged.
        if view == "events":
            return is_event
        if view == "completed":
            return item.source_type in {"todo", "reminder"} and item.completed
        if view == "read":
            return item.source_type in {"todo", "reminder"} and item.read and not item.completed
        if item.completed:
            return False
        if item.read:
            return False
        if view == "today":
            if is_event:
                return item_day <= today
            return any(
                entry.source_type == "todo" and entry.source_id == item.source_id
                for entry in self.memory.todo_view_today()
            )
        if view == "upcoming":
            if is_event:
                return item_day > today and self._event_in_reminder_window(item)
            return item_day > today
        return False

    def _label(self, item: CenterItem) -> str:
        if item.source_type in {"todo", "reminder"}:
            status = "提醒" if item.source_type == "reminder" else "待办"
            if item.completed:
                status += " · 已完成"
            if item.read:
                status += " · 已读"
            mode_label = {
                REMINDER_NONE: "无提醒",
                REMINDER_PET: "六毛提醒",
                REMINDER_ALARM: "🔊 六毛闹钟",
            }.get(item.reminder_mode, "六毛提醒" if item.reminder else "无提醒")
            status += f" · {mode_label}"
            if item.reminder and item.time_text and item.reminder_minutes_before:
                status += f"（提前{item.reminder_minutes_before}分钟）"
            if item.reminder_suppressed:
                status += " · 原提醒时间已过，请重新设置"
        else:
            status = item.detail
        time_part = f" · {item.time_text}" if item.time_text else ""
        event_part = f"{item.date_text}{time_part}" if item.date_text else ""
        first_line = f"{'✓ ' if item.completed else '○ '}{item.title}{time_part if not item.date_text else ''}"
        second_line = f"\n{event_part} · {status}" if event_part else f"\n{status}"
        return first_line + second_line

    def refresh(self) -> None:
        if self._alarm_center is not None:
            self._alarm_center.todos = list(self.memory.todos.items)
            self._alarm_center.refresh()
        all_items = self._all_items()
        if self._queue_list is not None:
            self._queue_list.blockSignals(True)
            self._queue_list.clear()
            queue_rows = sorted(
                (
                    item
                    for item in all_items
                    if item.source_type in {"todo", "reminder"}
                    and not item.completed
                    and not item.read
                    and item.queue_position in set(range(1, 11))
                ),
                key=lambda item: int(item.queue_position or 99),
            )
            for item in queue_rows:
                event_text = f"{item.date_text}{(' · ' + item.time_text) if item.time_text else ''}"
                row_text = f"≡  {item.title}"
                if event_text:
                    row_text += f"\n{event_text}"
                row = QListWidgetItem(row_text, self._queue_list)
                row.setData(Qt.ItemDataRole.UserRole, item.id)
                row.setData(Qt.ItemDataRole.UserRole + 1, item)
                row.setToolTip("拖动调整顺序；双击编辑")
            if not queue_rows:
                empty = QListWidgetItem("这里还没有待办。点击“＋ 新建”添加。", self._queue_list)
                empty.setFlags(Qt.ItemFlag.ItemIsEnabled)
                empty.setForeground(Qt.GlobalColor.gray)
            self._queue_list.blockSignals(False)
        for listing in self._lists:
            view = str(listing.property("view_key"))
            current = (
                listing.currentItem().data(Qt.ItemDataRole.UserRole)
                if listing.currentItem()
                else None
            )
            listing.blockSignals(True)
            listing.clear()
            rows = [item for item in all_items if self._partition(item, view)]
            if view == "today":
                # The visible main page is the unified sticky-note list above;
                # do not render a second date-sorted copy underneath it.
                rows = []
            rows.sort(
                key=lambda item: (
                    item.date_text,
                    item.time_text or "99:99",
                    item.title,
                )
            )
            for item in rows:
                row = QListWidgetItem(self._label(item), listing)
                row.setData(Qt.ItemDataRole.UserRole, item.id)
                row.setData(Qt.ItemDataRole.UserRole + 1, item)
                if item.source_type in {"todo", "reminder"}:
                    row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    row.setCheckState(
                        Qt.CheckState.Checked if item.completed else Qt.CheckState.Unchecked
                    )
                else:
                    row.setFlags(row.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                row.setToolTip("双击编辑；右键或下方按钮删除")
            if not rows:
                empty = QListWidgetItem(
                    "这里还没有事项。点击“＋ 新建”添加。", listing
                )
                empty.setFlags(Qt.ItemFlag.ItemIsEnabled)
                empty.setForeground(Qt.GlobalColor.gray)
            listing.blockSignals(False)
            if current:
                for index in range(listing.count()):
                    if listing.item(index).data(Qt.ItemDataRole.UserRole) == current:
                        listing.setCurrentRow(index)
                        break
        self._update_buttons()

    def _current_row(self) -> QListWidgetItem | None:
        if self.tabs.currentIndex() == getattr(self, "_alarm_tab_index", -1):
            return None
        if self.tabs.currentIndex() == 0 and self._queue_list is not None:
            if self._queue_list.currentItem() is not None:
                return self._queue_list.currentItem()
        return self._lists[self.tabs.currentIndex()].currentItem()

    def _current_model(self) -> CenterItem | None:
        row = self._current_row()
        value = row.data(Qt.ItemDataRole.UserRole + 1) if row else None
        return value if isinstance(value, CenterItem) else None

    def _reorder_queue(self, item_ids: list[str]) -> None:
        """Persist the queue list's drag order in one manager update."""

        self.memory.todos.reorder_queue(item_ids)
        self.refresh()
        self.changed.emit()

    def _add_to_queue(self, item_id: str, position: int) -> None:
        item = self.memory.todos.get(item_id)
        if item is None or item.completed or item.read:
            return
        self.memory.todos.set_queue_position(item_id, position)
        self.refresh()
        self.changed.emit()

    def _update_buttons(self) -> None:
        item = self._current_model()
        enabled = item is not None
        self.edit_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)
        can_restore = (
            item is not None
            and item.source_type in {"todo", "reminder"}
            and item.completed
        )
        self.restore_button.setVisible(can_restore)
        self.restore_button.setEnabled(can_restore)
        can_read = item is not None and item.source_type in {"todo", "reminder"} and not item.completed
        self.read_button.setEnabled(can_read)
        self.read_button.setText("恢复显示" if can_read and item.read else "标为已读")

    def _toggle_read_current(self) -> None:
        item = self._current_model()
        if item is None or item.source_type not in {"todo", "reminder"}:
            return
        if self.memory.read_todo(item.source_id, not item.read):
            self.refresh()
            self.changed.emit()

    def _restore_current(self) -> None:
        item = self._current_model()
        if item is None or item.source_type not in {"todo", "reminder"} or not item.completed:
            return
        if self.memory.restore_todo(item.source_id):
            self.refresh()
            self.changed.emit()

    def _new_item(self) -> None:
        menu = QMenu(self)
        for label, kind in (
            ("新建待办", "todo"),
            ("新建提醒", "reminder"),
            ("新建倒计时", "countdown"),
            ("新建纪念日", "anniversary"),
        ):
            action = menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, selected=kind: self._new_item_type(selected)
            )
        menu.exec(
            self.add_button.mapToGlobal(self.add_button.rect().bottomLeft())
        )

    def _new_item_type(self, kind: str) -> None:
        dialog = _ItemEditor(self.memory, forced_type=kind, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.changed.emit()

    def _edit_row(self, _row: QListWidgetItem) -> None:
        self._edit_current()

    def _edit_current(self) -> None:
        item = self._current_model()
        if item is None:
            return
        dialog = _ItemEditor(self.memory, item, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.changed.emit()

    def _delete_current(self) -> None:
        item = self._current_model()
        if item is None:
            return
        answer = QMessageBox.question(
            self,
            "删除事项",
            f"确定删除“{item.title}”？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if item.source_type in {"todo", "reminder"}:
            self.memory.todos.delete(item.source_id)
        elif item.source_type == "countdown":
            self.memory.countdowns.delete(item.source_id)
        else:
            self.memory.anniversaries.delete(item.source_id)
        self.refresh()
        self.changed.emit()

    def _item_changed(self, row: QListWidgetItem) -> None:
        if not row or not row.data(Qt.ItemDataRole.UserRole + 1):
            return
        item = row.data(Qt.ItemDataRole.UserRole + 1)
        completed = row.checkState() == Qt.CheckState.Checked
        if item.source_type in {"todo", "reminder"}:
            if not completed and item.completed:
                self.memory.restore_todo(item.source_id)
            else:
                saved = self.memory.todos.complete(item.source_id, completed)
                if completed:
                    self.memory.reminders.complete_for_source(saved.id)
                    self.memory.alarms.sync_todo(saved, reminder_mode="none")
                else:
                    self.memory.sync_todo_reminder(saved)
        elif item.source_type == "countdown":
            if completed:
                self.memory.complete_countdown(item.source_id)
            else:
                self.memory.countdowns.update(item.source_id, completed=False)
        elif item.source_type == "anniversary":
            if completed:
                self.memory.anniversaries.acknowledge(item.source_id)
            else:
                self.memory.anniversaries.update(
                    item.source_id, acknowledged_date=None
                )
        self.refresh()
        self.changed.emit()

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()

