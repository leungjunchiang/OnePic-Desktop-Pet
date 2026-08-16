"""A small, pet-attached Todo strip.

The compact Todo surface is deliberately separate from ``TodayNoteWindow``.
It is a frameless tool window with no title, statistics, chat text, or
dashboard chrome.  ``PetWindow`` owns its lifetime and repositions it below
the pet whenever the pet moves.
"""

from __future__ import annotations

import logging
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


LOGGER = logging.getLogger(__name__)


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
    """A task row whose text and height are measured independently.

    The more button belongs to this row, rather than to a shared action rail.
    That distinction matters once one task wraps to two lines: every row can
    then grow without moving or overlapping another task's action button.
    """

    MIN_HEIGHT = 36
    VERTICAL_PADDING = 12
    CHECKBOX_WIDTH = 22
    BUTTON_SIZE = 32
    ROW_LEFT = 5
    ROW_RIGHT = 4
    CONTENT_GAP = 4
    MAX_LINES = 2

    selected = Signal(str)
    checked = Signal(str, bool)
    more_requested = Signal(str, object)

    def __init__(self, task: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.task_id = str(task.id)
        self._full_text = ""
        self._completed = False
        self._content_width_hint = 80
        self._line_count = 1
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(self.ROW_LEFT, 4, self.ROW_RIGHT, 4)
        layout.setSpacing(self.CONTENT_GAP)

        self.checkbox = QCheckBox(self)
        self.checkbox.setFixedWidth(self.CHECKBOX_WIDTH)
        self.checkbox.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.checkbox.setChecked(bool(task.completed))
        self.checkbox.setToolTip("标记完成")
        self.checkbox.stateChanged.connect(
            lambda state: self.checked.emit(
                self.task_id, state == Qt.CheckState.Checked.value
            )
        )
        # Align to the first line, not the vertical middle of a wrapped row.
        layout.addWidget(self.checkbox, 0, Qt.AlignmentFlag.AlignTop)

        self.label = QLabel(self)
        self.label.setObjectName("todoTitle")
        self.label.setWordWrap(False)
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.label.setMinimumWidth(24)
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.label.setToolTip(str(task.title))
        self.label.installEventFilter(self)
        layout.addWidget(self.label, 1, Qt.AlignmentFlag.AlignTop)

        self.more_button = QToolButton(self)
        self.more_button.setObjectName("moreButton")
        self.more_button.setText("⋯")
        self.more_button.setFixedSize(self.BUTTON_SIZE, self.BUTTON_SIZE)
        self.more_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.more_button.setToolTip("任务操作")
        self.more_button.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.more_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.more_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_button.clicked.connect(self._request_more)
        layout.addWidget(self.more_button, 0, Qt.AlignmentFlag.AlignTop)

        self.set_task(task)
        self.installEventFilter(self)

    def _request_more(self) -> None:
        self.more_requested.emit(self.task_id, self.more_button)

    def set_task(self, task: Any) -> None:
        text = str(getattr(task, "display_text", "") or task.title).replace("\n", " ")
        self._full_text = text
        self._completed = bool(task.completed)
        self.label.setToolTip(text)
        self._update_text_layout()
        self._apply_completion_style()

    def _apply_completion_style(self) -> None:
        if self._completed:
            self.label.setStyleSheet("color:#819399;text-decoration:line-through;")
        else:
            self.label.setStyleSheet("color:#183c4c;")

    @staticmethod
    def _wrap_lines(text: str, metrics: QFontMetrics, width: int) -> list[str]:
        """Wrap by rendered width and reserve an ellipsis only after line 2."""

        width = max(24, int(width))
        if not text:
            return [""]
        lines: list[str] = []
        current = ""
        for character in text:
            candidate = current + character
            if current and metrics.horizontalAdvance(candidate) > width:
                lines.append(current)
                current = character
            else:
                current = candidate
        if current or not lines:
            lines.append(current)
        if len(lines) <= TodoRow.MAX_LINES:
            return lines

        # Keep the first complete line.  The remaining content is elided to
        # fit the second line only after wrapping has already been attempted.
        remainder = "".join(lines[1:])
        second = metrics.elidedText(remainder, Qt.TextElideMode.ElideRight, width)
        return [lines[0], second]

    def _update_text_layout(self) -> None:
        metrics = QFontMetrics(self.label.font())
        available = self.label.width() or self._content_width_hint
        lines = self._wrap_lines(self._full_text, metrics, available)
        self._line_count = len(lines)
        self.label.setText("\n".join(lines))
        line_height = max(1, metrics.lineSpacing())
        self.label.setFixedHeight(line_height * self._line_count)
        self.setFixedHeight(
            max(self.MIN_HEIGHT, line_height * self._line_count + self.VERTICAL_PADDING)
        )

    def set_content_width(self, width: int) -> None:
        self._content_width_hint = max(24, int(width))
        self.label.setFixedWidth(self._content_width_hint)
        self._update_text_layout()

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)
        available = max(24, self.width() - self.ROW_LEFT - self.ROW_RIGHT - self.CHECKBOX_WIDTH - self.BUTTON_SIZE - self.CONTENT_GAP * 2)
        if available != self.label.width():
            self._content_width_hint = available
            self.label.setFixedWidth(available)
            self._update_text_layout()

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


class CompactTodoPanel(QWidget):
    """Frameless Todo strip that is visually attached to the desktop pet."""

    task_selected = Signal(str)
    task_checked = Signal(str, bool)
    task_changed = Signal()

    MAX_COLLAPSED_ROWS = 1
    MAX_EXPANDED_ROWS = 3
    MIN_WIDTH = 156
    MAX_WIDTH = 320
    CONTENT_MAX_WIDTH = 220
    PANEL_HORIZONTAL_OVERHEAD = 81
    ROW_HEIGHT = TodoRow.MIN_HEIGHT
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
        # The compact strip deliberately renders at most three rows.  The
        # panel grows to the measured row heights instead of reserving a
        # scrollbar that could steal the last few pixels from the action area.
        self.rows_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rows_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.rows_container = QWidget(self.rows_scroll)
        self.rows_container.setObjectName("todoRows")
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(6)
        self.rows_scroll.setWidget(self.rows_container)

        # Each TodoRow owns its own more button.  The add button is a final
        # list row, so a wrapped task can grow without shifting another row's
        # action or clipping a shared action rail.
        root.addWidget(self.rows_scroll)

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
        self._event_refresh_timer = QTimer(self)
        self._event_refresh_timer.setInterval(60_000)
        self._event_refresh_timer.timeout.connect(self.refresh)
        self._event_refresh_timer.start()
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
            item for item in self.memory.todo_view_today()
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
        for task in display_tasks:
            row = TodoRow(
                task,
                self.rows_container,
            )
            row.set_selected(task.id == self.selected_task_id)
            row.selected.connect(self._select_task)
            row.checked.connect(self._check_task)
            row.more_requested.connect(self._show_task_menu)
            self._rows[task.id] = row
            self.rows_layout.addWidget(row)

        add_row = QWidget(self.rows_container)
        add_layout = QHBoxLayout(add_row)
        add_layout.setContentsMargins(TodoRow.ROW_LEFT, 2, TodoRow.ROW_RIGHT, 2)
        add_layout.setSpacing(0)
        add_layout.addStretch(1)
        self.add_button = QToolButton(add_row)
        self.add_button.setObjectName("addButton")
        self.add_button.setText("＋")
        self.add_button.setFixedSize(TodoRow.BUTTON_SIZE, TodoRow.BUTTON_SIZE)
        self.add_button.setToolTip("添加待办")
        self.add_button.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.add_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_button.clicked.connect(self.add_task)
        add_layout.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignTop)
        add_row.setFixedHeight(TodoRow.BUTTON_SIZE + 4)
        self._add_row = add_row
        self.rows_layout.addWidget(add_row)
        has_multiple = len(tasks) > 1
        self.expand_button.setVisible(has_multiple)
        self.expand_button.setText("⌃" if self.collapsed else "⌄")
        self.expand_button.setToolTip("展开更多待办" if self.collapsed else "收起待办")
        self.rows_scroll.setVisible(True)
        self._resize_to_content()

    def _resize_to_content(self) -> None:
        count = len(self._visible_tasks())
        visible_rows = min(
            count,
            self.MAX_COLLAPSED_ROWS if self.collapsed else self.MAX_EXPANDED_ROWS,
        )
        self.ensurePolished()
        root_layout = self.layout()
        root_layout.invalidate()
        root_layout.activate()

        # The panel width is already based on the longest visible title.  Use
        # the actual viewport width for the label, keeping checkbox and more
        # button fixed while the content area gets all remaining space.
        container_width = max(1, self.width() - 10)
        self.rows_scroll.setFixedWidth(container_width)
        self.rows_scroll.ensurePolished()
        content_width = max(
            24,
            container_width
            - TodoRow.ROW_LEFT
            - TodoRow.ROW_RIGHT
            - TodoRow.CHECKBOX_WIDTH
            - TodoRow.BUTTON_SIZE
            - TodoRow.CONTENT_GAP * 2,
        )
        self.rows_container.setFixedWidth(container_width)
        for row in self._rows.values():
            row.set_content_width(content_width)
            row.setFixedWidth(self.rows_container.width())

        self.rows_layout.invalidate()
        self.rows_layout.activate()
        add_row = getattr(self, "_add_row", None)
        if add_row is not None:
            add_row.setFixedWidth(self.rows_container.width())
        row_heights = [row.height() for row in self._rows.values()]
        row_gap = max(0, self.rows_layout.spacing())
        list_height = sum(row_heights)
        if row_heights:
            list_height += (len(row_heights) - 1) * row_gap
        if add_row is not None:
            list_height += (row_gap if row_heights else 0) + add_row.height()
        self.rows_container.adjustSize()
        self.rows_scroll.setFixedHeight(max(36, list_height + 4))
        # The expand control is useful only when there is something to
        # expand.  Keeping its layout row alive for a single task used to
        # leave a large empty tail below the accessory.
        # ``isVisible()`` is false while the companion native window itself
        # is hidden, even when the button has been explicitly enabled for the
        # next show.  Use the widget visibility property so the first layout
        # calculation also reserves the footer correctly.
        footer_height = self.expand_button.sizeHint().height() if not self.expand_button.isHidden() else 0

        # This is a native, frameless companion window.  Its own geometry is
        # the clipping boundary, so calculate it from the real layout after
        # every child has received its final size.  The previous code used
        # ``body + footer + 8`` and omitted the root layout spacing and a
        # safety pixel; that cut the lower half of the add button on Windows.
        root_layout.invalidate()
        root_layout.activate()
        layout_height = root_layout.sizeHint().height()
        explicit_height = list_height + footer_height + 12 + (2 if footer_height else 0)
        self.setFixedHeight(max(38, layout_height + 2, explicit_height))
        root_layout.activate()
        self._log_geometry("resize")

    @staticmethod
    def _task_text(task: Any) -> str:
        return str(getattr(task, "display_text", "") or getattr(task, "title", ""))

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
        content = min(self.CONTENT_MAX_WIDTH, max(60, longest))
        desired = content + self.PANEL_HORIZONTAL_OVERHEAD
        self.setFixedWidth(max(self.MIN_WIDTH, min(self.MAX_WIDTH, desired)))

    def _log_geometry(self, reason: str) -> None:
        """Log the actual widget/window sizes when debug logging is enabled."""

        if not LOGGER.isEnabledFor(logging.DEBUG):
            return
        root_layout = self.layout()
        first_row = next(iter(self._rows.values()), None)
        LOGGER.debug(
            "[TodoLayout] reason=%s panel=(x=%s,y=%s,w=%s,h=%s) "
            "requested=(w=%s,h=%s) rows=(w=%s,h=%s) first_row=(x=%s,y=%s,w=%s,h=%s) "
            "more=(x=%s,y=%s,w=%s,h=%s) add=(x=%s,y=%s,w=%s,h=%s)",
            reason,
            self.x(), self.y(), self.width(), self.height(),
            root_layout.sizeHint().width(), root_layout.sizeHint().height(),
            self.rows_scroll.width(), self.rows_scroll.height(),
            first_row.x() if first_row else -1,
            first_row.y() if first_row else -1,
            first_row.width() if first_row else -1,
            first_row.height() if first_row else -1,
            first_row.more_button.x() if first_row else -1,
            first_row.more_button.y() if first_row else -1,
            first_row.more_button.width() if first_row else -1,
            first_row.more_button.height() if first_row else -1,
            self.add_button.x(), self.add_button.y(),
            self.add_button.width(), self.add_button.height(),
        )

    def _select_task(self, task_id: str) -> None:
        task = self.memory.get_todo_view_item(task_id)
        if task is None:
            return
        if task.source_type == "todo":
            self.memory.select_task(task.source_id)
        self.selected_task_id = task.id
        for key, row in self._rows.items():
            row.set_selected(key == task.id)
        self.task_selected.emit(task.id)

    def _check_task(self, task_id: str, completed: bool) -> None:
        task = self.memory.get_todo_view_item(task_id)
        if task is None or bool(task.completed) == bool(completed):
            return
        if not self.memory.complete_todo_view_item(task_id, completed):
            return
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
        task = self.memory.get_todo_view_item(task_id)
        if task is None:
            return
        menu = QMenu(self)
        edit = time_action = pin = None
        if task.source_type == "todo":
            edit = menu.addAction("编辑")
            time_action = menu.addAction("修改时间")
            pin = menu.addAction("取消置顶" if task.important else "置顶")
        elif task.source_type == "anniversary":
            menu.addAction("这个日子先不显示").setData("acknowledge")
        complete = menu.addAction("取消完成" if task.completed else "完成")
        menu.addSeparator()
        delete = menu.addAction("删除")
        chosen = menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        if edit is not None and chosen is edit:
            self._edit_task(task_id, include_time=True)
        elif time_action is not None and chosen is time_action:
            self._edit_task(task_id, include_time=True, time_only=True)
        elif pin is not None and chosen is pin:
            self.memory.todos.update(task_id, important=not task.important)
            self.refresh()
            self.task_changed.emit()
        elif chosen is complete:
            self._check_task(task_id, not task.completed)
        elif chosen is not None and chosen.data() == "acknowledge":
            self.memory.complete_todo_view_item(task_id, True)
            self.refresh()
            self.task_changed.emit()
        elif chosen is delete:
            answer = QMessageBox.question(
                self,
                "删除待办",
                f"确定删除“{task.title}”？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.memory.delete_todo_view_item(task_id)
                self.refresh()
                self.task_changed.emit()

    def _edit_task(self, task_id: str, *, include_time: bool, time_only: bool = False) -> None:
        task = self.memory.get_todo_view_item(task_id)
        if task is None or task.source_type != "todo":
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
        self.memory.todos.update(task.source_id, **changes)
        self.refresh()
        self.task_changed.emit()

