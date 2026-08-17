from __future__ import annotations

from onepic_desktop_pet.menu_model import UnifiedMenuModel


def _top_level_titles(model: UnifiedMenuModel) -> list[str]:
    return [item.title for item in model.items() if not item.separator]


def test_unified_menu_model_shares_dynamic_work_and_visibility_state() -> None:
    state = {
        "work_action_label": "暂停工作",
        "visible": False,
        "always_on_top": True,
        "music_status": "当前播放：陈楚生 · 有没有人告诉你",
    }
    called: list[tuple[str, bool]] = []
    callbacks = {
        key: (lambda checked=False, name=key: called.append((name, checked)))
        for key in (
            "chat",
            "work",
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
    music = next(item for item in model.items() if item.title == "音乐")
    assert music.children[-1].title == state["music_status"]
    assert music.children[-1].enabled is False
    assert next(item for item in model.items() if item.title == "始终置顶（关闭即桌面模式）").checked

    model.execute("topmost", False)
    model.execute("visibility")
    assert called[-2:] == [("topmost", False), ("visibility", False)]

