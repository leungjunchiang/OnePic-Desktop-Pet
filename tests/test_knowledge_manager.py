from __future__ import annotations

from onepic_desktop_pet.knowledge_manager import KnowledgeManager
from onepic_desktop_pet.resources import resource_path


def test_local_knowledge_is_split_and_retrieved_by_keywords() -> None:
    manager = KnowledgeManager(resource_path("resources"))
    assert len(manager.blocks) >= 10
    hits = manager.search("深圳 酒吧 驻唱")
    assert hits
    assert "深圳" in hits[0].block.title or "深圳" in hits[0].block.content
    assert len(hits) <= 3


def test_normal_work_message_does_not_pull_father_knowledge() -> None:
    manager = KnowledgeManager(resource_path("resources"))
    assert manager.search("今天论文写不动") == ()


def test_broad_profile_retrieves_multiple_timeline_blocks() -> None:
    manager = KnowledgeManager(resource_path("resources"))
    hits = manager.search(
        "陈楚生的经历如何",
        limit=8,
        domains=("profile", "timeline", "history"),
    )
    assert len(hits) >= 6
    titles = " ".join(hit.block.title for hit in hits)
    assert "深圳" in titles
    assert "2007" in titles


def test_relations_and_song_catalog_are_local_data() -> None:
    manager = KnowledgeManager(resource_path("resources"))
    relation_text = str(manager.relations)
    song_titles = {item["title"] for item in manager.songs["songs"]}
    assert "我爹" in relation_text
    assert "有没有人告诉你" in song_titles
    assert "荒野国王" in song_titles


def test_material_cards_cover_baishizhou_and_shui_bi_shui_cha() -> None:
    manager = KnowledgeManager(resource_path("resources"))
    baishizhou = manager.search("白石洲这首歌讲了什么", limit=3)
    assert baishizhou
    assert any("深圳白石洲" in hit.block.content for hit in baishizhou)

    meme = manager.search("谁比谁差是在什么场合", limit=3)
    assert meme
    assert any("四公" in hit.block.content and "披荆斩棘" in hit.block.content for hit in meme)
