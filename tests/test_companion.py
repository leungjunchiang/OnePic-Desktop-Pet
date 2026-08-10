"""
本模块测试 Lili 的离线喂食、陪伴动作、工作提醒、牢骚、报时和关键词对话逻辑。

测试不创建窗口、不写聊天记录，也不访问网络。
"""

import pytest

from onepic_desktop_pet.behavior import PetMood, PetState
from onepic_desktop_pet.companion import (
    COMPANION_ACTIONS,
    CompanionModel,
    FOOD_OPTIONS,
)


def test_food_menu_includes_coffee_and_tea() -> None:
    assert [food.key for food in FOOD_OPTIONS] == [
        "apple", "cookie", "milk", "coffee", "tea"
    ]
    assert [food.label for food in FOOD_OPTIONS][-2:] == ["咖啡", "热茶"]


def test_feeding_increases_fullness_energy_and_affinity() -> None:
    mood = PetMood(affinity=50, energy=60, boredom=30, fullness=55)
    companion = CompanionModel(mood)

    reply = companion.feed("apple")

    assert reply.state is PetState.HAPPY
    assert "苹果" in reply.text
    assert (mood.affinity, mood.energy, mood.boredom, mood.fullness) == (
        53,
        62,
        24,
        73,
    )


def test_full_pet_declines_more_food_without_overflow() -> None:
    mood = PetMood(fullness=98)
    companion = CompanionModel(mood)

    reply = companion.feed("milk")

    assert reply.state is PetState.CURIOUS
    assert "圆滚滚" in reply.text
    assert mood.fullness == 98


def test_unknown_food_is_rejected() -> None:
    with pytest.raises(ValueError, match="未知食物"):
        CompanionModel(PetMood()).feed("hotpot")


def test_offline_dialogue_covers_work_fatigue_and_fallback() -> None:
    companion = CompanionModel(PetMood())

    assert companion.reply_to("今天工作很多").state is PetState.HAPPY
    assert companion.reply_to("我有点累").state is PetState.SLEEPY
    fallback = companion.reply_to("请提醒我整理桌面")
    assert fallback.state is PetState.CURIOUS
    assert "整理桌面" in fallback.text
    assert not hasattr(companion, "history")


def test_companion_actions_cover_work_love_encouragement_and_comfort() -> None:
    companion = CompanionModel(PetMood())

    assert [action.key for action in COMPANION_ACTIONS] == [
        "focus",
        "encourage",
        "love",
        "celebrate",
        "comfort",
        "rest",
        "stretch",
        "think",
        "quiet",
        "victory",
    ]
    assert companion.perform_action("focus").state is PetState.SIT
    assert companion.perform_action("encourage").state is PetState.WAVE
    assert companion.perform_action("love").state is PetState.SHY
    assert companion.perform_action("celebrate").state is PetState.HAPPY
    assert companion.perform_action("comfort").state is PetState.SHY
    assert companion.perform_action("rest").state is PetState.SLEEPY


def test_dialogue_gives_specific_loving_and_supportive_responses() -> None:
    companion = CompanionModel(PetMood())

    assert companion.reply_to("六毛我爱你").state is PetState.SHY
    assert "也很爱你" in companion.reply_to("我喜欢你").text
    assert "不代表你不行" in companion.reply_to("我什么都做不到").text
    assert "不是对你的判决" in companion.reply_to("今天工作出错了").text
    assert "六毛在这里" in companion.reply_to("我觉得很孤独").text
    assert "只做五分钟" in companion.reply_to("完全没动力").text


def test_work_timer_messages_encourage_and_advise_rest() -> None:
    companion = CompanionModel(PetMood())

    assert companion.work_started().state is PetState.SIT
    assert "25分钟" in companion.work_reminder("focus", "25分钟").text
    assert "活动" in companion.work_reminder("break", "50分钟").text
    long_reply = companion.work_reminder("long_break", "1小时30分钟")
    assert long_reply.state is PetState.SLEEPY
    assert "别拿身体硬撑" in long_reply.text
    assert "值得" in companion.work_finished("1小时").text


def test_ambient_grumble_and_hourly_announcement_are_local() -> None:
    companion = CompanionModel(PetMood())

    assert companion.ambient_grumble().text
    morning = companion.hourly_announcement(9)
    late = companion.hourly_announcement(23)
    assert morning.text.startswith("现在是 09:00")
    assert "休息" in late.text
