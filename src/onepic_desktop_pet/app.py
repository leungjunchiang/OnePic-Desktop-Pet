"""
本模块管理 Lili 应用生命周期、精简系统托盘菜单和退出时的位置保存。

职责范围：
- 创建或复用 QApplication；
- 在创建应用前启用适合不同显示器缩放比例的高 DPI 舍入策略；
- 创建 PetWindow 和精简 QSystemTrayIcon；
- 托盘只保留显示、快捷口袋、聊天、设置与退出，主要互动直接在六毛窗口完成；
- 托盘设置动作显式标记为 ``user_action``，其他来源无法创建连接与陪伴窗口；
- 托盘提供“始终置顶/桌面模式”开关，并与宠物右键菜单和设置页保持同步；
- 退出前将窗口位置和用户选择的尺寸写入设置文件；
- 为自动验证提供定时退出的 smoke-test 参数。

Agent 快速定位：
- 生命周期封装位于 DesktopPetApplication；
- 托盘菜单构建位于 _create_tray()；
- 持久化与退出位于 quit()；
- 外部调用入口位于 run()。

输入为可选的无界面冒烟测试时长，输出为 Qt 事件循环退出码。
副作用包括创建桌面窗口、托盘图标和用户设置文件；不修改项目默认配置或原始素材。
"""

from __future__ import annotations

import os
import logging
import sys

from PySide6.QtCore import QProcess, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import __version__
from .config import PET_NAME, PetSettings, load_settings, save_settings
from .companion import APP_DISPLAY_NAME
from .content_updates import (
    ContentUpdateResult,
    reload_runtime_content,
)
from .program_updates import (
    ProgramRelease,
    ProgramUpdateCheckResult,
    ProgramUpdateResult,
    UpdateState,
)
from .resources import resource_path
from .update_worker import (
    ContentUpdateWorker,
    ProgramUpdateCheckWorker,
    ProgramUpdateDownloadWorker,
)
from .update_manager import UpdateManager
from .window import PetWindow


LOGGER = logging.getLogger(__name__)


class DesktopPetApplication:
    """封装窗口、托盘与持久化状态的桌面宠物应用。"""

    def __init__(self, settings: PetSettings | None = None) -> None:
        if QApplication.instance() is None:
            QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
            )
        self.qt_app = QApplication.instance() or QApplication(sys.argv)
        self.qt_app.setApplicationName(APP_DISPLAY_NAME)
        self.qt_app.setApplicationDisplayName(APP_DISPLAY_NAME)
        self.qt_app.setQuitOnLastWindowClosed(False)
        self.settings = settings or load_settings()
        self.window = PetWindow(self.settings)
        self.window.quit_requested.connect(self.quit)
        self.update_manager = UpdateManager()
        self._content_update_worker: ContentUpdateWorker | None = None
        self._content_update_manual = False
        self._program_update_check_worker: ProgramUpdateCheckWorker | None = None
        self._program_update_download_worker: ProgramUpdateDownloadWorker | None = None
        self._program_update_manual = False
        self._program_release: ProgramRelease | None = None
        self.program_update_state = UpdateState.IDLE
        self.tray = self._create_tray()
        self.window.owner_nickname_changed.connect(self._owner_nickname_changed)

    def _create_tray(self) -> QSystemTrayIcon:
        """创建系统托盘图标及其操作菜单。"""

        icon = QIcon(str(resource_path("assets/icons/pet.png")))
        tray = QSystemTrayIcon(icon, self.qt_app)
        pet_name = PET_NAME
        tray.setToolTip(f"Lili · {pet_name}")
        menu = QMenu()

        show_action = QAction("显示宠物", menu)
        show_action.triggered.connect(self.show_window)
        menu.addAction(show_action)

        panel_action = QAction(f"{pet_name}快捷口袋", menu)
        panel_action.triggered.connect(self.window.show_quick_panel)
        menu.addAction(panel_action)

        dialogue_action = QAction(f"和{pet_name}聊聊…", menu)
        dialogue_action.triggered.connect(self.window.prompt_dialogue)
        menu.addAction(dialogue_action)

        rename_action = QAction("修改主人称呼…", menu)
        rename_action.triggered.connect(self.window.rename_pet)
        menu.addAction(rename_action)

        social_action = QAction("搭子与自习室…", menu)
        social_action.triggered.connect(self.window.open_social_hub)
        menu.addAction(social_action)

        todo_action = QAction("待办…", menu)
        todo_action.triggered.connect(self.window.show_compact_todos)
        menu.addAction(todo_action)

        update_action = QAction("检查补充内容更新", menu)
        # QAction.triggered emits a checked bool.  Do not pass that bool as
        # the ``manual`` argument: a tray click is always an explicit user
        # request and must show progress/result feedback.
        update_action.triggered.connect(
            lambda _checked=False: self.check_content_updates(True)
        )
        menu.addAction(update_action)

        program_update_action = QAction("检查程序更新", menu)
        program_update_action.triggered.connect(
            lambda _checked=False: self.check_program_updates(True)
        )
        menu.addAction(program_update_action)

        ai_settings_action = QAction("AI 与陪伴设置…", menu)
        ai_settings_action.triggered.connect(
            lambda _checked=False: self.window.open_settings("user_action")
        )
        menu.addAction(ai_settings_action)

        topmost_action = QAction("始终置顶（关闭即桌面模式）", menu)
        topmost_action.setCheckable(True)
        topmost_action.setChecked(self.settings.always_on_top)
        topmost_action.toggled.connect(self.window.set_always_on_top)
        self.window.always_on_top_changed.connect(topmost_action.setChecked)
        menu.addAction(topmost_action)
        self.topmost_action = topmost_action

        hide_action = QAction("隐藏宠物", menu)
        hide_action.triggered.connect(self.window.hide)
        menu.addAction(hide_action)
        menu.addSeparator()

        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)

        tray.setContextMenu(menu)
        self.tray_menu = menu
        self.panel_action = panel_action
        self.dialogue_action = dialogue_action
        self.rename_action = rename_action
        self.update_action = update_action
        self.program_update_action = program_update_action
        tray.activated.connect(self._tray_activated)
        return tray

    def _owner_nickname_changed(self, _owner_nickname: str) -> None:
        """Keep the pet identity fixed while refreshing the rename entry."""

        self.tray.setToolTip(f"Lili · {PET_NAME}")
        self.panel_action.setText(f"{PET_NAME}快捷口袋")
        self.dialogue_action.setText(f"和{PET_NAME}聊聊…")
        self.rename_action.setText("修改主人称呼…")

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """单击或双击托盘图标时显示宠物。"""

        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_window()

    def show_window(self) -> None:
        """显示宠物但不夺走用户正在输入文字的窗口焦点。"""

        self.window.show()
        self.window._ensure_on_top()

    def start(self, smoke_test_ms: int | None = None) -> int:
        """显示应用并进入事件循环；可选定时退出用于自动验证。"""

        self.window.place_at_start()
        self.show_window()
        paper_mode = getattr(self.settings, "today_note_display_mode", "pending")
        note_style = getattr(self.settings, "today_note_mode", "detailed")
        should_show_paper = paper_mode == "always" or (
            paper_mode == "pending" and bool(self.window.time_memory.todos.pending())
        ) or bool(getattr(self.settings, "today_note_autoshow", False))
        if paper_mode != "hidden" and note_style != "hidden" and should_show_paper:
            QTimer.singleShot(300, self.window.show_today_note)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        if not self._content_updates_disabled():
            # The startup check is deliberately delayed and silent.  It only
            # fetches the manifest; changed files are downloaded in a worker.
            QTimer.singleShot(2500, lambda: self.check_content_updates(False))
        if self._program_updates_enabled():
            # The program check is metadata-only.  A download and installer
            # launch happen only after the user confirms the discovered release.
            QTimer.singleShot(5000, lambda: self.check_program_updates(False))
        if smoke_test_ms is not None:
            QTimer.singleShot(max(1, smoke_test_ms), self.quit)
        return self.qt_app.exec()

    def quit(self) -> None:
        """保存窗口位置、隐藏托盘并退出应用。"""

        self.settings.start_x = self.window.x()
        self.settings.start_y = self.window.y()
        try:
            if self._content_update_worker is not None and self._content_update_worker.isRunning():
                self._content_update_worker.requestInterruption()
                self._content_update_worker.wait(6000)
            for worker in (
                self._program_update_check_worker,
                self._program_update_download_worker,
            ):
                if worker is not None and worker.isRunning():
                    worker.requestInterruption()
                    worker.wait(6000)
            self.window.shutdown_work_timer()
            save_settings(self.settings)
        finally:
            self.tray.hide()
            self.window.close()
            self.qt_app.quit()

    def check_content_updates(self, manual: bool = True) -> None:
        """Check only the signed-by-hash content manifest, never the EXE."""

        if self._content_update_worker is not None and self._content_update_worker.isRunning():
            return
        # The setting controls silent startup checks only.  A user clicking
        # the tray action is an explicit request and should still work.  The
        # environment switch remains a hard disable for test/admin runs.
        if os.environ.get("LILI_DISABLE_CONTENT_UPDATES", "").strip() == "1":
            return
        if (not bool(getattr(self.settings, "content_updates_enabled", True))) and not manual:
            return
        worker = ContentUpdateWorker(self.update_manager, self.qt_app)
        self._content_update_worker = worker
        self._content_update_manual = bool(manual)
        worker.completed.connect(self._content_update_completed)
        worker.failed.connect(self._content_update_failed)
        worker.finished.connect(self._content_update_finished)
        worker.start()

    def _content_updates_disabled(self) -> bool:
        return (not bool(getattr(self.settings, "content_updates_enabled", True))) or os.environ.get(
            "LILI_DISABLE_CONTENT_UPDATES", ""
        ).strip() == "1"

    def _program_updates_enabled(self) -> bool:
        return bool(getattr(self.settings, "program_updates_enabled", True)) and os.environ.get(
            "LILI_DISABLE_PROGRAM_UPDATES", ""
        ).strip() != "1"

    def check_program_updates(self, manual: bool = True) -> None:
        """Check the official GitHub installer without blocking the UI."""

        LOGGER.info("[Update] menu/worker entry: manual=%s current=%s", manual, __version__)

        if self._program_update_check_worker is not None and self._program_update_check_worker.isRunning():
            if manual:
                self.window.show_speech("程序更新正在检查中…", 2400)
            return
        if not manual and not self._program_updates_enabled():
            return
        self.program_update_state = UpdateState.CHECKING
        if manual:
            self.window.show_speech("正在检查程序更新…", 2400)
        LOGGER.info("[Update] check_app_update started")
        worker = ProgramUpdateCheckWorker(self.update_manager, self.qt_app)
        self._program_update_check_worker = worker
        self._program_update_manual = bool(manual)
        worker.completed.connect(self._program_update_checked)
        worker.failed.connect(self._program_update_check_failed)
        worker.finished.connect(self._program_update_check_finished)
        worker.start()

    def _program_update_check_finished(self) -> None:
        worker = self._program_update_check_worker
        self._program_update_check_worker = None
        if worker is not None:
            worker.deleteLater()

    def _program_update_checked(self, result: object) -> None:
        LOGGER.info("[Update] check_app_update completed result=%r", result)
        if not isinstance(result, ProgramUpdateCheckResult):
            self.program_update_state = UpdateState.ERROR
            if self._program_update_manual:
                QMessageBox.warning(
                    self.window,
                    "检查程序更新",
                    f"更新信息异常，当前程序不会被修改。\n当前版本：{__version__}",
                )
            return
        if result.release is None:
            self.program_update_state = UpdateState.UP_TO_DATE
            if self._program_update_manual:
                QMessageBox.information(
                    self.window,
                    "检查程序更新",
                    (
                        "暂未找到可用的程序发布版本。\n"
                        if result.status == "no_release"
                        else "六毛已经是最新版。\n"
                    )
                    + f"当前版本：{result.current_version}\n"
                    + f"最新版本：{result.latest_version}\n"
                    + "更新源：GitHub Releases",
                )
            return
        self.program_update_state = UpdateState.UPDATE_AVAILABLE
        release = result.release
        self._program_release = release
        size_mb = max(1, round(release.asset_size / 1024 / 1024))
        notes = [
            line.strip(" -*•\t")
            for line in release.release_notes.splitlines()
            if line.strip()
        ][:2]
        notes_text = "\n\n更新说明：\n" + "\n".join(f"• {line}" for line in notes) if notes else ""
        answer = QMessageBox.question(
            self.window,
            "发现六毛新版本",
            f"发现新版本 Lili {release.version}\n"
            f"当前版本：{result.current_version}\n"
            f"更新大小：约 {size_mb} MB{notes_text}\n\n"
            "下载后会校验安装包，再启动更新。是否现在更新？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._download_program_update(release)
        elif self._program_update_manual:
            self.window.show_speech("好，先不更新。需要时可以从托盘再次检查。", 3200)

    def _program_update_check_failed(self, message: str) -> None:
        LOGGER.warning("[Update] check_app_update failed: %s", message)
        self.program_update_state = UpdateState.ERROR
        if self._program_update_manual:
            QMessageBox.warning(
                self.window,
                "检查程序更新失败",
                "暂时无法检查程序更新。\n"
                f"当前版本：{__version__}\n"
                f"原因：{message or '无法连接更新服务器'}",
            )

    def _download_program_update(self, release: ProgramRelease) -> None:
        if self._program_update_download_worker is not None and self._program_update_download_worker.isRunning():
            return
        self.program_update_state = UpdateState.DOWNLOADING
        self.window.show_speech(f"正在下载 Lili {release.version}，校验后再安装。", 4200)
        worker = ProgramUpdateDownloadWorker(self.update_manager, release, self.qt_app)
        self._program_update_download_worker = worker
        worker.completed.connect(self._program_update_downloaded)
        worker.failed.connect(self._program_update_download_failed)
        worker.finished.connect(self._program_update_download_finished)
        worker.start()

    def _program_update_download_finished(self) -> None:
        worker = self._program_update_download_worker
        self._program_update_download_worker = None
        if worker is not None:
            worker.deleteLater()

    def _program_update_download_failed(self, message: str) -> None:
        self.program_update_state = UpdateState.ERROR
        QMessageBox.warning(
            self.window,
            "程序更新失败",
            "安装包下载或校验失败，没有改动当前程序。\n"
            f"原因：{message or '未知错误'}",
        )

    def _program_update_downloaded(self, result: object) -> None:
        if not isinstance(result, ProgramUpdateResult):
            self.program_update_state = UpdateState.ERROR
            self.window.show_speech("更新包无效，没有改动当前程序。", 4200)
            return
        self.program_update_state = UpdateState.READY_TO_INSTALL
        installer = str(result.installer_path)
        if sys.platform == "win32":
            started = QProcess.startDetached(
                installer,
                ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
            )
        elif sys.platform == "darwin":
            started = QProcess.startDetached("open", [installer])
        else:
            started = False
        if not started:
            self.program_update_state = UpdateState.ERROR
            self.window.show_speech("更新包已下载，但无法自动打开安装程序。", 4200)
            return
        self.program_update_state = UpdateState.INSTALLING
        self.window.show_speech("更新程序已启动，六毛先重启一下。", 3000)
        QTimer.singleShot(500, self.quit)

    def _content_update_finished(self) -> None:
        worker = self._content_update_worker
        self._content_update_worker = None
        if worker is not None:
            worker.deleteLater()

    def _content_update_completed(self, result: object) -> None:
        manual = self._content_update_manual
        if not isinstance(result, ContentUpdateResult):
            if manual:
                self.window.show_speech("现在没有新的补充内容。", 3000)
            return
        try:
            reload_runtime_content()
            self.window._pixmaps = self.window._load_pixmaps()
            self.window._render_cache.clear()
            self.window._mask_cache.clear()
            self.window._refresh_pixmap()
        except Exception:
            # A content patch is still valid even if a currently displayed
            # optional asset cannot be reloaded until the next restart.
            pass
        if manual:
            self.window.show_speech(
                f"补充了 {len(result.updated_files)} 个内容文件。", 3600
            )

    def _content_update_failed(self, message: str) -> None:
        # Startup checks are intentionally quiet for offline users.  Manual
        # checks provide a useful, non-technical status bubble.
        if self._content_update_manual:
            self.window.show_speech("补充内容暂时没连上，稍后再试。", 3600)


def run(smoke_test_ms: int | None = None) -> int:
    """创建并运行桌面宠物应用。"""

    return DesktopPetApplication().start(smoke_test_ms=smoke_test_ms)
