"""A small, pet-attached Todo strip.

The compact Todo surface is deliberately separate from ``TodayNoteWindow``.
It is a frameless tool window with no title, statistics, chat text, or
dashboard chrome.  ``PetWindow`` owns its lifetime and repositions it below
the pet whenever the pet moves.
"""

from __future__ import annotations

import logging
from datetime import date
from time import monotonic
from typing import Any, Callable

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .time_memory import TimeMemory
from .todo_manager import REMINDER_ALARM, REMINDER_NONE, REMINDER_PET


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
/* Keep the measured text width equal to the drawable text width.  The
   panel reserves the checkbox and action column explicitly, so an extra
   stylesheet padding here would silently eat the final glyphs. */
QLabel#todoTitle { color: #183c4c; padding: 0; }
QToolButton { color: #557681; background: transparent; border: 0; padding: 1px 4px; }
QToolButton:hover { color: #0c807b; background: rgba(185, 228, 220, 130); border-radius: 7px; }
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

    The panel owns the single More button.  Keeping the row free of action
    controls means wrapped rows can grow naturally without clipping a button
    or leaving a fake action column at the end of every item.
    """

    MIN_HEIGHT = 36
    VERTICAL_PADDING = 12
    CHECKBOX_WIDTH = 22
    ROW_LEFT = 5
    ROW_RIGHT = 4
    CONTENT_GAP = 6
    MAX_LINES = 2
    # QFontMetrics.lineSpacing() is a baseline-to-baseline distance.  It is
    # not guaranteed to include the full painted glyph box on every platform
    # and font/DPI combination, so reserve a couple of pixels around the
    # actual font height instead of fixing the label to lineSpacing alone.
    GLYPH_SAFETY = 2

    selected = Signal(str)
    checked = Signal(str, bool)

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
        self.label.setContentsMargins(0, 0, 0, 0)
        self.label.setMargin(0)
        self.label.setMinimumWidth(24)
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.label.setToolTip(str(task.title))
        self.label.installEventFilter(self)
        layout.addWidget(self.label, 1, Qt.AlignmentFlag.AlignTop)

        self.set_task(task)
        self.installEventFilter(self)

    def set_task(self, task: Any) -> None:
        text = str(getattr(task, "display_text", "") or task.title).replace("\n", " ")
        queue_position = getattr(task, "queue_position", None)
        if queue_position in {1, 2, 3, 4, 5}:
            text = f"{('①', '②', '③', '④', '⑤')[int(queue_position) - 1]} {text}"
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
        # Use the larger of the font's painted box and baseline spacing, then
        # add a small rasterisation/DPI allowance.  Without this, glyphs with
        # descenders (for example “有”“还”“天”) can have their lower pixels
        # clipped even though the row's nominal line spacing looks correct.
        line_height = max(1, metrics.height(), metrics.lineSpacing()) + self.GLYPH_SAFETY
        self.label.setFixedHeight(line_height * self._line_count)
        self.setFixedHeight(
            max(
                self.MIN_HEIGHT,
                line_height * self._line_count + self.VERTICAL_PADDING,
            )
        )

    def set_content_width(self, width: int) -> None:
        self._content_width_hint = max(24, int(width))
        self.label.setFixedWidth(self._content_width_hint)
        self._update_text_layout()

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)
        available = max(
            24,
            self.width()
            - self.ROW_LEFT
            - self.ROW_RIGHT
            - self.CHECKBOX_WIDTH
            - self.CONTENT_GAP,
        )
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
        if (watched is self or watched is self.label) and event.type() == QEvent.Type.MouseButtonPress:
            self.selected.emit(self.task_id)
        return False


class CompactTodoPanel(QWidget):
    """Frameless Todo strip that is visually attached to the desktop pet."""

    task_selected = Signal(str)
    task_checked = Signal(str, bool)
    task_changed = Signal()

    MAX_VISIBLE_ROWS = 5
    MIN_WIDTH = 156
    MAX_WIDTH = 320
    CONTENT_MIN_WIDTH = 64
    CONTENT_MAX_WIDTH = 220
    ACTION_COLUMN_WIDTH = 40
    ACTION_BUTTON_SIZE = 32
    # Keep a small visual gap after Qt/DPI rounding; the measured native
    # distance between the two 32px buttons remains at least 8px.
    ACTION_GAP = 10
    # Do not size the action rail to the mathematical sum of its children.
    # Windows/DPI rounding and the native button frame can paint a few pixels
    # outside the logical 32px box.  The old exact-height rail was therefore
    # clipped at the bottom on some displays, most visibly on the `+` button.
    ACTION_VERTICAL_PADDING = 6
    PANEL_GAP = 8
    PANEL_VERTICAL_SAFETY = 4
    TEXT_MEASURE_SAFETY = 14
    ROW_HEIGHT = TodoRow.MIN_HEIGHT
    MAX_SCROLL_HEIGHT = 360
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
        self.selected_task_id = str(memory.current_task_id or "")
        self._just_completed: dict[str, float] = {}
        self._rows: dict[str, TodoRow] = {}
        self._list_width = 100
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

        root = QHBoxLayout(self)
        root.setContentsMargins(5, 4, 5, 4)
        root.setSpacing(self.PANEL_GAP)

        self.rows_scroll = QScrollArea(self)
        self.rows_scroll.setWidgetResizable(False)
        self.rows_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rows_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.rows_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.rows_scroll.setContentsMargins(0, 0, 0, 0)
        self.rows_scroll.viewport().setContentsMargins(0, 0, 0, 0)
        self.rows_container = QWidget(self.rows_scroll)
        self.rows_container.setObjectName("todoRows")
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(6)
        self.rows_scroll.setWidget(self.rows_container)

        root.addWidget(self.rows_scroll, 0, Qt.AlignmentFlag.AlignVCenter)

        self.action_column = QWidget(self)
        self.action_column.setObjectName("todoActionColumn")
        self.action_column.setFixedWidth(self.ACTION_COLUMN_WIDTH)
        action_layout = QVBoxLayout(self.action_column)
        action_layout.setContentsMargins(
            0,
            self.ACTION_VERTICAL_PADDING,
            0,
            self.ACTION_VERTICAL_PADDING,
        )
        action_layout.setSpacing(self.ACTION_GAP)
        action_layout.addStretch(1)

        self.more_button = QToolButton(self.action_column)
        self.more_button.setObjectName("moreButton")
        self.more_button.setText("⋯")
        self.more_button.setFixedSize(self.ACTION_BUTTON_SIZE, self.ACTION_BUTTON_SIZE)
        self.more_button.setToolTip("待办管理")
        self.more_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.more_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_button.clicked.connect(self._show_management_menu)
        action_layout.addWidget(self.more_button, 0, Qt.AlignmentFlag.AlignHCenter)

        self.add_button = QToolButton(self.action_column)
        self.add_button.setObjectName("addButton")
        self.add_button.setText("＋")
        self.add_button.setFixedSize(self.ACTION_BUTTON_SIZE, self.ACTION_BUTTON_SIZE)
        self.add_button.setToolTip("添加待办")
        self.add_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_button.clicked.connect(self.add_task)
        action_layout.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignHCenter)
        action_layout.addStretch(1)
        self.action_column.setFixedHeight(self._action_column_required_height())
        root.addWidget(self.action_column, 0, Qt.AlignmentFlag.AlignVCenter)
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
        """Compatibility no-op; the compact list no longer hides tasks."""

        del collapsed
        self.refresh()

    def _visible_tasks(self) -> list[Any]:
        now = monotonic()
        self._just_completed = {
            key: deadline for key, deadline in self._just_completed.items() if deadline > now
        }
        tasks = [
            item for item in self.memory.todo_view_upcoming()
            if not item.completed or item.id in self._just_completed
        ]
        return sorted(tasks, key=self._task_priority_key)

    def _task_priority_key(self, task: Any) -> tuple[object, ...]:
        """Return stable priority order for the visible Todo list."""

        current = str(getattr(task, "id", "")) == str(self.memory.current_task_id or "")
        important = bool(getattr(task, "important", False))
        try:
            day_value = (date.fromisoformat(str(getattr(task, "date", ""))) - self.memory.now().date()).days
        except (TypeError, ValueError):
            day_value = 3650
        raw_time = str(getattr(task, "time", "") or "")
        try:
            hour, minute = (int(part) for part in raw_time.split(":", 1))
            time_value = hour * 60 + minute
        except (TypeError, ValueError):
            time_value = 24 * 60 + 1
        raw_priority = getattr(task, "priority", None)
        try:
            explicit_priority = int(raw_priority) if raw_priority is not None else None
        except (TypeError, ValueError):
            explicit_priority = None
        if explicit_priority not in {1, 2, 3}:
            explicit_priority = None
        raw_queue = getattr(task, "queue_position", None)
        try:
            queue_position = int(raw_queue) if raw_queue is not None else None
        except (TypeError, ValueError):
            queue_position = None
        if queue_position not in {1, 2, 3, 4, 5}:
            queue_position = None
        return (
            -int(current),
            0 if queue_position is not None else 1,
            queue_position if queue_position is not None else 99,
            0 if explicit_priority is not None else (1 if important else 2),
            explicit_priority if explicit_priority is not None else 99,
            max(0, day_value),
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
        self._resize_width(tasks[: self.MAX_VISIBLE_ROWS])
        for task in tasks:
            row = TodoRow(
                task,
                self.rows_container,
            )
            row.set_selected(task.id == self.selected_task_id)
            row.selected.connect(self._select_task)
            row.checked.connect(self._check_task)
            self._rows[task.id] = row
            self.rows_layout.addWidget(row)
        self.rows_scroll.setVisible(True)
        self._resize_to_content()

    def _resize_to_content(self) -> None:
        """Measure rows first, then size the scroll host and native window."""

        self.ensurePolished()
        root_layout = self.layout()
        root_layout.invalidate()
        root_layout.activate()

        list_width = max(1, self._list_width)
        self.rows_scroll.setFixedWidth(list_width)
        self.rows_container.setFixedWidth(list_width)
        content_width = max(
            24,
            list_width
            - TodoRow.ROW_LEFT
            - TodoRow.ROW_RIGHT
            - TodoRow.CHECKBOX_WIDTH
            - TodoRow.CONTENT_GAP,
        )
        for row in self._rows.values():
            row.set_content_width(content_width)
            row.setFixedWidth(list_width)

        self.rows_layout.invalidate()
        self.rows_layout.activate()
        # TodoRow sets a fixed height after measuring its actual label font.
        # QWidget.sizeHint() can still expose the pre-layout hint here, which
        # is smaller than the rendered row and causes the scroll viewport to
        # clip the bottom row.  Use the current widget geometry as the source
        # of truth after content widths have been assigned.
        row_heights = [row.height() for row in self._rows.values()]
        row_gap = max(0, self.rows_layout.spacing())
        content_height = sum(row_heights)
        if row_heights:
            content_height += (len(row_heights) - 1) * row_gap
        list_height = max(36, content_height)
        self.rows_container.setFixedHeight(list_height)
        self.rows_scroll.setFixedHeight(
            min(self.MAX_SCROLL_HEIGHT, max(36, list_height + 4))
        )
        self.action_column.setFixedHeight(self._action_column_required_height())
        root_layout.invalidate()
        root_layout.activate()
        # QScrollArea.sizeHint() is allowed to report a smaller viewport than
        # its fixed child when the parent has not been shown yet.  Using that
        # hint for the native companion window was the source of the last
        # clipping bug: the rows container was 108px tall while the visible
        # viewport was only 94px.  Derive the minimum from the actual measured
        # list and action rail, then add the root layout's vertical margins.
        root_margins = root_layout.contentsMargins()
        layout_height = root_layout.sizeHint().height()
        list_required = list_height + 4  # scroll-frame breathing room
        action_required = self.action_column.height()
        content_required = max(list_required, action_required)
        margin_height = root_margins.top() + root_margins.bottom()
        self.setFixedHeight(
            max(
                content_required + margin_height + self.PANEL_VERTICAL_SAFETY,
                layout_height + self.PANEL_VERTICAL_SAFETY,
            )
        )
        root_layout.activate()
        self._log_geometry("resize")

    def _action_column_required_height(self) -> int:
        """Return the full logical height needed by the two action buttons.

        The padding is intentional: a fixed-height QWidget that ends exactly
        at the button's logical bottom can lose the lower border/anti-aliased
        pixels after Windows device-pixel conversion.  Keeping the clearance
        in the layout (rather than adding a one-off button offset) also makes
        the guarantee hold when the panel is repositioned or refreshed.
        """

        return (
            self.ACTION_VERTICAL_PADDING * 2
            + self.ACTION_BUTTON_SIZE * 2
            + self.ACTION_GAP
        )

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
        content = min(
            self.CONTENT_MAX_WIDTH,
            max(self.CONTENT_MIN_WIDTH, longest + self.TEXT_MEASURE_SAFETY),
        )
        list_desired = (
            TodoRow.ROW_LEFT
            + TodoRow.CHECKBOX_WIDTH
            + TodoRow.CONTENT_GAP
            + content
            + TodoRow.ROW_RIGHT
        )
        panel_desired = 5 + list_desired + self.PANEL_GAP + self.ACTION_COLUMN_WIDTH + 5
        panel_width = max(self.MIN_WIDTH, min(self.MAX_WIDTH, panel_desired))
        self.setFixedWidth(panel_width)
        # Reserve the complete action rail before assigning any width to text.
        self._list_width = max(
            64,
            panel_width
            - 5
            - self.PANEL_GAP
            - self.ACTION_COLUMN_WIDTH
            - 5,
        )

    def _log_geometry(self, reason: str) -> None:
        """Log the actual widget/window sizes when debug logging is enabled."""

        if not LOGGER.isEnabledFor(logging.DEBUG):
            return
        root_layout = self.layout()
        first_row = next(iter(self._rows.values()), None)
        LOGGER.debug(
            "[TodoLayout] reason=%s panel=(x=%s,y=%s,w=%s,h=%s) "
            "requested=(w=%s,h=%s) rows=(x=%s,y=%s,w=%s,h=%s) "
            "first_row=(x=%s,y=%s,w=%s,h=%s) action=(x=%s,y=%s,w=%s,h=%s) "
            "more=(x=%s,y=%s,w=%s,h=%s) add=(x=%s,y=%s,w=%s,h=%s)",
            reason,
            self.x(), self.y(), self.width(), self.height(),
            root_layout.sizeHint().width(), root_layout.sizeHint().height(),
            self.rows_scroll.x(), self.rows_scroll.y(),
            self.rows_scroll.width(), self.rows_scroll.height(),
            first_row.x() if first_row else -1,
            first_row.y() if first_row else -1,
            first_row.width() if first_row else -1,
            first_row.height() if first_row else -1,
            self.action_column.x(), self.action_column.y(),
            self.action_column.width(), self.action_column.height(),
            self.more_button.x(), self.more_button.y(),
            self.more_button.width(), self.more_button.height(),
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

    def _show_management_menu(self) -> None:
        """Open the one panel-level menu for the selected Todo row."""

        task = self.memory.get_todo_view_item(self.selected_task_id)
        menu = QMenu(self)
        edit = time_action = pin = read_action = None
        complete = delete = None
        if task is not None:
            if task.source_type == "todo":
                edit = menu.addAction("编辑选中待办")
                time_action = menu.addAction("修改时间")
                pin = menu.addAction("取消置顶" if task.important else "置顶")
                read_action = menu.addAction("标为已读（从桌面贴纸收起）")
            elif task.source_type == "anniversary":
                menu.addAction("这个日子先不显示").setData("acknowledge")
            complete = menu.addAction("取消完成" if task.completed else "完成")
            menu.addSeparator()
            delete = menu.addAction("删除")
        else:
            menu.addAction("先点选一项待办").setEnabled(False)
        menu.addSeparator()
        add = menu.addAction("添加待办")
        chosen = menu.exec(
            self.more_button.mapToGlobal(self.more_button.rect().bottomLeft())
        )
        if task is not None and edit is not None and chosen is edit:
            self._edit_task(task.id, include_time=True)
        elif task is not None and time_action is not None and chosen is time_action:
            self._edit_task(task.id, include_time=True, time_only=True)
        elif task is not None and pin is not None and chosen is pin:
            self.memory.todos.update(task.source_id, important=not task.important)
            self.refresh()
            self.task_changed.emit()
        elif task is not None and read_action is not None and chosen is read_action:
            self.memory.read_todo_view_item(task.id, True)
            self.selected_task_id = ""
            self.refresh()
            self.task_changed.emit()
        elif task is not None and chosen is complete:
            self._check_task(task.id, not task.completed)
        elif task is not None and chosen is not None and chosen.data() == "acknowledge":
            self.memory.complete_todo_view_item(task.id, True)
            self.refresh()
            self.task_changed.emit()
        elif task is not None and delete is not None and chosen is delete:
            answer = QMessageBox.question(
                self,
                "删除待办",
                f"确定删除“{task.title}”？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.memory.delete_todo_view_item(task.id)
                self.selected_task_id = ""
                self.refresh()
                self.task_changed.emit()
        elif chosen is add:
            self.add_task()

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
        queue_position = QComboBox(dialog)
        queue_position.addItem("未排顺位（按时间）", None)
        for position, label in enumerate(("第1", "第2", "第3", "第4", "第5"), start=1):
            queue_position.addItem(label, position)
        queue_index = queue_position.findData(getattr(task, "queue_position", None))
        queue_position.setCurrentIndex(queue_index if queue_index >= 0 else 0)
        reminder_mode = QComboBox(dialog)
        reminder_mode.addItem("不提醒", REMINDER_NONE)
        reminder_mode.addItem("六毛提醒（无声音）", REMINDER_PET)
        reminder_mode.addItem("六毛闹钟（播放系统提示音）", REMINDER_ALARM)
        mode = str(getattr(task, "reminder_mode", "") or (REMINDER_PET if getattr(task, "reminder", False) else REMINDER_NONE))
        mode_index = reminder_mode.findData(mode)
        reminder_mode.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        reminder_minutes = QSpinBox(dialog)
        reminder_minutes.setRange(0, 24 * 60)
        reminder_minutes.setSuffix(" 分钟前")
        reminder_minutes.setValue(
            max(0, min(24 * 60, int(getattr(task, "reminder_minutes_before", 10) or 0)))
        )
        reminder_minutes.setEnabled(reminder_mode.currentData() != REMINDER_NONE)
        reminder_mode.currentIndexChanged.connect(
            lambda: reminder_minutes.setEnabled(reminder_mode.currentData() != REMINDER_NONE)
        )
        if not time_only:
            form.addRow("任务", title)
            form.addRow("时间", time)
            form.addRow("", important)
            form.addRow("任务顺位", queue_position)
            form.addRow("提醒方式", reminder_mode)
            form.addRow("提前提醒", reminder_minutes)
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
            changes.update(
                title=title.text().strip(),
                important=important.isChecked(),
                queue_position=queue_position.currentData(),
                reminder_mode=reminder_mode.currentData(),
                reminder=reminder_mode.currentData() != REMINDER_NONE,
                reminder_minutes_before=reminder_minutes.value(),
            )
        saved = self.memory.todos.update(task.source_id, **changes)
        self.memory.sync_todo_reminder(saved)
        self.refresh()
        self.task_changed.emit()

