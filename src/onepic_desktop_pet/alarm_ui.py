"""Non-modal UI for managing alarms and showing alarm-style recovery cards.

闹钟卡片在 Windows 上只通过原生窗口层级 API 调整临时置顶，不在显示后
反复切换 Qt window flag，避免触发 Qt 原生窗口重建。
"""

from __future__ import annotations

import ctypes
import sys
import threading
import weakref
from ctypes import wintypes
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QDateTime, QEvent, QTime, QUrl, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
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
from .lifecycle_log import lifecycle_log

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


class AlarmPopupState(str, Enum):
    """Lifecycle of a firing alarm card's window-level behavior."""

    UNSEEN = "unseen"
    ACKNOWLEDGED = "acknowledged"
    DISMISSED = "dismissed"


class AlarmSoundSelector(QWidget):
    """Shared sound picker used by standalone alarms and Todo alarms."""

    changed = Signal(str)
    # ``_WindowsAlarmAudio`` invokes callbacks from daemon workers.  Forward
    # the failure through a queued Qt signal before touching any multimedia
    # object, otherwise a native worker callback could race the GUI thread.
    _preview_mci_error = Signal(int, str, str, str)

    def __init__(
        self,
        library: AlarmSoundLibrary | None,
        selected_id: str = "system",
        parent=None,
    ) -> None:
        super().__init__(parent)
        lifecycle_log("alarm.sound_selector.create", self)
        self.destroyed.connect(
            lambda _obj=None: lifecycle_log(
                "alarm.sound_selector.destroy", class_name="AlarmSoundSelector"
            )
        )
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
        # Qt 6.11's Windows multimedia backend can block the GUI thread in
        # QMediaPlayer.stop().  AlarmCard already uses the asynchronous MCI
        # backend below; the selector uses the same safe route first.  Some
        # Windows installations cannot decode MP3 through MCI (error 277),
        # so keep a Qt player as a *failure-only* fallback.  It is never
        # stopped synchronously; the timeout merely mutes it.
        use_qt_preview = sys.platform != "win32"
        self._preview_output = QAudioOutput(self) if use_qt_preview and QAudioOutput is not None else None
        self._preview_player = QMediaPlayer(self) if use_qt_preview and QMediaPlayer is not None else None
        use_windows_qt_fallback = (
            sys.platform == "win32" and QAudioOutput is not None and QMediaPlayer is not None
        )
        self._preview_fallback_output = (
            QAudioOutput(self) if use_windows_qt_fallback else None
        )
        self._preview_fallback_player = (
            QMediaPlayer(self) if use_windows_qt_fallback else None
        )
        self._preview_fallback_active = False
        self._preview_generation = 0
        self._windows_preview_audio: _WindowsAlarmAudio | None = None
        self._preview_stop_timer = QTimer(self)
        self._preview_stop_timer.setSingleShot(True)
        self._preview_stop_timer.timeout.connect(self.stop_preview)
        if self._preview_player is not None and self._preview_output is not None:
            self._preview_player.setAudioOutput(self._preview_output)
            self._preview_player.playbackStateChanged.connect(
                lambda state: lifecycle_log(
                    "media.preview.state_changed",
                    self._preview_player,
                    owner="AlarmSoundSelector",
                    signal="playbackStateChanged",
                    state=str(state),
                )
            )
            self._preview_player.errorOccurred.connect(
                lambda *args: self._qt_preview_error(self._preview_player, *args)
            )
        if self._preview_fallback_player is not None and self._preview_fallback_output is not None:
            self._preview_fallback_player.setAudioOutput(self._preview_fallback_output)
            self._preview_fallback_player.playbackStateChanged.connect(
                lambda state: lifecycle_log(
                    "media.preview.qt_fallback.state_changed",
                    self._preview_fallback_player,
                    owner="AlarmSoundSelector",
                    signal="playbackStateChanged",
                    state=str(state),
                )
            )
            self._preview_fallback_player.errorOccurred.connect(
                lambda *args: self._qt_preview_error(self._preview_fallback_player, *args)
            )
        self._preview_mci_error.connect(
            self._on_preview_mci_error,
            Qt.ConnectionType.QueuedConnection,
        )
        self.refresh(selected_id)

    def _qt_preview_error(self, player, *args: object) -> None:
        lifecycle_log(
            "media.preview.error",
            player,
            owner="AlarmSoundSelector",
            error=" ".join(str(item) for item in args),
        )
        if player is self._preview_fallback_player and sys.platform == "win32":
            # Do not call QMediaPlayer.stop() on the GUI thread.  Muting is
            # immediate and leaves native teardown to Qt's normal lifecycle.
            self._mute_qt_fallback_preview()
        else:
            self.stop_preview()

    def _mute_qt_fallback_preview(self) -> None:
        self._preview_fallback_active = False
        if self._preview_fallback_output is not None:
            self._preview_fallback_output.setVolume(0.0)

    def _play_qt_preview(self, path: str, sound_id: str, *, fallback: bool) -> bool:
        player = self._preview_fallback_player if fallback else self._preview_player
        output = self._preview_fallback_output if fallback else self._preview_output
        if player is None:
            return False
        if output is not None:
            output.setVolume(0.7)
        player.setSource(QUrl.fromLocalFile(str(path)))
        lifecycle_log(
            "media.preview.qt_fallback.play" if fallback else "media.preview.play",
            player,
            owner="AlarmSoundSelector",
            sound_id=sound_id,
        )
        player.play()
        if fallback:
            self._preview_fallback_active = True
        self._preview_stop_timer.start(15_000)
        return True

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
        if sys.platform == "win32":
            generation = self._preview_generation

            def _on_mci_error(error: object) -> None:
                lifecycle_log(
                    "media.preview.mci.error",
                    class_name="WindowsAlarmAudio",
                    sound_id=sound_id,
                    error=str(error),
                )
                self._preview_mci_error.emit(
                    generation,
                    sound_id,
                    str(path),
                    str(error),
                )

            backend = _WindowsAlarmAudio(
                str(path),
                volume=70,
                on_finished=lambda: lifecycle_log(
                    "media.preview.mci.stop_complete",
                    class_name="WindowsAlarmAudio",
                    sound_id=sound_id,
                ),
                on_error=_on_mci_error,
            )
            if not backend.available:
                if not self._play_qt_preview(str(path), sound_id, fallback=True):
                    QApplication.beep()
                return
            self._windows_preview_audio = backend
            lifecycle_log(
                "media.preview.mci.play",
                class_name="WindowsAlarmAudio",
                sound_id=sound_id,
            )
            backend.start()
            self._preview_stop_timer.start(15_000)
            return
        if not self._play_qt_preview(str(path), sound_id, fallback=False):
            QApplication.beep()

    def _on_preview_mci_error(
        self,
        generation: int,
        sound_id: str,
        path: str,
        error: str,
    ) -> None:
        if generation != self._preview_generation:
            return
        self._windows_preview_audio = None
        lifecycle_log(
            "media.preview.mci.fallback",
            self,
            owner="AlarmSoundSelector",
            sound_id=sound_id,
            error=error,
        )
        if not self._play_qt_preview(path, sound_id, fallback=True):
            QApplication.beep()

    def stop_preview(self) -> None:
        self._preview_generation += 1
        self._preview_stop_timer.stop()
        backend = self._windows_preview_audio
        self._windows_preview_audio = None
        if backend is not None:
            lifecycle_log(
                "media.preview.mci.stop_request",
                class_name="WindowsAlarmAudio",
            )
            # request_stop only starts a daemon worker.  It never waits for
            # WinMM, so a button click, dialog close, or automatic timeout
            # cannot block the Qt event loop.
            backend.request_stop()
        if self._preview_fallback_active:
            self._mute_qt_fallback_preview()
        if backend is not None:
            return
        if self._preview_player is not None:
            lifecycle_log(
                "media.preview.stop",
                self._preview_player,
                owner="AlarmSoundSelector",
            )
            self._preview_player.stop()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.stop_preview()
        super().closeEvent(event)


class _WindowsAlarmAudio:
    """Play one alarm file through Windows MCI, outside Qt Multimedia.

    Qt 6.11.2's Windows multimedia backend can block inside
    ``QMediaPlayer.stop()`` while the GUI thread is dispatching a button
    click.  The alarm card therefore uses this small, process-local backend
    for custom sounds on Windows.  MCI commands run on daemon threads and
    the GUI never waits for either the command or the thread.
    """

    _instances: weakref.WeakSet["_WindowsAlarmAudio"] = weakref.WeakSet()
    _instances_lock = threading.Lock()

    def __init__(
        self,
        path: str,
        *,
        volume: int,
        on_finished,
        on_error,
    ) -> None:
        self.path = str(path)
        self.volume = max(0, min(100, int(volume or 0)))
        self.alias = f"lili_alarm_{uuid4().hex[:12]}"
        self._on_finished = on_finished
        self._on_error = on_error
        self._lock = threading.Lock()
        self._opened = False
        self._start_requested = False
        self._stop_requested = False
        self._mci = None
        try:
            mci = ctypes.windll.winmm.mciSendStringW
            mci.argtypes = [
                wintypes.LPCWSTR,
                wintypes.LPWSTR,
                wintypes.UINT,
                wintypes.HWND,
            ]
            mci.restype = wintypes.DWORD
            self._mci = mci
        except (AttributeError, OSError):
            self._mci = None
        with self._instances_lock:
            self._instances.add(self)

    @property
    def available(self) -> bool:
        return self._mci is not None

    def start(self) -> None:
        if self._start_requested or not self.available:
            if not self.available:
                self._notify_error("winmm 不可用")
            return
        self._start_requested = True
        threading.Thread(
            target=self._start_worker,
            name="LiliAlarmAudioStart",
            daemon=True,
        ).start()

    def request_stop(self) -> None:
        """Request stop without joining the worker or waiting in the GUI."""

        with self._lock:
            if self._stop_requested:
                return
            self._stop_requested = True
        threading.Thread(
            target=self._stop_worker,
            name="LiliAlarmAudioStop",
            daemon=True,
        ).start()

    @classmethod
    def request_stop_all(cls) -> None:
        """Stop every alarm backend owned by this process asynchronously."""

        with cls._instances_lock:
            instances = tuple(cls._instances)
        for instance in instances:
            instance.request_stop()

    def _send(self, command: str) -> int:
        mci = self._mci
        if mci is None:
            return 1
        try:
            return int(mci(command, None, 0, 0))
        except (OSError, TypeError, ValueError):
            return 1

    def _start_worker(self) -> None:
        error = ""
        with self._lock:
            if self._stop_requested:
                self._notify_finished()
                return
            extension = str(self.path).lower().rsplit(".", 1)[-1]
            device_type = "waveaudio" if extension == "wav" else "mpegvideo"
            safe_path = self.path.replace('"', '""')
            result = self._send(
                f'open "{safe_path}" type {device_type} alias {self.alias}'
            )
            if result:
                error = f"mci open failed: {result}"
            else:
                self._opened = True
                volume = self.volume * 10
                self._send(f"setaudio {self.alias} volume to {volume}")
                result = self._send(f"play {self.alias} repeat")
                if result:
                    error = f"mci play failed: {result}"
                    self._send(f"close {self.alias}")
                    self._opened = False
        if error:
            lifecycle_log(
                "media.alarm.mci.error",
                class_name="WindowsAlarmAudio",
                alarm_path_extension=str(self.path).lower().rsplit(".", 1)[-1],
                error=error,
            )
            self._notify_error(error)

    def _stop_worker(self) -> None:
        with self._lock:
            # ``_opened`` is only a Python-side hint and can be stale if the
            # start/stop workers race. Always issue both native commands;
            # ``wait`` is confined to this daemon thread, never the GUI.
            stop_result = self._send(f"stop {self.alias} wait")
            stop_retry_result = 0
            if stop_result:
                # Some MCI device drivers reject the optional wait flag for
                # ``stop``. Retry the plain command before closing the alias.
                stop_retry_result = self._send(f"stop {self.alias}")
            close_result = self._send(f"close {self.alias} wait")
            close_retry_result = 0
            if close_result:
                close_retry_result = self._send(f"close {self.alias}")
            self._opened = False
        lifecycle_log(
            "media.alarm.mci.stop_commands",
            class_name="WindowsAlarmAudio",
            alarm_alias=self.alias,
            stop_result=stop_result,
            stop_retry_result=stop_retry_result,
            close_result=close_result,
            close_retry_result=close_retry_result,
        )
        self._notify_finished()

    def _notify_finished(self) -> None:
        try:
            self._on_finished()
        except Exception:
            return

    def _notify_error(self, error: str) -> None:
        try:
            self._on_error(str(error))
        except Exception:
            return


class AlarmCard(QDialog):
    """A frameless, movable, non-modal top-level alarm card.

    The card keeps the QuickAction visual language instead of showing a
    second native title bar. A newly fired card is brought to the foreground
    once with a temporary topmost flag so a reminder cannot ring behind Word
    or a browser. The first real interaction acknowledges it and immediately
    restores normal window ordering; snoozing creates a new ``UNSEEN`` card.
    """

    start_requested = Signal(str)
    snooze_requested = Signal(str, int)
    dismiss_requested = Signal(str)
    _windows_audio_finished = Signal()
    _windows_audio_error = Signal(str)
    # Emitted only after a closing card's media backend has received its
    # stop request.  PetWindow uses this as the safe point for deleteLater().
    audio_cleanup_finished = Signal()

    def __init__(
        self,
        alarm: Alarm,
        parent=None,
        *,
        sound_library: AlarmSoundLibrary | None = None,
    ) -> None:
        super().__init__(None)
        lifecycle_log(
            "alarm.popup.create",
            self,
            alarm_id=str(alarm.id),
            title=str(alarm.title),
        )
        alarm_id_for_destroy = str(alarm.id)
        self.destroyed.connect(
            lambda _obj=None, value=alarm_id_for_destroy: lifecycle_log(
                "alarm.popup.destroy",
                class_name="AlarmCard",
                alarm_id=value,
            )
        )
        self.alarm = alarm
        self._schedule_generation = int(getattr(alarm, "schedule_generation", 1) or 1)
        self.sound_library = sound_library
        self._state = AlarmPopupState.UNSEEN
        self._audio_started = False
        self._audio_closing = False
        self._action_requested = False
        self._suppress_close_action = False
        self._close_requested = False
        self._media_stop_pending = False
        self._media_stop_completed = False
        self._media_drained = False
        self._audio_cleanup_ready = False
        self._windows_audio = None
        self._qt_fallback_output = None
        self._qt_fallback_player = None
        self._qt_fallback_active = False
        self._custom_audio_path = None
        self._drag_offset = None
        self._pending_topmost = None
        self.setObjectName("alarmCard")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
        )
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setStyleSheet(ALARM_STYLE)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(7)
        title = QLabel(str(alarm.title))
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title)
        # ``snooze_until`` is the effective next occurrence.  Showing it is
        # important for a recovered alarm: the card must not still display
        # the old time after the scheduler has moved it 30 minutes forward.
        trigger = QLabel(
            self._display_trigger(
                alarm.snooze_until or alarm.last_triggered_slot or alarm.trigger_at
            )
        )
        trigger.setStyleSheet("font-size: 32px; font-weight: 800; color:#24475b;")
        layout.insertWidget(0, trigger)
        custom_id = str(alarm.sound_id or "") not in {"", "system", "default"}
        custom_available = bool(
            custom_id
            and self.sound_library is not None
            and self.sound_library.resolve_path(alarm.sound_id) is not None
            and (QMediaPlayer is not None or sys.platform == "win32")
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
        use_qt_media = sys.platform != "win32"
        self._audio_output = QAudioOutput(self) if use_qt_media and QAudioOutput is not None else None
        self._media_player = QMediaPlayer(self) if use_qt_media and QMediaPlayer is not None else None
        self._using_system_sound = True
        if self._media_player is not None and self._audio_output is not None:
            self._media_player.setAudioOutput(self._audio_output)
            self._audio_output.setVolume(max(0, min(100, int(alarm.volume or 0))) / 100)
            self._media_player.mediaStatusChanged.connect(self._media_status_changed)
            self._media_player.playbackStateChanged.connect(
                lambda state: lifecycle_log(
                    "media.alarm.state_changed",
                    self._media_player,
                    owner="AlarmCard",
                    alarm_id=str(self.alarm.id),
                    signal="playbackStateChanged",
                    state=str(state),
                )
            )
            self._media_player.errorOccurred.connect(self._media_error)
        self._sound_timer = None
        self._sound_stop_timer = None
        self._audio_start_timer = QTimer(self)
        self._audio_start_timer.setSingleShot(True)
        # Kept as a cancellation fence for callers from older builds.  New
        # cards start audio synchronously after ``show()``; a 120 ms delay
        # makes a due-at-:00 alarm audibly late and is unnecessary because
        # both native MCI and QMediaPlayer start asynchronously themselves.
        self._audio_start_timer.setInterval(0)
        self._audio_start_timer.timeout.connect(self._start_alarm_audio)
        self._media_stop_timer = QTimer(self)
        self._media_stop_timer.setSingleShot(True)
        self._media_stop_timer.timeout.connect(self._finish_media_stop)
        self._topmost_timer = QTimer(self)
        self._topmost_timer.setSingleShot(True)
        self._topmost_timer.timeout.connect(self._apply_pending_topmost)
        self._custom_audio = False
        self._windows_audio_finished.connect(
            self._on_windows_audio_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._windows_audio_error.connect(
            self._on_windows_audio_error,
            Qt.ConnectionType.QueuedConnection,
        )
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
            self._acknowledge_alarm()
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
        lifecycle_log("alarm.action_button.enter", self, action="start")
        self._acknowledge_alarm()
        if not self._prepare_action("start"):
            return
        self.start_requested.emit(self.alarm.id)
        lifecycle_log("alarm.action_button.return", self, action="start")

    def _request_snooze(self, minutes: int) -> None:
        lifecycle_log(
            "alarm.action_button.enter",
            self,
            action="snooze",
            minutes=int(minutes),
        )
        self._acknowledge_alarm()
        if not self._prepare_action("snooze"):
            return
        self.snooze_requested.emit(self.alarm.id, int(minutes))
        lifecycle_log(
            "alarm.action_button.return",
            self,
            action="snooze",
            minutes=int(minutes),
        )

    def _request_dismiss(self) -> None:
        lifecycle_log("alarm.action_button.enter", self, action="dismiss")
        self._acknowledge_alarm()
        if not self._prepare_action("dismiss"):
            return
        self.dismiss_requested.emit(self.alarm.id)
        lifecycle_log("alarm.action_button.return", self, action="dismiss")

    @property
    def popup_state(self) -> AlarmPopupState:
        return self._state

    @property
    def schedule_generation(self) -> int:
        """Generation captured when this foreground card was created."""

        return self._schedule_generation

    def show_alarm_foreground(self) -> None:
        """Show once in front of the active app, then start the sound."""

        lifecycle_log("alarm.popup.show.request", self, alarm_id=str(self.alarm.id))
        self._state = AlarmPopupState.UNSEEN
        self.show()
        # Headless Qt platforms do not have a native foreground window.  In
        # particular, macOS's offscreen backend can block in raise_/activate
        # while handling a top-level show.  The real desktop path still uses
        # both calls; the headless path only needs the widget to be shown so
        # that lifecycle and audio scheduling can be tested deterministically.
        if QApplication.platformName().casefold() not in {"offscreen", "minimal"}:
            self.raise_()
            self.activateWindow()
        self._queue_temporary_topmost(True)
        # Start immediately after the first foreground presentation.  The
        # media backends do their native work asynchronously, so this does
        # not block the Qt event loop or require an artificial delay.
        self._start_alarm_audio()
        lifecycle_log("alarm.popup.show.complete", self, alarm_id=str(self.alarm.id))

    def _acknowledge_alarm(self) -> None:
        """Release temporary topmost as soon as the user touches the card."""

        if self._state != AlarmPopupState.UNSEEN:
            return
        self._state = AlarmPopupState.ACKNOWLEDGED
        # Do not call a native z-order API from inside a mouse event.  Queue it
        # until Qt finishes dispatching the click, just like the action signal
        # below, to avoid re-entering the window manager from QPushButton.
        self._queue_temporary_topmost(False)

    def _queue_temporary_topmost(self, enabled: bool) -> None:
        self._pending_topmost = bool(enabled)
        self._topmost_timer.start(0)

    def _apply_pending_topmost(self) -> None:
        enabled = self._pending_topmost
        self._pending_topmost = None
        if enabled is None or not self.isVisible():
            return
        self._set_temporary_topmost(enabled)

    def _set_temporary_topmost(self, enabled: bool) -> None:
        """Adjust z-order without rebuilding a visible Qt native window.

        ``QWidget.setWindowFlag`` on an already-visible top-level window can
        destroy and recreate its native handle.  That is especially risky
        while a mouse event is being delivered and was the most suspicious
        path behind the Qt6Core native crash.  Windows' z-order API changes
        only the existing HWND, preserving the Qt object and its handle.
        """

        if sys.platform == "win32":
            try:
                user32 = ctypes.windll.user32
                user32.SetWindowPos.argtypes = [
                    wintypes.HWND,
                    wintypes.HWND,
                    wintypes.INT,
                    wintypes.INT,
                    wintypes.INT,
                    wintypes.INT,
                    wintypes.UINT,
                ]
                user32.SetWindowPos.restype = wintypes.BOOL
                user32.SetWindowPos(
                    wintypes.HWND(int(self.winId())),
                    wintypes.HWND(-1 if enabled else -2),
                    0,
                    0,
                    0,
                    0,
                    0x0001 | 0x0002 | 0x0010,  # NOMOVE | NOSIZE | NOACTIVATE
                )
            except (AttributeError, OSError, TypeError, ValueError):
                # The regular raise/activate path above still presents the
                # card if the platform API is unavailable or the handle is
                # not ready yet.  Do not fall back to setWindowFlag here.
                pass
            return

        # On other platforms, show()/raise_()/activateWindow() already give
        # the card a foreground presentation.  Do not mutate Qt window flags
        # after showing it; this keeps the lifecycle safe across backends.

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
        if is_custom and path is not None and (
            self._media_player is not None or sys.platform == "win32"
        ):
            self._custom_audio = True
            self._using_system_sound = False
            self._custom_audio_path = str(path)
            if self._media_player is not None:
                self._media_player.setSource(QUrl.fromLocalFile(str(path)))
            else:
                self._windows_audio = _WindowsAlarmAudio(
                    str(path),
                    volume=int(alarm.volume or 0),
                    on_finished=self._windows_audio_finished.emit,
                    on_error=self._windows_audio_error.emit,
                )
                if not self._windows_audio.available:
                    self._windows_audio = None
                    if not self._configure_qt_fallback(str(path)):
                        self._custom_audio = False
                        self._using_system_sound = True
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

    def _ensure_qt_fallback(self) -> bool:
        """Create the Windows Qt player used only after MCI cannot decode."""

        if self._qt_fallback_player is not None and self._qt_fallback_output is not None:
            return True
        if QAudioOutput is None or QMediaPlayer is None:
            return False
        try:
            output = QAudioOutput(self)
            player = QMediaPlayer(self)
            player.setAudioOutput(output)
            player.mediaStatusChanged.connect(self._qt_fallback_status_changed)
            player.errorOccurred.connect(self._qt_fallback_error)
        except Exception as exc:  # pragma: no cover - depends on native Qt backend
            lifecycle_log(
                "media.alarm.qt_fallback.unavailable",
                class_name="AlarmCard",
                alarm_id=str(self.alarm.id),
                error=str(exc),
            )
            return False
        self._qt_fallback_output = output
        self._qt_fallback_player = player
        return True

    def _configure_qt_fallback(self, path: str) -> bool:
        if not self._ensure_qt_fallback():
            return False
        try:
            self._qt_fallback_output.setVolume(
                max(0, min(100, int(self.alarm.volume or 0))) / 100
            )
            self._qt_fallback_player.setSource(QUrl.fromLocalFile(str(path)))
        except Exception as exc:  # pragma: no cover - depends on native Qt backend
            lifecycle_log(
                "media.alarm.qt_fallback.configure_error",
                class_name="AlarmCard",
                alarm_id=str(self.alarm.id),
                error=str(exc),
            )
            return False
        return True

    def _play_qt_fallback(self, path: str | None = None) -> bool:
        fallback_path = str(path or self._custom_audio_path or "")
        if not fallback_path or not self._configure_qt_fallback(fallback_path):
            return False
        try:
            self._qt_fallback_output.setVolume(
                max(0, min(100, int(self.alarm.volume or 0))) / 100
            )
            self._qt_fallback_active = True
            lifecycle_log(
                "media.alarm.qt_fallback.play",
                self._qt_fallback_player,
                owner="AlarmCard",
                alarm_id=str(self.alarm.id),
                reason="mci_decode_failure",
            )
            self._qt_fallback_player.play()
        except Exception as exc:  # pragma: no cover - depends on native Qt backend
            self._qt_fallback_active = False
            lifecycle_log(
                "media.alarm.qt_fallback.play_error",
                class_name="AlarmCard",
                alarm_id=str(self.alarm.id),
                error=str(exc),
            )
            return False
        return True

    def _qt_fallback_status_changed(self, status) -> None:
        lifecycle_log(
            "media.alarm.qt_fallback.status_changed",
            self._qt_fallback_player,
            owner="AlarmCard",
            alarm_id=str(self.alarm.id),
            status=str(status),
        )
        if (
            status == QMediaPlayer.MediaStatus.EndOfMedia
            and self._qt_fallback_active
            and not self._audio_closing
            and self._state != AlarmPopupState.DISMISSED
            and self._custom_audio
        ):
            self._qt_fallback_player.play()

    def _qt_fallback_error(self, *args: object) -> None:
        self._qt_fallback_active = False
        lifecycle_log(
            "media.alarm.qt_fallback.error",
            self._qt_fallback_player,
            owner="AlarmCard",
            alarm_id=str(self.alarm.id),
            error=" ".join(str(item) for item in args),
        )
        if self._audio_closing:
            self._media_stop_completed = True
            self._media_drained = True
            self._emit_audio_cleanup_finished()
            return
        self._fallback_to_system()

    def _start_alarm_audio(self) -> None:
        """Start audio only after the first foreground presentation."""

        if self._audio_closing or self._state == AlarmPopupState.DISMISSED or self._audio_started:
            return
        self._audio_started = True
        lifecycle_log(
            "media.alarm.play.request",
            self._media_player,
            owner="AlarmCard",
            alarm_id=str(self.alarm.id),
            custom_audio=self._custom_audio,
        )
        if not self.alarm.sound_enabled:
            return
        if self._custom_audio and self._windows_audio is not None:
            lifecycle_log(
                "media.alarm.backend_start",
                class_name="WindowsAlarmAudio",
                alarm_id=str(self.alarm.id),
                backend="winmm-mci",
            )
            self._windows_audio.start()
            return
        if self._custom_audio and self._media_player is not None:
            self._media_player.play()
            return
        if self._custom_audio and self._play_qt_fallback():
            return
        if self._custom_audio:
            self._fallback_to_system()
            return
        if self._sound_stop_timer is not None:
            self._sound_stop_timer.start()
        if self._sound_timer is not None:
            self._sound_timer.start()
        self._play_system_sound()

    def _start_sound(self) -> None:
        """Compatibility entry point for callers from older builds."""
        self._start_alarm_audio()

    def _media_status_changed(self, status) -> None:
        lifecycle_log(
            "media.alarm.status_changed",
            self._media_player,
            owner="AlarmCard",
            alarm_id=str(self.alarm.id),
            status=str(status),
        )
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self._audio_closing:
            # The close path mutes the output and disables looping.  Once the
            # current item reaches its natural end, the player is stopped by
            # the backend and it is safe for PetWindow to delete the card.
            self._media_stop_completed = True
            self._media_drained = True
            lifecycle_log(
                "media.alarm.drain_complete",
                self._media_player,
                owner="AlarmCard",
                alarm_id=str(self.alarm.id),
            )
            self._emit_audio_cleanup_finished()
            return
        if (
            not self._audio_closing
            and self._state != AlarmPopupState.DISMISSED
            and self._custom_audio
            and not self._using_system_sound
            and self._media_player is not None
        ):
            if status == QMediaPlayer.MediaStatus.EndOfMedia:
                lifecycle_log(
                    "media.alarm.play.loop",
                    self._media_player,
                    owner="AlarmCard",
                    alarm_id=str(self.alarm.id),
                )
                self._media_player.play()

    def _media_error(self, *args: object) -> None:
        lifecycle_log(
            "media.alarm.error",
            self._media_player,
            owner="AlarmCard",
            alarm_id=str(self.alarm.id),
            error=" ".join(str(item) for item in args),
        )
        if self._audio_closing:
            self._media_stop_completed = True
            self._media_drained = True
            self._emit_audio_cleanup_finished()
            return
        self._fallback_to_system()

    def _on_windows_audio_finished(self) -> None:
        """Receive the daemon stop completion on Qt's GUI thread."""

        self._media_stop_pending = False
        self._media_stop_completed = True
        self._media_drained = True
        lifecycle_log(
            "media.alarm.mci.stop_complete",
            class_name="WindowsAlarmAudio",
            alarm_id=str(self.alarm.id),
        )
        self._emit_audio_cleanup_finished()

    def _on_windows_audio_error(self, error: str) -> None:
        lifecycle_log(
            "media.alarm.mci.error_received",
            class_name="WindowsAlarmAudio",
            alarm_id=str(self.alarm.id),
            error=str(error),
        )
        backend = self._windows_audio
        self._windows_audio = None
        if backend is not None:
            backend.request_stop()
        if self._audio_closing:
            self._on_windows_audio_finished()
            return
        if self._custom_audio and self._play_qt_fallback(self._custom_audio_path):
            return
        self._fallback_to_system()

    def _fallback_to_system(self) -> None:
        if self._audio_closing or self._state == AlarmPopupState.DISMISSED:
            return
        if self._custom_audio:
            self._custom_audio = False
        self._using_system_sound = True
        self._qt_fallback_active = False
        if self._qt_fallback_output is not None:
            self._qt_fallback_output.setVolume(0)
        if self._media_player is not None:
            # Error handling runs from a QMediaPlayer callback.  Queue the
            # stop as well so an error/status signal cannot re-enter native
            # multimedia cleanup synchronously.
            self._queue_media_stop("fallback_to_system")
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

        lifecycle_log("alarm.popup.close.request", self, alarm_id=str(self.alarm.id), source="application")
        self._close_requested = True
        self._suppress_close_action = True
        self._state = AlarmPopupState.DISMISSED
        # closeEvent is not guaranteed for every native shutdown path (for
        # example a card that has already been hidden), so request cleanup
        # explicitly before asking Qt to close the window.
        self._stop_sound()
        self.close()

    @property
    def audio_cleanup_ready(self) -> bool:
        """Whether it is safe for the owner to schedule this card's deletion."""

        return self._audio_cleanup_ready

    def _emit_audio_cleanup_finished(self) -> None:
        if not self._close_requested or self._audio_cleanup_ready:
            return
        self._audio_cleanup_ready = True
        lifecycle_log(
            "alarm.popup.audio_cleanup_finished",
            self,
            alarm_id=str(self.alarm.id),
        )
        self.audio_cleanup_finished.emit()

    def _finish_media_stop(self) -> None:
        """Mute custom audio without synchronously stopping Qt Multimedia."""

        self._media_stop_pending = False
        if self._windows_audio is not None:
            lifecycle_log(
                "media.alarm.stop.defer",
                class_name="WindowsAlarmAudio",
                alarm_id=str(self.alarm.id),
                reason="async_winmm_stop",
            )
            self._windows_audio.request_stop()
            # Defensive process-wide cleanup also covers a card whose Qt
            # wrapper was retired while its native MCI alias remained open.
            _WindowsAlarmAudio.request_stop_all()
            # ``_media_stop_completed`` means that the stop request has been
            # dispatched, not that the daemon MCI call has returned.  The
            # latter is represented by ``_media_drained`` and the queued
            # cleanup signal below.
            self._media_stop_completed = True
            # The daemon worker will signal the final completion.  The
            # native Windows audio API is no longer part of the Qt GUI call
            # stack, and the button path never waits for it.
            return
        if self._media_player is None or self._media_stop_completed:
            self._media_stop_completed = True
            if self._media_player is None:
                self._media_drained = True
            if self._media_drained:
                self._emit_audio_cleanup_finished()
            return
        lifecycle_log(
            "media.alarm.stop.defer",
            self._media_player,
            owner="AlarmCard",
            alarm_id=str(self.alarm.id),
            reason="mute_and_drain",
        )
        if self._audio_output is not None:
            self._audio_output.setVolume(0)
        self._media_stop_completed = True
        try:
            stopped = (
                self._media_player.playbackState()
                == QMediaPlayer.PlaybackState.StoppedState
            )
        except RuntimeError:
            stopped = True
        if not stopped and sys.platform != "win32":
            # Windows custom audio uses MCI above. On other platforms the
            # queued player can be stopped safely, so do not leave a looping
            # QMediaPlayer running silently after the card is closed.
            try:
                self._media_player.stop()
            except RuntimeError:
                pass
            stopped = True
        if stopped:
            self._media_drained = True
            self._emit_audio_cleanup_finished()

    def _queue_media_stop(self, reason: str) -> None:
        """Queue a single media stop and return to the caller immediately."""

        if self._qt_fallback_player is not None:
            # Qt 6.11's Windows backend can block in stop().  Muting and
            # disabling the loop is sufficient for a closing card, and keeps
            # cleanup out of the GUI thread's native multimedia call stack.
            self._qt_fallback_active = False
            if self._qt_fallback_output is not None:
                self._qt_fallback_output.setVolume(0)

        if self._windows_audio is not None:
            if self._media_stop_completed or self._media_stop_pending:
                return
            lifecycle_log(
                "media.alarm.stop.request",
                class_name="WindowsAlarmAudio",
                alarm_id=str(self.alarm.id),
                reason=reason,
            )
            self._media_stop_pending = True
            self._media_stop_timer.start(0)
            return
        if self._qt_fallback_player is not None:
            self._media_stop_completed = True
            self._media_drained = True
            self._emit_audio_cleanup_finished()
            return
        if self._media_player is None:
            self._media_stop_completed = True
            self._media_drained = True
            self._emit_audio_cleanup_finished()
            return
        if self._media_stop_completed:
            if self._media_drained:
                self._emit_audio_cleanup_finished()
            return
        if self._media_stop_pending:
            return
        lifecycle_log(
            "media.alarm.stop.request",
            self._media_player,
            owner="AlarmCard",
            alarm_id=str(self.alarm.id),
            reason=reason,
        )
        self._media_stop_pending = True
        self._media_stop_timer.start(0)

    def _stop_sound(self) -> None:
        """Stop all alarm audio without synchronously blocking a UI action."""

        self._audio_closing = True
        self._audio_start_timer.stop()
        if self._sound_timer is not None:
            self._sound_timer.stop()
        if self._sound_stop_timer is not None:
            self._sound_stop_timer.stop()
        if self._audio_output is not None:
            # Changing the output level is the only synchronous multimedia
            # operation allowed during an alarm button path.  It returns
            # immediately and prevents a draining song from being audible.
            self._audio_output.setVolume(0)
        if self._qt_fallback_output is not None:
            self._qt_fallback_output.setVolume(0)
        self._queue_media_stop("popup_close")

    def _prepare_action(self, action: str = "unknown") -> bool:
        """Make a button action idempotent before its queued owner handles it."""

        if self._action_requested:
            lifecycle_log(
                "alarm.action_button.duplicate",
                self,
                action=action,
            )
            return False
        self._action_requested = True
        self._suppress_close_action = True
        self._state = AlarmPopupState.DISMISSED
        lifecycle_log(
            "alarm.popup.hide.begin",
            self,
            action=action,
        )
        self.hide()
        lifecycle_log(
            "alarm.popup.hide.end",
            self,
            action=action,
        )
        # The button handler must return before touching the native media
        # backend.  The queued timer also keeps the card alive until the
        # owner has safely closed it and the backend has stopped.
        lifecycle_log(
            "alarm.stop_audio.begin",
            self,
            action=action,
        )
        self._stop_sound()
        lifecycle_log(
            "alarm.stop_audio.end",
            self,
            action=action,
        )
        return True

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        # Closing by Alt+F4/Cmd+W or by the window manager must stop both
        # system beeps and custom looping audio immediately.
        lifecycle_log("alarm.popup.close_event.begin", self, alarm_id=str(self.alarm.id))
        self._close_requested = True
        self._stop_sound()
        if not self._suppress_close_action:
            # Closing the frameless card means “dismiss this firing”, not
            # “quit Lili”. Repeating alarms remain scheduled for next time.
            self._suppress_close_action = True
            self._state = AlarmPopupState.DISMISSED
            self.dismiss_requested.emit(self.alarm.id)
        super().closeEvent(event)
        lifecycle_log("alarm.popup.close_event.end", self, alarm_id=str(self.alarm.id))


class AwayRecoveryCard(QDialog):
    """Show an alarm-style card when an automatic pause ends."""

    continue_requested = Signal()
    dismiss_requested = Signal()

    def __init__(self, reason: str, away_seconds: int, parent=None) -> None:
        # Keep this as a top-level card, just like AlarmCard, so it remains
        # visible after the pet is temporarily hidden by a fullscreen app.
        super().__init__(None)
        lifecycle_log(
            "away_recovery.create",
            self,
            reason=str(reason),
            away_seconds=int(away_seconds),
        )
        self.destroyed.connect(
            lambda _obj=None: lifecycle_log(
                "away_recovery.destroy", class_name="AwayRecoveryCard"
            )
        )
        self._suppress_close_action = False
        self.setObjectName("alarmCard")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
        )
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setStyleSheet(ALARM_STYLE)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(7)
        title = QLabel("六毛发现你回来啦")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title)
        self.trigger_label = QLabel()
        self.trigger_label.setStyleSheet(
            "font-size: 28px; font-weight: 800; color:#24475b;"
        )
        layout.addWidget(self.trigger_label)
        self.detail_label = QLabel()
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet("color:#607985;font-size:12px;")
        layout.addWidget(self.detail_label)

        actions = QVBoxLayout()
        actions.setSpacing(6)
        continue_button = QPushButton("继续工作")
        continue_button.setObjectName("primary")
        continue_button.clicked.connect(self._request_continue)
        actions.addWidget(continue_button)
        dismiss = QPushButton("暂不继续")
        dismiss.setObjectName("quiet")
        dismiss.clicked.connect(self._request_dismiss)
        actions.addWidget(dismiss, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(actions)

        self._close_shortcuts = []
        for sequence in ("Ctrl+W", "Meta+W"):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(self._request_dismiss)
            self._close_shortcuts.append(shortcut)
        self.set_away_context(reason, away_seconds)

    def set_away_context(self, reason: str, away_seconds: int) -> None:
        seconds = max(1, int(away_seconds))
        minutes, remainder = divmod(seconds, 60)
        duration = f"{minutes} 分钟"
        if remainder:
            duration += f" {remainder} 秒"
        if minutes == 0:
            duration = f"{remainder} 秒"
        self.trigger_label.setText("要继续工作吗？")
        if str(reason or "") == "fullscreen_video":
            detail = "刚才检测到视频或游戏全屏，六毛已经帮你暂停计时。"
        else:
            detail = f"刚才离开屏幕约 {duration}，六毛已经帮你暂停计时。"
        self.detail_label.setText(detail)

    def center_on_current_screen(self) -> None:
        self.adjustSize()
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(
            area.left() + max(0, (area.width() - self.width()) // 2),
            area.top() + max(0, (area.height() - self.height()) // 2),
        )

    def close_from_app(self) -> None:
        lifecycle_log("away_recovery.close.request", self, source="application")
        self._suppress_close_action = True
        self.close()

    def _request_continue(self) -> None:
        self._suppress_close_action = True
        self.continue_requested.emit()

    def _request_dismiss(self) -> None:
        self._suppress_close_action = True
        self.dismiss_requested.emit()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        lifecycle_log("away_recovery.close_event", self)
        if not self._suppress_close_action:
            self._suppress_close_action = True
            self.dismiss_requested.emit()
        event.accept()


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
            trigger = QDateTime.fromString(alarm.trigger_at[:19], Qt.DateFormat.ISODate)
            self.trigger.setDateTime(self._minute_aligned(trigger))
        else:
            default = QDateTime.currentDateTime().addSecs(60)
            self.trigger.setDateTime(self._minute_aligned(default))
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

    @staticmethod
    def _minute_aligned(value: QDateTime) -> QDateTime:
        clock = value.time()
        value.setTime(QTime(clock.hour(), clock.minute(), 0, 0))
        return value

    def values(self) -> dict[str, Any]:
        repeat_rule = self.repeat.currentData()
        if repeat_rule == "weekly":
            days = [
                str(check.property("weekday_index"))
                for check in self.weekday_checks
                if check.isChecked()
            ]
            repeat_rule = "weekly:" + ",".join(days) if days else REPEAT_ONCE
        trigger = self._minute_aligned(self.trigger.dateTime())
        return {
            "title": self.title.text().strip() or "六毛闹钟",
            "trigger_at": trigger.toString("yyyy-MM-ddTHH:mm:ss"),
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
