"""Windows 闹钟 Qt 解码器无窗口进程隔离；本地 IPC、限时停止，不读账号或访问网络。

复用 QtThreadAudio 播放、循环和断点恢复，只把原生生命周期移到辅助进程。
窗口应用不执行解码器 stop/destructor；Abort 或阻塞仅影响音频进程。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QCoreApplication, QObject, QProcess, QTimer, QUrl
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from . import alarm_audio_service as audio
from .alarm_audio_service import QtThreadAudio, _QT_AUDIO_JOBS
from .lifecycle_log import lifecycle_log


def _packet(kind, value):
    return (json.dumps({"kind": kind, "value": value}, ensure_ascii=True) + "\n").encode("utf-8")


def _worker_invocation(socket_name):
    if getattr(sys, "frozen", False):
        return sys.executable, ["--lili-alarm-audio-worker", socket_name]
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return str(pythonw if pythonw.exists() else sys.executable), [
        "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
        "from onepic_desktop_pet.alarm_audio_process import run_audio_worker; "
        "raise SystemExit(run_audio_worker(sys.argv[2]))",
        str(Path(__file__).resolve().parents[1]), socket_name]


class QtProcessAudio(QtThreadAudio):
    """保持现有命令桥接口，Windows 只在辅助进程内创建真正的播放器。"""

    def __init__(self):
        QObject.__init__(self, QApplication.instance())
        self.closed = self._stopping = False
        self._state = QMediaPlayer.PlaybackState.StoppedState
        self._source = None
        self._volume = 0.0
        self._loops = 1
        self._buffer = b""
        self._pending = []
        self._socket = None
        self._error_reported = False
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        name = "lili-alarm-" + uuid4().hex
        if not self._server.listen(name):
            raise RuntimeError("无法创建本地音频控制通道")
        self._server.newConnection.connect(self._connected)
        self.process = QProcess(self)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        self.process.readyReadStandardError.connect(lambda: self.process.readAllStandardError())
        self.process.readyReadStandardOutput.connect(lambda: self.process.readAllStandardOutput())
        self.command.connect(self._send)
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(self._kill_worker)
        program, arguments = _worker_invocation(name)
        _QT_AUDIO_JOBS.add(self)
        self.process.start(program, arguments)
        self._deadline.start(8000)
        lifecycle_log("media.alarm.process.start", backend="qt-isolated")

    def _connected(self):
        socket = self._server.nextPendingConnection()
        if self._socket is not None:
            socket.abort()
            socket.deleteLater()
            return
        self._socket = socket
        self._server.close()
        socket.readyRead.connect(self._receive)
        if not self._stopping:
            self._deadline.stop()
        for name, value in self._pending:
            socket.write(_packet(name, value))
        self._pending.clear()
        lifecycle_log("media.alarm.process.connected", process_id=int(self.process.processId()))

    def _send(self, name, value):
        if self.closed or (self._stopping and name != "stop"):
            return
        if isinstance(value, QUrl):
            value = value.toString()
        if self._socket is None:
            self._pending.append((name, value))
        else:
            self._socket.write(_packet(name, value))

    def _receive(self):
        self._buffer += bytes(self._socket.readAll())
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            try:
                data = json.loads(line)
                kind, value = data["kind"], data["value"]
                if kind == "state":
                    self._state_changed(QMediaPlayer.PlaybackState[value])
                elif kind == "status":
                    self.mediaStatusChanged.emit(QMediaPlayer.MediaStatus[value])
                elif kind == "position":
                    self.positionChanged.emit(int(value))
                elif kind == "error" and not self._stopping:
                    self._error_reported = True
                    self.errorOccurred.emit(QMediaPlayer.Error[value[0]], value[1])
                elif kind == "diagnostic":
                    lifecycle_log(value[0], backend="qt-isolated", **value[1])
            except (ValueError, KeyError, TypeError):
                lifecycle_log("media.alarm.process.invalid_packet")

    def stop(self):
        if self.closed or self._stopping:
            return
        lifecycle_log("media.alarm.process.stop_requested", process_id=int(self.process.processId()))
        self._stopping = True
        self._pending.clear()
        self._send("stop", None)
        self._deadline.start(2000)

    def _kill_worker(self):
        lifecycle_log("media.alarm.process.deadline", stopping=self._stopping)
        if self.process.state() != QProcess.ProcessState.NotRunning:
            # Kill only this private decoder; never wait on the GUI thread.
            self.process.kill()
        else:
            self._process_finished(-1, QProcess.ExitStatus.CrashExit)

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._process_finished(-1, QProcess.ExitStatus.CrashExit)

    def _process_finished(self, code, status):
        if self.closed:
            return
        lifecycle_log("media.alarm.process.finished", exit_code=int(code),
                      exit_status=str(status), requested_stop=self._stopping)
        if not self._stopping and not self._error_reported:
            self._error_reported = True
            self.errorOccurred.emit(QMediaPlayer.Error.ResourceError, "音乐播放进程已停止，改用系统提示音")
        self._deadline.stop()
        self._server.close()
        if self._socket is not None:
            self._socket.abort()
        self.closed = True
        self._state = QMediaPlayer.PlaybackState.StoppedState
        self.finished.emit()
        _QT_AUDIO_JOBS.discard(self)
        self.deleteLater()


def run_audio_worker(socket_name):
    """仅本机随机命名通道；不创建 QWidget，不经过单实例、账号或宠物初始化。"""
    if not socket_name.startswith("lili-alarm-") or len(socket_name) != 43:
        return 2
    app = QCoreApplication([sys.argv[0]])
    socket = QLocalSocket()
    buffer = b""
    backend = None

    def send(kind, value):
        if socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
            socket.write(_packet(kind, value))

    def connected():
        nonlocal backend
        # Existing worker diagnostics go to the owner; no second log writer.
        audio.lifecycle_log = lambda event, *args, **values: send("diagnostic", [event, values])
        backend = QtThreadAudio()
        backend.playbackStateChanged.connect(lambda value: send("state", value.name))
        backend.mediaStatusChanged.connect(lambda value: send("status", value.name))
        backend.positionChanged.connect(lambda value: send("position", value))
        backend.errorOccurred.connect(lambda code, message: send("error", [code.name, message]))
        backend.finished.connect(app.quit)

    def receive():
        nonlocal buffer
        buffer += bytes(socket.readAll())
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            try:
                data = json.loads(line)
                name, value = data["kind"], data["value"]
                if name == "source":
                    url = QUrl(value)
                    if not url.isLocalFile():
                        raise ValueError("闹钟音频必须为本地文件")
                    backend.setSource(url)
                elif name == "volume":
                    backend.setVolume(max(0., min(1., float(value))))
                elif name == "loops":
                    backend.setLoops(-1 if int(value) == -1 else 1)
                elif name == "play":
                    backend.play()
                elif name == "stop":
                    backend.stop()
            except (ValueError, KeyError, TypeError):
                send("error", ["ResourceError", "音频控制参数无效"])

    socket.connected.connect(connected)
    socket.readyRead.connect(receive)
    # Parent exit closes IPC. Exit even if the native decoder is stuck.
    socket.disconnected.connect(lambda: os._exit(0))
    socket.errorOccurred.connect(lambda error: os._exit(2))
    socket.connectToServer(socket_name)
    QTimer.singleShot(8000, lambda: os._exit(2) if backend is None else None)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run_audio_worker(sys.argv[1]) if len(sys.argv) == 2 else 2)
