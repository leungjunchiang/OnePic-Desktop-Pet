"""Qt worker for the optional content-only online updater."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from .content_updates import ContentUpdateManager, ContentUpdateResult


class ContentUpdateWorker(QThread):
    """Keep manifest and file I/O off the GUI thread."""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, manager: ContentUpdateManager, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager

    def run(self) -> None:
        try:
            result = self.manager.check_and_apply()
        except Exception as exc:  # convert network/validation errors to UI data
            self.failed.emit(str(exc))
        else:
            self.completed.emit(result)
