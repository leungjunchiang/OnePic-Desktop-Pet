"""Qt worker for the optional content-only online updater."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from .content_updates import ContentUpdateManager, ContentUpdateResult
from .program_updates import ProgramRelease, ProgramUpdateManager


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


class ProgramUpdateCheckWorker(QThread):
    """Check the latest installer without blocking the pet UI."""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, manager: ProgramUpdateManager, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager

    def run(self) -> None:
        try:
            result = self.manager.fetch_latest()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.completed.emit(result)


class ProgramUpdateDownloadWorker(QThread):
    """Download and verify the user-approved full-program installer."""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        manager: ProgramUpdateManager,
        release: ProgramRelease,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.release = release

    def run(self) -> None:
        try:
            result = self.manager.download_and_verify(self.release)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.completed.emit(result)

