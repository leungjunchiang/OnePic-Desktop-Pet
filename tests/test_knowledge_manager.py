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


def test_relations_and_song_catalog_are_local_data() -> None:
    manager = KnowledgeManager(resource_path("resources"))
    relation_text = str(manager.relations)
    song_titles = {item["title"] for item in manager.songs["songs"]}
    assert "我爹" in relation_text
    assert "有没有人告诉你" in song_titles
    assert "荒野国王" in song_titles
