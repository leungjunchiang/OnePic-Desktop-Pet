"""发布门禁：冻结 Windows 可执行文件以无窗口音频入口握手、播放并限时清理。"""
from pathlib import Path
import argparse
import sys
import tempfile
import time
import wave

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaDevices
from onepic_desktop_pet import alarm_audio_process as audio


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('executable')
    args = parser.parse_args()
    executable = str(Path(args.executable).resolve())
    app = QApplication([])
    audio._worker_invocation = lambda name: (executable, ['--lili-alarm-audio-worker', name])
    backend = audio.QtProcessAudio()
    errors, positions = [], []
    backend.errorOccurred.connect(lambda *values: errors.append(values))
    backend.positionChanged.connect(positions.append)

    def wait(predicate, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            app.processEvents()
            if predicate():
                return
            time.sleep(.01)
        raise RuntimeError('Packaged audio helper timeout')

    try:
        wait(lambda: backend._socket is not None or backend.closed, 10)
        assert not backend.closed and not errors, errors
        assert not QApplication.topLevelWidgets(), 'Audio helper created a main UI'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'silent.wav'
            with wave.open(str(path), 'wb') as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(48000)
                stream.writeframes(b'\x00\x00' * 96000)
            if QMediaDevices.audioOutputs():
                backend.setSource(QUrl.fromLocalFile(str(path)))
                backend.setLoops(-1)
                backend.setVolume(0)
                backend.play()
                wait(lambda: positions and positions[-1] >= 600 or errors, 10)
                assert not errors, errors
            backend.stop()
            wait(lambda: backend.closed, 4)
    finally:
        if not backend.closed:
            backend.stop()
            wait(lambda: backend.closed, 4)
    print('Packaged audio helper handshake/stop verified; playback checked when output available')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
