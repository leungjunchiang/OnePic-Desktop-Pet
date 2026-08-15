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

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .config import PET_NAME, PetSettings, load_settings, save_settings
from .companion import APP_DISPLAY_NAME
from .resources import resource_path
from .window import PetWindow


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

        paper_action = QAction("今日小纸条…", menu)
        paper_action.triggered.connect(self.window.show_today_note)
        menu.addAction(paper_action)

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
        should_show_paper = paper_mode == "always" or (
            paper_mode == "pending" and bool(self.window.time_memory.todos.pending())
        ) or bool(getattr(self.settings, "today_note_autoshow", False))
        if paper_mode != "hidden" and should_show_paper:
            QTimer.singleShot(300, self.window.show_today_note)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        if smoke_test_ms is not None:
            QTimer.singleShot(max(1, smoke_test_ms), self.quit)
        return self.qt_app.exec()

    def quit(self) -> None:
        """保存窗口位置、隐藏托盘并退出应用。"""

        self.settings.start_x = self.window.x()
        self.settings.start_y = self.window.y()
        try:
            self.window.shutdown_work_timer()
            save_settings(self.settings)
        finally:
            self.tray.hide()
            self.window.close()
            self.qt_app.quit()


def run(smoke_test_ms: int | None = None) -> int:
    """创建并运行桌面宠物应用。"""

    return DesktopPetApplication().start(smoke_test_ms=smoke_test_ms)
