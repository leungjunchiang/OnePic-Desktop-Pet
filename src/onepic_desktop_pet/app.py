"""
本模块管理桌面宠物应用生命周期、系统托盘菜单和退出时的位置保存。

职责范围：
- 创建或复用 QApplication；
- 在创建应用前启用适合不同显示器缩放比例的高 DPI 舍入策略；
- 创建 PetWindow 和 QSystemTrayIcon；
- 连接显示、隐藏、暂停跑动、喂食、离线对话、陪伴动作、工作计时和退出动作；
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

from .config import PetSettings, load_settings, save_settings
from .companion import APP_DISPLAY_NAME, COMPANION_ACTIONS, FOOD_OPTIONS
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

    def _create_tray(self) -> QSystemTrayIcon:
        """创建系统托盘图标及其操作菜单。"""

        icon = QIcon(str(resource_path("assets/icons/pet.png")))
        tray = QSystemTrayIcon(icon, self.qt_app)
        tray.setToolTip(APP_DISPLAY_NAME)
        menu = QMenu()

        show_action = QAction("显示宠物", menu)
        show_action.triggered.connect(self.show_window)
        menu.addAction(show_action)

        interact_action = QAction("和六毛打招呼", menu)
        interact_action.triggered.connect(self.window.trigger_interaction)
        menu.addAction(interact_action)

        dialogue_action = QAction("和六毛聊聊…", menu)
        dialogue_action.triggered.connect(self.window.prompt_dialogue)
        menu.addAction(dialogue_action)

        action_menu = menu.addMenu("六毛陪伴动作")
        for option in COMPANION_ACTIONS:
            action = QAction(option.label, menu)
            action.triggered.connect(
                lambda _checked=False, key=option.key: self.window.perform_companion_action(
                    key
                )
            )
            action_menu.addAction(action)

        work_menu = menu.addMenu("工作计时")
        start_work_action = QAction("开始/继续工作", menu)
        start_work_action.triggered.connect(self.window.start_work_timer)
        work_menu.addAction(start_work_action)
        pause_work_action = QAction("暂停计时并休息", menu)
        pause_work_action.triggered.connect(self.window.pause_work_timer)
        work_menu.addAction(pause_work_action)
        finish_work_action = QAction("完成本次工作", menu)
        finish_work_action.triggered.connect(self.window.finish_work_timer)
        work_menu.addAction(finish_work_action)
        show_work_action = QAction("查看今日累计", menu)
        show_work_action.triggered.connect(self.window.show_work_time)
        work_menu.addAction(show_work_action)

        food_menu = menu.addMenu("给六毛喂食")
        for food in FOOD_OPTIONS:
            food_action = QAction(food.label, menu)
            food_action.triggered.connect(
                lambda _checked=False, key=food.key: self.window.feed_pet(key)
            )
            food_menu.addAction(food_action)

        status_action = QAction("查看六毛状态", menu)
        status_action.triggered.connect(self.window.show_companion_status)
        menu.addAction(status_action)

        selfie_action = QAction("自拍一下", menu)
        selfie_action.triggered.connect(self.window.trigger_selfie)
        menu.addAction(selfie_action)

        pause_action = QAction("暂停/恢复跑动", menu)
        pause_action.triggered.connect(
            lambda: self.window.set_paused(not self.window.paused)
        )
        menu.addAction(pause_action)

        hide_action = QAction("隐藏宠物", menu)
        hide_action.triggered.connect(self.window.hide)
        menu.addAction(hide_action)
        menu.addSeparator()

        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)

        tray.setContextMenu(menu)
        self.tray_menu = menu
        tray.activated.connect(self._tray_activated)
        return tray

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """单击或双击托盘图标时显示宠物。"""

        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_window()

    def show_window(self) -> None:
        """显示宠物并将其提升到前台。"""

        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def start(self, smoke_test_ms: int | None = None) -> int:
        """显示应用并进入事件循环；可选定时退出用于自动验证。"""

        self.window.place_at_start()
        self.show_window()
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
