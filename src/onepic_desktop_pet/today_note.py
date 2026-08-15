"""Compact today's note and the non-resident time-memory window."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QInputDialog,
    QPushButton,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .time_memory import TimeMemory
from .time_service import format_duration


PAPER_STYLE = """
QDialog, QWidget { background:#f5f8fa; color:#263d4b; font-family:'Microsoft YaHei UI','PingFang SC',sans-serif; }
QListWidget { background:#ffffff; border:1px solid #c5d5dc; border-radius:10px; padding:4px; }
QListWidget::item { padding:7px 6px; min-height:20px; border-bottom:1px solid #edf1f3; }
QListWidget::item:selected { background:#dff1ed; color:#154b54; border-radius:7px; }
QPushButton, QToolButton { background:#d6ece8; color:#154b54; border:0; border-radius:8px; padding:5px 10px; min-height:24px; }
QPushButton:hover, QToolButton:hover { background:#c4e3de; }
QToolButton#moreButton { background:transparent; color:#607985; font-size:18px; padding:0 5px; }
QLineEdit { background:white; border:1px solid #b7ccd5; border-radius:8px; padding:7px; }
"""


class TodayNoteWindow(QDialog):
    """A small, taskbar-friendly note beside the pet.

    Selecting a row only changes the current task. It never starts, deletes,
    or hides that task; those operations have their own explicit buttons.
    """

    start_requested = Signal(str)
    complete_requested = Signal(str)
    select_requested = Signal(str)
    checkout_requested = Signal()
    rest_requested = Signal()
    memory_requested = Signal()

    def __init__(
        self,
        memory: TimeMemory,
        parent: QWidget | None = None,
        *,
        settings: Any | None = None,
        save_settings_callback: Callable[[Any], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.memory = memory
        self.settings = settings
        self.save_settings_callback = save_settings_callback
        self.folded = False
        self.hide_completed = bool(getattr(settings, "today_note_hide_completed", False))

        self.setWindowTitle("今日小纸条 · 六毛")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setFixedWidth(360)
        self.setMinimumHeight(220)
        self.setMaximumHeight(420)
        self.resize(360, 280)
        self.setStyleSheet(PAPER_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(4)
        self.title_label = QLabel("📋 今日小纸条")
        self.title_label.setStyleSheet("font-size:17px;font-weight:700;")
        header.addWidget(self.title_label)
        header.addStretch(1)
        self.more_button = QToolButton()
        self.more_button.setObjectName("moreButton")
        self.more_button.setText("···")
        self.more_button.setToolTip("更多")
        self.more_menu = self._build_more_menu()
        self.more_button.setMenu(self.more_menu)
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        header.addWidget(self.more_button)
        root.addLayout(header)

        self.important_label = QLabel()
        self.important_label.setWordWrap(True)
        self.important_label.setStyleSheet("font-size:13px;font-weight:700;color:#0c807b;")
        root.addWidget(self.important_label)

        self.folded_label = QLabel()
        self.folded_label.setStyleSheet("font-size:12px;color:#527080;")
        self.folded_label.hide()
        root.addWidget(self.folded_label)

        self.content = QWidget()
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(6)

        self.task_list = QListWidget()
        self.task_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.task_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.task_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.task_list.itemClicked.connect(self._select_item)
        content_layout.addWidget(self.task_list)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.start_button = QPushButton("▶ 开始")
        self.start_button.clicked.connect(self._start_selected)
        actions.addWidget(self.start_button)
        self.complete_button = QPushButton("✓ 完成")
        self.complete_button.clicked.connect(self._complete_selected)
        actions.addWidget(self.complete_button)
        self.add_button = QToolButton()
        self.add_button.setText("＋")
        self.add_button.setToolTip("添加待办")
        self.add_menu = QMenu(self)
        add_todo = QAction("添加待办", self)
        add_todo.triggered.connect(lambda: self.add_task(False))
        self.add_menu.addAction(add_todo)
        add_important = QAction("添加今日最重要的一件事", self)
        add_important.triggered.connect(lambda: self.add_task(True))
        self.add_menu.addAction(add_important)
        self.add_button.setMenu(self.add_menu)
        self.add_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        actions.addWidget(self.add_button)
        content_layout.addLayout(actions)

        self.stats_label = QLabel()
        self.stats_label.setStyleSheet("font-size:12px;color:#607985;")
        content_layout.addWidget(self.stats_label)
        root.addWidget(self.content)

        self.refresh()

    def _build_more_menu(self) -> QMenu:
        menu = QMenu(self)
        self.fold_action = QAction("折叠便签", self)
        self.fold_action.triggered.connect(self.toggle_fold)
        menu.addAction(self.fold_action)

        self.hide_completed_action = QAction("隐藏已完成", self)
        self.hide_completed_action.setCheckable(True)
        self.hide_completed_action.setChecked(self.hide_completed)
        self.hide_completed_action.triggered.connect(self._toggle_hide_completed)
        menu.addAction(self.hide_completed_action)
        menu.addSeparator()

        settings_action = QAction("显示设置", self)
        settings_action.triggered.connect(self.configure_display)
        menu.addAction(settings_action)
        memory_action = QAction("我的时光", self)
        memory_action.triggered.connect(self.memory_requested.emit)
        menu.addAction(memory_action)
        menu.addSeparator()

        checkout_action = QAction("今天收工", self)
        checkout_action.triggered.connect(self.checkout_requested.emit)
        menu.addAction(checkout_action)
        rest_action = QAction("今天休息", self)
        rest_action.triggered.connect(self.rest_requested.emit)
        menu.addAction(rest_action)
        return menu

    def refresh(self) -> None:
        tasks = self.memory.todos.today()
        selected_id = str(self.memory.current_task_id or "")
        visible_tasks = [item for item in tasks if not self.hide_completed or not item.completed]
        self.task_list.clear()

        important = self.memory.todos.important_for()
        if important is None:
            self.important_label.setText("★ 今日最重要：还没设置，点右下角 ＋ 添加")
        else:
            prefix = "✓ " if important.completed else ""
            self.important_label.setText(f"★ 今日最重要：{prefix}{important.title}")
            if important.completed:
                self.important_label.setStyleSheet("font-size:13px;color:#7b9098;text-decoration:line-through;")
            else:
                self.important_label.setStyleSheet("font-size:13px;font-weight:700;color:#0c807b;")

        for item in visible_tasks:
            selected = item.id == selected_id
            prefix = "✓" if item.completed else ("●" if selected else "○")
            parts = [f"{prefix} {item.title}"]
            if item.time:
                parts.append(item.time)
            if item.work_seconds:
                parts.append(format_duration(item.work_seconds))
            list_item = QListWidgetItem(" · ".join(parts))
            list_item.setData(Qt.ItemDataRole.UserRole, item.id)
            list_item.setToolTip(item.title)
            if item.completed:
                font = list_item.font()
                font.setStrikeOut(True)
                list_item.setFont(font)
                list_item.setForeground(Qt.GlobalColor.gray)
            self.task_list.addItem(list_item)

        if not visible_tasks:
            message = "今天还没有待办，点右下角 ＋ 添加" if not tasks else "已完成的任务已隐藏"
            empty = QListWidgetItem(message)
            empty.setFlags(Qt.ItemFlag.ItemIsEnabled)
            empty.setForeground(Qt.GlobalColor.gray)
            self.task_list.addItem(empty)
            self.task_list.setCurrentItem(None)
        else:
            for index in range(self.task_list.count()):
                item = self.task_list.item(index)
                if item.data(Qt.ItemDataRole.UserRole) == selected_id:
                    self.task_list.setCurrentItem(item)
                    break

        summary = self.memory.summary.today()
        self.stats_label.setText(
            f"今日专注 {summary['focus']} · 完成 {summary['completed_tasks']}/{summary['total_tasks']}"
        )
        pending_count = len([item for item in tasks if not item.completed])
        self.folded_label.setText(f"📋 今天 {pending_count} 件待办 · 今日专注 {summary['focus']}")
        self.hide_completed_action.setChecked(self.hide_completed)
        self.fold_action.setText("展开便签" if self.folded else "折叠便签")
        self._update_action_state()
        self._resize_to_content(len(visible_tasks))

    def _resize_to_content(self, task_count: int) -> None:
        if self.folded:
            self.setMinimumHeight(0)
            self.setMaximumHeight(78)
            self.setFixedHeight(70)
            return
        self.setMinimumHeight(220)
        self.setMaximumHeight(420)
        rows = max(1, min(5, task_count))
        self.task_list.setFixedHeight(42 + rows * 34)
        self.adjustSize()
        target = max(220, min(420, self.sizeHint().height()))
        self.resize(360, target)

    def _select_item(self, item: QListWidgetItem) -> None:
        task_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not task_id:
            return
        self.memory.select_task(task_id)
        self.select_requested.emit(task_id)
        self._update_action_state()

    def _selected_id(self) -> str:
        item = self.task_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item is not None else ""

    def _update_action_state(self) -> None:
        task_id = self._selected_id()
        task = self.memory.todos.get(task_id) if task_id else None
        self.start_button.setEnabled(task is not None and not task.completed)
        self.complete_button.setEnabled(task is not None and not task.completed)

    def _start_selected(self) -> None:
        task_id = self._selected_id()
        task = self.memory.todos.get(task_id) if task_id else None
        if task is not None and not task.completed:
            self.select_requested.emit(task_id)
            self.start_requested.emit(task_id)

    def _complete_selected(self) -> None:
        task_id = self._selected_id()
        task = self.memory.todos.get(task_id) if task_id else None
        if task is not None and not task.completed:
            self.complete_requested.emit(task_id)

    def add_task(self, important: bool = False) -> None:
        title, ok = QInputDialog.getText(self, "添加待办", "今天要做什么？")
        if not ok or not title.strip():
            return
        task = self.memory.todos.add(title.strip(), important=important)
        self.memory.select_task(task.id)
        self.select_requested.emit(task.id)
        self.refresh()

    def _toggle_hide_completed(self, checked: bool) -> None:
        self.hide_completed = bool(checked)
        if self.settings is not None:
            self.settings.today_note_hide_completed = self.hide_completed
            if self.save_settings_callback is not None:
                self.save_settings_callback(self.settings)
        self.refresh()

    def toggle_fold(self) -> None:
        self.folded = not self.folded
        self.content.setVisible(not self.folded)
        self.important_label.setVisible(not self.folded)
        self.folded_label.setVisible(self.folded)
        self.fold_action.setText("展开便签" if self.folded else "折叠便签")
        if self.settings is not None:
            self.settings.today_note_folded = self.folded
            if self.save_settings_callback is not None:
                self.save_settings_callback(self.settings)
        self._resize_to_content(self.task_list.count())

    def configure_display(self) -> None:
        """Persist the paper's display policy without creating another window."""

        if self.settings is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("今日小纸条显示设置")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        mode = QComboBox(dialog)
        mode.addItem("有待办时显示", "pending")
        mode.addItem("始终显示", "always")
        mode.addItem("隐藏", "hidden")
        current_mode = str(getattr(self.settings, "today_note_display_mode", "pending"))
        mode.setCurrentIndex(max(0, mode.findData(current_mode)))
        form.addRow("显示方式", mode)
        topmost = QCheckBox("窗口置顶", dialog)
        topmost.setChecked(bool(getattr(self.settings, "today_note_always_on_top", False)))
        autoshow = QCheckBox("启动时自动打开", dialog)
        autoshow.setChecked(bool(getattr(self.settings, "today_note_autoshow", False)))
        folded = QCheckBox("打开时默认折叠", dialog)
        folded.setChecked(bool(getattr(self.settings, "today_note_folded", False)))
        form.addRow("", topmost)
        form.addRow("", autoshow)
        form.addRow("", folded)
        layout.addLayout(form)
        hint = QLabel("便签会根据任务数量自动调整高度，任务过多时只在列表内滚动。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#607985;")
        layout.addWidget(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.settings.today_note_display_mode = str(mode.currentData())
        self.settings.today_note_always_on_top = topmost.isChecked()
        self.settings.today_note_autoshow = autoshow.isChecked()
        self.settings.today_note_folded = folded.isChecked()
        if self.save_settings_callback is not None:
            self.save_settings_callback(self.settings)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, topmost.isChecked())
        self.show()
        if folded.isChecked() != self.folded:
            self.toggle_fold()

    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()
        self.hide()


class TimeMemoryWindow(QDialog):
    """Non-resident review window for countdowns, anniversaries and timeline."""

    def __init__(self, memory: TimeMemory, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.memory = memory
        self.setWindowTitle("我的时光 · 六毛")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.resize(620, 480)
        self.setStyleSheet(PAPER_STYLE)
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.countdown_list = QListWidget()
        self.anniversary_list = QListWidget()
        self.timeline_list = QListWidget()
        self.countdown_list.itemDoubleClicked.connect(self._complete_countdown)
        self.tabs.addTab(self._page(self.countdown_list, self.add_countdown), "倒计时")
        self.tabs.addTab(self._page(self.anniversary_list, self.add_anniversary), "纪念日")
        self.tabs.addTab(self._page(self.timeline_list, self.add_timeline), "时光轴")
        root.addWidget(self.tabs)
        self.refresh()

    def _page(self, listing: QListWidget, callback) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(listing, 1)
        button = QPushButton("＋ 添加")
        button.clicked.connect(callback)
        layout.addWidget(button)
        return page

    def refresh(self) -> None:
        self.countdown_list.clear()
        countdowns = [
            (item, self.memory.countdowns.remaining_days(item))
            for item in self.memory.countdowns.items
            if not item.completed
        ]
        countdowns.sort(key=lambda pair: (not pair[0].pinned, pair[1], pair[0].title))
        for item, remaining in countdowns:
            if remaining == 0:
                label = "就是今天"
            elif remaining < 0:
                label = f"已经过去 {-remaining} 天"
            else:
                label = f"还有 {remaining} 天"
            list_item = QListWidgetItem(f"{item.title} · {label}")
            list_item.setData(Qt.ItemDataRole.UserRole, item.id)
            self.countdown_list.addItem(list_item)

        self.anniversary_list.clear()
        for item in self.memory.anniversaries.items:
            remaining = self.memory.anniversaries.remaining_days(item)
            label = "今天" if remaining == 0 else f"还有 {remaining} 天"
            self.anniversary_list.addItem(f"{item.title} · {label}")

        self.timeline_list.clear()
        for item in self.memory.timeline.query()[:100]:
            self.timeline_list.addItem(f"{item.date} {item.time}  {item.title}\n{item.description}")

    def add_countdown(self) -> None:
        title, ok = QInputDialog.getText(self, "添加倒计时", "事项名称")
        if not ok or not title.strip():
            return
        target, ok = QInputDialog.getText(self, "添加倒计时", "目标日期（YYYY-MM-DD）")
        if not ok or not target.strip():
            return
        try:
            self.memory.countdowns.add(title.strip(), target.strip())
        except (TypeError, ValueError):
            QMessageBox.warning(self, "倒计时", "日期格式不对，请填写 YYYY-MM-DD。")
        self.refresh()

    def add_anniversary(self) -> None:
        title, ok = QInputDialog.getText(self, "添加纪念日", "纪念什么？")
        if not ok or not title.strip():
            return
        value, ok = QInputDialog.getText(self, "添加纪念日", "日期（YYYY-MM-DD）")
        if not ok or not value.strip():
            return
        repeat, ok = QInputDialog.getItem(self, "添加纪念日", "重复方式", ["none", "yearly"], 1, False)
        if not ok:
            return
        try:
            self.memory.anniversaries.add(title.strip(), value.strip(), repeat=repeat)
        except (TypeError, ValueError):
            QMessageBox.warning(self, "纪念日", "日期格式不对，请填写 YYYY-MM-DD。")
        self.refresh()

    def _complete_countdown(self, item: QListWidgetItem) -> None:
        item_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if item_id:
            self.memory.complete_countdown(item_id)
            self.refresh()

    def add_timeline(self) -> None:
        title, ok = QInputDialog.getText(self, "记住今天", "今天发生了什么？")
        if ok and title.strip():
            self.memory.timeline.add(title.strip())
            self.refresh()
