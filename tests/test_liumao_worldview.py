from __future__ import annotations

import random

from onepic_desktop_pet.behavior import PetState
from onepic_desktop_pet.liumao_worldview import (
    classify_worldview,
    family_music_mode,
    worldview_prompt_context,
    worldview_response,
)


def test_worldview_identity_and_boundaries_are_short() -> None:
    assert worldview_response("你是谁", random.Random(1)).text == "六毛。"
    assert worldview_response("谁家的", random.Random(1)).text == "陈楚生家的。"
    assert worldview_response("陈楚生是谁", random.Random(1)).text == "我爹。"
    assert worldview_response("你认识陈楚生吗", random.Random(1)).key == "father_identity"
    assert "我爹" in worldview_response("你认识陈楚生吗", random.Random(1)).text
    assert worldview_response("你爹现在在哪", random.Random(1)).key == "privacy"
    assert "没跟我报备" in worldview_response("你爹现在在哪", random.Random(1)).text


def test_worldview_topics_use_family_tone_without_overloading_normal_chat() -> None:
    assert worldview_response("没结果但我还在努力工作", random.Random(1)).key == "effort"
    assert worldview_response("0713又凑一块了", random.Random(1)).key == "friends"
    assert worldview_response("今天有点累", random.Random(1)) is None
    assert classify_worldview("今天有点累") is None


def test_family_song_and_prompt_are_on_demand() -> None:
    response = worldview_response("有没有人告诉你这首别切", random.Random(0))
    assert response is not None
    assert response.state is PetState.SIT
    assert worldview_prompt_context("今天写论文") == ""
    assert "少说话" in worldview_prompt_context("正在听有没有人告诉你")
    assert "我爹" in worldview_prompt_context("你认识陈楚生吗")


def test_family_music_mode_only_uses_public_track_metadata() -> None:
    assert family_music_mode("陈楚生", "任意歌名")
    assert family_music_mode("", "有没有人告诉你")
    assert not family_music_mode("其他歌手", "其他歌名")

