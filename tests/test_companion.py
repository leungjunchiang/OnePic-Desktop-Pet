"""
本模块测试“六毛工作搭子”的离线喂食、状态反馈和关键词对话逻辑。

测试不创建窗口、不写聊天记录，也不访问网络。
"""

import pytest

from onepic_desktop_pet.behavior import PetMood, PetState
from onepic_desktop_pet.companion import CompanionModel, FOOD_OPTIONS


def test_food_menu_has_three_distinct_options() -> None:
    assert [food.key for food in FOOD_OPTIONS] == ["apple", "cookie", "milk"]
    assert [food.label for food in FOOD_OPTIONS] == ["苹果", "小饼干", "热牛奶"]


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
