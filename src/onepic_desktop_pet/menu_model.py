"""Shared menu model for the pet window, tray icon, and macOS Dock.

The menu is deliberately represented as small, platform-neutral specifications.
Windows and Qt render the same specs as ``QMenu`` actions, while macOS can
render them as ``NSMenu`` items without maintaining another command list.
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
        """Return the current menu projection for ``pet``, ``tray`` or ``dock``."""

        state = dict(self._state_provider())
        work_label = str(state.get("work_action_label") or "开始工作")
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
            self._optional("我的时光…", "time_memory"),
            self._optional("查看今日累计", "show_work_time"),
            self._optional("查看今日成长", "show_growth"),
            self._optional("查看陪伴报告", "show_report"),
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
        settings_children = [
            self._optional("主人称呼", "rename"),
            self._optional("AI 与陪伴", "settings"),
            self._optional("调整大小", "size"),
            MenuItemSpec("显示本轮工作时长", "show_work_duration", checkable=True, checked=bool(state.get("show_work_duration", True))),
            self._optional("提醒与报时", "settings"),
            self._optional("音乐设置", "settings"),
            self._optional("自习室设置", "settings"),
            MenuItemSpec("更新与关于", children=tuple(update_children)),
        ]
        settings_children = [item for item in settings_children if item is not None]

        if work_status == "focus":
            work_item = MenuItemSpec(
                work_label,
                children=(
                    MenuItemSpec("暂停工作", "work_pause"),
                    MenuItemSpec("结束工作", "work_finish"),
                ),
            )
        elif work_status == "rest":
            work_item = MenuItemSpec(
                work_label,
                children=(
                    MenuItemSpec("继续工作", "work_resume"),
                    MenuItemSpec("结束工作", "work_finish"),
                ),
            )
        else:
            work_item = MenuItemSpec(work_label, "work")

        entries: list[MenuItemSpec] = [
            MenuItemSpec(f"和{self.pet_name}聊聊…", "chat"),
            work_item,
            MenuItemSpec("搭子自习室…", "social"),
            MenuItemSpec("音乐", children=tuple(music_children)),
        ]
        if interaction_children:
            entries.append(MenuItemSpec("六毛互动", children=tuple(interaction_children)))

        # Separate high-frequency actions from history, outfit and todo
        # surfaces before the settings/system section.
        entries.append(MenuItemSpec.divider())
        if "outfit" in self._callbacks:
            entries.append(MenuItemSpec("换装与外观…", "outfit"))
        if work_record_children:
            entries.append(MenuItemSpec("工作记录", children=tuple(work_record_children)))
        if todo_children:
            entries.append(MenuItemSpec("待办", children=tuple(todo_children)))
        if "quick_panel" in self._callbacks:
            entries.append(MenuItemSpec("六毛快捷口袋", "quick_panel"))

        entries.extend(
            (
                MenuItemSpec.divider(),
            )
        )
        entries.append(MenuItemSpec("设置", children=tuple(settings_children)) if settings_children else MenuItemSpec("设置", "settings"))
        entries.append(
            MenuItemSpec(
                "始终置顶（关闭即桌面模式）",
                "topmost",
                checkable=True,
                checked=bool(state.get("always_on_top", False)),
            )
        )
        entries.extend(
            (
                MenuItemSpec.divider(),
                MenuItemSpec("隐藏六毛" if visible else "显示六毛", "visibility"),
                MenuItemSpec("退出", "quit"),
            )
        )
        return tuple(entries)


def populate_qmenu(menu, model: UnifiedMenuModel, context: str = "pet") -> None:
    """Render a :class:`UnifiedMenuModel` into a fresh Qt menu."""

    from PySide6.QtWidgets import QMenu

    def add_items(target: QMenu, items: tuple[MenuItemSpec, ...]) -> None:
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
            action.triggered.connect(
                lambda checked=False, command=spec.command: model.execute(command, checked)
            )

    add_items(menu, model.items(context))

