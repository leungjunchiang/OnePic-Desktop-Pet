"""真实辅助进程播放与按钮清理；解码器崩溃、停止阻塞和早期取消不影响 GUI。"""
import os
import sys
import time
import wave
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import QUrl, QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaPlayer, QMediaDevices
from onepic_desktop_pet import alarm_audio_process as process_audio
from onepic_desktop_pet import alarm_audio_service as audio


def app():
    return QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app().processEvents()
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), 'audio helper did not finish in time'


def silent_wave(path):
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(48000)
        f.writeframes(b'\x00\x00' * 96000)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows 音频进程；Mac 保留线程后端')
def test_real_process_plays_and_stops_without_touching_main_decoder(tmp_path):
    app()
    if not QMediaDevices.audioOutputs():
        pytest.skip("runner has no native audio output")
    path = tmp_path / 'silent.wav'
    silent_wave(path)
    before = set(QApplication.topLevelWidgets())
    backend, output = audio.create_qt_alarm_audio()
    assert isinstance(backend, process_audio.QtProcessAudio) and backend is output
    errors, positions, finished = [], [], []
    backend.errorOccurred.connect(lambda *args: errors.append(args))
    backend.positionChanged.connect(positions.append)
    backend.finished.connect(lambda: finished.append(True))
    try:
        backend.setSource(QUrl.fromLocalFile(str(path)))
        backend.setLoops(-1)
        backend.setVolume(0)
        backend.play()
        wait_for(lambda: positions and positions[-1] >= 600)
        assert not errors
        assert backend.process.processId() != os.getpid()
        assert not hasattr(backend, 'worker')  # GUI 没有原生解码器。
        assert set(QApplication.topLevelWidgets()) == before
        # Decoder runs even when parent GUI is occupied.
        time.sleep(.4)
        wait_for(lambda: positions[-1] >= 1000)
    finally:
        backend.stop()
        wait_for(lambda: backend.closed, timeout=4)
    assert finished == [True] and backend not in audio._QT_AUDIO_JOBS
    assert not errors


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native helper')
def test_stop_before_connection_is_silent_idempotent_and_drains():
    app()
    backend = process_audio.QtProcessAudio()
    errors, finished = [], []
    backend.errorOccurred.connect(lambda *args: errors.append(args))
    backend.finished.connect(lambda: finished.append(True))
    backend.play()
    backend.stop()
    backend.stop()
    wait_for(lambda: backend.closed, timeout=4)
    assert finished == [True] and not errors
    assert backend not in audio._QT_AUDIO_JOBS


def _fake_worker(tmp_path, monkeypatch, action):
    # Isolated fault injection: only the disposable audio child crashes/hangs.
    path = tmp_path / 'worker.py'
    path.write_text('''import os, sys
from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalSocket
app=QCoreApplication([])
socket=QLocalSocket()
buffer=b''
def receive():
 global buffer
 buffer += bytes(socket.readAll())
 if b'"stop"' in buffer:
  ACTION
socket.readyRead.connect(receive)
socket.connectToServer(sys.argv[1])
app.exec()
'''.replace('ACTION', action), encoding='utf-8')
    monkeypatch.setattr(process_audio, '_worker_invocation', lambda name: (sys.executable, [str(path), name]))
    backend = process_audio.QtProcessAudio()
    wait_for(lambda: backend._socket is not None)
    return backend


def test_native_abort_on_stop_is_contained_and_action_returns(tmp_path, monkeypatch):
    app()
    backend = _fake_worker(tmp_path, monkeypatch, 'os.abort()')
    finished, errors = [], []
    backend.finished.connect(lambda: finished.append(True))
    backend.errorOccurred.connect(lambda *args: errors.append(args))
    start = time.monotonic()
    backend.stop()
    assert time.monotonic() - start < .1
    wait_for(lambda: backend.closed, timeout=4)
    assert finished == [True] and not errors
    assert backend not in audio._QT_AUDIO_JOBS
    alive = []
    QTimer.singleShot(0, lambda: alive.append(True))
    wait_for(lambda: alive)


def test_stuck_native_stop_has_deadline_without_blocking_gui(tmp_path, monkeypatch):
    app()
    backend = _fake_worker(tmp_path, monkeypatch, 'pass')
    ticks = []
    timer = QTimer()
    timer.setInterval(25)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()
    try:
        backend.stop()
        wait_for(lambda: backend.closed, timeout=4)
        assert len(ticks) >= 20
        assert backend not in audio._QT_AUDIO_JOBS
    finally:
        timer.stop()
        if not backend.closed:
            backend.process.kill()
            wait_for(lambda: backend.closed)


def test_unexpected_helper_exit_reports_once_and_shutdown_uses_closed(tmp_path, monkeypatch):
    app()
    backend = _fake_worker(tmp_path, monkeypatch, 'pass')
    errors = []
    backend.errorOccurred.connect(lambda *args: errors.append(args))
    backend.process.kill()
    wait_for(lambda: backend.closed)
    assert len(errors) == 1
    backend._process_finished(1, None)
    assert len(errors) == 1


def test_frozen_entry_uses_same_executable_before_normal_application(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    program, args = process_audio._worker_invocation('lili-alarm-' + 'a' * 32)
    assert program == sys.executable
    assert args == ['--lili-alarm-audio-worker', 'lili-alarm-' + 'a' * 32]
    import importlib.util
    spec = importlib.util.spec_from_file_location('audio_test_main', Path(__file__).parents[1] / 'main.py')
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    seen = []
    monkeypatch.setattr(process_audio, 'run_audio_worker', lambda name: seen.append(name) or 12)
    monkeypatch.setattr(sys, 'argv', ['Lili.exe', *args])
    assert entry.main() == 12 and seen == [args[1]]

@pytest.mark.skipif(sys.platform != 'win32', reason='Windows 实际闹钟按钮与隔离回退播放器')
@pytest.mark.parametrize('action', ['start', 'snooze', 'dismiss'])
def test_real_alarm_buttons_close_once_and_wait_for_decoder_cleanup(tmp_path, action):
    from onepic_desktop_pet.alarm_ui import AlarmCard
    from onepic_desktop_pet.alarm_sounds import AlarmSoundLibrary
    from onepic_desktop_pet.alarm_manager import Alarm
    from PySide6.QtCore import Qt
    app()
    if not QMediaDevices.audioOutputs():
        pytest.skip('runner has no native audio output')
    path = tmp_path / 'button-silent.wav'
    silent_wave(path)
    library = AlarmSoundLibrary(tmp_path)
    sound = library.import_file(path)
    card = AlarmCard(Alarm(id='button-' + action, title='test', trigger_at='2026-10-10T11:30:00',
                           sound_id=sound.sound_id, volume=0, sound_enabled=True), sound_library=library)
    backend = card._qt_fallback_player
    assert isinstance(backend, process_audio.QtProcessAudio)
    calls, positions, cleanup = [], [], []
    backend.positionChanged.connect(positions.append)
    def handled(*values):
        calls.append(values)
        card.close_from_app()
    card.start_requested.connect(handled, Qt.ConnectionType.QueuedConnection)
    card.snooze_requested.connect(handled, Qt.ConnectionType.QueuedConnection)
    card.dismiss_requested.connect(handled, Qt.ConnectionType.QueuedConnection)
    card.audio_cleanup_finished.connect(lambda: cleanup.append(True))
    try:
        card.show_alarm_foreground()
        wait_for(lambda: positions and positions[-1] >= 400)
        begin = time.monotonic()
        if action == 'start':
            card._request_start()
        elif action == 'snooze':
            card._request_snooze(5)
        else:
            card._request_dismiss()
        assert time.monotonic() - begin < .2
        card._request_dismiss()  # Double-click cannot submit another action.
        wait_for(lambda: card.audio_cleanup_ready, timeout=4)
        assert not card.isVisible() and len(calls) == 1 and cleanup == [True]
        assert backend.closed and backend not in audio._QT_AUDIO_JOBS
    finally:
        card.close_from_app()
        if not backend.closed:
            wait_for(lambda: backend.closed, timeout=4)
        card.deleteLater()
        app().processEvents()
