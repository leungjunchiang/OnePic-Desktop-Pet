"""
本模块管理 Lili 应用生命周期、精简系统托盘菜单和退出时的位置保存。

职责范围：
- 创建或复用 QApplication；
- 在创建应用前启用适合不同显示器缩放比例的高 DPI 舍入策略；
- 创建 PetWindow 和精简系统状态入口；macOS 使用原生状态栏图标，其他平台使用 QSystemTrayIcon；
- 托盘只保留显示、快捷口袋、聊天、设置与退出，主要互动直接在六毛窗口完成；
- 托盘设置动作显式标记为 ``user_action``，其他来源无法创建连接与陪伴窗口；
- 托盘提供“始终置顶/桌面模式”开关，并与宠物右键菜单和设置页保持同步；
- 退出前将窗口位置和用户选择的尺寸写入设置文件；
- 为自动验证提供定时退出的 smoke-test 参数。
- 程序更新只允许用户从托盘或设置页手动触发；启动时不联网检查、不启动安装器、不退出主程序。
- 启动时使用与桌面待办小窗相同的近期待办投影，只在有未读待办时自动展示；
- 启动时先创建每用户应用数据目录，再建立 QLockFile，避免首次启动被误判为已有实例。

Agent 快速定位：
- 生命周期封装位于 DesktopPetApplication；
- 托盘菜单构建位于 _create_tray()；
- 持久化与退出位于 quit()；
- 外部调用入口位于 run()。

输入为可选的无界面冒烟测试时长，输出为 Qt 事件循环退出码。
副作用包括创建桌面窗口、托盘图标和用户设置文件；不修改项目默认配置或原始素材。
程序入口使用每用户 QLockFile，确保一个进程只拥有一个真实桌宠窗口。
"""

from __future__ import annotations

import os
import logging
import sys
from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import QLockFile, QProcess, Qt, QTimer, QObject
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QMessageBox,
    QProgressDialog,
    QSystemTrayIcon,
)

from . import __version__
from .config import PET_NAME, PetSettings, load_settings, save_settings
from .local_data import platform_app_data_root
from .companion import APP_DISPLAY_NAME
from .compact_todo import compact_todo_candidates
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
from .macos_dock import install_dock_menu, install_status_item
from .window import PetWindow


LOGGER = logging.getLogger(__name__)


def _uses_qt_system_tray() -> bool:
    """Return whether Qt should create the platform tray icon.

    macOS has a native ``NSStatusItem`` installed below. Creating a second
    ``QSystemTrayIcon`` there produces two Lili menu-bar entries and one can
    appear restricted by macOS while the other remains usable.
    """

    return sys.platform != "darwin"


class DesktopPetApplication(QObject):
    """封装窗口、托盘与持久化状态的桌面宠物应用。"""

    # QLockFile protects separate processes.  This second, process-local
    # guard protects against a launcher/reconnect path constructing the app
    # controller twice before Qt's event loop starts.
    _active_instance: ClassVar["DesktopPetApplication | None"] = None

    def __init__(
        self,
        settings: PetSettings | None = None,
        *,
        instance_lock: QLockFile | None = None,
    ) -> None:
        if type(self)._active_instance is not None:
            raise RuntimeError("Lili application ownership already exists")
        if QApplication.instance() is None:
            QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
            )
        self.qt_app = QApplication.instance() or QApplication(sys.argv)
        # Keep this controller in the GUI thread. Worker callbacks are
        # connected to methods on this object; QObject affinity makes Qt queue
        # those callbacks back to the main thread before they touch windows,
        # message boxes, or speech bubbles.
        super().__init__()
        self._instance_lock = instance_lock
        self.qt_app.setApplicationName(APP_DISPLAY_NAME)
        self.qt_app.setApplicationDisplayName(APP_DISPLAY_NAME)
        self.qt_app.setQuitOnLastWindowClosed(False)
        self.settings = settings or load_settings()
        self.window = PetWindow(self.settings)
        type(self)._active_instance = self
        self.window.quit_requested.connect(self.quit)
        self.update_manager = UpdateManager()
        self._content_update_worker: ContentUpdateWorker | None = None
        self._content_update_manual = False
        self._program_update_check_worker: ProgramUpdateCheckWorker | None = None
        self._program_update_download_worker: ProgramUpdateDownloadWorker | None = None
        self._program_update_progress: QProgressDialog | None = None
        self._program_update_manual = False
        self._program_release: ProgramRelease | None = None
        self._quit_started = False
        self.program_update_state = UpdateState.IDLE
        self.window.set_menu_external_callbacks(
            {
                "content_update": lambda _checked=False: self.check_content_updates(True),
                "program_update": lambda _checked=False: self.check_program_updates(True),
                "quit": lambda _checked=False: self.quit(),
            }
        )
        self.tray: QSystemTrayIcon | None = (
            self._create_tray() if _uses_qt_system_tray() else None
        )
        if self.tray is None:
            self.tray_menu = None
        self._dock_controller = install_dock_menu(self.window.unified_menu_model)
        self._status_item_controller = install_status_item(self.window.unified_menu_model)
        self.window.owner_nickname_changed.connect(self._owner_nickname_changed)

    def _create_tray(self) -> QSystemTrayIcon:
        """创建系统托盘图标及其操作菜单。"""

        icon = QIcon(str(resource_path("assets/icons/pet.png")))
        tray = QSystemTrayIcon(icon, self.qt_app)
        pet_name = PET_NAME
        tray.setToolTip(f"Lili · {pet_name}")
        menu = self.window.build_unified_menu(None, "tray")
        tray.setContextMenu(menu)
        self.tray_menu = menu
        menu.aboutToShow.connect(self._refresh_tray_menu)
        tray.activated.connect(self._tray_activated)
        return tray

    def _refresh_tray_menu(self) -> None:
        """Re-render dynamic work, visibility, music, and topmost state."""

        if self.tray is None or self.tray_menu is None:
            return
        # Keep the same standalone menu object attached to the status item;
        # only its dynamic action tree needs refreshing.
        self.window.refresh_unified_menu(self.tray_menu, "tray")

    def _owner_nickname_changed(self, _owner_nickname: str) -> None:
        """Keep the pet identity fixed while refreshing the rename entry."""

        if self.tray is not None:
            self.tray.setToolTip(f"Lili · {PET_NAME}")

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """单击或双击托盘图标时显示宠物。"""

        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_window()

    def show_window(self) -> None:
        """显示宠物但不夺走用户正在输入文字的窗口焦点。"""

        if sys.platform == "darwin":
            # Configure the NSPanel before its first show.  Showing first and
            # fixing the style in showEvent can briefly activate Lili and
            # steal a ChatGPT/Codex text field.
            self.window._apply_macos_window_behavior()
        self.window.show_pet()
        # PetWindow.showEvent schedules the one-time native macOS panel
        # configuration.  Calling it synchronously as well can make AppKit
        # re-apply the floating level during a user-initiated show and has
        # been observed to reactivate Lili on some macOS/Qt combinations.
        if sys.platform != "darwin":
            self.window._ensure_on_top()

    def start(self, smoke_test_ms: int | None = None) -> int:
        """显示应用并进入事件循环；可选定时退出用于自动验证。"""

        self.window.place_at_start()
        self.show_window()
        paper_mode = getattr(self.settings, "today_note_display_mode", "pending")
        note_style = getattr(self.settings, "today_note_mode", "compact")
        # Use the same upcoming projection as the compact strip. The old
        # ``todos.pending()`` check only looked at today's raw records and
        # could disagree with the panel for near-term/read transitions.
        has_pending_todos = bool(compact_todo_candidates(self.window.time_memory))
        should_show_paper = paper_mode == "always" or (
            paper_mode == "pending" and has_pending_todos
        ) or bool(getattr(self.settings, "today_note_autoshow", False))
        if paper_mode != "hidden" and note_style != "hidden" and should_show_paper:
            # A pending note may be shown at startup, but startup UI must
            # never steal the user's current editor/browser focus.  Explicit
            # user actions still open the normal interactive note window.
            QTimer.singleShot(300, lambda: self.window.show_today_note(passive=True))
        if self.tray is not None and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        if not self._content_updates_disabled():
            # The startup check is deliberately delayed and silent.  It only
            # fetches the manifest; changed files are downloaded in a worker.
            QTimer.singleShot(2500, lambda: self.check_content_updates(False))
        # Startup only checks release metadata.  Downloading, installing, and
        # quitting remain behind the explicit tray/settings action, so a newly
        # published Release can never make a healthy pet disappear on launch.
        if self._program_updates_enabled():
            QTimer.singleShot(5000, lambda: self.check_program_updates(False))
        if smoke_test_ms is not None:
            QTimer.singleShot(max(1, smoke_test_ms), self.quit)
        return self.qt_app.exec()

    def quit(self) -> None:
        """保存窗口位置、隐藏托盘并退出应用。"""

        if self._quit_started:
            return
        self._quit_started = True
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
            if type(self)._active_instance is self:
                type(self)._active_instance = None
            self._status_item_controller.close()
            self._dock_controller.close()
            if self.tray is not None:
                self.tray.hide()
            self.window.close()
            if self._instance_lock is not None and self._instance_lock.isLocked():
                self._instance_lock.unlock()
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
        if not self._program_updates_enabled():
            if manual:
                self.window.show_speech("程序更新已关闭；需要时可在设置中重新启用。", 3200)
            return
        self.program_update_state = UpdateState.CHECKING
        if manual:
            self.window.show_speech("正在检查程序更新…", 2400)
        LOGGER.info("[Update] check_app_update started")
        try:
            worker = ProgramUpdateCheckWorker(
                self.update_manager,
                self.qt_app,
                force=bool(manual),
            )
        except Exception as exc:
            # A constructor/runtime mismatch must not leave the UI stuck in
            # CHECKING forever without an error or a retry path.
            LOGGER.exception("[Update] failed to create program update worker")
            self._program_update_check_worker = None
            self._program_update_check_failed(str(exc))
            return
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
        if not self._program_update_manual:
            # Startup checks are informational only.  Showing a modal question
            # with a default Yes made an unattended launch look like a random
            # exit when the user accepted it accidentally or a window manager
            # delivered the default button key.  Updating remains an explicit
            # tray/menu action and therefore cannot interrupt fullscreen work.
            self.window.show_speech(
                f"发现新版本 Lili {release.version}，需要时可从托盘‘更新与关于’手动更新。",
                6200,
            )
            return
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
        self._show_program_download_progress(release)
        worker = ProgramUpdateDownloadWorker(self.update_manager, release, self.qt_app)
        self._program_update_download_worker = worker
        worker.completed.connect(self._program_update_downloaded)
        worker.failed.connect(self._program_update_download_failed)
        worker.progress.connect(self._program_download_progress_changed)
        worker.finished.connect(self._program_update_download_finished)
        worker.start()

    def _show_program_download_progress(self, release: ProgramRelease) -> None:
        """Show a real byte-progress dialog while the installer is downloading."""

        if self._program_update_progress is not None:
            self._program_update_progress.close()
            self._program_update_progress.deleteLater()
        progress = QProgressDialog(
            f"正在下载 Lili {release.version}…",
            "",
            0,
            100,
            self.window,
        )
        progress.setWindowTitle("下载程序更新")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setCancelButton(None)
        progress.setValue(0)
        progress.show()
        self._program_update_progress = progress

    def _program_download_progress_changed(self, downloaded: int, total: int) -> None:
        progress = self._program_update_progress
        if progress is None:
            return
        downloaded = max(0, int(downloaded))
        total = max(0, int(total))
        if total <= 0:
            progress.setRange(0, 0)
            progress.setLabelText("正在下载程序更新…（大小获取中）")
            return
        progress.setRange(0, 100)
        percent = min(100, max(0, int(downloaded * 100 / total)))
        downloaded_mb = downloaded / 1024 / 1024
        total_mb = total / 1024 / 1024
        progress.setValue(percent)
        progress.setLabelText(
            f"正在下载程序更新… {percent}%\n"
            f"已下载 {downloaded_mb:.1f} / {total_mb:.1f} MB"
        )

    def _close_program_download_progress(self) -> None:
        progress = self._program_update_progress
        self._program_update_progress = None
        if progress is not None:
            progress.close()
            progress.deleteLater()

    def _program_update_download_finished(self) -> None:
        worker = self._program_update_download_worker
        self._program_update_download_worker = None
        # Defensive cleanup for an unexpected thread termination; normal
        # success/failure paths close the dialog after verification.
        if worker is not None and self._program_update_progress is not None:
            self._close_program_download_progress()
        if worker is not None:
            worker.deleteLater()

    def _program_update_download_failed(self, message: str) -> None:
        self._close_program_download_progress()
        self.program_update_state = UpdateState.ERROR
        QMessageBox.warning(
            self.window,
            "程序更新失败",
            "安装包下载或校验失败，没有改动当前程序。\n"
            f"原因：{message or '未知错误'}",
        )

    def _program_update_downloaded(self, result: object) -> None:
        self._close_program_download_progress()
        if not isinstance(result, ProgramUpdateResult):
            self.program_update_state = UpdateState.ERROR
            self.window.show_speech("更新包无效，没有改动当前程序。", 4200)
            return
        # This is a defense-in-depth guard.  The startup path no longer
        # checks program releases, and only a user-confirmed manual action may
        # ever reach the installer.  If a stale worker callback arrives after
        # a lifecycle change, keep Lili running instead of launching or
        # scheduling a quit unexpectedly.
        if not self._program_update_manual:
            self.program_update_state = UpdateState.ERROR
            LOGGER.warning("[Update] refusing installer launch from non-manual check")
            self.window.show_speech("检测到新版本，但不会自动安装；请从托盘手动更新。", 4200)
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

    lock_path = _instance_lock_path()
    instance_lock = QLockFile(str(lock_path))
    instance_lock.setStaleLockTime(0)
    if not instance_lock.tryLock(100):
        LOGGER.warning("Lili 已有运行中的应用实例，忽略重复启动：lock=%s", lock_path)
        return 0
    try:
        return DesktopPetApplication(instance_lock=instance_lock).start(
            smoke_test_ms=smoke_test_ms
        )
    except Exception:
        if instance_lock.isLocked():
            instance_lock.unlock()
        raise


def _instance_lock_path() -> Path:
    """Return a writable per-user lock path and create its parent directory."""

    lock_path = platform_app_data_root() / "Lili" / "app.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    return lock_path
