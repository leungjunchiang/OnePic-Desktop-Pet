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
QWidget#todoActionColumn { background: transparent; border: 0; }
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
QToolButton#addButton {
    min-width: 32px; max-width: 32px;
    min-height: 32px; max-height: 32px;
    color: #557681;
    background: rgba(230, 244, 240, 180);
    border: 1px solid rgba(112, 173, 170, 95);
    border-radius: 8px;
    padding: 0;
}
QToolButton#addButton:hover {
    color: #0b625f;
    background: rgba(190, 231, 224, 220);
    border-color: rgba(36, 128, 128, 150);
}
QToolButton#moreButton {
    min-width: 32px; max-width: 32px;
    min-height: 32px; max-height: 32px;
    color: #315765;
    background: rgba(214, 238, 233, 210);
    border: 1px solid rgba(73, 137, 141, 125);
    border-radius: 8px;
    padding: 0;
    font-size: 17px;
    font-weight: 600;
}
QToolButton#moreButton:hover {
    color: #0b625f;
    background: rgba(182, 227, 217, 235);
    border-color: rgba(36, 128, 128, 180);
}
QToolButton#moreButton:pressed {
    color: #ffffff;
    background: #4c9a9b;
    border-color: #3d8084;
}
"""


class TodoRow(QWidget):
    """One compact task row: checkbox, short label, and a tiny more button."""

    selected = Signal(str)
    checked = Signal(str, bool)
    more_requested = Signal(str, object)

    def __init__(
        self,
        task: Any,
        parent: QWidget | None = None,
        *,
        action_parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.task_id = str(task.id)
        self._full_text = ""
        self.setFixedHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 0, 4, 0)
        layout.setSpacing(4)
        self.checkbox = QCheckBox(self)
        self.checkbox.setFixedWidth(22)
        self.checkbox.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
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
        self.label.setMinimumWidth(0)
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.label.setToolTip(str(task.title))
        self.label.installEventFilter(self)
        self.set_task(task)
        layout.addWidget(self.label, 1, Qt.AlignmentFlag.AlignVCenter)
        # In the panel the button is reparented into the fixed action column;
        # keeping it exposed on the row preserves the row-level signal API.
        self.more_button = QToolButton(action_parent or self)
        self.more_button.setObjectName("moreButton")
        self.more_button.setText("⋯")
        self.more_button.setFixedSize(TodoActionColumn.BUTTON_SIZE, TodoActionColumn.BUTTON_SIZE)
        self.more_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.more_button.setToolTip("任务操作")
        # This is intentionally a real button, not a decorative label.  The
        # panel is a separate native window, so keeping mouse events enabled
        # here prevents the pet's drag surface from swallowing the click.
        self.more_button.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.more_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.more_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_button.clicked.connect(self._request_more)
        # The action button is the third fixed-width segment.  The label is
        # the only stretchable segment, so long titles can only elide text.
        if action_parent is None:
            layout.addWidget(self.more_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.installEventFilter(self)

    def _request_more(self) -> None:
        self.more_requested.emit(self.task_id, self.more_button)

    def set_task(self, task: Any) -> None:
        text = str(task.title)
        if getattr(task, "time", None):
            text += f" · {task.time}"
        self._full_text = text
        self.label.setToolTip(text)
        self._update_label_text()
        if bool(task.completed):
            self.label.setStyleSheet("color:#819399;text-decoration:line-through;")
        else:
            self.label.setStyleSheet("color:#183c4c;")

    def _update_label_text(self) -> None:
        """Fit the label to the actual row width instead of a magic number."""

        available = max(24, self.label.contentsRect().width())
        self.label.setText(
            QFontMetrics(self.label.font()).elidedText(
                self._full_text,
                Qt.TextElideMode.ElideRight,
                available,
            )
        )

    def resizeEvent(self, event: object) -> None:
        self._update_label_text()
        super().resizeEvent(event)

    def set_selected(self, selected: bool) -> None:
        self.setStyleSheet(
            "background:rgba(190, 231, 224, 150);border-radius:8px;"
            if selected
            else "background:transparent;"
        )

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        # Do not install a catch-all click handler on the more button.  The
        # QToolButton owns that hit area and must always receive its click.
        if (watched is self or watched is self.label) and event.type() == QEvent.Type.MouseButtonPress:
            self.selected.emit(self.task_id)
        return False


class TodoActionColumn(QWidget):
    """Fixed-width action rail shared by all visible Todo rows."""

    WIDTH = 40
    BUTTON_SIZE = 32
    ROW_GAP = 1
    ADD_GAP = 8

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("todoActionColumn")
        self.setFixedWidth(self.WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(self.ROW_GAP)
        self.add_button = QToolButton(self)
        self.add_button.setObjectName("addButton")
        self.add_button.setText("＋")
        self.add_button.setFixedSize(self.BUTTON_SIZE, self.BUTTON_SIZE)
        self.add_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.add_button.setToolTip("添加待办")

    def rebuild(self, buttons: list[QToolButton], visible_rows: int) -> None:
        """Replace row actions without changing the rail width."""

        while self.layout.count():
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget is None or widget is self.add_button:
                continue
            widget.setParent(None)
            widget.deleteLater()
        for button in buttons:
            button.setParent(self)
            self.layout.addWidget(
                button,
                0,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            )
        if buttons:
            self.layout.addSpacing(self.ADD_GAP)
        self.layout.addWidget(
            self.add_button,
            0,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
        )
        button_stack = len(buttons) * self.BUTTON_SIZE + max(0, len(buttons) - 1) * self.ROW_GAP
        button_stack += self.ADD_GAP if buttons else 0
        button_stack += self.BUTTON_SIZE
        row_stack = max(0, visible_rows) * CompactTodoPanel.ROW_HEIGHT
        self.setFixedHeight(max(row_stack, button_stack, self.BUTTON_SIZE))


class CompactTodoPanel(QWidget):
    """Frameless Todo strip that is visually attached to the desktop pet."""

    task_selected = Signal(str)
    task_checked = Signal(str, bool)
    task_changed = Signal()

    MAX_COLLAPSED_ROWS = 1
    MAX_EXPANDED_ROWS = 3
    MIN_WIDTH = 156
    MAX_WIDTH = 320
    ROW_HEIGHT = 34
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
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setMaximumWidth(self.MAX_WIDTH)

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

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(6)
        body.addWidget(self.rows_scroll, 1)
        # The rail is part of the panel's layout, not a footer widget.  Its
        # width never changes when a title grows, so neither action can be
        # pushed outside the native window or clipped by the row container.
        self.action_column = TodoActionColumn(self)
        self.add_button = self.action_column.add_button
        self.add_button.clicked.connect(self.add_task)
        body.addWidget(self.action_column, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(body)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addStretch(1)
        self.expand_button = QToolButton(self)
        self.expand_button.setObjectName("expandButton")
        self.expand_button.setText("⌄")
        self.expand_button.setToolTip("收起待办")
        self.expand_button.setFixedSize(24, 26)
        self.expand_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.expand_button.clicked.connect(self._toggle_expanded)
        footer.addWidget(self.expand_button, 0, Qt.AlignmentFlag.AlignBottom)
        # Kept as an alias for older callers/tests.  There is only one real
        # toggle now; the former second button caused the collapsed state to
        # turn into an untargetable ellipsis.
        self.collapse_button = self.expand_button
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
        self.expanded = not self.collapsed
        self.refresh()

    def _toggle_expanded(self) -> None:
        if len(self._visible_tasks()) <= 1:
            return
        self.set_collapsed(not self.collapsed)

    def _visible_tasks(self) -> list[Any]:
        now = monotonic()
        self._just_completed = {
            key: deadline for key, deadline in self._just_completed.items() if deadline > now
        }
        tasks = [
            item
            for item in self.memory.todos.today()
            if not item.completed or item.id in self._just_completed
        ]
        return sorted(tasks, key=self._task_priority_key)

    def _task_priority_key(self, task: Any) -> tuple[int, int, int, str]:
        """Return the stable order used by both expanded and collapsed views."""

        current = str(getattr(task, "id", "")) == str(self.memory.current_task_id or "")
        important = bool(getattr(task, "important", False))
        raw_time = str(getattr(task, "time", "") or "")
        try:
            hour, minute = (int(part) for part in raw_time.split(":", 1))
            time_value = hour * 60 + minute
        except (TypeError, ValueError):
            time_value = 24 * 60 + 1
        return (
            -int(current),
            -int(important),
            time_value,
            str(getattr(task, "created_at", "") or ""),
        )

    def refresh(self) -> None:
        for row in tuple(self._rows.values()):
            row.deleteLater()
        self._rows.clear()
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        tasks = self._visible_tasks()
        display_limit = self.MAX_COLLAPSED_ROWS if self.collapsed else self.MAX_EXPANDED_ROWS
        display_tasks = tasks[:display_limit]
        self._resize_width(display_tasks)
        action_buttons: list[QToolButton] = []
        for task in display_tasks:
            row = TodoRow(
                task,
                self.rows_container,
                action_parent=self.action_column,
            )
            row.set_selected(task.id == self.selected_task_id)
            row.selected.connect(self._select_task)
            row.checked.connect(self._check_task)
            row.more_requested.connect(self._show_task_menu)
            self._rows[task.id] = row
            action_buttons.append(row.more_button)
            self.rows_layout.addWidget(row)
        self.rows_layout.addStretch(1)
        self.action_column.rebuild(action_buttons, len(display_tasks))
        has_multiple = len(tasks) > 1
        self.expand_button.setVisible(has_multiple)
        self.expand_button.setText("⌃" if self.collapsed else "⌄")
        self.expand_button.setToolTip("展开更多待办" if self.collapsed else "收起待办")
        self.rows_scroll.setVisible(bool(display_tasks))
        self._resize_to_content()

    def _resize_to_content(self) -> None:
        count = len(self._visible_tasks())
        visible_rows = min(
            count,
            self.MAX_COLLAPSED_ROWS if self.collapsed else self.MAX_EXPANDED_ROWS,
        )
        if visible_rows:
            self.rows_scroll.setFixedHeight(visible_rows * self.ROW_HEIGHT)
        else:
            self.rows_scroll.setFixedHeight(0)
        button_stack = (
            visible_rows * TodoActionColumn.BUTTON_SIZE
            + max(0, visible_rows - 1) * TodoActionColumn.ROW_GAP
            + (TodoActionColumn.ADD_GAP if visible_rows else 0)
            + TodoActionColumn.BUTTON_SIZE
        )
        body_height = max(
            visible_rows * self.ROW_HEIGHT,
            button_stack,
            TodoActionColumn.BUTTON_SIZE,
        )
        self.action_column.setFixedHeight(body_height)
        # No fixed empty canvas: an empty panel is just the tiny add affordance.
        self.adjustSize()
        footer_height = 27
        content_height = body_height + footer_height + 8
        self.setFixedHeight(max(38, content_height))

    @staticmethod
    def _task_text(task: Any) -> str:
        text = str(getattr(task, "title", ""))
        if getattr(task, "time", None):
            text += f" · {task.time}"
        return text

    def _resize_width(self, tasks: list[Any]) -> None:
        """Use one content-hugging width for all visible rows.

        The width includes the checkbox, row padding, and the real action
        button.  It is intentionally bounded so one verbose task cannot turn
        the pet accessory into a second application window.
        """

        metrics = QFontMetrics(self.font())
        longest = max(
            (metrics.horizontalAdvance(self._task_text(task)) for task in tasks),
            default=0,
        )
        # Left row padding + checkbox + checkbox gap + body gap + the fixed
        # 40px action rail + panel margins.  The rail owns both buttons, so
        # title width can never consume their space.
        fixed_controls = 5 + 22 + 4 + 6 + TodoActionColumn.WIDTH + 10
        desired = longest + fixed_controls
        self.setFixedWidth(max(self.MIN_WIDTH, min(self.MAX_WIDTH, desired)))

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
