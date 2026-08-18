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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .time_memory import TimeMemory


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
        self.reminder = QCheckBox("进入提醒窗口后显示", self)
        self.show_before = QLineEdit(self)
        self.show_before.setPlaceholderText("默认 7 天")
        self.note = QLineEdit(self)
        self.note.setPlaceholderText("可选备注")
        form.addRow("类型", self.kind)
        form.addRow("标题", self.title)
        form.addRow("日期", self.date)
        form.addRow("时间", self.time)
        form.addRow("重复", self.repeat)
        form.addRow("提醒", self.reminder)
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
        if item:
            self.kind.setEnabled(False)
            self._load_item(item)
        elif forced_type:
            index = self.kind.findData(forced_type)
            if index >= 0:
                self.kind.setCurrentIndex(index)
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
                self.reminder.setChecked(bool(getattr(source, "reminder", False)))

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
        self._set_row_visible(self.reminder, not is_event)
        self._set_row_visible(self.show_before, is_event)
        self._set_row_visible(self.note, is_event)

    def save(self) -> None:
        kind = str(self.kind.currentData())
        title = self.title.text().strip()
        if not title:
            raise ValueError("标题不能为空")
        day = self.date.text().strip() or self.memory.now().date().isoformat()
        if kind in {"todo", "reminder"}:
            values = {
                "title": title,
                "date": day,
                "time": self.time.text().strip() or None,
                "reminder": kind == "reminder" or self.reminder.isChecked(),
            }
            if self.item and self.item.source_type in {"todo", "reminder"}:
                self.memory.todos.update(self.item.source_id, **values)
            else:
                self.memory.todos.add(source="todo_center", **values)
            return
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
            "今天、未来事项、倒计时和纪念日都在这里；桌面小待办与本页共用同一份数据。"
        )
        subtitle.setObjectName("subtitle")
        root.addWidget(subtitle)
        self.tabs = QTabWidget(self)
        self._lists: list[QListWidget] = []
        for label, key in (
            ("今天", "today"),
            ("即将到来", "upcoming"),
            ("倒计时·纪念日", "events"),
            ("已完成", "completed"),
        ):
            listing = QListWidget(self)
            listing.setProperty("view_key", key)
            listing.itemChanged.connect(self._item_changed)
            listing.itemDoubleClicked.connect(self._edit_row)
            self._lists.append(listing)
            page = QWidget(self)
            layout = QVBoxLayout(page)
            layout.addWidget(listing, 1)
            hint = QLabel("双击编辑；勾选可完成或恢复。")
            hint.setObjectName("subtitle")
            layout.addWidget(hint)
            self.tabs.addTab(page, label)
        root.addWidget(self.tabs, 1)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("＋ 新建", self)
        self.add_button.setObjectName("primary")
        self.add_button.clicked.connect(self._new_item)
        self.edit_button = QPushButton("编辑", self)
        self.edit_button.clicked.connect(self._edit_current)
        self.delete_button = QPushButton("删除", self)
        self.delete_button.clicked.connect(self._delete_current)
        buttons.addWidget(self.add_button)
        buttons.addStretch(1)
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
            detail = "提醒" if item.reminder else "待办"
            result.append(
                CenterItem(
                    item.id,
                    "reminder" if item.reminder else "todo",
                    item.id,
                    item.title,
                    item.date,
                    item.time or "",
                    item.completed,
                    detail,
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

    def _partition(self, item: CenterItem, view: str) -> bool:
        try:
            item_day = date.fromisoformat(item.date_text[:10])
        except ValueError:
            item_day = self.memory.now().date()
        today = self.memory.now().date()
        if view == "completed":
            return item.completed
        if item.completed:
            return False
        if view == "today":
            return item_day <= today
        if view == "upcoming":
            return item_day > today
        return item.source_type in {"countdown", "anniversary"}

    def _label(self, item: CenterItem) -> str:
        if item.source_type in {"todo", "reminder"}:
            status = "提醒" if item.source_type == "reminder" else "待办"
            if item.completed:
                status += " · 已完成"
        else:
            status = item.detail
        time_part = f" · {item.time_text}" if item.time_text else ""
        return (
            f"{'✓ ' if item.completed else '○ '}{item.title}{time_part}\n"
            f"{item.date_text} · {status}"
        )

    def refresh(self) -> None:
        all_items = self._all_items()
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
            rows.sort(key=lambda item: (item.date_text, item.time_text, item.title))
            for item in rows:
                row = QListWidgetItem(self._label(item), listing)
                row.setData(Qt.ItemDataRole.UserRole, item.id)
                row.setData(Qt.ItemDataRole.UserRole + 1, item)
                row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                row.setCheckState(
                    Qt.CheckState.Checked if item.completed else Qt.CheckState.Unchecked
                )
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
        return self._lists[self.tabs.currentIndex()].currentItem()

    def _current_model(self) -> CenterItem | None:
        row = self._current_row()
        value = row.data(Qt.ItemDataRole.UserRole + 1) if row else None
        return value if isinstance(value, CenterItem) else None

    def _update_buttons(self) -> None:
        enabled = self._current_model() is not None
        self.edit_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)

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
            self.memory.todos.complete(item.source_id, completed)
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
