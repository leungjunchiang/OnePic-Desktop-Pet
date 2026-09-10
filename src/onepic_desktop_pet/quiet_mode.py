"""Privacy-preserving smart do-not-disturb heuristics."""

from __future__ import annotations

from dataclasses import dataclass

from .activity import active_application_name, active_window_is_fullscreen, classify_application


@dataclass(frozen=True)
class QuietModeSnapshot:
    blocked: bool
    reason: str = ""


def detect_quiet_mode(process_name: str | None = None, fullscreen: bool | None = None) -> QuietModeSnapshot:
    """Detect meeting/presentation/game/fullscreen contexts from process name only."""

    name = str(process_name if process_name is not None else active_application_name()).casefold()
    if any(token in name for token in ("teams", "zoom", "腾讯会议", "dingtalk", "lark", "feishu", "webex")):
        return QuietModeSnapshot(True, "会议中")
    if any(token in name for token in ("powerpnt", "keynote", "obs", "screenpresso", "presentation")):
        return QuietModeSnapshot(True, "演示或录屏中")
    if any(token in name for token in ("steam", "epicgames", "league", "valorant", "genshin", "minecraft", "game")):
        return QuietModeSnapshot(True, "游戏中")
    if bool(fullscreen if fullscreen is not None else active_window_is_fullscreen()):
        return QuietModeSnapshot(True, "全屏工作中")
    if classify_application(name) == "coding" and any(token in name for token in ("terminal", "code", "pycharm")):
        return QuietModeSnapshot(False, "高强度工作可选择免打扰")
    return QuietModeSnapshot(False, "")
