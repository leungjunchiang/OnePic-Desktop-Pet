"""A small, pet-attached Todo strip.

The compact Todo surface is deliberately separate from ``TodayNoteWindow``.
It is a frameless tool window with no title, statistics, chat text, or
dashboard chrome.  ``PetWindow`` owns its lifetime and repositions it below
the pet whenever the pet moves.
"""

from __future__ import annotations

from time import monotonic
from typing import Any, Callable

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .time_memory import TimeMemory


COMPACT_TODO_STYLE = """
QWidget#compactTodoPanel {
    background: rgba(247, 252, 251, 242);
    border: 1px solid rgba(92, 157, 160, 150);
    border-radius: 12px;
}
QWidget#todoRows { background: transparent; border: 0; }
QScrollArea { background: transparent; border: 0; }
QCheckBox { spacing: 6px; color: #183c4c; }
QCheckBox::indicator { width: 16px; height: 16px; }
QCheckBox::indicator:unchecked {
    border: 1px solid #77a5ac; border-radius: 8px; background: #ffffff;
}
QCheckBox::indicator:checked {
    border: 1px solid #2faaa0; border-radius: 8px; background: #55c7b1;
}
QLabel#todoTitle { color: #183c4c; padding: 0 2px; }
QToolButton { color: #557681; background: transparent; border: 0; padding: 1px 4px; }
QToolButton:hover { color: #0c807b; background: rgba(185, 228, 220, 130); border-radius: 7px; }
QToolButton#addButton, QToolButton#expandButton { font-size: 15px; }
"""


class TodoRow(QWidget):
    """One compact task row: checkbox, short label, and a tiny more button."""

    selected = Signal(str)
    checked = Signal(str, bool)
    more_requested = Signal(str, object)

    def __init__(self, task: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.task_id = str(task.id)
        self.setFixedHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 0, 2, 0)
        layout.setSpacing(4)
        self.checkbox = QCheckBox(self)
        self.checkbox.setChecked(bool(task.completed))
        self.checkbox.setToolTip("标记完成")
        self.checkbox.stateChanged.connect(
            lambda state: self.checked.emit(
                self.task_id, state == Qt.CheckState.Checked.value
            )
        )
        layout.addWidget(self.checkbox, 0, Qt.AlignmentFlag.AlignVCenter)
        self.label = QLabel(self)
        self.label.setObjectName("todoTitle")
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.label.setToolTip(str(task.title))
        self.label.installEventFilter(self)
        self.set_task(task)
        layout.addWidget(self.label, 1, Qt.AlignmentFlag.AlignVCenter)
        self.more_button = QToolButton(self)
        self.more_button.setText("⋯")
        self.more_button.setFixedWidth(23)
        self.more_button.setToolTip("任务操作")
        self.more_button.clicked.connect(
            lambda: self.more_requested.emit(self.task_id, self.more_button)
        )
        layout.addWidget(self.more_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.installEventFilter(self)

    def set_task(self, task: Any) -> None:
        text = str(task.title)
        if getattr(task, "time", None):
            text += f" · {task.time}"
        self.label.setText(
            QFontMetrics(self.label.font()).elidedText(
                text, Qt.TextElideMode.ElideRight, 198
            )
        )
        if bool(task.completed):
            self.label.setStyleSheet("color:#819399;text-decoration:line-through;")
        else:
            self.label.setStyleSheet("color:#183c4c;")

    def set_selected(self, selected: bool) -> None:
        self.setStyleSheet(
            "background:rgba(190, 231, 224, 150);border-radius:8px;"
            if selected
            else "background:transparent;"
        )

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        if (watched is self or watched is self.label) and event.type() == QEvent.Type.MouseButtonPress:
            self.selected.emit(self.task_id)
        return super().eventFilter(watched, event)


class CompactTodoPanel(QWidget):
    """Frameless Todo strip that is visually attached to the desktop pet."""

    task_selected = Signal(str)
    task_checked = Signal(str, bool)
    task_changed = Signal()

    MAX_COLLAPSED_ROWS = 5
    MAX_EXPANDED_ROWS = 8
    COMPLETION_PREVIEW_SECONDS = 1.1

    def __init__(
        self,
        memory: TimeMemory,
        parent: QWidget | None = None,
        *,
        settings: Any | None = None,
        save_settings_callback: Callable[[Any], None] | None = None,
    ) -> None:
        # This intentionally has no QWidget parent: it is a companion native
        # window, while PetWindow remains responsible for positioning it.
        super().__init__(None)
        self.memory = memory
        self.settings = settings
        self.save_settings_callback = save_settings_callback
        self.collapsed = False
        self.expanded = False
        self.selected_task_id = str(memory.current_task_id or "")
        self._just_completed: dict[str, float] = {}
        self._rows: dict[str, TodoRow] = {}
        self.setObjectName("compactTodoPanel")
        self.setWindowTitle("")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(COMPACT_TODO_STYLE)
        self.setFixedWidth(260)

        root = QVBoxLayout(self)
        root.setContentsMargins(5, 4, 5, 4)
        root.setSpacing(2)

        self.rows_scroll = QScrollArea(self)
        self.rows_scroll.setWidgetResizable(True)
        self.rows_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rows_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.rows_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.rows_container = QWidget(self.rows_scroll)
        self.rows_container.setObjectName("todoRows")
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(1)
        self.rows_scroll.setWidget(self.rows_container)
        root.addWidget(self.rows_scroll)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addStretch(1)
        self.add_button = QToolButton(self)
        self.add_button.setObjectName("addButton")
        self.add_button.setText("＋")
        self.add_button.setToolTip("添加待办")
        self.add_button.clicked.connect(self.add_task)
        footer.addWidget(self.add_button)
        self.expand_button = QToolButton(self)
        self.expand_button.setObjectName("expandButton")
        self.expand_button.setToolTip("展开更多待办")
        self.expand_button.clicked.connect(self._toggle_expanded)
        footer.addWidget(self.expand_button)
        self.collapse_button = QToolButton(self)
        self.collapse_button.setText("⌄")
        self.collapse_button.setToolTip("收起待办")
        self.collapse_button.clicked.connect(lambda: self.set_collapsed(True))
        footer.addWidget(self.collapse_button)
        root.addLayout(footer)
        self.refresh()

    @property
    def rows(self) -> dict[str, TodoRow]:
        return self._rows

    def set_companion_topmost(self, enabled: bool) -> None:
        """Apply the same topmost choice as the pet without a title bar."""

        position = self.pos()
        visible = self.isVisible()
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if enabled:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.move(position)
        if visible:
            self.show()

    def set_collapsed(self, collapsed: bool) -> None:
        self.collapsed = bool(collapsed)
        self.rows_scroll.setVisible(not self.collapsed)
        self.add_button.setVisible(not self.collapsed)
        self.expand_button.setVisible(
            False
            if self.collapsed
            else len(self._visible_tasks()) > self.MAX_COLLAPSED_ROWS
        )
        self.collapse_button.setText("···" if self.collapsed else "⌄")
        self.collapse_button.setToolTip("展开待办" if self.collapsed else "收起待办")
        if self.collapsed:
            self.setFixedHeight(28)
        else:
            self._resize_to_content()

    def _toggle_expanded(self) -> None:
        self.expanded = not self.expanded
        self.expand_button.setText("∧" if self.expanded else "∨")
        self.expand_button.setToolTip("收起更多待办" if self.expanded else "展开更多待办")
        self._resize_to_content()

    def _visible_tasks(self) -> list[Any]:
        now = monotonic()
        self._just_completed = {
            key: deadline for key, deadline in self._just_completed.items() if deadline > now
        }
        return [
            item
            for item in self.memory.todos.today()
            if not item.completed or item.id in self._just_completed
        ]

    def refresh(self) -> None:
        if self.collapsed:
            self._resize_to_content()
            return
        for row in tuple(self._rows.values()):
            row.deleteLater()
        self._rows.clear()
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        tasks = self._visible_tasks()
        for task in tasks:
            row = TodoRow(task, self.rows_container)
            row.set_selected(task.id == self.selected_task_id)
            row.selected.connect(self._select_task)
            row.checked.connect(self._check_task)
            row.more_requested.connect(self._show_task_menu)
            self._rows[task.id] = row
            self.rows_layout.addWidget(row)
        self.rows_layout.addStretch(1)
        self.expand_button.setVisible(len(tasks) > self.MAX_COLLAPSED_ROWS)
        self.expand_button.setText("∧" if self.expanded else "∨")
        self._resize_to_content()

    def _resize_to_content(self) -> None:
        if self.collapsed:
            self.setFixedHeight(28)
            return
        count = len(self._visible_tasks())
        visible_rows = min(count, self.MAX_EXPANDED_ROWS if self.expanded else self.MAX_COLLAPSED_ROWS)
        visible_rows = max(1, visible_rows)
        self.rows_scroll.setFixedHeight(visible_rows * 35)
        self.adjustSize()
        self.setFixedHeight(min(40 + visible_rows * 35, 40 + self.MAX_EXPANDED_ROWS * 35))

    def _select_task(self, task_id: str) -> None:
        task = self.memory.todos.get(task_id)
        if task is None:
            return
        self.memory.select_task(task.id)
        self.selected_task_id = task.id
        for key, row in self._rows.items():
            row.set_selected(key == task.id)
        self.task_selected.emit(task.id)

    def _check_task(self, task_id: str, completed: bool) -> None:
        task = self.memory.todos.get(task_id)
        if task is None or bool(task.completed) == bool(completed):
            return
        self.memory.todos.complete(task_id, completed)
        if completed:
            self._just_completed[task_id] = monotonic() + self.COMPLETION_PREVIEW_SECONDS
            QTimer.singleShot(
                int(self.COMPLETION_PREVIEW_SECONDS * 1000) + 60,
                self.refresh,
            )
        self.task_checked.emit(task_id, completed)
        self.task_changed.emit()
        if not completed:
            self.refresh()

    def add_task(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        title, ok = QInputDialog.getText(self, "添加待办", "要做什么？")
        if not ok or not title.strip():
            return
        task = self.memory.todos.add(title.strip())
        self.memory.select_task(task.id)
        self.task_selected.emit(task.id)
        self.refresh()
        self.task_changed.emit()

    def _show_task_menu(self, task_id: str, button: object) -> None:
        task = self.memory.todos.get(task_id)
        if task is None:
            return
        menu = QMenu(self)
        edit = menu.addAction("编辑")
        time_action = menu.addAction("修改时间")
        pin = menu.addAction("取消置顶" if task.important else "置顶")
        complete = menu.addAction("取消完成" if task.completed else "完成")
        menu.addSeparator()
        delete = menu.addAction("删除")
        chosen = menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        if chosen is edit:
            self._edit_task(task_id, include_time=True)
        elif chosen is time_action:
            self._edit_task(task_id, include_time=True, time_only=True)
        elif chosen is pin:
            self.memory.todos.update(task_id, important=not task.important)
            self.refresh()
            self.task_changed.emit()
        elif chosen is complete:
            self._check_task(task_id, not task.completed)
        elif chosen is delete:
            answer = QMessageBox.question(
                self,
                "删除待办",
                f"确定删除“{task.title}”？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.memory.todos.delete(task_id)
                self.refresh()
                self.task_changed.emit()

    def _edit_task(self, task_id: str, *, include_time: bool, time_only: bool = False) -> None:
        task = self.memory.todos.get(task_id)
        if task is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("修改待办")
        form = QFormLayout(dialog)
        title = QLineEdit(task.title, dialog)
        time = QLineEdit(task.time or "", dialog)
        time.setPlaceholderText("可选，例如 20:00")
        important = QCheckBox("置顶", dialog)
        important.setChecked(task.important)
        if not time_only:
            form.addRow("任务", title)
            form.addRow("时间", time)
            form.addRow("", important)
        else:
            form.addRow("时间", time)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not time_only and not title.text().strip():
            return
        changes: dict[str, Any] = {"time": time.text().strip() or None}
        if not time_only:
            changes.update(title=title.text().strip(), important=important.isChecked())
        self.memory.todos.update(task_id, **changes)
        self.refresh()
        self.task_changed.emit()
