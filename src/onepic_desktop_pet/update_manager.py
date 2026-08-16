"""Single entry point for content and full-program update services."""

from __future__ import annotations

from .content_updates import ContentUpdateManager, ContentUpdateResult
from .program_updates import (
    ProgramRelease,
    ProgramUpdateCheckResult,
    ProgramUpdateManager,
    ProgramUpdateResult,
)


class UpdateManager:
    """Keep update sources separate while giving the UI one service owner.

    Content patches may be applied while Lili is running.  Program packages
    always go through the full-program manager and require a restart.  The
    class deliberately contains no UI code, so tray actions and settings do
    not need to know which network source is being used.
    """

    def __init__(
        self,
        *,
        content: ContentUpdateManager | None = None,
        program: ProgramUpdateManager | None = None,
    ) -> None:
        self.content = content or ContentUpdateManager()
        self.program = program or ProgramUpdateManager()

    def check_content_update(self) -> ContentUpdateResult | None:
        return self.content.check_and_apply()

    def check_app_update(self) -> ProgramUpdateCheckResult:
        return self.program.check_latest()

    def download_app_update(self, release: ProgramRelease) -> ProgramUpdateResult:
        return self.program.download_and_verify(release)
