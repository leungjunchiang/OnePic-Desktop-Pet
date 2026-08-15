"""A compact, normal taskbar window for today's paper and time memories."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
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
    QMessageBox,
    QInputDialog,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .time_memory import TimeMemory
from .time_service import format_duration


PAPER_STYLE = """
QDialog, QWidget { background:#f5f8fa; color:#263d4b; font-family:'Microsoft YaHei UI','PingFang SC',sans-serif; }
QListWidget { background:#ffffff; border:1px solid #c5d5dc; border-radius:12px; padding:6px; }
QListWidget::item { padding:8px 6px; border-bottom:1px solid #edf1f3; }
QPushButton { background:#d6ece8; color:#154b54; border:0; border-radius:9px; padding:7px 12px; }
QPushButton:hover { background:#c4e3de; }
QLineEdit { background:white; border:1px solid #b7ccd5; border-radius:8px; padding:7px; }
"""


class TodayNoteWindow(QDialog):
    """The one paper window; it can be hidden or folded without being modal."""

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
        self.setWindowTitle("今日小纸条 · 六毛")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowSystemMenuHint | Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.WindowCloseButtonHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMinimumSize(380, 260)
        self.resize(460, 520)
        self.setStyleSheet(PAPER_STYLE)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 16)
        header = QHBoxLayout()
        self.title_label = QLabel("📋 今日小纸条")
        self.title_label.setStyleSheet("font-size:20px;font-weight:700;")
        header.addWidget(self.title_label)
        header.addStretch(1)
        self.fold_button = QPushButton("折叠")
        self.fold_button.clicked.connect(self.toggle_fold)
        header.addWidget(self.fold_button)
        self.settings_button = QPushButton("显示设置")
        self.settings_button.clicked.connect(self.configure_display)
        header.addWidget(self.settings_button)
        self.memory_button = QPushButton("我的时光")
        self.memory_button.clicked.connect(self.memory_requested.emit)
        header.addWidget(self.memory_button)
        root.addLayout(header)
        self.folded_label = QLabel()
        self.folded_label.setStyleSheet("font-size:13px;color:#527080;")
        self.folded_label.hide()
        root.addWidget(self.folded_label)
        self.content = QWidget()
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        self.important_label = QLabel()
        self.important_label.setWordWrap(True)
        self.important_label.setStyleSheet("font-size:15px;font-weight:700;color:#0c807b;")
        content_layout.addWidget(self.important_label)
        self.task_list = QListWidget()
        self.task_list.itemDoubleClicked.connect(self._select_item)
        content_layout.addWidget(self.task_list, 1)
        actions = QHBoxLayout()
        self.start_button = QPushButton("开始选中")
        self.start_button.clicked.connect(self._start_selected)
        actions.addWidget(self.start_button)
        self.complete_button = QPushButton("完成选中")
        self.complete_button.clicked.connect(self._complete_selected)
        actions.addWidget(self.complete_button)
        self.add_button = QPushButton("添加待办")
        self.add_button.clicked.connect(self.add_task)
        actions.addWidget(self.add_button)
        self.checkout_button = QPushButton("今天收工")
        self.checkout_button.clicked.connect(self.checkout_requested.emit)
        actions.addWidget(self.checkout_button)
        self.rest_button = QPushButton("今天休息")
        self.rest_button.clicked.connect(self.rest_requested.emit)
        actions.addWidget(self.rest_button)
        content_layout.addLayout(actions)
        self.stats_label = QLabel()
        self.stats_label.setStyleSheet("color:#607985;")
        content_layout.addWidget(self.stats_label)
        self.countdown_label = QLabel()
        self.countdown_label.setWordWrap(True)
        self.countdown_label.setStyleSheet("color:#8a5b3d;font-weight:600;")
        content_layout.addWidget(self.countdown_label)
        root.addWidget(self.content, 1)
        self.refresh()

    def refresh(self) -> None:
        tasks = self.memory.todos.today()
        self.task_list.clear()
        important = next((item for item in tasks if item.important and not item.completed), None)
        self.important_label.setText(f"★ 今日重点：{important.title}" if important else "★ 今日重点：还没设，先挑一件最重要的")
        for item in tasks:
            prefix = "✓" if item.completed else "□"
            when = f" · {item.time}" if item.time else ""
            work = f" · {format_duration(item.work_seconds)}" if item.work_seconds else ""
            list_item = QListWidgetItem(f"{prefix} {item.title}{when}{work}")
            list_item.setData(Qt.ItemDataRole.UserRole, item.id)
            if item.completed:
                list_item.setForeground(Qt.GlobalColor.gray)
            self.task_list.addItem(list_item)
        summary = self.memory.summary.today()
        self.stats_label.setText(f"今天已专注 {summary['focus']} · 完成 {summary['completed_tasks']}/{summary['total_tasks']} · 工作段 {summary['sessions']} 次")
        desktop_countdowns = self.memory.countdowns.desktop_items(3)
        if desktop_countdowns:
            labels = []
            for item, remaining in desktop_countdowns:
                if remaining == 0:
                    label = "就是今天"
                elif remaining < 0:
                    label = f"已经过去 {-remaining} 天"
                else:
                    label = f"还有 {remaining} 天"
                labels.append(f"{item.title}：{label}")
            self.countdown_label.setText("重要日子 · " + "  |  ".join(labels))
        else:
            self.countdown_label.clear()
        self.folded_label.setText(f"📋 今天 {len([item for item in tasks if not item.completed])} 件待办 · 已专注 {summary['focus']}")

    def _select_item(self, item: QListWidgetItem) -> None:
        task_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if task_id:
            self.memory.select_task(task_id)
            self.select_requested.emit(task_id)
            self.start_requested.emit(task_id)

    def _selected_id(self) -> str:
        item = self.task_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item is not None else ""

    def _start_selected(self) -> None:
        task_id = self._selected_id()
        if task_id:
            self.select_requested.emit(task_id)
            self.start_requested.emit(task_id)

    def _complete_selected(self) -> None:
        task_id = self._selected_id()
        if task_id:
            self.complete_requested.emit(task_id)

    def add_task(self) -> None:
        title, ok = QInputDialogCompat.get_text(self, "添加待办", "今天要做什么？")
        if ok and title.strip():
            self.memory.todos.add(title.strip())
            self.refresh()

    def toggle_fold(self) -> None:
        self.folded = not self.folded
        self.content.setVisible(not self.folded)
        self.folded_label.setVisible(self.folded)
        self.fold_button.setText("展开" if self.folded else "折叠")
        if self.folded:
            self.setFixedHeight(78)
        else:
            self.setMinimumHeight(260)
            self.setMaximumHeight(16777215)
            self.resize(max(380, self.width()), max(360, self.height()))

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
        mode.addItem("启动时隐藏", "hidden")
        current_mode = str(getattr(self.settings, "today_note_display_mode", "pending"))
        index = max(0, mode.findData(current_mode))
        mode.setCurrentIndex(index)
        form.addRow("启动显示", mode)
        topmost = QCheckBox("窗口始终置顶", dialog)
        topmost.setChecked(bool(getattr(self.settings, "today_note_always_on_top", False)))
        autoshow = QCheckBox("启动时自动打开", dialog)
        autoshow.setChecked(bool(getattr(self.settings, "today_note_autoshow", False)))
        folded = QCheckBox("打开时默认折叠", dialog)
        folded.setChecked(bool(getattr(self.settings, "today_note_folded", False)))
        form.addRow("", topmost)
        form.addRow("", autoshow)
        form.addRow("", folded)
        layout.addLayout(form)
        hint = QLabel("可以从右键菜单随时显示或隐藏；折叠后只占一条小纸条。")
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


class QInputDialogCompat:
    @staticmethod
    def get_text(parent: QWidget, title: str, label: str) -> tuple[str, bool]:
        from PySide6.QtWidgets import QInputDialog
        return QInputDialog.getText(parent, title, label)


class TimeMemoryWindow(QDialog):
    """Non-resident review window for countdowns, anniversaries and timeline."""

    def __init__(self, memory: TimeMemory, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.memory = memory
        self.setWindowTitle("我的时光 · 六毛")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowSystemMenuHint | Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.WindowCloseButtonHint)
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
        page = QWidget(); layout = QVBoxLayout(page); layout.addWidget(listing, 1)
        button = QPushButton("＋ 添加")
        button.clicked.connect(callback)
        layout.addWidget(button)
        return page

    def refresh(self) -> None:
        self.countdown_list.clear()
        countdowns = [(item, self.memory.countdowns.remaining_days(item)) for item in self.memory.countdowns.items if not item.completed]
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
            self.anniversary_list.addItem(f"{item.title} · {'今天' if remaining == 0 else '还有 ' + str(remaining) + ' 天'}")
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
        title, ok = QInputDialogCompat.get_text(self, "记住今天", "今天发生了什么？")
        if ok and title.strip():
            self.memory.timeline.add(title.strip())
            self.refresh()
