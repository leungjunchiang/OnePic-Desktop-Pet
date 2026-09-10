"""六毛的轻量本地知识检索层；当前消息优先，结构化卡片按需取用。

This is intentionally a keyword/tag index rather than a vector database.  The
knowledge file is split into titled blocks once at startup, and a request only
receives the few blocks with the highest lexical score.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from .chat_intent import classify_intent, knowledge_retrieval_query
from .resources import resource_path
from .story_trigger_engine import StoryMatch, get_story_trigger_engine


_SEPARATOR = re.compile(r"\n\s*={10,}\s*\n")
_METADATA = re.compile(r"^(关键词|關鍵詞|标签|標籤)\s*[:：]\s*(.*)$")
LOGGER = logging.getLogger(__name__)


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
        # The compact TXT remains the compatibility source.  The richer
        # timeline is loaded as separate blocks so a broad profile question
        # can retrieve several stages without sending the whole TXT.
        timeline = _read_json(self.resource_dir / "chen_chusheng_timeline.json", {})
        timeline_items = timeline.get("blocks", []) if isinstance(timeline, dict) else []
        known_ids = {block.block_id for block in blocks}
        if isinstance(timeline_items, list):
            for index, item in enumerate(timeline_items):
                if not isinstance(item, dict):
                    continue
                block_id = str(item.get("id") or _slug(str(item.get("title", "")), index)).strip()
                if not block_id or block_id in known_ids:
                    continue
                title = str(item.get("title", "")).strip()
                content = str(item.get("content", "")).strip()
                if not title or not content:
                    continue
                blocks.append(
                    KnowledgeBlock(
                        block_id=block_id,
                        title=title,
                        content=content,
                        keywords=_as_json_strings(item.get("keywords")),
                        tags=_as_json_strings(item.get("tags")),
                    )
                )
                known_ids.add(block_id)
        # The long user-supplied research files are build-time sources only.
        # Runtime retrieval uses these short cards so ordinary chat never
        # scans or injects the raw documents.
        cards = _read_json(self.resource_dir / "chen_content_cards.json", {})
        if isinstance(cards, dict):
            for group_name in ("person_cards", "meme_cards"):
                group = cards.get(group_name, [])
                if not isinstance(group, list):
                    continue
                for index, item in enumerate(group):
                    if not isinstance(item, dict):
                        continue
                    block_id = str(item.get("id") or _slug(str(item.get("title", "")), index)).strip()
                    title = str(item.get("title") or "").strip()
                    content = str(item.get("content") or "").strip()
                    if not block_id or not title or not content or block_id in known_ids:
                        continue
                    blocks.append(
                        KnowledgeBlock(
                            block_id=block_id,
                            title=title,
                            content=content,
                            keywords=_as_json_strings(item.get("keywords")),
                            tags=_as_json_strings(item.get("tags")),
                        )
                    )
                    known_ids.add(block_id)
        return tuple(blocks)

    def search(
        self,
        query: str,
        history: Iterable[tuple[str, str]] = (),
        *,
        limit: int = 3,
        domains: Iterable[str] = (),
    ) -> tuple[KnowledgeHit, ...]:
        # Retrieval is current-turn first.  Appending the previous assistant
        # answer here made one mistaken biography answer reinforce itself on
        # every later turn.  ``history`` remains in the signature for callers
        # and compatibility, but only the short resolved query supplied by
        # ``retrieve_prompt_context`` is searchable.
        text = str(query or "").casefold()
        wanted_domains = {str(domain).casefold() for domain in domains if str(domain).strip()}
        hits: list[KnowledgeHit] = []
        for block in self.blocks:
            score = 0
            for keyword in (*block.keywords, *block.tags):
                if keyword.casefold() in text:
                    score += 2 if len(keyword) >= 3 else 1
            if block.title.casefold() in text:
                score += 3
            matched_domains = wanted_domains.intersection(tag.casefold() for tag in block.tags)
            if matched_domains:
                # Domain boosts make broad questions such as “经历如何”
                # retrieve the timeline even when the query has no exact
                # year/place keyword.  It never applies to casual chat,
                # because the caller only supplies domains for knowledge
                # intents.
                score += 4 + len(matched_domains)
            if score:
                hits.append(KnowledgeHit(block, score))
        order = {block.block_id: index for index, block in enumerate(self.blocks)}
        hits.sort(key=lambda hit: (-hit.score, order.get(hit.block.block_id, 0)))
        return tuple(hits[: max(0, limit)])

    def by_ids(self, block_ids: Iterable[str]) -> tuple[KnowledgeBlock, ...]:
        wanted = set(block_ids)
        return tuple(
            block
            for block in self.blocks
            if block.block_id in wanted or block.title in wanted
        )


def _as_json_strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _render_context(
    hits: Iterable[KnowledgeHit],
    story: StoryMatch | None,
    *,
    max_blocks: int = 4,
    max_chars: int = 3600,
) -> str:
    blocks: list[KnowledgeBlock] = [hit.block for hit in hits]
    if story is not None:
        manager = get_knowledge_manager()
        by_id = {block.block_id: block for block in manager.by_ids(story.story.related_knowledge)}
        blocks.extend(block for block_id, block in by_id.items() if block_id not in {item.block_id for item in blocks})
    if not blocks and story is None:
        return ""
    parts = ["本轮仅供参考的六毛本地知识（只使用与当前问题相关的片段）："]
    for block in blocks[:max_blocks]:
        parts.append(f"【{block.title}】\n{block.content[:900]}")
    if story is not None:
        parts.append(
            "故事触发建议："
            f"{story.story.story_summary}；语气：{story.story.reply_style}。"
            "只在自然相关时提一次‘我爹’，不要编造私人原话。"
        )
    return "\n\n".join(parts)[:max_chars]


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
    intent = classify_intent(message, entries)
    if not intent.need_knowledge:
        return ""
    retrieval_started = time.monotonic()
    hits = manager.search(
        knowledge_retrieval_query(message, entries),
        (),
        limit=intent.retrieval_limit,
        domains=intent.knowledge_domains,
    )
    LOGGER.info(
        "AI knowledge metrics intent=%s retrieval_ms=%d rag_blocks=%d",
        intent.primary_intent,
        int((time.monotonic() - retrieval_started) * 1000),
        len(hits),
    )
    # Retrieval must be side-effect free.  The chat manager is the only layer
    # allowed to consume a story cooldown; the model merely receives a hint.
    story = None
    if intent.story_allowed:
        story = get_story_trigger_engine().match(message, entries, mark_used=False)
    max_chars = 3000 if intent.primary_intent == "chen_chusheng_profile" else 2200
    return _render_context(
        hits,
        story,
        max_blocks=min(5, max(1, intent.retrieval_limit)),
        max_chars=max_chars,
    )


def story_match(
    message: str,
    history: Iterable[tuple[str, str]] = (),
) -> StoryMatch | None:
    return get_story_trigger_engine().match(message, history, mark_used=True)
