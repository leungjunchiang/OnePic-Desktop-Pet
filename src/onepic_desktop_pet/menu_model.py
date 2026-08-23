"""Shared menu model for the pet window, tray icon, and macOS status item.

The menu is deliberately represented as small, platform-neutral specifications.
Windows and Qt render the same specs as ``QMenu`` actions, while the macOS
status item can render them as ``NSMenu`` items without maintaining another
command list. The macOS Dock menu is intentionally owned by macOS itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping


MenuCallback = Callable[[bool], object]


@dataclass(frozen=True)
class MenuItemSpec:
    """One menu item, including an optional nested submenu."""

    title: str = ""
    command: str | None = None
    children: tuple["MenuItemSpec", ...] = ()
    enabled: bool = True
    checkable: bool = False
    checked: bool = False
    separator: bool = False

    @classmethod
    def divider(cls) -> "MenuItemSpec":
        return cls(separator=True)


class UnifiedMenuModel:
    """Build and dispatch the single menu definition used by all entrances."""

    def __init__(
        self,
        *,
        pet_name: str,
        state_provider: Callable[[], Mapping[str, object]],
        callbacks: Mapping[str, MenuCallback],
    ) -> None:
        self.pet_name = pet_name.strip() or "六毛"
        self._state_provider = state_provider
        self._callbacks = dict(callbacks)

    def execute(self, command: str | None, checked: bool = False) -> None:
        """Run a command from either a Qt action or a native menu item."""

        if command is None:
            return
        callback = self._callbacks.get(command)
        if callback is not None:
            callback(bool(checked))

    def _optional(self, title: str, command: str) -> MenuItemSpec | None:
        if command not in self._callbacks:
            return None
        return MenuItemSpec(title, command)

    def items(self, context: str = "pet") -> tuple[MenuItemSpec, ...]:
        """Return the same app menu for every Lili entry point.

        ``context`` remains available for callers and diagnostics. The menu
        is deliberately platform-neutral: the pet, Windows tray, and macOS
        status item all expose the same commands in the same order.
        """

        state = dict(self._state_provider())
        work_status = str(state.get("work_status") or "idle")
        visible = bool(state.get("visible", True))
        music_children = [
            MenuItemSpec("播放 / 暂停", "music_toggle"),
            MenuItemSpec("上一首", "music_previous"),
            MenuItemSpec("下一首", "music_next"),
            MenuItemSpec("随机听陈楚生", "music_random"),
        ]

        interaction_children = [
            MenuItemSpec("给我一个抱抱", "companion_love"),
            MenuItemSpec("为我加油", "companion_encourage"),
            MenuItemSpec("提醒我休息", "companion_rest"),
            MenuItemSpec("查看心情与能量", "companion_status"),
        ]
        interaction_children = [
            item for item in interaction_children if item.command in self._callbacks
        ]

        todo_children = [
            self._optional("显示待办", "show_todos"),
            self._optional("隐藏待办", "hide_todos"),
            self._optional("添加待办…", "add_todo"),
        ]
        todo_children = [item for item in todo_children if item is not None]

        work_record_children = [
            self._optional("工作报告…", "show_report"),
            self._optional("我的时光…", "time_memory"),
            self._optional("查看今日累计", "show_work_time"),
            self._optional("查看今日成长", "show_growth"),
            self._optional("六毛钱包与工资条…", "economy"),
            self._optional(f"打开{self.pet_name}相册", "open_album"),
        ]
        work_record_children = [item for item in work_record_children if item is not None]

        update_children = [
            self._optional("检查补充内容更新", "content_update"),
            self._optional("更新到最新版本…", "program_update"),
            MenuItemSpec(
                f"当前程序版本：{str(state.get('program_version') or '未知')}",
                enabled=False,
            ),
            MenuItemSpec(
                f"当前内容版本：{str(state.get('content_version') or '内置内容')}",
                enabled=False,
            ),
        ]
        update_children = [item for item in update_children if item is not None]
        duration_item = (
            MenuItemSpec(
                "显示本轮工作时长",
                "show_work_duration",
                checkable=True,
                checked=bool(state.get("show_work_duration", True)),
            )
            if "show_work_duration" in self._callbacks
            else None
        )
        shortcut_children = [
            self._optional("六毛快捷口袋…", "quick_panel"),
            self._optional("调整大小…", "size"),
            self._optional("六毛闹钟…", "alarms"),
            self._optional("主人称呼…", "rename"),
            duration_item,
            MenuItemSpec("更新与关于", children=tuple(update_children)),
        ]
        shortcut_children = [item for item in shortcut_children if item is not None]

        work_entries: list[MenuItemSpec] = []
        if work_status == "focus":
            work_entries.extend(
                (
                    MenuItemSpec(
                        str(state.get("work_status_text") or "⏱ 正在工作"),
                        enabled=False,
                    ),
                    MenuItemSpec("暂停工作", "work_pause"),
                    MenuItemSpec("结束本轮工作", "work_finish"),
                )
            )
        elif work_status == "rest":
            work_entries.extend(
                (
                    MenuItemSpec(
                        str(state.get("work_status_text") or "⏱ 工作已暂停"),
                        enabled=False,
                    ),
                    MenuItemSpec("继续工作", "work_resume"),
                    MenuItemSpec("结束本轮工作", "work_finish"),
                )
            )
        else:
            work_entries.append(MenuItemSpec("开始工作", "work"))

        display_mode_children = (
            MenuItemSpec(
                "始终置顶",
                "topmost_on",
                checkable=True,
                checked=bool(state.get("always_on_top", False)),
            ),
            MenuItemSpec(
                "桌面模式",
                "topmost_off",
                checkable=True,
                checked=not bool(state.get("always_on_top", False)),
            ),
        )

        entries: list[MenuItemSpec] = [
            MenuItemSpec(f"和{self.pet_name}聊聊…", "chat"),
        ]
        entries.extend(work_entries)

        entries.append(MenuItemSpec.divider())
        entries.extend(
            (
                MenuItemSpec("搭子自习室…", "social"),
                MenuItemSpec("音乐", children=tuple(music_children)),
            )
        )
        entries.append(MenuItemSpec.divider())
        if todo_children:
            entries.append(MenuItemSpec("待办", children=tuple(todo_children)))
        if work_record_children:
            entries.append(MenuItemSpec("工作记录", children=tuple(work_record_children)))
        if shortcut_children:
            entries.append(MenuItemSpec("快捷工具", children=tuple(shortcut_children)))
        if interaction_children:
            entries.append(MenuItemSpec("六毛互动", children=tuple(interaction_children)))

        entries.append(MenuItemSpec.divider())
        if "outfit" in self._callbacks:
            entries.append(MenuItemSpec("换装与外观…", "outfit"))
        entries.extend(
            (
                MenuItemSpec("显示模式", children=display_mode_children),
                MenuItemSpec("设置…", "settings"),
                MenuItemSpec.divider(),
                MenuItemSpec("隐藏六毛" if visible else "显示六毛", "visibility"),
                MenuItemSpec("退出六毛", "quit"),
            )
        )
        return tuple(entries)


def populate_qmenu(menu, model: UnifiedMenuModel, context: str = "pet") -> None:
    """Render a :class:`UnifiedMenuModel` into a fresh Qt menu."""

    from PySide6.QtGui import QActionGroup
    from PySide6.QtWidgets import QMenu

    def add_items(target: QMenu, items: tuple[MenuItemSpec, ...]) -> None:
        checkable_actions = []
        for spec in items:
            if spec.separator:
                target.addSeparator()
                continue
            if spec.children:
                submenu = target.addMenu(spec.title)
                submenu.setEnabled(spec.enabled)
                add_items(submenu, spec.children)
                continue
            action = target.addAction(spec.title)
            action.setEnabled(spec.enabled)
            action.setCheckable(spec.checkable)
            action.setChecked(spec.checked)
            if spec.checkable:
                checkable_actions.append(action)
            action.triggered.connect(
                lambda checked=False, command=spec.command: model.execute(command, checked)
            )
        if checkable_actions:
            group = QActionGroup(target)
            group.setExclusive(True)
            for action in checkable_actions:
                group.addAction(action)

    add_items(menu, model.items(context))
