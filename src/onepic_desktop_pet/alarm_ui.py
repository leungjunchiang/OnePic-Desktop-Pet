"""Non-modal UI for managing and handling Lili alarms."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import QDateTime, QEvent, QUrl, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGraphicsDropShadowEffect,
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
from .alarm_sounds import AlarmSoundLibrary

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
except ImportError:  # pragma: no cover - depends on the bundled Qt runtime
    QAudioOutput = QMediaPlayer = None


ALARM_STYLE = """
QDialog#alarmCenter, QDialog#alarmEditor { background: #eef5f8; color: #24475b; }
QDialog#alarmCard { background: #f4fafc; color: #24475b;
    border: 1px solid #9fc2cd; border-radius: 18px; }
QDialog#alarmCard QLabel { background: transparent; border: none; }
QDialog#alarmCard QPushButton { background: #e7f3f6; color: #24475b;
    border: 1px solid #9fcbd5; border-radius: 10px; padding: 7px 12px; }
QDialog#alarmCard QPushButton:hover { background: #d8edf1; border-color: #e46d70; }
QDialog#alarmCard QPushButton#primary { background: #d0e9ef; font-weight: 700; }
QDialog#alarmCard QPushButton#quiet { background: transparent; border-color: transparent; color: #607985; }
QListWidget { background: rgba(255,255,255,210); border: 1px solid #b4ccd5; border-radius: 10px; }
"""


class AlarmSoundSelector(QWidget):
    """Shared sound picker used by standalone alarms and Todo alarms."""

    changed = Signal(str)

    def __init__(
        self,
        library: AlarmSoundLibrary | None,
        selected_id: str = "system",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.library = library or AlarmSoundLibrary(persist=False)
        self.combo = QComboBox(self)
        self.import_button = QPushButton("导入自定义音频…", self)
        self.preview_button = QPushButton("试听", self)
        self.stop_button = QPushButton("停止", self)
        self.delete_button = QPushButton("删除", self)
        self.import_button.clicked.connect(self._import)
        self.preview_button.clicked.connect(self.preview)
        self.stop_button.clicked.connect(self.stop_preview)
        self.delete_button.clicked.connect(self._delete)
        self.combo.currentIndexChanged.connect(self._changed_once)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        row.addWidget(self.combo, 1)
        row.addWidget(self.import_button)
        row.addWidget(self.preview_button)
        row.addWidget(self.stop_button)
        row.addWidget(self.delete_button)
        self._preview_output = QAudioOutput(self) if QAudioOutput is not None else None
        self._preview_player = QMediaPlayer(self) if QMediaPlayer is not None else None
        if self._preview_player is not None and self._preview_output is not None:
            self._preview_player.setAudioOutput(self._preview_output)
            self._preview_player.errorOccurred.connect(lambda *_args: self.stop_preview())
        self.refresh(selected_id)

    def refresh(self, selected_id: str | None = None) -> None:
        selected = str(selected_id or self.current_sound_id() or "system")
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem("系统提示音", "system")
        self.combo.addItem("六毛默认铃声（无内置音频时使用系统音）", "default")
        for sound in self.library.items:
            self.combo.addItem(sound.display_name, sound.sound_id)
        index = self.combo.findData(selected)
        self.combo.setCurrentIndex(index if index >= 0 else 0)
        self.combo.blockSignals(False)
        self.delete_button.setEnabled(self.combo.currentData() not in {"system", "default", None})

    def _changed_once(self, _index: int) -> None:
        self.changed.emit(self.current_sound_id())

    def current_sound_id(self) -> str:
        return str(self.combo.currentData() or "system")

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入自定义闹钟音频",
            "",
            "音频文件 (*.mp3 *.wav *.m4a *.aac *.flac *.ogg);;所有文件 (*)",
        )
        if not path:
            return
        try:
            sound = self.library.import_file(path)
        except (OSError, ValueError) as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "无法导入自定义音频", str(exc))
            return
        self.refresh(sound.sound_id)
        self.changed.emit(sound.sound_id)

    def _delete(self) -> None:
        sound_id = self.current_sound_id()
        if sound_id in {"system", "default"}:
            return
        sound = self.library.get(sound_id)
        if sound is None:
            return
        from PySide6.QtWidgets import QMessageBox
        answer = QMessageBox.question(
            self,
            "删除自定义音频",
            f"删除“{sound.display_name}”？引用它的闹钟会自动回退到系统提示音。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.stop_preview()
        self.library.remove(sound_id)
        self.refresh("system")
        self.changed.emit("system")

    def preview(self) -> None:
        self.stop_preview()
        sound_id = self.current_sound_id()
        path = self.library.resolve_path(sound_id)
        if path is None:
            QApplication.beep()
            return
        if self._preview_player is None:
            QApplication.beep()
            return
        if self._preview_output is not None:
            self._preview_output.setVolume(0.7)
        self._preview_player.setSource(QUrl.fromLocalFile(str(path)))
        self._preview_player.play()
        QTimer.singleShot(15_000, self.stop_preview)

    def stop_preview(self) -> None:
        if self._preview_player is not None:
            self._preview_player.stop()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.stop_preview()
        super().closeEvent(event)


class AlarmCard(QDialog):
    """A frameless, movable, non-modal top-level alarm card.

    The card keeps the QuickAction visual language instead of showing a
    second native title bar.  It is still a normal top-level window: it does
    not stay on top, does not become modal, and never re-activates itself.
    """

    start_requested = Signal(str)
    snooze_requested = Signal(str, int)
    dismiss_requested = Signal(str)

    def __init__(
        self,
        alarm: Alarm,
        parent=None,
        *,
        sound_library: AlarmSoundLibrary | None = None,
    ) -> None:
        super().__init__(None)
        self.alarm = alarm
        self.sound_library = sound_library
        self._suppress_close_action = False
        self._drag_offset = None
        self.setObjectName("alarmCard")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
        )
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setStyleSheet(ALARM_STYLE)
        self.setMinimumWidth(420)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor("#9bb5bf"))
        self.setGraphicsEffect(shadow)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(7)
        title = QLabel(str(alarm.title))
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title)
        trigger = QLabel(self._display_trigger(alarm.trigger_at))
        trigger.setStyleSheet("font-size: 32px; font-weight: 800; color:#24475b;")
        layout.insertWidget(0, trigger)
        custom_id = str(alarm.sound_id or "") not in {"", "system", "default"}
        custom_available = bool(
            custom_id
            and self.sound_library is not None
            and self.sound_library.resolve_path(alarm.sound_id) is not None
            and QMediaPlayer is not None
        )
        if not alarm.sound_enabled:
            sound_text = "静音闹钟 · 只显示六毛提醒"
        elif custom_available:
            sound_text = f"🎵 {self._sound_name(alarm.sound_id)} · 单曲循环"
        elif custom_id:
            sound_text = "自定义音频不可用 · 已回退系统提示音 · 最长60秒"
        else:
            sound_text = f"🔔 {self._sound_name(alarm.sound_id)} · 最长60秒"
        sound_hint = QLabel(sound_text)
        sound_hint.setStyleSheet("color:#607985;font-size:11px;")
        layout.addWidget(sound_hint)
        actions = QVBoxLayout()
        actions.setSpacing(6)
        start = QPushButton("开始工作" if alarm.linked_todo_id else "开始30分钟")
        start.setObjectName("primary")
        start.clicked.connect(self._request_start)
        actions.addWidget(start)
        snoozes = QHBoxLayout()
        snoozes.setSpacing(5)
        for minutes in (5, 10, 30):
            button = QPushButton(f"后{minutes}分")
            button.clicked.connect(
                lambda _checked=False, value=minutes: self._request_snooze(value)
            )
            snoozes.addWidget(button)
        actions.addLayout(snoozes)
        close = QPushButton("关闭")
        close.setObjectName("quiet")
        close.clicked.connect(self._request_dismiss)
        actions.addWidget(close, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(actions)
        self._audio_output = QAudioOutput(self) if QAudioOutput is not None else None
        self._media_player = QMediaPlayer(self) if QMediaPlayer is not None else None
        self._using_system_sound = True
        if self._media_player is not None and self._audio_output is not None:
            self._media_player.setAudioOutput(self._audio_output)
            self._audio_output.setVolume(max(0, min(100, int(alarm.volume or 0))) / 100)
            self._media_player.mediaStatusChanged.connect(self._media_status_changed)
            self._media_player.errorOccurred.connect(lambda *_args: self._fallback_to_system())
        self._sound_timer = None
        self._sound_stop_timer = None
        self._custom_audio = False
        self._configure_sound(alarm)

        # Frameless cards still support the two platform close shortcuts.
        # Alt+F4 is delivered as a normal closeEvent by the window manager.
        self._close_shortcuts = []
        for sequence in ("Ctrl+W", "Meta+W"):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(self._request_dismiss)
            self._close_shortcuts.append(shortcut)

        # Blank card areas and labels are drag handles; controls remain normal
        # clickable widgets.  This is deliberately local to the card and does
        # not use a global mouse grab.
        self._install_drag_filters()

    def _install_drag_filters(self) -> None:
        self.installEventFilter(self)
        for child in self.findChildren(QWidget):
            child.installEventFilter(self)

    @staticmethod
    def _is_interactive_widget(widget: QWidget | None) -> bool:
        interactive = (QPushButton, QLineEdit, QComboBox, QSpinBox)
        current = widget
        while current is not None:
            if isinstance(current, interactive):
                return True
            current = current.parentWidget()
        return False

    def eventFilter(self, watched: object, event: object) -> bool:
        event_type = getattr(event, "type", lambda: None)()
        if event_type == QEvent.Type.MouseButtonPress and getattr(event, "button", lambda: None)() == Qt.MouseButton.LeftButton:
            widget = watched if isinstance(watched, QWidget) else self
            if not self._is_interactive_widget(widget):
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
        elif event_type == QEvent.Type.MouseMove and self._drag_offset is not None:
            if getattr(event, "buttons", lambda: Qt.MouseButton.NoButton)() & Qt.MouseButton.LeftButton:
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                return True
        elif event_type == QEvent.Type.MouseButtonRelease and self._drag_offset is not None:
            self._drag_offset = None
            return True
        return super().eventFilter(watched, event)

    def _request_start(self) -> None:
        self._stop_sound()
        self._suppress_close_action = True
        self.start_requested.emit(self.alarm.id)

    def _request_snooze(self, minutes: int) -> None:
        self._stop_sound()
        self._suppress_close_action = True
        self.snooze_requested.emit(self.alarm.id, int(minutes))

    def _request_dismiss(self) -> None:
        self._stop_sound()
        self._suppress_close_action = True
        self.dismiss_requested.emit(self.alarm.id)

    @staticmethod
    def _display_trigger(value: str) -> str:
        return str(value or "")[:16].replace("T", " ").split(" ", 1)[1] if " " in str(value or "") else "⏰"

    def _sound_name(self, sound_id: str) -> str:
        return self.sound_library.display_name(sound_id) if self.sound_library else (
            "系统提示音" if sound_id in {"", "system", "default"} else "自定义音频"
        )

    def _configure_sound(self, alarm: Alarm) -> None:
        if not alarm.sound_enabled:
            return
        path = self.sound_library.resolve_path(alarm.sound_id) if self.sound_library else None
        is_custom = str(alarm.sound_id or "") not in {"", "system", "default"}
        if is_custom and path is not None and self._media_player is not None:
            self._custom_audio = True
            self._using_system_sound = False
            self._media_player.setSource(QUrl.fromLocalFile(str(path)))
            self._media_player.play()
            return
        # System/default sounds are short platform alerts.  Missing custom
        # files intentionally fall back to this same bounded behavior.
        self._using_system_sound = True
        self._sound_timer = QTimer(self)
        self._sound_timer.setInterval(3_000)
        self._sound_timer.timeout.connect(self._play_system_sound)
        self._sound_stop_timer = QTimer(self)
        self._sound_stop_timer.setSingleShot(True)
        self._sound_stop_timer.setInterval(max(1, int(alarm.max_ring_seconds or 60)) * 1_000)
        self._sound_stop_timer.timeout.connect(self._stop_sound)
        self._sound_stop_timer.start()
        self._sound_timer.start()
        self._play_system_sound()

    def _start_sound(self) -> None:
        """Compatibility entry point for callers from older builds."""
        path = self.sound_library.resolve_path(self.alarm.sound_id) if self.sound_library else None
        if path is not None and self._media_player is not None:
            self._using_system_sound = False
            self._media_player.setSource(QUrl.fromLocalFile(str(path)))
            self._media_player.play()
            return
        self._fallback_to_system()

    def _media_status_changed(self, status) -> None:
        if self._custom_audio and not self._using_system_sound and self._media_player is not None:
            if status == QMediaPlayer.MediaStatus.EndOfMedia:
                self._media_player.play()

    def _fallback_to_system(self) -> None:
        if self._custom_audio:
            self._custom_audio = False
        self._using_system_sound = True
        if self._media_player is not None:
            self._media_player.stop()
        if self._sound_timer is None:
            self._sound_timer = QTimer(self)
            self._sound_timer.setInterval(3_000)
            self._sound_timer.timeout.connect(self._play_system_sound)
        if self._sound_stop_timer is None:
            self._sound_stop_timer = QTimer(self)
            self._sound_stop_timer.setSingleShot(True)
            self._sound_stop_timer.setInterval(60_000)
            self._sound_stop_timer.timeout.connect(self._stop_sound)
        self._sound_stop_timer.start()
        self._sound_timer.start()
        self._play_system_sound()

    @staticmethod
    def _play_system_sound() -> None:
        QApplication.beep()

    def center_on_current_screen(self) -> None:
        """Center once on the screen the user is currently using.

        This is called only before the first ``show()``.  After that, native
        window movement and the user's own drag position are left untouched.
        """

        self.adjustSize()
        screen = QApplication.screenAt(QCursor.pos())
        if screen is None:
            active_window = QApplication.activeWindow()
            if active_window is not None:
                screen = QApplication.screenAt(active_window.frameGeometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = area.left() + max(0, (area.width() - self.width()) // 2)
        y = area.top() + max(0, (area.height() - self.height()) // 2)
        self.move(x, y)

    def close_from_app(self) -> None:
        """Close without treating application cleanup as user dismissal."""

        self._suppress_close_action = True
        self.close()

    def _stop_sound(self) -> None:
        if self._sound_timer is not None:
            self._sound_timer.stop()
        if self._sound_stop_timer is not None:
            self._sound_stop_timer.stop()
        if self._media_player is not None:
            self._media_player.stop()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        # Closing by Alt+F4/Cmd+W or by the window manager must stop both
        # system beeps and custom looping audio immediately.
        self._stop_sound()
        if not self._suppress_close_action:
            # Closing the frameless card means “dismiss this firing”, not
            # “quit Lili”. Repeating alarms remain scheduled for next time.
            self._suppress_close_action = True
            self.dismiss_requested.emit(self.alarm.id)
        super().closeEvent(event)


class AlarmEditDialog(QDialog):
    def __init__(
        self,
        todos: list[Any],
        alarm: Alarm | None = None,
        parent=None,
        *,
        sound_library: AlarmSoundLibrary | None = None,
    ) -> None:
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
        self.enabled = QCheckBox("启用此闹钟", self)
        self.enabled.setChecked(bool(alarm.enabled) if alarm else True)
        self.sound = QCheckBox("到点播放提示音", self)
        self.sound.setChecked(bool(alarm.sound_enabled) if alarm else True)
        self.sound_selector = AlarmSoundSelector(
            sound_library,
            str(alarm.sound_id if alarm else "system"),
            self,
        )
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
        form.addRow("", self.enabled)
        form.addRow("稍后默认", self.snooze)
        form.addRow("关联待办", self.linked_todo)
        form.addRow("", self.sound)
        form.addRow("铃声", self.sound_selector)
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
            "enabled": self.enabled.isChecked(),
            "sound_enabled": self.sound.isChecked(),
            "sound_id": self.sound_selector.current_sound_id(),
            "volume": self.volume.value(),
            "max_ring_seconds": 60,
            "allow_during_dnd": self.allow_dnd.isChecked(),
            "snooze_minutes": self.snooze.value(),
            "linked_todo_id": self.linked_todo.currentData() or None,
        }


class AlarmCenterDialog(QDialog):
    """A small local alarm manager; it is intentionally not modal."""

    changed = Signal()

    def __init__(
        self,
        alarms: AlarmManager,
        todos: list[Any],
        parent=None,
        *,
        sound_library: AlarmSoundLibrary | None = None,
    ) -> None:
        super().__init__(parent)
        self.alarms = alarms
        self.todos = todos
        self.sound_library = sound_library
        self.setObjectName("alarmCenter")
        self.setWindowTitle("六毛闹钟")
        self.setMinimumSize(520, 390)
        self.setStyleSheet(ALARM_STYLE)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "到点后六毛会来找你；可以开始工作、稍后提醒或关闭。"
                "闹钟不会抢其它软件焦点，也可以在列表中直接开启或关闭。"
            )
        )
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
            status = "响铃中" if alarm.enabled and alarm.active else ("已启用" if alarm.enabled else "已关闭")
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, alarm.id)
            self.list.addItem(item)

            row = QWidget(self.list)
            row.setObjectName("alarmRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 5, 8, 5)
            row_layout.setSpacing(10)

            toggle = QCheckBox("开启" if alarm.enabled else "关闭", row)
            toggle.setToolTip("开启或关闭这个闹钟；关闭只停用调度，不会删除闹钟")
            toggle.setChecked(alarm.enabled)
            toggle.toggled.connect(
                lambda enabled, alarm_id=alarm.id: self._toggle_enabled(alarm_id, enabled)
            )

            summary = QLabel(
                f"{alarm.title} · {alarm.trigger_at[:16].replace('T', ' ')} · {repeat}",
                row,
            )
            summary.setWordWrap(True)
            summary.setToolTip("双击这一行或点击下方“编辑”修改闹钟")

            status_label = QLabel(status, row)
            status_label.setObjectName("alarmStatus")
            row_layout.addWidget(toggle)
            row_layout.addWidget(summary, 1)
            row_layout.addWidget(status_label)

            item.setSizeHint(row.sizeHint())
            self.list.setItemWidget(item, row)

    def _toggle_enabled(self, alarm_id: str, enabled: bool) -> None:
        """Toggle the persisted alarm state without creating a second source of truth."""
        alarm = self.alarms.get(alarm_id)
        if alarm is None or alarm.enabled == enabled:
            return
        self.alarms.set_enabled(alarm_id, enabled)
        self.refresh()
        self.changed.emit()

    def _selected(self) -> Alarm | None:
        item = self.list.currentItem()
        return self.alarms.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _add(self) -> None:
        dialog = AlarmEditDialog(list(self.todos), parent=self, sound_library=self.sound_library)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.alarms.add(**dialog.values())
        self.refresh()
        self.changed.emit()

    def _edit_selected(self, *_args) -> None:
        alarm = self._selected()
        if alarm is None:
            return
        dialog = AlarmEditDialog(
            list(self.todos), alarm=alarm, parent=self, sound_library=self.sound_library
        )
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
