from __future__ import annotations

from onepic_desktop_pet.chat_intent import (
    CASUAL_CHAT,
    CHEN_PROFILE,
    RELATION_QUERY,
    SONG_QUERY,
    WORK_COMPANION,
    classify_intent,
    is_topic_shift,
)
from onepic_desktop_pet.knowledge_manager import retrieve_prompt_context


def test_song_title_sentence_ambiguity_prefers_casual_chat() -> None:
    intent = classify_intent("有没有人告诉你")
    assert intent.primary_intent == CASUAL_CHAT
    assert intent.need_knowledge is False


def test_explicit_song_question_is_knowledge_query() -> None:
    assert classify_intent("《有没有人告诉你》是谁唱的？").primary_intent == SONG_QUERY


def test_broad_profile_gets_large_retrieval_budget() -> None:
    intent = classify_intent("陈楚生的经历如何")
    assert intent.primary_intent == CHEN_PROFILE
    assert intent.retrieval_limit >= 8
    assert intent.answer_style == "detailed"


def test_relation_query_is_not_generic_friends_chat() -> None:
    intent = classify_intent("0713是哪六个人？")
    assert intent.primary_intent == RELATION_QUERY
    assert intent.need_knowledge is True


def test_work_companion_does_not_request_father_knowledge() -> None:
    intent = classify_intent("今天论文写不动")
    assert intent.primary_intent == WORK_COMPANION
    assert intent.need_knowledge is False
    assert intent.story_allowed is True


def test_unrelated_question_does_not_inherit_previous_song_topic() -> None:
    history = [
        ("user", "那你知道《趋光号》这张专辑吗"),
        ("assistant", "知道，这是陈楚生的作品。"),
    ]

    intent = classify_intent("你知道人生的意义是什么吗", history)

    assert intent.primary_intent == CASUAL_CHAT
    assert intent.need_knowledge is False
    assert retrieve_prompt_context("你知道人生的意义是什么吗", history) == ""
    assert is_topic_shift("你知道人生的意义是什么吗", history) is True


def test_anaphoric_followup_keeps_previous_family_topic() -> None:
    history = [
        ("user", "你知道陈楚生吗"),
        ("assistant", "知道，这是六毛的我爹。"),
    ]

    intent = classify_intent("那他的经历呢", history)

    assert intent.primary_intent == CHEN_PROFILE
    assert is_topic_shift("那他的经历呢", history) is False

