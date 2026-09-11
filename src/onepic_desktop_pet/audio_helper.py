"""Headless Qt Multimedia worker used by Windows alarm playback.

This module intentionally owns no desktop window, tray icon, login session, or
network client.  It is launched as a short-lived child process so a native
Windows decoder stall cannot block the main Lili event loop.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QCoreApplication, QUrl, QTimer

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
except ImportError:  # pragma: no cover - depends on the packaged Qt runtime
    QAudioOutput = QMediaPlayer = None


def run_audio_helper(path: str, volume: int) -> int:
    """Play one file continuously until the helper process is terminated."""

    if QAudioOutput is None or QMediaPlayer is None:
        return 2
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    output = QAudioOutput()
    output.setVolume(max(0, min(100, int(volume or 0))) / 100)
    player = QMediaPlayer()
    player.setAudioOutput(output)
    manual_loop = True
    try:
        player.setLoops(-1)
        manual_loop = False
    except (AttributeError, TypeError):
        pass

    if manual_loop:
        def restart_at_end(status) -> None:
            if status == QMediaPlayer.MediaStatus.EndOfMedia:
                player.setPosition(0)
                player.play()

        player.mediaStatusChanged.connect(restart_at_end)

    def fail(*_args: object) -> None:
        app.exit(2)

    player.errorOccurred.connect(fail)
    try:
        player.setSource(QUrl.fromLocalFile(str(path)))
        QTimer.singleShot(0, player.play)
    except Exception:
        return 2
    return int(app.exec())

