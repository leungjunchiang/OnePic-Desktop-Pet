"""Small local retrieval layer for Lili's father-related knowledge.

This is intentionally a keyword/tag index rather than a vector database.  The
knowledge file is split into titled blocks once at startup, and a request only
receives the few blocks with the highest lexical score.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from .resources import resource_path
from .story_trigger_engine import StoryMatch, get_story_trigger_engine


_SEPARATOR = re.compile(r"\n\s*={10,}\s*\n")
_METADATA = re.compile(r"^(关键词|關鍵詞|标签|標籤)\s*[:：]\s*(.*)$")


@dataclass(frozen=True)
class KnowledgeBlock:
    block_id: str
    title: str
    content: str
    keywords: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True)
class KnowledgeHit:
    block: KnowledgeBlock
    score: int


def _read_json(path: Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return default


def _split_metadata(block_text: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    lines = [line.strip() for line in block_text.splitlines() if line.strip()]
    if not lines:
        return "", (), ()
    title = lines[0].lstrip("# ").strip()
    keywords: list[str] = []
    tags: list[str] = []
    content_lines: list[str] = []
    for line in lines:
        match = _METADATA.match(line)
        if match:
            target = keywords if match.group(1) in {"关键词", "關鍵詞"} else tags
            target.extend(item.strip() for item in match.group(2).split(",") if item.strip())
        else:
            content_lines.append(line)
    return title, tuple(dict.fromkeys(keywords)), tuple(dict.fromkeys(tags))


def _slug(value: str, index: int) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return normalized or f"block_{index}"


class KnowledgeManager:
    def __init__(self, resource_dir: Path | None = None) -> None:
        self.resource_dir = resource_dir or resource_path("resources")
        self.blocks = self._load_blocks()
        self.songs = _read_json(self.resource_dir / "chen_chusheng_songs.json", {})
        self.relations = _read_json(self.resource_dir / "chen_chusheng_relations.json", {})

    def _load_blocks(self) -> tuple[KnowledgeBlock, ...]:
        try:
            text = (self.resource_dir / "chen_chusheng_knowledge.txt").read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return ()
        blocks: list[KnowledgeBlock] = []
        for index, raw in enumerate(_SEPARATOR.split(text)):
            title, keywords, tags = _split_metadata(raw)
            if not title:
                continue
            content_lines = [line.strip() for line in raw.splitlines() if line.strip()]
            content = "\n".join(
                line for line in content_lines
                if not _METADATA.match(line)
            )
            blocks.append(
                KnowledgeBlock(
                    block_id=_slug(title, index),
                    title=title,
                    content=content,
                    keywords=keywords,
                    tags=tags,
                )
            )
        return tuple(blocks)

    def search(
        self,
        query: str,
        history: Iterable[tuple[str, str]] = (),
        *,
        limit: int = 3,
    ) -> tuple[KnowledgeHit, ...]:
        recent = " ".join(
            str(content or "")
            for role, content in history
            if role in {"user", "assistant"}
        )[-900:]
        text = f"{recent} {query}".casefold()
        hits: list[KnowledgeHit] = []
        for block in self.blocks:
            score = 0
            for keyword in (*block.keywords, *block.tags):
                if keyword.casefold() in text:
                    score += 2 if len(keyword) >= 3 else 1
            if block.title.casefold() in text:
                score += 3
            if score:
                hits.append(KnowledgeHit(block, score))
        hits.sort(key=lambda hit: (-hit.score, hit.block.block_id))
        return tuple(hits[: max(0, limit)])

    def by_ids(self, block_ids: Iterable[str]) -> tuple[KnowledgeBlock, ...]:
        wanted = set(block_ids)
        return tuple(
            block
            for block in self.blocks
            if block.block_id in wanted or block.title in wanted
        )


def _render_context(hits: Iterable[KnowledgeHit], story: StoryMatch | None) -> str:
    blocks: list[KnowledgeBlock] = [hit.block for hit in hits]
    if story is not None:
        manager = get_knowledge_manager()
        by_id = {block.block_id: block for block in manager.by_ids(story.story.related_knowledge)}
        blocks.extend(block for block_id, block in by_id.items() if block_id not in {item.block_id for item in blocks})
    if not blocks and story is None:
        return ""
    parts = ["本轮仅供参考的六毛本地知识（只使用与当前问题相关的片段）："]
    for block in blocks[:4]:
        parts.append(f"【{block.title}】\n{block.content[:900]}")
    if story is not None:
        parts.append(
            "故事触发建议："
            f"{story.story.story_summary}；语气：{story.story.reply_style}。"
            "只在自然相关时提一次‘我爹’，不要编造私人原话。"
        )
    return "\n\n".join(parts)[:3600]


@lru_cache(maxsize=1)
def get_knowledge_manager() -> KnowledgeManager:
    try:
        return KnowledgeManager()
    except (FileNotFoundError, OSError):
        # Missing optional resources must never break the chat fallback.
        manager = KnowledgeManager.__new__(KnowledgeManager)
        manager.resource_dir = Path(".")
        manager.blocks = ()
        manager.songs = {}
        manager.relations = {}
        return manager


def retrieve_prompt_context(
    message: str,
    history: Iterable[tuple[str, str]] = (),
) -> str:
    manager = get_knowledge_manager()
    entries = tuple(history)
    hits = manager.search(message, entries, limit=3)
    story = get_story_trigger_engine().match(message, entries, mark_used=True)
    return _render_context(hits, story)


def story_match(
    message: str,
    history: Iterable[tuple[str, str]] = (),
) -> StoryMatch | None:
    return get_story_trigger_engine().match(message, history, mark_used=True)
