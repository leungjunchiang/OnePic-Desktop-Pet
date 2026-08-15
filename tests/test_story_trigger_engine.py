from __future__ import annotations

import pytest

from onepic_desktop_pet.story_trigger_engine import (
    StoryTriggerEngine,
    load_story_triggers,
)


@pytest.mark.parametrize(
    ("message", "story_id"),
    (
        ("做了很久一直没结果", "long_no_result"),
        ("没人看见我做的东西", "long_no_result"),
        ("我想放弃学习", "quit_learning"),
        ("我想换城市重新开始", "restart_city"),
        ("是不是太晚了，年龄大了", "too_late"),
        ("终于做完论文了", "major_task_done"),
        ("我凌晨还在工作", "work_too_long"),
        ("今天特别想家", "homesick"),
        ("我想去三亚看看", "homesick"),
        ("连续工作三小时了", "work_too_long"),
    ),
)
def test_ten_story_dialogues_select_the_expected_low_frequency_story(
    message: str,
    story_id: str,
) -> None:
    engine = StoryTriggerEngine(load_story_triggers())
    match = engine.match(message)
    assert match is not None
    assert match.story_id == story_id


def test_cooldown_prevents_repeating_the_same_story() -> None:
    engine = StoryTriggerEngine(load_story_triggers())
    assert engine.match("做了很久一直没结果") is not None
    assert engine.match("还是一直没结果") is None


def test_plain_work_struggle_does_not_force_a_father_story() -> None:
    engine = StoryTriggerEngine(load_story_triggers())
    assert engine.match("今天论文写不动") is None
