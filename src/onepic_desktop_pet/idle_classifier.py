"""Local, privacy-preserving classification for idle focus episodes.

The classifier deliberately uses only coarse signals.  It never records
keystrokes, pointer positions, window titles, or document contents.  A low
confidence result defaults to rest and is eligible for one small correction
hint; a user's correction is stored as an application-specific rule by the
window layer.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IdleEvidence:
    """Coarse context captured around the start of an idle episode."""

    app_name: str = ""
    app_category: str = "other"
    locked: bool = False
    sleeping: bool = False
    fullscreen: bool = False
    media_playing: bool = False
    user_rule: str = ""


@dataclass(frozen=True)
class IdleClassification:
    """A decision plus enough explanation to make it reviewable locally."""

    decision: str
    confidence: float
    reason: str
    app_key: str


def application_rule_key(app_name: str, app_category: str = "other") -> str:
    """Return a stable, privacy-light key for a local correction rule."""

    name = str(app_name or "").replace("\x00", "").strip().casefold()
    if name:
        return name[:160]
    category = str(app_category or "other").strip().casefold()
    return category[:80] or "other"


def classify_idle(evidence: IdleEvidence) -> IdleClassification:
    """Classify an absence without pretending uncertain evidence is certain.

    A saved rule always wins.  Locked/sleeping and actively playing media are
    strong rest signals.  Full-screen work and known work/reading apps are
    useful focus signals.  Everything else remains low-confidence and defaults
    to rest, which is the least surprising accounting choice.
    """

    app_key = application_rule_key(evidence.app_name, evidence.app_category)
    rule = str(evidence.user_rule or "").strip().casefold()
    if rule in {"rest", "focus"}:
        return IdleClassification(rule, 0.99, "已按你对该应用的历史选择处理", app_key)
    if evidence.locked or evidence.sleeping:
        return IdleClassification("rest", 0.99, "电脑处于锁屏或睡眠状态", app_key)
    if evidence.media_playing or evidence.app_category == "music":
        return IdleClassification("rest", 0.92, "检测到媒体播放或音乐应用", app_key)
    if evidence.fullscreen:
        return IdleClassification("focus", 0.84, "前台窗口处于全屏工作状态", app_key)
    if evidence.app_category in {"office", "coding", "reading"}:
        return IdleClassification("focus", 0.78, "前台应用属于工作或阅读场景", app_key)
    return IdleClassification("rest", 0.58, "当前没有足够证据判断为专注", app_key)

