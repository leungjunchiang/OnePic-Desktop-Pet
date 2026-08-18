from __future__ import annotations

from onepic_desktop_pet.menu_model import UnifiedMenuModel


def _top_level_titles(model: UnifiedMenuModel) -> list[str]:
    return [item.title for item in model.items() if not item.separator]


def test_unified_menu_model_shares_dynamic_work_and_visibility_state() -> None:
    state = {
        "work_action_label": "暂停工作",
        "work_status": "focus",
        "visible": False,
        "always_on_top": True,
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
            "music_random",
            "companion_love",
            "companion_encourage",
            "companion_rest",
            "companion_status",
            "outfit",
            "rename",
            "settings",
            "size",
            "content_update",
            "program_update",
            "topmost",
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
    assert titles[:6] == [
        "和六毛聊聊…",
        "暂停工作",
        "搭子自习室…",
        "音乐",
        "六毛互动",
        "换装与外观…",
    ]
    assert "设置" in titles
    assert "显示六毛" in titles
    assert "检查补充内容更新" not in titles
    assert "检查程序更新" not in titles
    settings = next(item for item in model.items() if item.title == "设置")
    settings_titles = [item.title for item in settings.children]
    assert settings_titles[:3] == ["主人称呼", "AI 与陪伴", "调整大小"]
    updates = next(item for item in settings.children if item.title == "更新与关于")
    assert [item.title for item in updates.children[:2]] == ["检查补充内容更新", "更新到最新版本…"]
    music = next(item for item in model.items() if item.title == "音乐")
    assert [item.title for item in music.children] == [
        "播放 / 暂停",
        "上一首",
        "下一首",
        "随机听陈楚生",
    ]
    assert next(item for item in model.items() if item.title == "始终置顶（关闭即桌面模式）").checked

    model.execute("topmost", False)
    model.execute("visibility")
    assert called[-2:] == [("topmost", False), ("visibility", False)]


def test_unified_menu_model_exposes_end_work_for_focus_and_paused_sessions() -> None:
    callbacks = {
        command: (lambda _checked=False: None)
        for command in ("work_pause", "work_resume", "work_finish")
    }

    for status, label, expected in (
        ("focus", "暂停工作", ["暂停工作", "结束工作"]),
        ("rest", "继续工作", ["继续工作", "结束工作"]),
    ):
        model = UnifiedMenuModel(
            pet_name="六毛",
            state_provider=lambda status=status, label=label: {
                "work_status": status,
                "work_action_label": label,
            },
            callbacks=callbacks,
        )
        item = next(item for item in model.items() if item.title == label)
        assert item.children
        assert [child.title for child in item.children] == expected
        assert all(child.enabled for child in item.children)



def test_unified_menu_model_exposes_optional_duration_setting() -> None:
    model = UnifiedMenuModel(
        pet_name="六毛",
        state_provider=lambda: {"show_work_duration": False},
        callbacks={"show_work_duration": lambda checked=False: None},
    )
    settings = next(item for item in model.items() if item.title == "设置")
    duration = next(item for item in settings.children if item.command == "show_work_duration")
    assert duration.title == "显示本轮工作时长"
    assert duration.checkable is True
    assert duration.checked is False