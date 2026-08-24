"""
本模块验证桌面宠物窗口的连续帧控制、表情符号、轮廓遮罩、DPI 渲染缓存、分区互动、
喂食、离线对话、陪伴动作、工作计时和自拍成片。

测试在 Qt 的离屏平台中创建真实 PetWindow，但不显示到用户桌面、不写配置文件，
也不启动系统托盘。重点检查透明区域不会形成完整矩形点击区、重复绘制能够复用缓存，
以及坐下过渡可正向停在末帧并反向回到站立帧。
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ONEPIC_USE_DEMO_ASSETS", "1")

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QScrollArea

from onepic_desktop_pet.ai import AIConnectionError, CredentialStore
from onepic_desktop_pet import __version__
from onepic_desktop_pet.behavior import PetState, StateDecision
from onepic_desktop_pet.chat_manager import AgentConnectionState
from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.emotion_effects import emotion_effect_name
from onepic_desktop_pet.window import PetWindow
from onepic_desktop_pet.chat import AISettingsDialog, ChatDialog
from onepic_desktop_pet.time_memory import TimeMemory
from onepic_desktop_pet.compact_todo import CompactTodoPanel, TodoRow
from onepic_desktop_pet.today_note import TimeMemoryWindow, TodayNoteWindow
from onepic_desktop_pet.work_timer import WorkTimerModel


def _create_window() -> tuple[QApplication, PetWindow]:
    """创建或复用离屏 Qt 应用，并返回采用默认设置的宠物窗口。"""

    app = QApplication.instance() or QApplication([])
    window = PetWindow(PetSettings())
    window.show()
    app.processEvents()
    return app, window


def test_pet_and_ambient_bubbles_never_accept_keyboard_focus() -> None:
    """桌宠周期置顶时不得抢走微信、Word 等当前输入窗口。"""

    app, window = _create_window()
    assert window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert window.speech_bubble.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert window.photo_bubble.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    for accessory in (window.quick_panel, window.work_controls, window.work_duration_bubble):
        assert accessory.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
        assert accessory.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    window.close()
    window.deleteLater()
    app.processEvents()


def test_macos_pet_does_not_poll_native_topmost_layer(monkeypatch) -> None:
    """macOS must not re-apply the native level while another app is active."""

    monkeypatch.setattr("onepic_desktop_pet.window.sys.platform", "darwin")
    app, window = _create_window()
    assert not window.topmost_timer.isActive()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_macos_accessory_raise_is_suppressed(monkeypatch) -> None:
    """macOS accessory refreshes must not reorder the owning application."""

    monkeypatch.setattr("onepic_desktop_pet.window.sys.platform", "darwin")

    class RaisingProbe:
        def __init__(self) -> None:
            self.calls = 0

        def raise_(self) -> None:
            self.calls += 1

    probe = RaisingProbe()
    PetWindow._raise_accessory(probe)  # type: ignore[arg-type]
    assert probe.calls == 0


def test_topmost_desktop_mode_switch_preserves_interaction_window(monkeypatch) -> None:
    """切换层级不得丢失位置、动画状态、轮廓穿透或无焦点标志。"""

    app, window = _create_window()
    monkeypatch.setattr("onepic_desktop_pet.window.save_settings", lambda _settings: None)
    window.move(123, 77)
    window.set_state(PetState.WALK)
    window.animation_timer.stop()
    frame = window._frame_index

    window.set_always_on_top(False)
    app.processEvents()

    assert window.settings.always_on_top is False
    assert not window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    # Cocoa's offscreen backend aligns logical coordinates to a display pixel
    # grid (2 px on arm64 runners, 4 px on Intel runners).
    assert (window.pos() - QPoint(123, 77)).manhattanLength() <= 4
    assert window.state is PetState.WALK
    assert window._frame_index == frame
    assert not window.animation_timer.isActive()
    assert not window.mask().isEmpty()

    window.set_always_on_top(True)
    app.processEvents()

    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert (window.pos() - QPoint(123, 77)).manhattanLength() <= 4
    window.close()
    window.deleteLater()
    app.processEvents()


def test_connection_and_companion_settings_scroll_and_include_music_clients() -> None:
    """小屏幕可滚动到全部陪伴选项，并能选择 Apple Music/Spotify。"""

    app = QApplication.instance() or QApplication([])
    dialog = AISettingsDialog(PetSettings(), CredentialStore())
    assert dialog.findChild(QScrollArea) is not None
    assert dialog.allow_autonomous_walk.isChecked() is False
    services = {
        dialog.music_service.itemData(index)
        for index in range(dialog.music_service.count())
    }
    assert {"auto", "qq", "netease", "kugou", "apple", "spotify"} <= services
    assert dialog.music_service.currentData() == "auto"
    assert dialog.apple_music_path.isEnabled()
    assert dialog.spotify_music_path.isEnabled()
    assert dialog.version_label.text().startswith(f"程序版本：{__version__}")
    assert dialog.always_on_top.isChecked()
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_owner_nickname_changes_social_identity_without_changing_pet_name() -> None:
    app = QApplication.instance() or QApplication([])
    settings = PetSettings()
    settings_dialog = AISettingsDialog(settings, CredentialStore())

    assert settings_dialog.owner_nickname.text() == ""
    settings_dialog.owner_nickname.setText("团团")
    settings_dialog.apply()

    assert settings.owner_nickname == "团团"
    assert settings.pet_name == "六毛"
    chat = ChatDialog(None, settings.pet_name)
    assert chat.windowTitle() == "和六毛聊聊"
    assert chat.pet_title.text() == "和六毛聊聊"
    assert chat.rename_button.text() == "修改主人称呼"
    assert "主人称呼" in chat.rename_button.toolTip() or "自习室" in chat.rename_button.toolTip()
    chat.set_pet_name("阿毛")
    assert chat.windowTitle() == "和六毛聊聊"
    assert chat.input.placeholderText() == "跟六毛说点什么……"

    settings_dialog.close()
    settings_dialog.deleteLater()
    chat.close()
    chat.deleteLater()
    app.processEvents()


def test_autonomous_walk_setting_is_applied_without_disabling_ambient_animation() -> None:
    app, window = _create_window()
    assert window.settings.allow_autonomous_walk is False
    assert window._walk_allowed() is False
    assert window.animation_timer.isActive()

    window.set_allow_autonomous_walk(True, persist=False)
    assert window.settings.allow_autonomous_walk is True
    assert window._walk_allowed() is True

    window.set_allow_autonomous_walk(False, persist=False)
    assert window._walk_allowed() is False
    assert window.animation_timer.isActive()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_chat_rename_button_opens_visible_rename_flow(monkeypatch) -> None:
    app, window = _create_window()
    monkeypatch.setattr("onepic_desktop_pet.window.save_settings", lambda _settings: None)
    monkeypatch.setattr(
        "onepic_desktop_pet.window.QInputDialog.getText",
        lambda *args, **kwargs: ("团子", True),
    )

    window.prompt_dialogue()
    assert window._chat_dialog is not None
    assert window._chat_dialog.rename_button.isVisible()
    window._chat_dialog.rename_button.click()
    app.processEvents()

    assert window.settings.owner_nickname == "团子"
    assert window.settings.pet_name == "六毛"
    assert window._chat_dialog.pet_title.text() == "和六毛聊聊"
    assert window.windowTitle().endswith("· 六毛")
    window.close()
    window.deleteLater()
    app.processEvents()


def test_chat_connected_status_is_not_rendered_twice() -> None:
    dialog = ChatDialog()
    dialog.set_provider("codex", "connected", "Codex 已连接。")
    assert dialog.status_label.text() == "Codex（使用本机登录） · 已连接，优先使用 AI"
    assert "\n" not in dialog.status_label.text()
    dialog.close()
    dialog.deleteLater()


def test_compact_todo_panel_is_frameless_and_keeps_only_todos(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    memory = TimeMemory(tmp_path, persist=False)
    task = memory.todos.add("整理回归结果")
    panel = CompactTodoPanel(memory, settings=PetSettings(today_note_mode="compact"))
    panel.show()
    app.processEvents()

    assert panel.windowTitle() == ""
    assert panel.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert not panel.findChildren(QLabel, "todayNoteTitle")
    assert set(panel.rows) == {task.id}
    assert panel.rows[task.id].checkbox.isChecked() is False

    panel.rows[task.id].checkbox.setChecked(True)
    app.processEvents()
    assert memory.todos.get(task.id).completed is True
    assert not panel.isVisible()
    assert not panel.rows_scroll.isVisible()
    assert not panel.action_column.isVisible()
    assert set(panel.rows) == set()
    assert not hasattr(panel, "expand_button")
    assert not panel.more_button.isVisible()
    assert not panel.add_button.isVisible()

    replacement = memory.todos.add("重新出现")
    panel.refresh()
    panel.show()
    app.processEvents()
    assert panel.isVisible()
    assert set(panel.rows) == {replacement.id}
    assert panel.more_button.isVisible()
    assert panel.add_button.isVisible()
    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_compact_todo_panel_reappears_after_todo_center_write(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    memory = TimeMemory(tmp_path, persist=False)
    window = PetWindow(
        PetSettings(today_note_mode="compact"),
        work_timer=WorkTimerModel(path=tmp_path / "work_timer.json"),
    )
    window.time_memory = memory
    window.show()
    app.processEvents()

    window.show_compact_todos()
    app.processEvents()
    panel = window._compact_todo_panel
    assert panel is not None
    assert not panel.isVisible()

    memory.todos.add("从空状态恢复")
    window._refresh_todo_surfaces()
    app.processEvents()

    assert panel.isVisible()
    assert panel.more_button.isVisible()
    assert panel.add_button.isVisible()
    assert len(panel.rows) == 1
    window.close()
    window.deleteLater()
    app.processEvents()


def test_pending_todos_restore_a_hidden_compact_panel_automatically(tmp_path) -> None:
    """An unfinished Todo must remain visible without requiring a manual restore."""

    app = QApplication.instance() or QApplication([])
    memory = TimeMemory(tmp_path, persist=False)
    memory.todos.add("仍然需要显示")
    window = PetWindow(
        PetSettings(today_note_mode="compact"),
        work_timer=WorkTimerModel(path=tmp_path / "work_timer.json"),
    )
    window.time_memory = memory
    window.show()
    app.processEvents()

    window.show_compact_todos()
    app.processEvents()
    panel = window._compact_todo_panel
    assert panel is not None and panel.isVisible()

    window.hide_compact_todos()
    assert not panel.isVisible()

    window._refresh_todo_surfaces()
    app.processEvents()
    assert panel.isVisible()
    assert set(panel.rows) == set(panel.visible_task_ids)

    window.close()
    window.deleteLater()
    app.processEvents()


def test_show_todos_command_restores_an_existing_hidden_panel(tmp_path) -> None:
    """Manual “显示待办” must restore tasks already present in the panel."""

    app = QApplication.instance() or QApplication([])
    memory = TimeMemory(tmp_path, persist=False)
    memory.todos.add("仍然需要显示")
    window = PetWindow(
        PetSettings(today_note_mode="compact"),
        work_timer=WorkTimerModel(path=tmp_path / "work_timer.json"),
    )
    window.time_memory = memory
    window.show()
    app.processEvents()

    window.show_compact_todos()
    app.processEvents()
    panel = window._compact_todo_panel
    assert panel is not None and panel.isVisible()

    window.hide_compact_todos()
    assert not panel.isVisible()

    window._menu_callbacks()["show_todos"]()
    app.processEvents()
    assert panel.isVisible()
    assert set(panel.rows) == set(panel.visible_task_ids)

    window.close()
    window.deleteLater()
    app.processEvents()


def test_compact_todo_panel_supports_three_rows_and_follows_pet(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    memory = TimeMemory(tmp_path, persist=False)
    memory.todos.add("修改论文", time="20:00")
    memory.todos.add("整理回归结果")
    memory.todos.add("发材料", time="22:30")
    window = PetWindow(
        PetSettings(today_note_mode="compact"),
        work_timer=WorkTimerModel(path=tmp_path / "work_timer.json"),
    )
    window.time_memory = memory
    window.move(120, 120)
    window.show()
    app.processEvents()
    window.show_compact_todos()
    app.processEvents()
    panel = window._compact_todo_panel
    assert panel is not None
    assert len(panel.rows) == 3
    assert panel.rows_scroll.height() >= sum(row.height() for row in panel.rows.values())
    assert len(panel.rows) == 3
    row = next(iter(panel.rows.values()))
    assert not hasattr(row, "more_button")
    assert panel.more_button.isEnabled()
    assert panel.more_button.parent() is panel.action_column
    assert 32 <= panel.more_button.width() <= 36
    assert 32 <= panel.add_button.width() <= 36
    assert panel.more_button.x() + panel.more_button.width() <= panel.action_column.width()
    assert panel.add_button.x() + panel.add_button.width() <= panel.action_column.width()
    more_center = panel.more_button.mapTo(panel, panel.more_button.rect().center()).x()
    add_center = panel.add_button.mapTo(panel, panel.add_button.rect().center()).x()
    assert abs(more_center - add_center) <= 1
    add_top = panel.add_button.mapTo(panel, QPoint(0, 0)).y()
    more_bottom = panel.more_button.mapTo(panel, QPoint(0, panel.more_button.height())).y()
    assert add_top >= more_bottom + 8
    assert add_top + panel.add_button.height() <= panel.height() - panel.PANEL_VERTICAL_SAFETY
    action_add_bottom = panel.add_button.y() + panel.add_button.height()
    assert action_add_bottom <= panel.action_column.height() - panel.PANEL_VERTICAL_SAFETY
    timed_row = next(row for row in panel.rows.values() if "20:00" in row.label.toolTip())
    assert "20:00" in timed_row.label.text()
    # The unified menu owns one real clickable button.  Select a row first;
    # production opens the QMenu from this exact button hit area.
    row.selected.emit(row.task_id)
    assert panel.selected_task_id == row.task_id
    before = panel.pos()
    # Keep the second position inside the offscreen test monitor.  The
    # companion is clamped to the available geometry, so moving farther
    # right/down can legitimately leave it at the same clamped position.
    window.move(10, 20)
    app.processEvents()
    assert panel.pos() != before
    visible_bounds = window.mask().boundingRect()
    pet_left = window.x() + visible_bounds.left()
    pet_right = window.x() + visible_bounds.right() + 1
    available = (QApplication.screenAt(window.geometry().center()) or QApplication.primaryScreen()).availableGeometry()
    if pet_left - panel.width() - 8 >= available.left():
        assert panel.x() + panel.width() + 8 <= pet_left
    elif pet_right + 8 + panel.width() <= available.right() + 1:
        assert panel.x() >= pet_right + 8
    else:
        assert panel.y() >= window.y() + visible_bounds.bottom() + 1 + 6 or panel.y() <= window.y() + visible_bounds.top() - panel.height() - 6
    window.close()
    window.deleteLater()
    app.processEvents()


def test_compact_todo_panel_hugs_task_content_and_repositions_after_refresh(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    memory = TimeMemory(tmp_path, persist=False)
    task = memory.todos.add("论文")
    window = PetWindow(
        PetSettings(today_note_mode="compact"),
        work_timer=WorkTimerModel(path=tmp_path / "work_timer.json"),
    )
    window.time_memory = memory
    window.move(400, 180)
    window.show_compact_todos()
    app.processEvents()
    panel = window._compact_todo_panel
    assert panel is not None
    short_width = panel.width()
    assert panel.MIN_WIDTH <= short_width <= panel.MAX_WIDTH

    memory.todos.update(
        task.id,
        title="修改论文第三部分机制分析并整理稳健性回归结果",
        time="22:00",
    )
    window._refresh_todo_surfaces()
    app.processEvents()

    assert short_width < panel.width() <= panel.MAX_WIDTH
    assert panel.rows[task.id].label.toolTip() == "修改论文第三部分机制分析并整理稳健性回归结果 · 22:00"
    row = panel.rows[task.id]
    assert "\n" in row.label.text()
    assert row.height() > panel.ROW_HEIGHT
    assert row.label.geometry().right() <= row.width()
    assert not hasattr(row, "more_button")
    assert abs(
        panel.more_button.mapTo(panel, panel.more_button.rect().center()).x()
        - panel.add_button.mapTo(panel, panel.add_button.rect().center()).x()
    ) <= 1
    assert panel.add_button.y() + panel.add_button.height() <= panel.height() - panel.PANEL_VERTICAL_SAFETY
    assert panel.add_button.y() + panel.add_button.height() <= panel.height() - panel.layout().contentsMargins().bottom()
    panel_rect = panel.geometry()
    # PetWindow is a transparent native host and is intentionally wider than
    # the visible sprite.  The accessory must not overlap the sprite's real
    # mask (with the same small anti-aliasing safety margin used by the
    # production placement code), but it may occupy transparent host pixels.
    visible_bounds = window.mask().boundingRect().translated(window.pos())
    pet_rect = visible_bounds.adjusted(-6, -6, 6, 6)
    assert not panel_rect.intersects(pet_rect)
    assert panel.x() != 0 or panel.y() != 0
    window.close()
    window.deleteLater()
    app.processEvents()


def test_time_memory_window_keeps_ids_for_edit_and_delete_menus(tmp_path) -> None:
    """倒计时、纪念日、时光轴列表都保留真实 ID，菜单才能编辑/删除原记录。"""

    app = QApplication.instance() or QApplication([])
    memory = TimeMemory(tmp_path, persist=False)
    countdown = memory.countdowns.add("论文投稿", "2026-09-01")
    anniversary = memory.anniversaries.add("六毛来到桌面", "2025-08-15", repeat="yearly")
    event = memory.timeline.add("第一次投出论文")
    dialog = TimeMemoryWindow(memory)
    app.processEvents()

    assert dialog.countdown_list.item(0).data(Qt.ItemDataRole.UserRole) == countdown.id
    assert dialog.anniversary_list.item(0).data(Qt.ItemDataRole.UserRole) == anniversary.id
    assert dialog.timeline_list.item(0).data(Qt.ItemDataRole.UserRole) == event.id
    assert dialog.countdown_list.toolTip().startswith("双击编辑")

    assert memory.timeline.delete(event.id)
    assert memory.timeline.query() == []
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_time_memory_and_detailed_todo_are_normal_taskbar_windows(tmp_path) -> None:
    """可最小化窗口进入任务栏，不再成为桌宠的附属小框。"""

    app = QApplication.instance() or QApplication([])
    memory = TimeMemory(tmp_path, persist=False)
    time_memory = TimeMemoryWindow(memory)
    detailed = TodayNoteWindow(memory)

    for dialog in (time_memory, detailed):
        flags = dialog.windowFlags()
        assert (int(flags) & 0x0F) == int(Qt.WindowType.Window)
        assert flags & Qt.WindowType.WindowMinimizeButtonHint
        assert flags & Qt.WindowType.WindowSystemMenuHint
        assert flags & Qt.WindowType.WindowCloseButtonHint
        assert not flags & Qt.WindowType.FramelessWindowHint
        assert not flags & Qt.WindowType.WindowStaysOnTopHint
        assert dialog.parent() is None
        assert not dialog.isModal()

        dialog.show()
        app.processEvents()
        dialog.showMinimized()
        app.processEvents()
        assert dialog.isMinimized()
        dialog.showNormal()
        app.processEvents()

    time_memory.close()
    detailed.close()
    time_memory.deleteLater()
    detailed.deleteLater()
    app.processEvents()


def test_todo_row_wraps_before_eliding_and_limits_to_two_lines() -> None:
    app = QApplication.instance() or QApplication([])
    metrics = QFontMetrics(app.font())

    short = TodoRow._wrap_lines("论文", metrics, 180)
    medium = TodoRow._wrap_lines("开始写论文 · 09:30", metrics, 220)
    long = TodoRow._wrap_lines("codex重置之后重新处理自习室连接问题", metrics, 180)
    very_long = TodoRow._wrap_lines(
        "继续修改当前六毛桌面待办条和在线更新功能并完成真实测试" * 3,
        metrics,
        180,
    )

    assert len(short) == 1 and "…" not in short[0]
    assert len(medium) == 1 and "…" not in medium[0]
    assert len(long) == 2
    assert len(very_long) == 2 and very_long[1].endswith("…")


def test_todo_row_reserves_full_font_box_for_descenders(tmp_path) -> None:
    """Todo text must not lose its lower glyph pixels at any supported DPI."""

    app = QApplication.instance() or QApplication([])
    memory = TimeMemory(tmp_path, persist=False)
    task = memory.todos.add("开始写论文 · 09:30")
    panel = CompactTodoPanel(memory, settings=PetSettings(today_note_mode="compact"))
    panel.show()
    app.processEvents()

    row = panel.rows[task.id]
    app.processEvents()
    metrics = row.label.fontMetrics()
    expected_label_height = (
        max(metrics.height(), metrics.lineSpacing()) + TodoRow.GLYPH_SAFETY
    ) * row._line_count

    assert row.label.height() >= expected_label_height
    margins = row.layout().contentsMargins()
    assert row.height() >= row.label.height() + margins.top() + margins.bottom()
    assert row.label.geometry().bottom() < row.height()

    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_hourly_unlocks_never_override_manual_outfit_selection(monkeypatch) -> None:
    """小时成长线只解锁娃衣，不能把用户选好的外观强行换掉。"""
    app, window = _create_window()
    monkeypatch.setattr("onepic_desktop_pet.window.save_settings", lambda _settings: None)
    window.settings.equipped_outfit = "hour-01"
    window.work_timer._lifetime_seconds = 10 * 3600
    window.work_timer._running_since = None
    window.work_timer._notified_outfit_count = 0

    window._sync_hourly_outfit(announce=False)
    assert window.work_timer.unlocked_outfit_count() == 10
    assert window.settings.equipped_outfit == "hour-01"

    window._sync_hourly_outfit(announce=True)
    assert window.settings.equipped_outfit == "hour-01"
    window.close()
    window.deleteLater()
    app.processEvents()


def test_study_room_menu_restores_minimized_window() -> None:
    """再次点击自习室入口会恢复原窗口，而不是重复创建窗口。"""
    app, window = _create_window()
    window.open_social_hub()
    dialog = window._social_dialog
    assert dialog is not None
    assert dialog.parent() is None
    dialog.showMinimized()
    app.processEvents()

    window.open_social_hub()
    app.processEvents()
    assert window._social_dialog is dialog
    assert not dialog.isMinimized()
    dialog.close()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_background_visit_refresh_uses_one_compact_status_bubble() -> None:
    app, window = _create_window()
    peer = {"id": "visit-1", "nickname": "搭子", "today_seconds": 5}
    window._show_buddy_visit(peer)
    app.processEvents()
    assert window.visit_status_bubble.isVisible()
    assert window.visit_status_bubble.text() == "搭子正在串门"
    assert not window._buddy_visit_window.isVisible()
    window._show_buddy_visit(peer)
    assert window.visit_status_bubble.text() == "搭子正在串门"
    window.close()
    window.deleteLater()
    app.processEvents()



def test_visit_status_bubble_sits_below_todo_and_left_of_work_duration() -> None:
    """串门标签使用六毛下方的状态行，不遮挡待办内容。"""

    app, window = _create_window()
    window.move(220, 120)
    window.start_work_timer()
    window._show_buddy_visit({"id": "visit-layout", "nickname": "搭子", "today_seconds": 5})
    app.processEvents()

    bubble = window.visit_status_bubble
    assert bubble.isVisible()
    assert bubble.y() >= window.y() + window.height()
    if window.work_duration_bubble.isVisible():
        assert bubble.geometry().right() < window.work_duration_bubble.geometry().left()
        assert bubble.y() == window.work_duration_bubble.y()

    window.close()
    window.deleteLater()
    app.processEvents()

def test_fullscreen_hides_and_restores_previous_pet_surfaces(monkeypatch) -> None:
    """全屏时让位，退出全屏后只恢复进入前已经可见的界面。"""

    app, window = _create_window()
    window.quick_panel.show()
    window.work_duration_bubble.show()
    app.processEvents()
    assert window.isVisible()
    assert window.quick_panel.isVisible()
    assert window.work_duration_bubble.isVisible()

    monkeypatch.setattr("onepic_desktop_pet.window.active_window_is_fullscreen", lambda: True)
    window._sync_fullscreen_visibility()
    app.processEvents()
    assert window._fullscreen_hidden
    assert not window.isVisible()
    assert not window.quick_panel.isVisible()
    assert not window.work_duration_bubble.isVisible()

    # A live focus refresh may try to show the duration bubble while the
    # foreground app is still fullscreen. That refresh must remain hidden.
    window._update_work_duration_bubble()
    app.processEvents()
    assert not window.work_duration_bubble.isVisible()

    monkeypatch.setattr("onepic_desktop_pet.window.active_window_is_fullscreen", lambda: False)
    window._sync_fullscreen_visibility()
    app.processEvents()
    assert not window._fullscreen_hidden
    assert window.isVisible()
    assert window.quick_panel.isVisible()
    assert window.work_duration_bubble.isVisible()

    window.close()
    window.deleteLater()
    app.processEvents()


def test_manual_hide_also_hides_duration_bubble_until_explicit_show() -> None:
    """隐藏六毛 must not leave the detached live-duration badge behind."""

    app, window = _create_window()
    window.start_work_timer()
    app.processEvents()
    assert window.work_duration_bubble.isVisible()

    window.hide_pet()
    app.processEvents()
    assert not window.isVisible()
    assert not window.work_duration_bubble.isVisible()

    window._update_work_duration_bubble()
    app.processEvents()
    assert not window.work_duration_bubble.isVisible()

    window.show_pet()
    app.processEvents()
    assert window.isVisible()
    assert window.work_duration_bubble.isVisible()
    window.close(); window.deleteLater(); app.processEvents()


def test_relaunch_does_not_restore_old_active_visit() -> None:
    """重新启动只接受本次进程之后产生的串门，不复活旧场景。"""

    app, window = _create_window()
    old_visit = {
        "id": "old-visit",
        "visit_started_at": "2000-01-01T00:00:00+00:00",
    }
    assert window._active_visits_after_startup([old_visit]) == []

    fresh_time = (window._process_started_at + timedelta(seconds=1)).isoformat()
    fresh_visit = {"id": "fresh-visit", "visit_started_at": fresh_time}
    assert window._active_visits_after_startup([fresh_visit]) == [fresh_visit]

    window.close()
    window.deleteLater()
    app.processEvents()


def test_window_uses_character_mask_and_reuses_render_cache() -> None:
    app, window = _create_window()
    initial_render_count = len(window._render_cache)
    initial_mask_count = len(window._mask_cache)

    window._refresh_pixmap()

    assert not window.mask().isEmpty()
    assert window.mask().boundingRect().width() < window.width()
    assert len(window._render_cache) == initial_render_count
    assert len(window._mask_cache) == initial_mask_count
    window.close()
    window.deleteLater()
    app.processEvents()


def test_sit_animation_holds_then_reverses_to_standing_frame() -> None:
    app, window = _create_window()
    window.set_state(PetState.SIT)

    for _ in range(len(window._pixmaps[PetState.SIT])):
        window._animation_tick()

    assert window._frame_index == len(window._pixmaps[PetState.SIT]) - 1
    assert not window.animation_timer.isActive()

    window._reverse_transition_to_idle()
    for _ in range(len(window._pixmaps[PetState.SIT]) - 1):
        window._animation_tick()

    assert window._frame_index == 0
    assert not window.animation_timer.isActive()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_walk_pauses_briefly_when_turning_at_screen_edge() -> None:
    app, window = _create_window()
    window.set_state(PetState.WALK)
    window.direction = -1
    window._frame_index = 3
    window.move(0, 0)
    window._screen_geometry = lambda: QRect(0, 0, 1000, 1000)

    window._movement_tick()

    assert window.direction == 1
    assert window._turn_paused
    assert window.turn_timer.isActive()
    assert not window.animation_timer.isActive()

    window._finish_turn()

    assert not window._turn_paused
    assert window.animation_timer.isActive()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_walk_vertical_offset_follows_footfall_frames() -> None:
    """行走起伏必须跟随连续帧，而不是由独立的慢速浮动计时器驱动。"""

    app, window = _create_window()
    window.set_state(PetState.WALK)
    offsets = [window.label.y()]

    for _ in range(3):
        window._animation_tick()
        offsets.append(window.label.y())

    assert offsets == [3, 5, 2, 0]
    window.close()
    window.deleteLater()
    app.processEvents()


def test_walk_uses_subpixel_phase_synced_speed(monkeypatch) -> None:
    """水平移动应亚像素累计，落脚阶段减速而不冻结，随后平滑加速。"""

    app, window = _create_window()
    window.set_state(PetState.WALK)
    window.direction = 1
    window.move(100, 0)
    window._movement_x = 100.0
    window._last_movement_at = 10.0
    window._screen_geometry = lambda: QRect(0, 0, 1000, 1000)
    current_time = [10.016]
    monkeypatch.setattr(
        "onepic_desktop_pet.window.time.monotonic",
        lambda: current_time[0],
    )

    window._frame_index = 0
    window._movement_tick()
    assert round(window._movement_x, 2) == 100.45
    assert window.x() == 100

    current_time[0] = 10.032
    window._frame_index = 3
    window._movement_tick()
    assert window._movement_speed_pixels_per_second() == 62.5
    assert round(window._movement_x, 2) == 102.1
    assert window.x() == 102
    window.close()
    window.deleteLater()
    app.processEvents()


def test_walk_motion_curve_avoids_freeze_and_balances_both_steps() -> None:
    """移动曲线不应停顿后猛跳，且左右两个半步必须使用相同节奏。"""

    app, window = _create_window()

    assert min(window._walk_motion_factors) > 0.0
    assert max(window._walk_motion_factors) / min(window._walk_motion_factors) < 4
    assert window._walk_motion_factors[:4] == window._walk_motion_factors[4:]
    assert sum(window._walk_motion_factors) / 8 == 1.0

    window.close()
    window.deleteLater()
    app.processEvents()


def test_drag_state_uses_dedicated_suspended_animation() -> None:
    """拖拽状态应加载三帧悬空素材，而不是回退到待机站立。"""

    app, window = _create_window()
    window.set_state(PetState.DRAG)

    display_state, _pixmap = window._current_source()
    assert display_state is PetState.DRAG
    assert len(window._pixmaps[PetState.DRAG]) == 3
    assert window.animation_timer.isActive()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_interaction_states_have_reusable_emotion_symbols() -> None:
    """互动表情应使用独立符号层，换角色素材后仍然能够显示。"""

    expected = {
        PetState.HAPPY: "sparkle",
        PetState.SHY: "heart",
        PetState.SURPRISED: "exclamation",
        PetState.ANNOYED: "anger",
        PetState.SLEEPY: "sleep",
        PetState.CURIOUS: "question",
        PetState.SELFIE: "flash",
        PetState.DRAG: "sweat",
    }
    assert {state: emotion_effect_name(state) for state in expected} == expected
    assert emotion_effect_name(PetState.IDLE) is None


def test_emotion_symbol_timer_follows_current_state() -> None:
    """进入表情状态时符号应动画，恢复待机后必须停止计时器。"""

    app, window = _create_window()
    window.set_state(PetState.SURPRISED)
    assert window.effect_timer.isActive()
    assert not window.label.pixmap().isNull()

    window.set_state(PetState.IDLE)
    assert not window.effect_timer.isActive()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_inactivity_progresses_from_sit_to_sleep() -> None:
    """超过睡眠阈值后仍应先完整坐下，再播放坐姿入睡序列。"""

    settings = PetSettings(inactive_sit_ms=10000, inactive_sleep_ms=20000)
    app = QApplication.instance() or QApplication([])
    window = PetWindow(settings)
    window._last_user_interaction = time.monotonic() - 21
    window.set_state(PetState.IDLE)

    window._state_timeout()
    assert window.state is PetState.SIT
    assert window._sleep_after_sit

    window._state_timeout()
    assert window.state is PetState.SLEEP
    assert not window._sleep_after_sit
    window.close()
    window.deleteLater()
    app.processEvents()


def test_pause_disables_running_but_keeps_ambient_state_timer() -> None:
    """暂停跑动时应进入生活状态并继续计时，而不是冻结在站立帧。"""

    app, window = _create_window()
    window.set_state(PetState.WALK)
    window.behavior.next_autonomous_state = (
        lambda _current, allow_walk: StateDecision(PetState.SIT, 2000)
    )

    window.set_paused(True)

    assert window.paused
    assert window.state is PetState.SIT
    assert window.state_timer.isActive()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_display_size_preset_updates_geometry_and_settings() -> None:
    """右键尺寸预设应立即改变窗口和标签尺寸，并写回设置对象。"""

    app, window = _create_window()
    window.set_display_height(280)

    assert window.settings.display_height == 280
    assert window.height() == 294
    assert window.label.height() == 288
    assert not window.mask().isEmpty()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_default_workmate_size_is_smaller_than_previous_standard() -> None:
    """六毛工作搭子的首次启动高度应从旧版进一步缩小到 160。"""

    app, window = _create_window()

    assert window.settings.display_height == 160
    assert window.height() == 174
    window.close()
    window.deleteLater()
    app.processEvents()


def test_quick_panel_double_click_behavior_toggles_and_auto_hides() -> None:
    """快捷口袋只在主动打开时显示，再次调用会立即收起。"""

    app, window = _create_window()
    window.show_quick_panel()
    app.processEvents()
    assert window.quick_panel.isVisible()
    assert window.quick_panel.hide_timer.isActive()
    window.show_quick_panel()
    assert not window.quick_panel.isVisible()
    window.close(); window.deleteLater(); app.processEvents()


def test_received_social_drink_changes_pose_without_pausing_focus() -> None:
    app, window = _create_window()
    window.start_work_timer()
    assert window.work_timer.is_running
    window._handle_food_interaction_accepted(
        {"kind": "food_milk_tea", "payload": {"duration_minutes": 10}}
    )
    assert window.work_timer.is_running
    assert window._ambient_activity == "milk-tea"
    window.close(); window.deleteLater(); app.processEvents()



def test_social_food_overrides_hourly_outfit_and_cake_keeps_focus_running(monkeypatch) -> None:
    """食物互动造型应盖过永久娃衣，但不能把专注计时变成休息。"""

    app, window = _create_window()
    window.settings.equipped_outfit = "hour-07"
    window.start_work_timer()

    window._handle_food_interaction_accepted(
        {
            "kind": "food_cake_share",
            "payload": {"item_key": "cake", "duration_minutes": 0},
        }
    )

    assert window.work_timer.is_running
    assert window._ambient_activity == "feast"
    assert window._social_food_activity_until > time.monotonic()

    captured: list[bool] = []

    def probe(source, activity, outfit, phase, *, food_scene=False):
        captured.append(bool(food_scene))
        return source

    monkeypatch.setattr("onepic_desktop_pet.window.draw_activity_overlay", probe)
    window._refresh_pixmap()
    assert captured and captured[-1] is True

    window.close(); window.deleteLater(); app.processEvents()

def test_quick_panel_has_six_high_frequency_entries_and_secondary_report() -> None:
    """快捷面板有六个主入口，工作报告只作为悬停时的次级按钮。"""

    app, window = _create_window()
    buttons = [
        window.quick_panel.chat_button,
        window.quick_panel.work_button,
        window.quick_panel.report_button,
        window.quick_panel.todo_button,
        window.quick_panel.social_button,
        window.quick_panel.music_button,
        window.quick_panel.food_button,
    ]
    assert [button.objectName() for button in buttons] == [
        "quickAction_chat",
        "quickAction_work",
        "quickAction_report",
        "quickAction_todo",
        "quickAction_social",
        "quickAction_music",
        "quickAction_food",
    ]
    assert [button.text() for button in buttons] == ["", "", "", "", "", "", ""]
    assert [button.toolTip() for button in buttons] == ["聊聊", "开始工作", "工作报告", "待办", "搭子自习室", "音乐", "喂食"]
    assert all(not button.icon().isNull() for button in buttons)
    assert not window.quick_panel.title.isVisible()
    assert window.quick_panel.objectName() == "quickActionDock"
    assert all(button.size() == QSize(42, 42) for button in buttons)
    assert window.quick_panel.report_button.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert window.quick_panel.report_button.graphicsEffect() is None
    assert not window.quick_panel.report_button.isVisible()
    for button, label in zip(buttons, ("聊聊", "开始工作", "工作报告", "待办", "搭子自习室", "音乐", "喂食")):
        window.quick_panel._show_hint(button)
        app.processEvents()
        assert window.quick_panel.hover_hint.text() == label
        window.quick_panel._hide_hint()
    # Primary labels are below their icon when the screen has room.
    window.quick_panel._show_hint(window.quick_panel.chat_button)
    app.processEvents()
    chat_top = window.quick_panel.chat_button.mapToGlobal(QPoint(0, 0))
    hint_top = window.quick_panel.hover_hint.pos()
    area = app.screenAt(chat_top).availableGeometry()
    if chat_top.y() + window.quick_panel.chat_button.height() + window.quick_panel.hover_hint.height() + 7 <= area.bottom():
        assert hint_top.y() >= chat_top.y() + window.quick_panel.chat_button.height()
    window.quick_panel._hide_hint()
    assert "color: #111111" in window.quick_panel.hover_hint.styleSheet()
    window.quick_panel._hide_hint()
    assert not window.quick_panel.hover_hint.isVisible()
    assert 50 <= window.quick_panel.sizeHint().height() <= 60
    window.move(300, 200)
    window.show_quick_panel()
    app.processEvents()
    first_offset = window.quick_panel.pos() - window.pos()
    window.move(340, 240)
    app.processEvents()
    assert window.quick_panel.pos() - window.pos() == first_offset
    assert window.quick_panel.y() + window.quick_panel.height() + 12 <= window.y()
    panel_size = window.quick_panel.size()
    window.quick_panel._set_hover_button(window.quick_panel.work_button)
    assert window.quick_panel.report_button.isVisible()
    assert window.quick_panel.size() == panel_size
    work_global_top = window.quick_panel.work_button.mapToGlobal(QPoint(0, 0))
    report_global_bottom = window.quick_panel.report_button.mapToGlobal(
        QPoint(0, window.quick_panel.report_button.height())
    )
    assert report_global_bottom.y() <= work_global_top.y()
    window.quick_panel._set_hover_button(window.quick_panel.report_button)
    assert window.quick_panel.hover_hint.text() == "工作报告"
    app.processEvents()
    report_top = window.quick_panel.report_button.mapToGlobal(QPoint(0, 0))
    report_hint_bottom = window.quick_panel.hover_hint.pos().y() + window.quick_panel.hover_hint.height()
    area = app.screenAt(report_top).availableGeometry()
    if report_top.y() - window.quick_panel.hover_hint.height() - 7 >= area.top():
        assert report_hint_bottom <= report_top.y()
    report_signal = QSignalSpy(window.quick_panel.work_report_requested)
    window.quick_panel.report_button.click()
    app.processEvents()
    assert report_signal.count() == 1
    assert window._work_report_dialog is not None
    assert window._work_report_dialog.isVisible()
    assert not window.quick_panel.isVisible()
    assert not window.quick_panel.report_button.isVisible()
    window._work_report_dialog.close()
    window.quick_panel._set_hover_button(window.quick_panel.chat_button)
    window.quick_panel._set_report_button_visible(False)
    assert not window.quick_panel.report_button.isVisible()
    window.close(); window.deleteLater(); app.processEvents()


def test_quick_panel_hover_label_switches_without_click() -> None:
    """Moving across shortcuts must switch labels immediately, including macOS."""

    app, window = _create_window()
    panel = window.quick_panel
    panel.show()
    app.processEvents()
    assert panel._hover_poll_timer.isActive()

    panel._button_at_global_pos = lambda _position: panel.chat_button
    panel._poll_hover_button()
    assert panel.hover_hint.text() == "聊聊"

    panel._button_at_global_pos = lambda _position: panel.social_button
    panel._poll_hover_button()
    assert panel.hover_hint.text() == "搭子自习室"

    panel._button_at_global_pos = lambda _position: None
    panel._poll_hover_button()
    assert not panel.hover_hint.isVisible()
    window.close(); window.deleteLater(); app.processEvents()


def test_quick_panel_hides_detached_report_with_work_shortcut() -> None:
    """工作报告按钮必须和开始/暂停快捷面板同步移动、收起。"""

    app, window = _create_window()
    window.move(320, 220)
    window.show_quick_panel()
    app.processEvents()
    panel = window.quick_panel

    panel._set_hover_button(panel.work_button)
    app.processEvents()
    assert panel.report_button.isVisible()
    first_work_top = panel.work_button.mapToGlobal(QPoint(0, 0))
    first_report_top = panel.report_button.mapToGlobal(QPoint(0, 0))

    window.move(380, 260)
    app.processEvents()
    moved_work_top = panel.work_button.mapToGlobal(QPoint(0, 0))
    moved_report_top = panel.report_button.mapToGlobal(QPoint(0, 0))
    assert moved_report_top - moved_work_top == first_report_top - first_work_top

    # The primary work action collapses the whole dock, including its
    # detached report button, before changing the shared focus state.
    panel.work_button.click()
    app.processEvents()
    assert not panel.isVisible()
    assert not panel.report_button.isVisible()
    assert not panel.hover_hint.isVisible()
    assert window.focus_session.snapshot().status == "focus"

    window.close(); window.deleteLater(); app.processEvents()


def test_macos_hover_hint_is_configured_before_show(monkeypatch) -> None:
    """Native macOS hint setup must not hide the first unclicked tooltip."""

    monkeypatch.setattr("onepic_desktop_pet.controls.sys.platform", "darwin")
    app, window = _create_window()
    panel = window.quick_panel
    seen_visibility: list[bool] = []
    panel.set_window_behavior_callback(
        lambda widget, **_kwargs: seen_visibility.append(widget.isVisible())
    )
    panel._show_hint(panel.todo_button)
    assert seen_visibility == [False]
    assert panel.hover_hint.text() == "待办"
    window.close(); window.deleteLater(); app.processEvents()


def test_work_duration_stays_below_pet_and_reserves_bottom_space() -> None:
    """工作计时在屏幕底边仍固定在六毛下方，不与快捷栏抢位置。"""

    app, window = _create_window()
    area = window._screen_geometry()
    assert area is not None
    window.move(area.right() - window.width() - 4, area.bottom() - window.height())
    window.focus_session.start()
    app.processEvents()

    bubble = window.work_duration_bubble
    assert bubble.isVisible()
    assert bubble.y() >= window.y() + window.height()
    assert bubble.y() + bubble.height() <= area.bottom() + 1
    assert window.y() + window.height() + bubble.height() + 5 <= area.bottom() + 1

    first_offset = bubble.pos() - window.pos()
    window.move(window.x() - 80, window.y() - 40)
    app.processEvents()
    assert bubble.pos() - window.pos() == first_offset

    window.focus_session.finish()
    window.close(); window.deleteLater(); app.processEvents()


def test_work_controls_belong_to_pet_and_follow_focus_state() -> None:
    """右键工作控制条按状态变化、位于六毛上方并随六毛移动。"""

    app, window = _create_window()
    window.move(300, 180)
    window.show_work_controls()
    app.processEvents()

    controls = window.work_controls
    assert controls.isVisible()
    assert controls.pause_button.text() == "开始工作"
    assert not controls.finish_button.isVisible()

    # IDLE 右键控制只提供开始，执行后自动收起。
    controls.pause_button.click()
    app.processEvents()
    assert window.focus_session.snapshot().status == "focus"
    assert not controls.isVisible()

    window.speech_bubble.hide()
    window.show_work_controls()
    app.processEvents()
    assert controls.pause_button.text() == "暂停工作"
    assert controls.finish_button.isVisible()
    assert not controls.geometry().intersects(window.geometry())

    # 工作开始时的提示气泡会占用上方空间；关闭它后验证默认上方布局。
    window.speech_bubble.hide()
    window._position_work_controls()
    assert controls.y() + controls.height() + 10 <= window.y()
    first_offset = controls.pos() - window.pos()

    window.pause_work_timer()
    app.processEvents()
    assert not controls.isVisible()

    window.show_work_controls()
    app.processEvents()
    assert controls.pause_button.text() == "继续工作"
    assert controls.finish_button.isVisible()

    controls.pause_button.click()
    app.processEvents()
    assert window.focus_session.snapshot().status == "focus"
    assert not controls.isVisible()

    window.speech_bubble.hide()
    window.show_work_controls()
    app.processEvents()
    window.move(340, 220)
    app.processEvents()
    assert controls.pos() - window.pos() == first_offset
    window.finish_work_timer()
    assert not controls.isVisible()
    window.close(); window.deleteLater(); app.processEvents()


def test_left_click_does_not_show_work_controls() -> None:
    """普通左击六毛只触发宠物互动，不再弹出工作按钮条。"""

    app, window = _create_window()
    window.show_work_controls()
    assert window.work_controls.isVisible()

    window._handle_click(QPoint(window.width() // 2, 20))
    app.processEvents()

    assert not window.work_controls.isVisible()
    window.close(); window.deleteLater(); app.processEvents()


def test_feeding_updates_fullness_and_shows_speech_bubble() -> None:
    """从菜单喂苹果应更新状态，并在人物附近显示文字反馈。"""

    app, window = _create_window()
    initial_fullness = window.mood.fullness

    reply = window.feed_pet("apple")
    app.processEvents()

    assert window.mood.fullness == initial_fullness + 18
    assert reply.state is PetState.HAPPY
    assert window.state is PetState.HAPPY
    assert window.speech_bubble.isVisible()
    assert "苹果" in window.speech_bubble.text()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_dialogue_is_handled_locally_and_shows_reply() -> None:
    """输入工作话题后应直接生成本地回复，不依赖外部服务。"""

    app, window = _create_window()

    reply = window.talk_to_pet("今天工作很多")
    app.processEvents()

    assert reply.state is PetState.HAPPY
    assert "十分钟" in reply.text
    assert window.speech_bubble.text() == reply.text
    assert window.speech_bubble.isVisible()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_context_menu_uses_direct_high_frequency_entries() -> None:
    """六毛本体、任务栏和状态栏使用同一套高频入口和动态状态。"""

    app, window = _create_window()
    # Arrange the dynamic menu test in IDLE even if an earlier test left a
    # shared focus session paused.
    window.finish_work_timer()
    menu = window.build_unified_menu(None, "tray")
    assert menu.parent() is None
    labels = [action.text() for action in menu.actions() if not action.isSeparator()]
    assert labels[:2] == [
        "和六毛聊聊…",
        "开始工作",
    ]
    assert "待办与提醒" in labels
    assert "更新与关于" in labels
    assert labels.index("更新与关于") > labels.index("设置")
    assert "显示与窗口" in labels
    assert "设置" in labels
    assert "隐藏六毛" in labels
    assert "退出六毛" in labels
    assert not any("快捷工具" in label for label in labels)
    assert not any("›" in label for label in labels)
    music = next(action for action in menu.actions() if action.text() == "音乐")
    music_labels = [action.text() for action in music.menu().actions() if not action.isSeparator()]
    assert music_labels == ["播放 / 暂停", "上一首", "下一首", "听陈楚生…", "音乐平台"]
    platform_menu = next(action for action in music.menu().actions() if action.text() == "音乐平台")
    assert [action.text() for action in platform_menu.menu().actions()] == [
        "跟随系统默认",
        "网易云音乐",
        "QQ 音乐",
        "Apple Music",
        "酷狗音乐",
        "汽水音乐",
    ]
    assert "工作报告…" in labels
    assert "工作记录" not in labels
    assert "六毛互动" not in labels
    todo = next(action for action in menu.actions() if action.text() == "待办与提醒")
    assert [action.text() for action in todo.menu().actions()] == ["查看待办…", "新建待办…", "六毛闹钟…"]
    display = next(action for action in menu.actions() if action.text() == "显示与窗口")
    assert [action.text() for action in display.menu().actions()] == [
        "六毛大小…", "显示本轮工作时长", "始终置顶", "桌面模式"
    ]
    settings = next(action for action in menu.actions() if action.text() == "设置")
    assert [action.text() for action in settings.menu().actions()] == ["主人称呼…", "设置…"]
    separators = [index for index, action in enumerate(menu.actions()) if action.isSeparator()]
    assert len(separators) == 5
    pet_menu = window._build_context_menu()
    assert [action.text() for action in pet_menu.actions()] == [action.text() for action in menu.actions()]
    pet_menu.close()
    menu.close()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_tray_menu_keeps_work_actions_available_when_pet_is_not_active() -> None:
    """状态栏菜单独立于六毛窗口，暂停时继续/结束都可用。"""

    app, window = _create_window()
    window.start_work_timer()
    window.pause_work_timer()
    window.hide()
    menu = window.build_unified_menu(None, "tray")

    status = next(action for action in menu.actions() if action.text().startswith("⏱ "))
    assert status.isEnabled() is False
    work = next(action for action in menu.actions() if action.text() == "继续工作")
    finish = next(action for action in menu.actions() if action.text() == "结束本轮工作")
    assert work.menu() is None
    assert finish.menu() is None
    assert work.isEnabled() and finish.isEnabled()
    assert all(
        action.isEnabled()
        for action in menu.actions()
        if not action.isSeparator() and action is not status
    )

    menu.close()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_pet_context_menu_matches_the_tray_menu() -> None:
    """六毛本体右键不再使用另一套旧菜单。"""

    app, window = _create_window()
    menu = window._build_context_menu()
    actions = [action for action in menu.actions() if not action.isSeparator()]
    tray = window.build_unified_menu(None, "tray")
    assert [action.text() for action in menu.actions()] == [
        action.text() for action in tray.actions()
    ]
    assert "选项" not in [action.text() for action in actions]
    assert "显示所有窗口" not in [action.text() for action in actions]
    assert "退出" not in [action.text() for action in actions]
    tray.close()
    menu.close()
    window.close()
    window.deleteLater()
    app.processEvents()


def _legacy_context_menu_uses_five_clear_groups_with_working_submenus() -> None:
    """右键菜单只保留五个一级分组，功能放入语义明确的子菜单。"""

    app, window = _create_window()
    menu = window._build_context_menu()
    actions = {action.text(): action for action in menu.actions()}
    assert list(actions) == ["聊天与陪伴", "动作与外观", "音乐与娱乐", "专注与自习", "系统与显示"]

    chat_actions = {action.text(): action for action in actions["聊天与陪伴"].menu().actions()}
    appearance_actions = {action.text(): action for action in actions["动作与外观"].menu().actions()}
    music_actions = {action.text(): action for action in actions["音乐与娱乐"].menu().actions()}
    focus_actions = {action.text(): action for action in actions["专注与自习"].menu().actions()}
    system_actions = {action.text(): action for action in actions["系统与显示"].menu().actions() if not action.isSeparator()}

    assert "和六毛聊聊…" in chat_actions
    assert "陪伴动作" in chat_actions
    assert "喂食、饮品与状态" in chat_actions
    assert "六毛搭子自习室…" in chat_actions
    assert "完整图片动作" in appearance_actions
    assert "工作时长娃衣" in appearance_actions
    assert "控制正在运行的播放器" in music_actions
    assert any(text.startswith("工作计时：") for text in focus_actions)
    assert "AI 与陪伴设置…" in system_actions
    assert system_actions["偶尔发牢骚"].isChecked()
    assert not system_actions["整点报时"].isChecked()
    food_menu = chat_actions["喂食、饮品与状态"].menu()
    assert food_menu is not None
    food_actions = [action.text() for action in food_menu.actions() if not action.isSeparator()]
    assert food_actions == [
        "苹果",
        "小饼干",
        "热牛奶",
        "咖啡",
        "热茶",
        "查看六毛心情与能量",
    ]
    assert not any("打招呼" in text for text in chat_actions)
    assert "连续调节宠物大小…" in system_actions
    work_action = next(action for text, action in focus_actions.items() if text.startswith("工作计时："))
    work_labels = [action.text() for action in work_action.menu().actions()]
    assert "查看今日 0–8 小时成长线" in work_labels
    assert "今天六毛陪你做了什么" in work_labels
    menu.close()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_companion_action_shows_loving_animation_and_words() -> None:
    """主动选择抱抱应增加亲密度，并显示爱意动作与安慰话语。"""

    app, window = _create_window()
    initial_affinity = window.mood.affinity

    reply = window.perform_companion_action("love")
    app.processEvents()

    assert reply.state is PetState.SHY
    assert window.state is PetState.SHY
    assert window.mood.affinity == initial_affinity + 5
    assert window.speech_bubble.text() == reply.text
    assert any(word in reply.text for word in ("抱抱", "贴贴"))
    window.close()
    window.deleteLater()
    app.processEvents()


def test_diverse_action_sequence_and_drinks_use_existing_frames() -> None:
    """伸展与喝茶应组合已验收状态，而不是静止地只换一句文字。"""

    app, window = _create_window()
    reply = window.perform_companion_action("stretch")
    assert reply.state is PetState.WAVE
    assert window.state is PetState.SIT
    tea = window.feed_pet("tea")
    assert "热茶" in tea.text
    assert window.state is PetState.SIT
    window.close()
    window.deleteLater()
    app.processEvents()


def test_hourly_announcement_can_be_disabled_and_deduplicates() -> None:
    """整点报时默认不触发，开启后同一小时只触发一次。"""

    app, window = _create_window()
    now = datetime(2026, 8, 10, 14, 0, 5)
    assert window._maybe_announce_hour(now) is False
    window.settings.hourly_announcement = True
    assert window._maybe_announce_hour(now) is True
    assert "14:00" in window.speech_bubble.text()
    assert window._maybe_announce_hour(now) is False
    window.close()
    window.deleteLater()
    app.processEvents()


def test_daily_report_uses_configured_cutoff_once_per_day(monkeypatch) -> None:
    app, window = _create_window()
    calls = []
    window.settings.daily_report_enabled = True
    window.settings.daily_report_time = "22:30"
    report_day = datetime.fromisoformat(window.daily_stats.date).date()

    def fake_report(*, show_dialog, mark_generated=False):
        calls.append((show_dialog, mark_generated))
        if mark_generated:
            window.daily_stats.mark_report_generated()
        return Path("/tmp/lili-test-report.png")

    monkeypatch.setattr(window, "_generate_daily_report", fake_report)
    assert window._maybe_generate_scheduled_daily_report(datetime.combine(report_day, datetime.min.time()).replace(hour=22, minute=29)) is False
    assert window._maybe_generate_scheduled_daily_report(datetime.combine(report_day, datetime.min.time()).replace(hour=22, minute=30)) is True
    assert window._maybe_generate_scheduled_daily_report(datetime.combine(report_day, datetime.min.time()).replace(hour=23, minute=0)) is False
    assert calls == [(False, True)]
    window.close(); window.deleteLater(); app.processEvents()

def test_input_idle_under_ten_minutes_keeps_working(monkeypatch) -> None:
    """The ten-minute grace period does not pause early."""
    app, window = _create_window()
    monkeypatch.setattr(
        "onepic_desktop_pet.window.system_session_state",
        lambda: {"locked": False, "sleeping": False},
    )
    monkeypatch.setattr("onepic_desktop_pet.window.system_idle_seconds", lambda: 599)
    window.settings.auto_pause_on_idle = True
    window.start_work_timer()
    window._check_input_idle()
    assert window.work_timer.is_running
    window.close(); window.deleteLater(); app.processEvents()


def test_input_idle_at_ten_minutes_pauses_without_auto_resume(monkeypatch) -> None:
    app, window = _create_window()
    monkeypatch.setattr(
        "onepic_desktop_pet.window.system_session_state",
        lambda: {"locked": False, "sleeping": False},
    )
    monkeypatch.setattr("onepic_desktop_pet.window.system_idle_seconds", lambda: 600)
    window.settings.auto_pause_on_idle = True
    window.start_work_timer()
    window._check_input_idle()
    assert not window.work_timer.is_running
    assert window.work_timer.state == "paused_idle"
    # Returning input is not a resume command.
    monkeypatch.setattr("onepic_desktop_pet.window.system_idle_seconds", lambda: 0)
    window._check_input_idle()
    assert not window.work_timer.is_running
    window.close(); window.deleteLater(); app.processEvents()


def test_browser_video_fullscreen_pauses_after_short_confirmation(monkeypatch) -> None:
    """Real video fullscreen hides the pet and pauses work after confirmation."""

    app, window = _create_window()
    monkeypatch.setattr(
        "onepic_desktop_pet.window.system_session_state",
        lambda: {"locked": False, "sleeping": False},
    )
    monkeypatch.setattr("onepic_desktop_pet.window.system_idle_seconds", lambda: 0)
    monkeypatch.setattr("onepic_desktop_pet.window.active_fullscreen_video", lambda: True)
    window.settings.auto_pause_on_fullscreen_video = True
    window.start_work_timer()

    # The first observation only arms the debounce window.  A second
    # observation after four seconds confirms that fullscreen is persistent.
    window._check_input_idle()
    assert window.work_timer.is_running
    window._fullscreen_video_started_at -= 4.1
    window._check_input_idle()

    assert not window.work_timer.is_running
    assert window.work_timer.pause_reason == "fullscreen_video"
    window.close(); window.deleteLater(); app.processEvents()


def test_game_fullscreen_pauses_after_short_confirmation(monkeypatch) -> None:
    """全屏游戏与视频一样隐藏六毛并暂停当前工作轮次。"""

    app, window = _create_window()
    monkeypatch.setattr(
        "onepic_desktop_pet.window.system_session_state",
        lambda: {"locked": False, "sleeping": False},
    )
    monkeypatch.setattr("onepic_desktop_pet.window.system_idle_seconds", lambda: 0)
    monkeypatch.setattr("onepic_desktop_pet.window.active_fullscreen_video", lambda: False)
    monkeypatch.setattr("onepic_desktop_pet.window.active_fullscreen_game", lambda: True)
    window.settings.auto_pause_on_fullscreen_video = True
    window.start_work_timer()

    window._check_input_idle()
    assert window.work_timer.is_running
    window._fullscreen_video_started_at -= 4.1
    window._check_input_idle()

    assert not window.work_timer.is_running
    assert window.work_timer.pause_reason == "fullscreen_video"
    window.close(); window.deleteLater(); app.processEvents()


def test_verified_sleep_can_pause_work_timer(monkeypatch) -> None:
    """Only the explicit OS sleep signal may trigger an automatic pause."""
    app, window = _create_window()
    monkeypatch.setattr(
        "onepic_desktop_pet.window.system_session_state",
        lambda: {"locked": False, "sleeping": True},
    )
    window.start_work_timer()
    window._check_input_idle()
    assert not window.work_timer.is_running
    assert "睡眠" in window.speech_bubble.text()
    window.close(); window.deleteLater(); app.processEvents()


def test_verified_lock_screen_can_pause_work_timer(monkeypatch) -> None:
    """Locking the computer pauses the timer; ordinary input silence does not."""
    app, window = _create_window()
    monkeypatch.setattr(
        "onepic_desktop_pet.window.system_session_state",
        lambda: {"locked": True, "sleeping": False},
    )
    window.start_work_timer()
    window._check_input_idle()
    assert not window.work_timer.is_running
    assert "锁屏" in window.speech_bubble.text()
    window.close(); window.deleteLater(); app.processEvents()

def test_work_timer_start_status_reminder_and_finish(tmp_path) -> None:
    """工作计时应显示今日累计，并在连续工作过久时劝用户休息。"""

    now = [datetime(2026, 8, 10, 9, 0, 0)]
    monotonic = [100.0]
    timer = WorkTimerModel(
        path=tmp_path / "work_timer.json",
        now_provider=lambda: now[0],
        monotonic_provider=lambda: monotonic[0],
    )
    app = QApplication.instance() or QApplication([])
    window = PetWindow(PetSettings(), work_timer=timer)
    window.show()
    app.processEvents()

    start_reply = window.start_work_timer()
    assert start_reply.state is PetState.SIT
    assert timer.is_running
    assert window.paused

    now[0] += timedelta(minutes=50)
    monotonic[0] += 50 * 60
    window._work_timer_tick()
    app.processEvents()
    assert window.state is PetState.SLEEPY
    assert "活动" in window.speech_bubble.text()
    assert "50分钟" in timer.status_text()

    finish_reply = window.finish_work_timer()
    assert finish_reply.state is PetState.HAPPY
    assert not timer.is_running
    assert not window.paused
    assert "50分钟" in finish_reply.text
    window.close()
    window.deleteLater()
    app.processEvents()


def test_dialogue_panel_passes_text_to_local_reply() -> None:
    """新版聊天面板发送文字后应交给离线规则并显示回复。"""

    app, window = _create_window()
    window.prompt_dialogue()
    assert window._chat_dialog is not None
    settings_spy = QSignalSpy(window._chat_dialog.settings_requested)
    window._chat_dialog.input.setText("今天有点累")
    window._chat_dialog._submit()
    app.processEvents()

    assert window.state is PetState.SLEEPY
    assert "喝口水" in window.speech_bubble.text()
    assert "离线" in window._chat_dialog.status_label.text()
    assert settings_spy.count() == 0
    window.close()
    window.deleteLater()
    app.processEvents()


def test_agent_checking_never_opens_settings_and_complex_chat_shows_buttons() -> None:
    """Agent 尚在检测时聊天应立即离线回复，只显示手动按钮而不弹设置。"""

    app, window = _create_window()
    window.settings.ai_provider = "codex"
    window.prompt_dialogue()
    assert window._chat_dialog is not None
    settings_spy = QSignalSpy(window._chat_dialog.settings_requested)

    window._chat_dialog.input.setText("帮我写代码并分析这个项目")
    window._chat_dialog._submit()
    app.processEvents()

    assert settings_spy.count() == 0
    assert "离线模式" in window._chat_dialog.transcript.toPlainText()
    assert window._chat_dialog.recovery_actions.isVisible()
    assert "正在后台检测" in window._chat_dialog.status_label.text()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_settings_open_path_rejects_every_non_user_source(monkeypatch) -> None:
    """Agent 检测、错误、超时或内部调用均不能创建设置窗口。"""

    created = []

    class FakeSettingsDialog:
        def __init__(self, *_args, **_kwargs) -> None:
            created.append(True)

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(
        "onepic_desktop_pet.window.AISettingsDialog",
        FakeSettingsDialog,
    )
    app, window = _create_window()

    for source in (
        "startup_detection",
        "checking",
        "agent_disconnected",
        "agent_error",
        "agent_timeout",
        "cli_not_found",
        "ai_call_failed",
        "internal",
        "",
    ):
        assert window.open_settings(source) is False
    assert created == []

    assert window.open_settings("user_action") is True
    assert created == [True]
    window.close()
    window.deleteLater()
    app.processEvents()


@pytest.mark.parametrize(
    "scenario",
    ("connected", "checking", "disconnected", "error", "timeout"),
)
def test_ten_messages_in_every_agent_state_never_open_settings(
    monkeypatch,
    scenario: str,
) -> None:
    """五种 Agent 情况各连续发送十条消息都不得产生设置窗口副作用。"""

    settings_opened = []

    class ForbiddenSettingsDialog:
        def __init__(self, *_args, **_kwargs) -> None:
            settings_opened.append(True)
            raise AssertionError("聊天或 Agent 状态不得创建设置窗口")

    monkeypatch.setattr(
        "onepic_desktop_pet.window.AISettingsDialog",
        ForbiddenSettingsDialog,
    )
    app, window = _create_window()
    window.settings.ai_provider = "codex"
    service_calls = []

    def successful_reply(*_args, **_kwargs) -> str:
        service_calls.append("success")
        return "AI 六毛在这里。"

    def timed_out_reply(*_args, **_kwargs) -> str:
        service_calls.append("timeout")
        raise AIConnectionError("Codex 请求超时。")

    if scenario == "connected":
        window.agent_manager.mark_runtime_success("codex")
        monkeypatch.setattr(window.chat_manager.service, "stream_reply", successful_reply)
    elif scenario == "timeout":
        window.agent_manager.mark_runtime_success("codex")
        monkeypatch.setattr(window.chat_manager.service, "stream_reply", timed_out_reply)
    else:
        state = AgentConnectionState(scenario)
        window.agent_manager._set_status("codex", state, f"测试状态：{scenario}")

    window.prompt_dialogue()
    assert window._chat_dialog is not None
    settings_signal = QSignalSpy(window._chat_dialog.settings_requested)

    for index in range(10):
        window._chat_dialog.input.setText(f"第 {index + 1} 条测试消息")
        window._chat_dialog._submit()
        thread = window.chat_manager._thread
        if thread is not None:
            assert thread.wait(2000)
        app.processEvents()

    assert settings_opened == []
    assert settings_signal.count() == 0
    if scenario == "connected":
        assert len(service_calls) == 10
    elif scenario == "timeout":
        assert service_calls == ["timeout"]
        assert window.agent_manager.status("codex").state is AgentConnectionState.ERROR
    else:
        assert service_calls == []
    window.close()
    window.deleteLater()
    app.processEvents()


def test_interaction_zones_map_head_face_body_and_camera() -> None:
    """窗口相对位置应稳定映射为四种点击区域。"""

    app, window = _create_window()
    center_x = window.label.x() + window.label.width() // 2
    assert window._interaction_zone(QPoint(center_x, 20)) == "head"
    assert (
        window._interaction_zone(
            QPoint(center_x, round(window.label.height() * 0.34))
        )
        == "face"
    )
    assert (
        window._interaction_zone(
            QPoint(
                window.label.x() + round(window.label.width() * 0.2),
                round(window.label.height() * 0.62),
            )
        )
        == "camera"
    )
    assert (
        window._interaction_zone(
            QPoint(center_x, round(window.label.height() * 0.7))
        )
        == "body"
    )
    window.close()
    window.deleteLater()
    app.processEvents()


def test_head_click_tilts_curiously_and_five_body_pokes_annoy() -> None:
    """点头应歪头好奇，短时间连续戳五次身体才切换到轻微生气。"""

    app, window = _create_window()
    initial_affinity = window.mood.affinity
    head = QPoint(window.width() // 2, 20)
    body = QPoint(window.width() // 2, round(window.label.height() * 0.7))

    window._handle_click(head)
    assert window.mood.affinity == initial_affinity + 5
    assert window.state is PetState.CURIOUS

    for _ in range(4):
        window._handle_click(body)
        assert window.state is PetState.SHY
    window._handle_click(body)
    assert window.state is PetState.ANNOYED
    assert window.daily_stats.touches >= 6
    assert window.mood.affinity < initial_affinity + 5
    window.close()
    window.deleteLater()
    app.processEvents()


def test_selfie_completion_shows_photo_bubble() -> None:
    """没有用户原图时不得用生成动画末帧冒充自拍照片。"""

    app, window = _create_window()
    window._selfie_photo = type(window._selfie_photo)()
    window.set_state(PetState.SELFIE)
    window._finish_interaction()
    app.processEvents()

    assert not window.photo_bubble.isVisible()
    window.photo_bubble.hide()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_selfie_photo_uses_high_dpi_backing_pixels() -> None:
    """200% 缩放时横竖照片都应使用高分辨率像素并限制逻辑尺寸。"""

    app, window = _create_window()
    window._selfie_photo = window._pixmaps[PetState.SELFIE][-1]
    photo = window._scaled_selfie_photo(2.0)

    assert photo.devicePixelRatio() == 2.0
    assert max(photo.width(), photo.height()) >= 300
    assert round(photo.width() / photo.devicePixelRatio()) <= 150
    assert round(photo.height() / photo.devicePixelRatio()) <= 210
    window.close()
    window.deleteLater()
    app.processEvents()


def test_selfie_photo_is_positioned_near_visible_character() -> None:
    """照片应贴近人物不透明轮廓，而不是贴着含大块留白的窗口边缘。"""

    app, window = _create_window()
    window._selfie_photo = window._pixmaps[PetState.SELFIE][-1]
    window.move(500, 300)
    window._screen_geometry = lambda: QRect(0, 0, 1200, 900)
    window.set_state(PetState.SELFIE)
    window._show_photo_bubble()
    app.processEvents()

    character_left = window.x() + window.mask().boundingRect().left()
    visual_gap = character_left - (
        window.photo_bubble.x() + window.photo_bubble.width()
    )
    # Windows high-DPI geometry can round the visual edge by one pixel.
    assert abs(visual_gap - 8) <= 1
    window.photo_bubble.hide()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_song_inspiration_uses_independent_timer() -> None:
    """开启歌词气泡后应有独立计时器，不再依赖随机牢骚触发。"""

    app, window = _create_window()

    assert window.song_timer.isActive()
    assert window.song_timer.remainingTime() > 0
    window.close()
    window.deleteLater()
    app.processEvents()


def test_babuda_fallback_changes_system_voice_tone() -> None:
    """未选择本地音频时，连续双击右键仍会获得不同语气的系统语音。"""

    class SpeechRecorder:
        def __init__(self) -> None:
            self.rates = []
            self.pitches = []
            self.words = []

        def setRate(self, value) -> None:
            self.rates.append(value)

        def setPitch(self, value) -> None:
            self.pitches.append(value)

        def say(self, value) -> None:
            self.words.append(value)

    app, window = _create_window()
    recorder = SpeechRecorder()
    window._speech_engine = recorder
    window.settings.babuda_audio_path = ""

    window.play_babuda_voice()
    window.play_babuda_voice()

    assert recorder.words == ["巴布达", "巴布达"]
    assert recorder.rates[0] != recorder.rates[1]
    assert recorder.pitches[0] != recorder.pitches[1]
    window.close()
    window.deleteLater()
    app.processEvents()


def test_long_press_puts_lili_to_sleep_without_plain_click() -> None:
    """长按应切换睡觉图片并记录一次睡觉，不再触发普通点击。"""

    app, window = _create_window()
    window._press_pending = True
    window.dragging = False

    window._trigger_long_press()

    assert window._long_press_triggered
    assert not window._press_pending
    assert window._ambient_activity == "sleep"
    assert window.daily_stats.sleeps == 1
    window.close()
    window.deleteLater()
    app.processEvents()


def test_complete_picture_actions_crossfade_without_resizing_window() -> None:
    """完整动作切换应短暂交叉淡化，结束后保持同一窗口尺寸。"""

    app, window = _create_window()
    original_size = window.size()

    window._set_temporary_activity("guitar", 5000)

    assert window.activity_transition_timer.isActive()
    assert not window._activity_transition_from.isNull()
    assert window.size() == original_size

    for _ in range(window._activity_transition_steps):
        window._activity_transition_tick()

    assert not window.activity_transition_timer.isActive()
    assert window._activity_transition_from.isNull()
    assert window._ambient_activity == "guitar"
    assert window.size() == original_size
    window.close()
    window.deleteLater()
    app.processEvents()
