"""六毛食物场景的纯规则；不维护饱食度，也不创建第二套计时器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class FoodSceneSpec:
    item_key: str
    title: str
    action: str
    scene_type: str
    default_minutes: int | None
    description: str


FOOD_SCENES: dict[str, FoodSceneSpec] = {
    "coffee": FoodSceneSpec("coffee", "☕ 咖啡开工", "喝咖啡", "focus", 30, "选件事情，和六毛干半小时。"),
    "expensive_coffee": FoodSceneSpec("expensive_coffee", "☕ 喝贵的", "昂贵咖啡", "deep_focus", 150, "最长 150 分钟深度工作，连续专注满 2 小时再得普通咖啡。"),
    "milk_tea": FoodSceneSpec("milk_tea", "🧋 奶茶时间", "奶茶", "rest", 10, "正式休息 10 或 15 分钟。"),
    "cake": FoodSceneSpec("cake", "🍰 庆祝一下", "蛋糕", "celebrate", 0, "给刚刚完成的事情留一个纪念。"),
    "tea": FoodSceneSpec("tea", "🍵 喝会儿茶", "茶", "companion", None, "没有固定倒计时，六毛陪你慢慢待一会儿。"),
}


def scene_spec(item_key: str) -> FoodSceneSpec | None:
    return FOOD_SCENES.get(str(item_key).strip())


def interaction_mode_action(mode: str, focusing: bool) -> str:
    """Return the receiver-side policy for a remote food event."""
    mode = str(mode or "focus_priority").strip().lower()
    if mode == "do_not_disturb":
        return "silent"
    if focusing and mode == "focus_priority":
        return "queue"
    return "immediate"


def scene_text(scene: Mapping[str, Any] | None) -> str:
    if not scene:
        return ""
    item_key = str(scene.get("item_key") or "")
    spec = scene_spec(item_key)
    title = spec.title if spec else str(scene.get("name") or "六毛场景")
    remaining = scene.get("remaining_seconds")
    if remaining is not None:
        seconds = max(0, int(remaining))
        return f"{title} · {seconds // 60:02d}:{seconds % 60:02d}"
    return title
