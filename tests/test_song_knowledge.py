"""歌曲作品卡与本地歌词匹配的安全边界。"""

from __future__ import annotations

import json

from onepic_desktop_pet.song_knowledge import (
    find_song_matches,
    load_public_cards,
    offline_song_reply,
    song_prompt_context,
)


def _private_catalog(tmp_path) -> str:
    path = tmp_path / "private-lyrics.txt"
    path.write_text(
        "有没有人告诉你\n陈楚生\n这是一个只在本机使用的歌词片段测试\n"
        "------------------------------\n"
        "山楂花\n陈楚生\n另一段本机歌词内容\n",
        encoding="utf-8",
    )
    return str(path)


def test_public_cards_are_metadata_only() -> None:
    cards = load_public_cards()
    assert len(cards) >= 117
    assert {card.title for card in cards} >= {"有没有人告诉你", "山楂花", "荒野国王"}
    payload = json.dumps([card.to_mapping() for card in cards], ensure_ascii=False)
    assert "歌词正文" not in payload
    assert "这是一个只在本机使用的歌词片段测试" not in payload
    assert all(not hasattr(card, "lyrics") for card in cards)


def test_bare_ambiguous_title_is_not_forced_into_song_match(tmp_path) -> None:
    assert find_song_matches("有没有人告诉你", configured_path=_private_catalog(tmp_path)) == ()


def test_explicit_song_question_uses_public_card_without_lyrics() -> None:
    context = song_prompt_context("《有没有人告诉你》是谁唱的？")
    assert "《有没有人告诉你》" in context
    assert "本地歌曲作品卡" in context
    assert "不要复述匹配到的原句" in context


def test_private_lyric_fragment_identifies_song_without_returning_line(tmp_path) -> None:
    path = _private_catalog(tmp_path)
    matches = find_song_matches("这是一个只在本机使用的歌词片段测试", configured_path=path)
    assert matches
    assert matches[0].card.title == "有没有人告诉你"
    assert matches[0].match_type == "lyric_fragment"
    context = song_prompt_context("这是一个只在本机使用的歌词片段测试", configured_path=path)
    assert "有没有人告诉你" in context
    assert "这是一个只在本机使用的歌词片段测试" not in context


def test_offline_song_reply_does_not_continue_lyrics(tmp_path) -> None:
    answer = offline_song_reply(
        "这是一个只在本机使用的歌词片段测试",
        configured_path=_private_catalog(tmp_path),
    )
    assert answer is not None
    assert "有没有人告诉你" in answer
    assert "这是一个只在本机使用的歌词片段测试" not in answer
