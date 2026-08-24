from __future__ import annotations

from onepic_desktop_pet.menu_model import UnifiedMenuModel


def _top_level_titles(model: UnifiedMenuModel) -> list[str]:
    return [item.title for item in model.items() if not item.separator]


def test_unified_menu_model_is_shared_by_all_lili_entry_points() -> None:
    state = {
        "work_status": "focus",
        "work_status_text": "⏱ 已工作 12:43",
        "visible": False,
        "always_on_top": True,
        "artist_music_service": "qq",
        "show_work_duration": False,
    }
    called: list[tuple[str, bool]] = []
    callbacks = {
        key: (lambda checked=False, name=key: called.append((name, checked)))
        for key in (
            "chat",
            "work",
            "work_pause",
            "work_resume",
            "work_finish",
            "social",
            "music_toggle",
            "music_previous",
            "music_next",
            "chen_artist",
            "artist_music_auto",
            "artist_music_netease",
            "artist_music_qq",
            "artist_music_apple",
            "artist_music_kugou",
            "artist_music_qishui",
            "outfit",
            "rename",
            "settings",
            "size",
            "alarms",
            "show_work_duration",
            "content_update",
            "program_update",
            "show_report",
            "show_todos",
            "add_todo",
            "topmost_on",
            "topmost_off",
            "visibility",
            "quit",
        )
    }
    model = UnifiedMenuModel(
        pet_name="六毛",
        state_provider=lambda: state,
        callbacks=callbacks,
    )

    titles = _top_level_titles(model)
    assert titles[:5] == [
        "和六毛聊聊…",
        "⏱ 已工作 12:43",
        "暂停工作",
        "结束本轮工作",
        "工作报告…",
    ]
    assert "搭子自习室…" in titles
    assert "待办与提醒" in titles
    assert "音乐" in titles
    assert "快捷工具" not in titles
    assert "显示与窗口" in titles
    assert "显示模式" not in titles
    assert "设置" in titles
    assert "更新与关于" in titles
    assert "工作记录" not in titles
    assert "六毛互动" not in titles
    assert "显示六毛" in titles

    todo = next(item for item in model.items() if item.title == "待办与提醒")
    assert [item.title for item in todo.children] == ["查看待办…", "新建待办…", "六毛闹钟…"]

    display = next(item for item in model.items() if item.title == "显示与窗口")
    assert [item.title for item in display.children] == [
        "六毛大小…",
        "显示本轮工作时长",
        "始终置顶",
        "桌面模式",
    ]
    assert display.children[1].checked is False
    assert display.children[2].checked is True
    assert display.children[3].checked is False

    settings = next(item for item in model.items() if item.title == "设置")
    assert [item.title for item in settings.children] == ["主人称呼…", "设置…"]

    updates = next(item for item in model.items() if item.title == "更新与关于")
    assert [item.title for item in updates.children[:2]] == ["检查补充内容更新", "更新到最新版本…"]

    music = next(item for item in model.items() if item.title == "音乐")
    assert [item.title for item in music.children] == [
        "播放 / 暂停",
        "上一首",
        "下一首",
        "",
        "听陈楚生…",
        "音乐平台",
    ]
    platform = next(item for item in music.children if item.title == "音乐平台")
    assert [item.title for item in platform.children] == [
        "跟随系统默认",
        "网易云音乐",
        "QQ 音乐",
        "Apple Music",
        "酷狗音乐",
        "汽水音乐",
    ]
    assert platform.children[2].checked is True

    assert model.items("pet") == model.items("tray") == model.items("status") == model.items("dock")

    model.execute("topmost_on", False)
    model.execute("visibility")
    assert called[-2:] == [("topmost_on", False), ("visibility", False)]


def test_unified_menu_model_exposes_end_work_for_focus_and_paused_sessions() -> None:
    callbacks = {
        command: (lambda _checked=False: None)
        for command in ("work_pause", "work_resume", "work_finish")
    }

    for status, label in (("focus", "暂停工作"), ("rest", "继续工作")):
        model = UnifiedMenuModel(
            pet_name="六毛",
            state_provider=lambda status=status, label=label: {
                "work_status": status,
                "work_action_label": label,
            },
            callbacks=callbacks,
        )
        titles = [item.title for item in model.items() if not item.separator]
        assert titles[titles.index(label) : titles.index(label) + 2] == [label, "结束本轮工作"]


def test_unified_menu_model_exposes_optional_duration_in_display_menu() -> None:
    model = UnifiedMenuModel(
        pet_name="六毛",
        state_provider=lambda: {"show_work_duration": False},
        callbacks={"show_work_duration": lambda checked=False: None},
    )
    display = next(item for item in model.items() if item.title == "显示与窗口")
    duration = next(item for item in display.children if item.command == "show_work_duration")
    assert duration.title == "显示本轮工作时长"
    assert duration.checkable is True
    assert duration.checked is False
