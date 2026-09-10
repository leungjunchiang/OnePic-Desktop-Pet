"""Qt worker for the optional content-only online updater."""

from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal

from .program_updates import ProgramRelease
from .update_manager import UpdateManager


LOGGER = logging.getLogger(__name__)


class ContentUpdateWorker(QThread):
    """Keep manifest and file I/O off the GUI thread."""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, manager: UpdateManager, parent=None, *, force: bool = False) -> None:
        super().__init__(parent)
        self.manager = manager
        self.force = bool(force)

    def run(self) -> None:
        LOGGER.info("[Update] content check started")
        try:
            result = self.manager.check_content_update()
        except Exception as exc:  # convert network/validation errors to UI data
            LOGGER.warning("[Update] content check failed: %s", exc)
            self.failed.emit(str(exc))
        else:
            LOGGER.info("[Update] content check completed: %r", result)
            self.completed.emit(result)


class ProgramUpdateCheckWorker(QThread):
    """Check the latest installer without blocking the pet UI."""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, manager: UpdateManager, parent=None, *, force: bool = False) -> None:
        super().__init__(parent)
        self.manager = manager
        self.force = bool(force)

    def run(self) -> None:
        LOGGER.info("[Update] GitHub release request started")
        try:
            result = self.manager.check_app_update(force=self.force)
        except Exception as exc:
            LOGGER.warning("[Update] GitHub release request failed: %s", exc)
            self.failed.emit(str(exc))
        else:
            LOGGER.info("[Update] GitHub release request completed: %r", result)
            self.completed.emit(result)


class ProgramUpdateDownloadWorker(QThread):
    """Download and verify the user-approved full-program installer."""

    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(
        self,
        manager: UpdateManager,
        release: ProgramRelease,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.release = release

    def run(self) -> None:
        LOGGER.info("[Update] installer download started: %s", self.release.asset_name)
        try:
            result = self.manager.download_app_update(
                self.release,
                progress=self.progress.emit,
            )
        except Exception as exc:
            LOGGER.warning("[Update] installer download failed: %s", exc)
            self.failed.emit(str(exc))
        else:
            LOGGER.info("[Update] installer download completed: %r", result)
            self.completed.emit(result)
