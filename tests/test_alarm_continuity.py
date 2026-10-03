"""铃声意外停止、循环边界、关闭竞态、Mac 线程桥与真实静音连续播放回归。"""
import os
import sys
import time
import wave
from collections import deque
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import QUrl, QTimer, Slot
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaPlayer, QMediaDevices
from onepic_desktop_pet import alarm_audio_service as audio


def app():
    return QApplication.instance() or QApplication([])


def pump(seconds):
    # QTest.qWait 的 C++ 等待可能持有 GIL，不能用它模拟独立 Python 音频线程。
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app().processEvents()
        time.sleep(.01)


def worker(monkeypatch):
    app()
    clock = [100.0]
    monkeypatch.setattr(audio, "monotonic", lambda: clock[0])
    w = audio._QtAudioWorker()
    p = SimpleNamespace(state=QMediaPlayer.PlaybackState.StoppedState,
                        status=QMediaPlayer.MediaStatus.LoadedMedia, plays=0, seeks=[], loads=[])
    p.playbackState = lambda: p.state
    p.mediaStatus = lambda: p.status
    p.duration = lambda: 10000
    p.bufferProgress = lambda: 1.0
    p.setPosition = lambda value: p.seeks.append(value)
    p.play = lambda: setattr(p, "plays", p.plays + 1)
    p.setSource = lambda value: p.loads.append(value)
    w.player = p
    w._want_play = True
    w._loops = -1
    w._position = 840
    w._last_progress = w._last_diagnostic = 98.0
    w._recoveries = deque()
    w._monitor = SimpleNamespace(stop=lambda: None, start=lambda: None)
    return w, p, clock


def test_loaded_after_one_second_resumes_last_position_without_reloading(monkeypatch):
    w, p, _ = worker(monkeypatch)
    w._position_changed(0)  # 原生停止发出的重置不能丢掉断点。
    w._check_playback()
    assert p.plays == 1 and p.seeks == [840] and p.loads == []
    p.state = QMediaPlayer.PlaybackState.PlayingState
    w._check_playback()
    assert p.plays == 1


@pytest.mark.parametrize("status", [QMediaPlayer.MediaStatus.LoadingMedia,
                                   QMediaPlayer.MediaStatus.BufferingMedia,
                                   QMediaPlayer.MediaStatus.StalledMedia,
                                   QMediaPlayer.MediaStatus.InvalidMedia])
def test_loading_or_buffering_is_not_restarted(monkeypatch, status):
    w, p, _ = worker(monkeypatch)
    p.status = status
    w._check_playback()
    assert p.plays == 0 and not p.loads


def test_normal_native_loop_and_finite_preview_end_are_not_restarted(monkeypatch):
    w, p, clock = worker(monkeypatch)
    p.status = QMediaPlayer.MediaStatus.EndOfMedia
    w._last_progress = clock[0] - .1
    w._check_playback()
    assert p.plays == 0  # 原生循环先有机会自行续播。
    w._last_progress -= 1
    w._check_playback()
    assert p.seeks == [0]
    w._loops = 1
    w._check_playback()
    assert not w._want_play and p.plays == 1


def test_recovery_is_bounded_and_errors_fall_back(monkeypatch):
    w, p, clock = worker(monkeypatch)
    errors = []
    w.error.connect(lambda code, message: errors.append(message))
    for _ in range(5):
        clock[0] += 1
        w._check_playback()
    assert p.plays == 3 and len(errors) == 1 and not w._want_play


def test_explicit_stop_disarms_before_native_callbacks(monkeypatch):
    w, p, _ = worker(monkeypatch)
    stopped = []
    w.output = SimpleNamespace(setVolume=lambda v: stopped.append(v))
    p.stop = lambda: (w._check_playback(), stopped.append("stop"))
    monkeypatch.setattr(w, "thread", lambda: SimpleNamespace(quit=lambda: stopped.append("quit")))
    w.command("stop", None)
    w._check_playback()
    assert stopped == [0, "stop", "quit"] and p.plays == 0


@pytest.mark.skipif(sys.platform == "darwin" and os.environ.get("QT_QPA_PLATFORM") in {"offscreen", "minimal"},
                    reason="macOS headless native alarm window unavailable")
def test_mac_alarm_uses_same_owned_worker_and_mutes_before_cleanup(tmp_path, monkeypatch):
    from onepic_desktop_pet import alarm_ui
    from onepic_desktop_pet.alarm_manager import Alarm
    from onepic_desktop_pet.alarm_sounds import AlarmSoundLibrary
    app()
    path = tmp_path / "music.wav"
    path.write_bytes(b"test fixture")
    library = AlarmSoundLibrary(tmp_path)
    sound = library.import_file(path)
    calls = []
    class Signal:
        def connect(self, *args): pass
    class Bridge:
        def __init__(self):
            self.mediaStatusChanged = self.playbackStateChanged = self.errorOccurred = Signal()
        def setAudioOutput(self, value): pass
        def setLoops(self, value): calls.append(("loops", value))
        def setVolume(self, value): calls.append(("volume", value))
        def setSource(self, value): calls.append(("source", value))
    bridge = Bridge()
    monkeypatch.setattr(alarm_ui.sys, "platform", "darwin")
    monkeypatch.setattr(alarm_ui, "create_qt_alarm_audio", lambda: (bridge, bridge))
    card = alarm_ui.AlarmCard(Alarm(id="mac-thread", title="test", trigger_at="2026-10-03T11:30:00",
                                   sound_id=sound.sound_id, sound_enabled=True), sound_library=library)
    assert card._media_player is bridge and card._audio_output is bridge
    assert len([c for c in calls if c[0] == "source"]) == 1
    assert ("loops", -1) in calls
    card._media_player = card._audio_output = None
    card.close_from_app()


@pytest.mark.skipif(sys.platform != "win32", reason="真实输出设备静音回归在 Windows 执行；其余使用确定性后端测试")
def test_real_thread_audio_keeps_looping_across_two_seconds_and_busy_ui(tmp_path):
    app()
    if not QMediaDevices.audioOutputs():
        pytest.skip("runner has no native audio output")
    path = tmp_path / "silent.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(b"\x00\x00" * 96000)  # 两秒原生循环，不发声音。
    backend = audio.QtThreadAudio()
    positions, errors = [], []
    backend.positionChanged.connect(positions.append)
    backend.errorOccurred.connect(lambda *args: errors.append(args))
    try:
        backend.setSource(QUrl.fromLocalFile(str(path)))
        backend.setLoops(-1)
        backend.setVolume(0)
        backend.play()
        pump(1.1)
        time.sleep(1.2)  # GUI 暂停处理时音频线程仍须前进。
        pump(2.2)
        assert not errors
        assert backend.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        assert sum(a > b + 300 for a, b in zip(positions, positions[1:])) >= 2, positions[::5]
    finally:
        backend.stop()
        deadline = time.monotonic() + 5
        while not backend.closed and time.monotonic() < deadline:
            pump(.02)
        assert backend.closed


@pytest.mark.skipif(sys.platform != "win32", reason="真实设备恢复验证在 Windows；确定性恢复回归覆盖所有平台")
def test_real_native_unexpected_stop_resumes_without_starting_song_over(tmp_path, monkeypatch):
    app()
    if not QMediaDevices.audioOutputs():
        pytest.skip("runner has no native audio output")
    path = tmp_path / "long-silent.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(b"\x00\x00" * 480000)
    interruptions = []
    class InterruptWorker(audio._QtAudioWorker):
        @Slot()
        def initialize(self):
            super().initialize()
            # 故障注入在音频所属线程；模拟日志中的意外 LoadedMedia。
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self.interrupt)
            timer.start(1300)
            self.interrupt_timer = timer
        @Slot()
        def interrupt(self):
            interruptions.append(self._position)
            self.player.stop()
    monkeypatch.setattr(audio, "_QtAudioWorker", InterruptWorker)
    backend = audio.QtThreadAudio()
    positions, errors = [], []
    backend.positionChanged.connect(positions.append)
    backend.errorOccurred.connect(lambda *args: errors.append(args))
    try:
        backend.setSource(QUrl.fromLocalFile(str(path)))
        backend.setLoops(-1)
        backend.setVolume(0)
        backend.play()
        pump(3.5)
        assert not errors and len(interruptions) == 1 and interruptions[0] >= 500
        # 原生 stop 的 0 后，应恢复断点，而不是从歌曲开头重播。
        zero = next(i for i, value in enumerate(positions) if i and value == 0 and positions[i-1] >= 500)
        assert positions[zero + 1] >= interruptions[0] - 100, positions
        assert positions[-1] > interruptions[0] + 500
        assert backend.playbackState() == QMediaPlayer.PlaybackState.PlayingState
    finally:
        backend.stop()
        deadline = time.monotonic() + 5
        while not backend.closed and time.monotonic() < deadline:
            pump(.02)
        assert backend.closed
