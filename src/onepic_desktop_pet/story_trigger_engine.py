"""Low-frequency story triggers for Lili's local character knowledge.

The engine deliberately stays small and deterministic.  It reads the local
JSON resource once, requires a configurable number of matching signals, and
remembers a cooldown per story so the same father-story cannot be repeated in
every consecutive chat turn.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .resources import resource_path


@dataclass(frozen=True)
class StoryTrigger:
    story_id: str
    keywords: tuple[str, ...]
    exclude_keywords: tuple[str, ...]
    related_knowledge: tuple[str, ...]
    story_summary: str
    reply_style: str
    reply_templates: tuple[str, ...]
    trigger_threshold: int
    cooldown_seconds: float
    strong_keywords: tuple[str, ...] = ()
    confidence_threshold: float = 0.72
    cooldown_turns: int = 6


@dataclass(frozen=True)
class StoryMatch:
    story: StoryTrigger
    matched_keywords: tuple[str, ...]

    @property
    def story_id(self) -> str:
        return self.story.story_id


def _as_strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def load_story_triggers(path: Path | None = None) -> tuple[StoryTrigger, ...]:
    """Load story definitions without importing Qt or contacting a service."""

    story_path = path or resource_path("resources/chen_chusheng_stories.json")
    payload = json.loads(story_path.read_text(encoding="utf-8"))
    items = payload.get("stories", payload) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return ()
    stories: list[StoryTrigger] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        story_id = str(item.get("id", "")).strip()
        if not story_id:
            continue
        stories.append(
            StoryTrigger(
                story_id=story_id,
                keywords=_as_strings(item.get("keywords")),
                exclude_keywords=_as_strings(item.get("exclude_keywords")),
                related_knowledge=_as_strings(item.get("related_knowledge")),
                story_summary=str(item.get("story_summary", "")).strip(),
                reply_style=str(item.get("reply_style", "")).strip(),
                reply_templates=_as_strings(item.get("reply_templates")),
                trigger_threshold=max(1, int(item.get("trigger_threshold", 1))),
                cooldown_seconds=max(0.0, float(item.get("cooldown", 3600))),
                strong_keywords=_as_strings(item.get("strong_keywords")),
                confidence_threshold=max(0.0, min(1.0, float(item.get("confidence", 0.72)))),
                cooldown_turns=max(1, int(item.get("cooldown_turns", 6))),
            )
        )
    return tuple(stories)


class StoryTriggerEngine:
    """Select a single high-confidence, cooldown-protected story."""

    def __init__(
        self,
        stories: Iterable[StoryTrigger] = (),
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.stories = tuple(stories)
        self.clock = clock
        self._last_used: dict[str, float] = {}
        self._last_used_turn: dict[str, int] = {}
        self._last_story_turn: int | None = None
        self._turn = 0

    @classmethod
    def from_resources(cls) -> "StoryTriggerEngine":
        try:
            stories = load_story_triggers()
        except (FileNotFoundError, OSError, ValueError, TypeError):
            stories = ()
        return cls(stories)

    @staticmethod
    def _message_text(message: str, history: Iterable[tuple[str, str]]) -> str:
        recent = [
            str(content or "")
            for role, content in history
            if role == "user"
        ][-4:]
        return " ".join([*recent, str(message or "")]).casefold()

    def match(
        self,
        message: str,
        history: Iterable[tuple[str, str]] = (),
        *,
        mark_used: bool = True,
    ) -> StoryMatch | None:
        text = self._message_text(message, history)
        if not text.strip():
            return None
        now = self.clock()
        if mark_used:
            # A turn advances even when no story matches; otherwise a
            # cooldown measured in turns could never expire during ordinary
            # conversation.
            self._turn += 1
        current_turn = self._turn
        candidates: list[tuple[float, StoryTrigger, tuple[str, ...]]] = []
        for story in self.stories:
            last_used = self._last_used.get(story.story_id)
            if last_used is not None and now - last_used < story.cooldown_seconds:
                continue
            last_turn = self._last_used_turn.get(story.story_id)
            if last_turn is not None and current_turn - last_turn < story.cooldown_turns:
                continue
            if self._last_story_turn is not None and current_turn - self._last_story_turn < 3:
                continue
            if any(marker.casefold() in text for marker in story.exclude_keywords):
                continue
            matched = tuple(
                keyword
                for keyword in story.keywords
                if keyword.casefold() in text
            )
            if len(set(matched)) < story.trigger_threshold:
                continue
            unique_matched = tuple(dict.fromkeys(matched))
            strong = tuple(
                keyword
                for keyword in story.strong_keywords
                if keyword.casefold() in text
            )
            # A strong phrase is enough for a high-confidence trigger.  A
            # generic keyword alone is deliberately capped below the default
            # threshold, so “今天论文写不动” cannot summon a father story.
            confidence = min(
                0.98,
                (0.84 if strong else 0.0)
                + min(0.12, max(0, len(set(unique_matched)) - len(set(strong))) * 0.04),
            )
            if confidence < story.confidence_threshold:
                continue
            candidates.append((confidence, story, unique_matched))
        if not candidates:
            return None
        _, story, matched = max(candidates, key=lambda item: item[0])
        if mark_used:
            self._last_used[story.story_id] = now
            self._last_used_turn[story.story_id] = current_turn
            self._last_story_turn = current_turn
        return StoryMatch(story, matched)

    def reset(self) -> None:
        self._last_used.clear()
        self._last_used_turn.clear()
        self._last_story_turn = None
        self._turn = 0


_DEFAULT_ENGINE: StoryTriggerEngine | None = None


def get_story_trigger_engine() -> StoryTriggerEngine:
    global _DEFAULT_ENGINE
    if _DEFAULT_ENGINE is None:
        _DEFAULT_ENGINE = StoryTriggerEngine.from_resources()
    return _DEFAULT_ENGINE

