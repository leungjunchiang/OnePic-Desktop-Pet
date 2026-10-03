"""线程内连续播放、有限断点恢复与本地诊断；显式停止及退出不允许重新起播。"""
from __future__ import annotations

from collections import deque
from time import monotonic
from PySide6.QtCore import QObject, QTimer, QThread, Signal, Slot, Qt
from PySide6.QtWidgets import QApplication
from .lifecycle_log import lifecycle_log

_QT_AUDIO_JOBS = set()


class _QtAudioWorker(QObject):
    state_changed = Signal(object)
    status_changed = Signal(object)
    error = Signal(object, str)
    position_changed = Signal(int)

    @Slot()
    def initialize(self):
        from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
        # 必须在所属线程中创建后端，不能迁移已经播放的原生解码器。
        self.output = QAudioOutput(self)
        self.output.setVolume(0)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.output)
        self._want_play = False
        self._loops = 1
        self._position = 0
        self._last_progress = monotonic()
        self._last_diagnostic = self._last_progress
        self._recoveries = deque()
        self._monitor = QTimer(self)
        self._monitor.setInterval(250)
        self._monitor.timeout.connect(self._check_playback)
        self.player.playbackStateChanged.connect(self._state_changed)
        self.player.mediaStatusChanged.connect(self._status_changed)
        self.player.errorOccurred.connect(self._error)
        self.player.positionChanged.connect(self._position_changed)

    def _state_changed(self, state):
        lifecycle_log("media.alarm.worker.state", state=str(state),
                      position_ms=self._position, requested_play=self._want_play)
        self.state_changed.emit(state)

    def _status_changed(self, status):
        self.status_changed.emit(status)

    def _position_changed(self, position):
        # stop() 会发出 position=0；保留最后有效位置用于意外停止的断点恢复。
        from PySide6.QtMultimedia import QMediaPlayer
        # Qt 在 stop 的 state_changed 之前就可能先发 position=0。
        if position > 0 and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            if position != self._position:
                self._last_progress = monotonic()
            self._position = position
        self.position_changed.emit(position)

    def _error(self, code, message):
        self._want_play = False
        self._monitor.stop()
        self.error.emit(code, message)

    def _check_playback(self):
        """仅恢复无错误的意外停止，播放和缓冲中绝不重复加载/重启歌曲。"""
        from PySide6.QtMultimedia import QMediaPlayer
        if not self._want_play:
            return
        now = monotonic()
        state, status = self.player.playbackState(), self.player.mediaStatus()
        if now - self._last_diagnostic >= 5:
            lifecycle_log("media.alarm.worker.progress", state=str(state), status=str(status),
                          position_ms=self._position, duration_ms=self.player.duration(),
                          buffer_progress=self.player.bufferProgress())
            self._last_diagnostic = now
        if state == QMediaPlayer.PlaybackState.PlayingState:
            return
        if status not in (QMediaPlayer.MediaStatus.LoadedMedia,
                          QMediaPlayer.MediaStatus.EndOfMedia):
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self._loops != -1:
            self._want_play = False
            self._monitor.stop()
            return
        if now - self._last_progress < .75:
            return
        while self._recoveries and now - self._recoveries[0] > 30:
            self._recoveries.popleft()
        if len(self._recoveries) >= 3:
            lifecycle_log("media.alarm.worker.recovery_exhausted", position_ms=self._position)
            self._error(QMediaPlayer.Error.ResourceError, "自定义铃声无法持续播放，改用系统提示音")
            return
        self._recoveries.append(now)
        position = 0 if status == QMediaPlayer.MediaStatus.EndOfMedia else self._position
        lifecycle_log("media.alarm.worker.resume", state=str(state), status=str(status),
                      position_ms=position, attempt=len(self._recoveries))
        self.player.setPosition(position)
        self._last_progress = now
        self.player.play()

    @Slot(object, object)
    def command(self, name, value):
        if name == "source":
            self._want_play = False
            self._monitor.stop()
            self._position = 0
            self._recoveries.clear()
            self.player.setSource(value)
        elif name == "loops":
            self._loops = value
            self.player.setLoops(value)
        elif name == "volume":
            self.output.setVolume(value)
        elif name == "play":
            self._want_play = True
            self._last_progress = monotonic()
            self._monitor.start()
            self.player.play()
        elif name == "stop":
            self._want_play = False
            self._monitor.stop()
            lifecycle_log("media.alarm.worker.stop", position_ms=self._position)
            self.output.setVolume(0)
            self.player.stop()
            self.thread().quit()


class QtThreadAudio(QObject):
    """Qt 原后端的线程命令桥；解码、加载和 stop 始终在同一个音频线程。"""
    command = Signal(object, object)
    playbackStateChanged = Signal(object)
    mediaStatusChanged = Signal(object)
    errorOccurred = Signal(object, str)
    positionChanged = Signal(int)
    finished = Signal()

    def __init__(self):
        super().__init__(QApplication.instance())
        from PySide6.QtMultimedia import QMediaPlayer
        self.closed = False
        self._stopping = False
        self._state = QMediaPlayer.PlaybackState.StoppedState
        self._source = None
        self._volume = 0.0
        self._loops = 1
        self.thread_owner = QThread()
        self.worker = _QtAudioWorker()
        self.worker.moveToThread(self.thread_owner)
        # started 在新线程发出；初始化必须先于事件队列中的 source/play/stop。
        self.thread_owner.started.connect(self.worker.initialize, Qt.ConnectionType.DirectConnection)
        self.command.connect(self.worker.command, Qt.ConnectionType.QueuedConnection)
        self.worker.state_changed.connect(self._state_changed)
        self.worker.status_changed.connect(self.mediaStatusChanged)
        self.worker.error.connect(self.errorOccurred)
        self.worker.position_changed.connect(self.positionChanged)
        self.thread_owner.finished.connect(self.worker.deleteLater)
        self.thread_owner.finished.connect(self._complete)
        _QT_AUDIO_JOBS.add(self)
        self.thread_owner.start()

    @Slot(object)
    def _state_changed(self, state):
        self._state = state
        self.playbackStateChanged.emit(state)

    def setAudioOutput(self, output):
        # 输出和播放器均由 worker 管理，UI 持有同一个命令桥。
        pass

    def setSource(self, source):
        self._source = source
        self.command.emit("source", source)

    def source(self):
        return self._source

    def setLoops(self, loops):
        self._loops = loops
        self.command.emit("loops", loops)

    def loops(self):
        return self._loops

    def setVolume(self, volume):
        self._volume = volume
        if not self.closed and not self._stopping:
            self.command.emit("volume", volume)

    def volume(self):
        return self._volume

    def playbackState(self):
        return self._state

    def play(self):
        if not self.closed and not self._stopping:
            self.command.emit("play", None)

    def stop(self):
        if not self.closed and not self._stopping:
            lifecycle_log("media.alarm.bridge.stop_requested")
            self._stopping = True
            self.command.emit("stop", None)

    @Slot()
    def _complete(self):
        # finished 信号早于线程内 DeferredDelete；禁止 GUI 销毁仍在清理的 QThread。
        if not self.thread_owner.wait(0):
            QTimer.singleShot(20, self._complete)
            return
        self.closed = True
        self.finished.emit()
        _QT_AUDIO_JOBS.discard(self)
        self.thread_owner.deleteLater()
        self.deleteLater()


def create_qt_alarm_audio():
    backend = QtThreadAudio()
    return backend, backend


class AlarmAudioService(QObject):
    """仅调度本地音频，不读数据库、不等待线程、不创建第二套解码器。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "idle"
        self.current = None
        self.pending = None
        self.stopping = []
        self.consumed = deque(maxlen=512)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._advance)

    @classmethod
    def shared(cls):
        app = QApplication.instance()
        service = getattr(app, "_lili_alarm_audio_service", None)
        if service is None:
            service = cls(app)
            app._lili_alarm_audio_service = service
            app.aboutToQuit.connect(service.clear)
        return service

    def play(self, owner, occurrence, start, stop, *, priority=1):
        """一个 occurrence 只提交一次；正式闹钟优先，试听不打断响铃。"""
        if occurrence in self.consumed:
            return False
        existing = self.pending or self.current
        if existing and existing[4] > priority:
            return False
        if self.current:
            self._retire()
        if self.pending:
            old, self.pending = self.pending, None
            old[3]()
            job = getattr(old[0], "_qt_stop_job", None)
            if job is not None:
                self.stopping.append(job)
        self.consumed.append(occurrence)
        self.pending = (owner, occurrence, start, stop, priority)
        self._advance()
        return True

    def _retire(self):
        entry, self.current = self.current, None
        if entry is None:
            return
        lifecycle_log("alarm.audio.retire", occurrence_id=entry[1])
        owner = entry[0]
        # 捕获旧原生后端：UI 清理会清空字段，不能误把 None 当停止确认。
        backend = getattr(owner, "_windows_audio", None) or getattr(owner, "_windows_preview_audio", None)
        if backend is not None:
            self.stopping.append(backend)
        self.state = "stopping"
        entry[3]()
        job = getattr(owner, "_qt_stop_job", None)
        if job is not None:
            self.stopping.append(job)

    def stop(self, owner):
        handled = False
        if self.pending and self.pending[0] is owner:
            self.pending[3]()
            self.pending = None
            handled = True
        if self.current and self.current[0] is owner:
            self._retire()
            handled = True
        self._advance()
        return handled

    def _advance(self):
        self.timer.stop()
        self.stopping = [b for b in self.stopping if not (
            getattr(b, "closed", True) or getattr(b, "playback_stopped", False))]
        if self.stopping:
            self.state = "stopping"
            self.timer.start()
            return
        if self.pending:
            entry, self.pending = self.pending, None
            self.current = entry
            self.state = "loading"
            lifecycle_log("alarm.audio.load", occurrence_id=entry[1])
            try:
                entry[2]()
            except Exception:
                self._retire()
                self._advance()
                raise
            if self.current is entry:
                self.state = "playing"
                lifecycle_log("alarm.audio.play", occurrence_id=entry[1])
        elif self.current is None:
            self.state = "stopped"

    def clear(self):
        if self.pending:
            self.pending[3]()
        self.pending = None
        self._retire()
        self.timer.stop()
        from .alarm_ui import _WindowsAlarmAudio
        _WindowsAlarmAudio.request_stop_all()
        # aboutToQuit 里等待1秒后继续销毁仍运行的音频桥会触发 Qt abort。
        # 这里只发停止命令；应用退出门禁在事件循环仍工作时确认线程已清理。
        for job in tuple(_QT_AUDIO_JOBS):
            job.stop()


def prepare_audio_shutdown() -> bool:
    """非阻塞退出门禁；没有音频时不创建播放器或共享服务。"""
    app = QApplication.instance()
    service = getattr(app, "_lili_alarm_audio_service", None)
    if service is not None:
        service.clear()
    ready = True
    for job in tuple(_QT_AUDIO_JOBS):
        job.stop()
        if not job.thread_owner.wait(0):
            ready = False
            lifecycle_log("alarm.audio.shutdown.pending", job, thread_class="QtThreadAudio")
    return ready
