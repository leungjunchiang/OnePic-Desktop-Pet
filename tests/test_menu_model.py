from __future__ import annotations

from onepic_desktop_pet.menu_model import UnifiedMenuModel


def _top_level_titles(model: UnifiedMenuModel) -> list[str]:
    return [item.title for item in model.items() if not item.separator]


def test_unified_menu_model_shares_dynamic_work_and_visibility_state() -> None:
    state = {
        "work_status": "focus",
        "work_status_text": "⏱ 已工作 12:43",
        "visible": False,
        "always_on_top": True,
        "artist_music_service": "qq",
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
            "companion_love",
            "companion_encourage",
            "companion_rest",
            "companion_status",
            "outfit",
            "rename",
            "settings",
            "size",
            "quick_panel",
            "alarms",
            "show_work_duration",
            "content_update",
            "program_update",
            "show_report",
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
    assert titles[:4] == [
        "和六毛聊聊…",
        "⏱ 已工作 12:43",
        "暂停工作",
        "结束本轮工作",
    ]
    assert "设置…" in titles
    assert "显示六毛" in titles
    assert "检查补充内容更新" not in titles
    assert "检查程序更新" not in titles
    work_record = next(item for item in model.items() if item.title == "工作记录")
    assert "工作报告…" in [item.title for item in work_record.children]
    assert "设置工作报告时间…" not in [item.title for item in work_record.children]
    shortcuts = next(item for item in model.items() if item.title == "快捷工具")
    shortcut_titles = [item.title for item in shortcuts.children]
    assert shortcut_titles[:4] == [
        "六毛快捷口袋…",
        "调整大小…",
        "六毛闹钟…",
        "主人称呼…",
    ]
    updates = next(item for item in shortcuts.children if item.title == "更新与关于")
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
    display_mode = next(item for item in model.items() if item.title == "显示模式")
    assert [item.title for item in display_mode.children] == ["始终置顶", "桌面模式"]
    assert display_mode.children[0].checked is True
    assert display_mode.children[1].checked is False

    # Every Lili-owned entry point renders the same model. The macOS Dock is
    # intentionally excluded because it remains a native system menu.
    assert model.items("pet") == model.items("tray") == model.items("status")

    model.execute("topmost_on", False)
    model.execute("visibility")
    assert called[-2:] == [("topmost_on", False), ("visibility", False)]


def test_unified_menu_model_exposes_end_work_for_focus_and_paused_sessions() -> None:
    callbacks = {
        command: (lambda _checked=False: None)
        for command in ("work_pause", "work_resume", "work_finish")
    }

    for status, label in (
        ("focus", "暂停工作"),
        ("rest", "继续工作"),
    ):
        model = UnifiedMenuModel(
            pet_name="六毛",
            state_provider=lambda status=status, label=label: {
                "work_status": status,
                "work_action_label": label,
            },
            callbacks=callbacks,
        )
        titles = [item.title for item in model.items() if not item.separator]
        assert titles[titles.index(label) : titles.index(label) + 2] == [
            label,
            "结束本轮工作",
        ]
        assert titles[titles.index(label) + 1] == "结束本轮工作"



def test_unified_menu_model_exposes_optional_duration_setting() -> None:
    model = UnifiedMenuModel(
        pet_name="六毛",
        state_provider=lambda: {"show_work_duration": False},
        callbacks={"show_work_duration": lambda checked=False: None},
    )
    shortcut = next(item for item in model.items() if item.title == "快捷工具")
    duration = next(item for item in shortcut.children if item.command == "show_work_duration")
    assert duration.title == "显示本轮工作时长"
    assert duration.checkable is True
    assert duration.checked is False
