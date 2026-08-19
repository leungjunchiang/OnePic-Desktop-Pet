"""六毛钱袋的本地优先经济核心。

这里保存的是六毛在荒野王国里的真实生活记录，而不是游戏属性：
真实专注产生工资；消费只改变钱袋余额；库存、家当、状态、图鉴和称号
都由同一个原子化账本服务维护。旧版 economy.json 会在读取时兼容，不会清空
余额、历史工资或昂贵咖啡库存。
"""

from __future__ import annotations

import copy
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .local_data import local_data_path, read_json, write_json_atomic


FOCUS_DAILY_CAP_SECONDS = 8 * 60 * 60
FOCUS_COINS_PER_HOUR = 6
EARLY_BIRD_START_HOUR = 10
EARLY_BIRD_MIN_SECONDS = 20 * 60
MAX_ACHIEVEMENT_AMOUNT = 100
MAX_MONTHLY_ACHIEVEMENTS = 3
MAX_MONTHLY_ACHIEVEMENT_SUBMISSIONS = 4
REQUIRED_ACHIEVEMENT_WITNESSES = 2
ACHIEVEMENT_REWARD = 200
# A companion scene without an explicit duration (currently tea) must not
# permanently block the next scene after an app restart.
OPEN_FOOD_SCENE_LIFETIME_SECONDS = 60

# Only fully implemented household facilities are available for new purchases.
ACTIVE_HOUSEHOLD_KEYS = frozenset({"coffee_pot"})


ITEM_CATALOG: dict[str, dict[str, Any]] = {
    "coffee": {
        "name": "普通咖啡", "price": 12, "group": "吃点喝点",
        "kind": "consumable", "state": "coffee", "collection": "coffee",
        "scene_type": "focus", "scene_minutes": 30,
        "description": "喝杯咖啡，选件事情和六毛干半小时。",
    },
    "expensive_coffee": {
        "name": "昂贵咖啡", "price": 60, "group": "吃点喝点",
        "kind": "consumable", "state": "expensive_coffee", "collection": "expensive_coffee",
        "scene_type": "deep_focus", "scene_minutes": 150,
        "description": "喝贵的，开一局最长 150 分钟深度工作；连续专注满 2 小时再得普通咖啡 ×1。",
    },
    "milk_tea": {
        "name": "奶茶", "price": 20, "group": "吃点喝点",
        "kind": "consumable", "state": "milk_tea_break", "collection": "milk_tea",
        "scene_type": "rest", "scene_minutes": 10,
        "description": "正式歇一会儿，选择 10 或 15 分钟。",
    },
    "cake": {
        "name": "小蛋糕", "price": 32, "group": "吃点喝点",
        "kind": "consumable", "state": "celebrating", "collection": "cake",
        "scene_type": "celebrate", "scene_minutes": 0,
        "description": "想庆祝就吃；六毛会把这段开心记下来。",
    },
    "tea": {
        "name": "茶", "price": 10, "group": "吃点喝点",
        "kind": "consumable", "state": "tea", "collection": "tea",
        "scene_type": "companion", "scene_minutes": 0,
        "description": "不赶时间，六毛陪你慢慢待一会儿。",
    },
    "alarm_clock": {
        "name": "小闹钟", "price": 15, "group": "添置家当",
        "kind": "household", "collection": "alarm_clock",
        "description": "六毛家的第一件家当；为以后叫醒和开工提醒预留位置。",
    },
    "coffee_pot": {
        "name": "咖啡壶", "price": 144, "group": "添置家当",
        "kind": "household", "collection": "coffee_pot",
        "description": "每天补给普通咖啡 ×1，每天最多一杯；每杯开启 30 分钟咖啡工作场景。",
        "effect": {"daily_inventory": {"coffee": 1}, "daily_limit": 1},
    },
    "desk_lamp": {
        "name": "小台灯", "price": 35, "group": "添置家当",
        "kind": "household", "collection": "desk_lamp",
        "description": "晚间工作时记录六毛家的夜读生活。",
    },
    "sofa": {
        "name": "小沙发", "price": 50, "group": "添置家当",
        "kind": "household", "collection": "sofa",
        "description": "正式休息时，六毛终于有地方躺一会儿。",
    },
    "bookshelf": {
        "name": "小书架", "price": 60, "group": "添置家当",
        "kind": "household", "collection": "bookshelf",
        "description": "长时间专注以后，偶尔触发看书生活事件。",
    },
    "radio": {
        "name": "收音机", "price": 80, "group": "添置家当",
        "kind": "household", "collection": "radio",
        "description": "和现有音乐入口相连，不新增播放器状态系统。",
    },
    "guitar": {
        "name": "吉他", "price": 120, "group": "添置家当",
        "kind": "household", "collection": "guitar",
        "description": "给六毛的生活添一点已有音乐动作的仪式感。",
    },
    "wild_bank": {
        "name": "荒野小金库", "price": 200, "group": "添置家当",
        "kind": "household", "collection": "wild_bank",
        "description": "长期攒钱的里程碑，不改变富豪榜统计。",
    },
}

# Older releases persisted some food counts under their Chinese display name,
# while newer releases use the stable item key. Treat both as the same slot so
# a visible warehouse item can always be consumed after an upgrade.
INVENTORY_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "coffee": ("coffee", "普通咖啡"),
    "expensive_coffee": ("expensive_coffee", "昂贵咖啡"),
    "milk_tea": ("milk_tea", "奶茶"),
    "cake": ("cake", "小蛋糕"),
    "tea": ("tea", "茶"),
}


CATEGORY_LABELS = {
    "salary": "有效专注工资",
    "focus_wage": "有效专注工资",
    "early_bird": "早鸟补贴",
    "early_bird_bonus": "早鸟补贴",
    "performance": "任务绩效",
    "windfall": "成果见证",
    "achievement_income": "成果见证",
    "social_reward": "搭子互动奖励",
    "special_reward": "特殊奖励",
    "reward": "奖励",
    "spend": "消费",
    "expense": "消费",
    "gift_sent": "请搭子",
    "gift_received": "收到搭子礼物",
    "item_use": "使用道具",
    "supply_reward": "每日补给",
    "other": "其他",
}
INCOME_CATEGORIES = {
    "salary", "focus_wage", "performance", "windfall",
    "achievement_income", "social_reward", "special_reward", "reward",
}
EXPENSE_CATEGORIES = {"spend", "expense", "gift_sent"}
MANUAL_INCOME_CATEGORIES = {"windfall", "achievement_income", "performance"}


@dataclass(frozen=True)
class WalletEvent:
    event_id: str
    category: str
    amount: int
    label: str
    source_key: str
    occurred_on: str
    created_at: str = ""
    direction: str = ""
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def type(self) -> str:
        return self.category

    def as_dict(self) -> dict[str, Any]:
        direction = self.direction or (
            "income" if self.amount > 0 else "expense" if self.amount < 0 else "event"
        )
        return {
            "event_id": self.event_id,
            "category": self.category,
            "type": self.category,
            "amount": self.amount,
            "label": self.label,
            "source_key": self.source_key,
            "occurred_on": self.occurred_on,
            "created_at": self.created_at,
            "direction": direction,
            "source": self.source or self.source_key,
            "metadata": dict(self.metadata),
        }


class EconomyLedger:
    """统一维护余额、收入、支出、库存、家当、状态与生活图鉴。"""

    def __init__(
        self,
        path: Path | None = None,
        *,
        now_provider: Callable[[], datetime] | None = None,
        persist: bool = True,
    ) -> None:
        self.path = path or local_data_path("economy.json")
        self._now = now_provider or (lambda: datetime.now().astimezone())
        self._persist = bool(persist)
        self._state: dict[str, Any] = {
            "version": 3,
            "balance": 0,
            "events": [],
            "daily_focus": {},
            "inventory": {"昂贵咖啡": 0},
            "owned_households": [],
            "life_collection": {},
            "titles": [],
            "active_states": {},
            "daily_life": {},
            "food_scene": None,
            "household_grants": {},
            "pending_achievements": [],
        }
        if self._persist:
            self._load()

    @property
    def balance(self) -> int:
        return max(0, int(self._state.get("balance") or 0))

    @property
    def inventory(self) -> dict[str, int]:
        raw = self._state.get("inventory") or {}
        result = {str(key): max(0, int(value or 0)) for key, value in raw.items()}
        legacy = result.get("昂贵咖啡", result.get("expensive_coffee", 0))
        result.setdefault("昂贵咖啡", legacy)
        result.setdefault("expensive_coffee", legacy)
        return result

    @property
    def owned_households(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self._state.get("owned_households", []) if str(item))

    @property
    def titles(self) -> tuple[str, ...]:
        self._refresh_titles()
        return tuple(str(item) for item in self._state.get("titles", []) if str(item))

    @property
    def events(self) -> tuple[WalletEvent, ...]:
        return tuple(
            self._event_from_dict(item)
            for item in self._state.get("events", [])
            if isinstance(item, dict)
        )

    @classmethod
    def catalog(cls) -> dict[str, dict[str, Any]]:
        return {key: dict(value) for key, value in ITEM_CATALOG.items()}

    def _load(self) -> None:
        data = read_json(self.path, {})
        if not isinstance(data, dict):
            return
        self._state.update(data)
        self._state["version"] = max(3, int(data.get("version") or 1))
        self._state["events"] = [
            item for item in data.get("events", []) if isinstance(item, dict)
        ]
        self._state["daily_focus"] = (
            data.get("daily_focus")
            if isinstance(data.get("daily_focus"), dict) else {}
        )
        self._state["inventory"] = (
            data.get("inventory")
            if isinstance(data.get("inventory"), dict) else {"昂贵咖啡": 0}
        )
        self._state["owned_households"] = [
            str(item) for item in (data.get("owned_households") or []) if str(item)
        ]
        self._state["life_collection"] = (
            data.get("life_collection")
            if isinstance(data.get("life_collection"), dict) else {}
        )
        self._state["titles"] = [
            str(item) for item in (data.get("titles") or []) if str(item)
        ]
        self._state["active_states"] = (
            data.get("active_states")
            if isinstance(data.get("active_states"), dict) else {}
        )
        self._state["daily_life"] = (
            data.get("daily_life")
            if isinstance(data.get("daily_life"), dict) else {}
        )
        self._state["food_scene"] = (
            data.get("food_scene") if isinstance(data.get("food_scene"), dict) else None
        )
        self._state["household_grants"] = (
            data.get("household_grants")
            if isinstance(data.get("household_grants"), dict) else {}
        )
        self._state["pending_achievements"] = [
            dict(item) for item in (data.get("pending_achievements") or [])
            if isinstance(item, dict)
        ]
        self._state["balance"] = max(0, int(data.get("balance") or 0))
        self._refresh_titles()

    def _save(self) -> None:
        if self._persist:
            write_json_atomic(self.path, self._state)

    def _atomic(self, action: Callable[[], Any]) -> Any:
        snapshot = copy.deepcopy(self._state)
        try:
            result = action()
            self._refresh_titles()
            self._save()
            return result
        except Exception:
            self._state = snapshot
            raise

    @staticmethod
    def _event_from_dict(item: dict[str, Any]) -> WalletEvent:
        amount = int(item.get("amount") or 0)
        return WalletEvent(
            event_id=str(item.get("event_id") or ""),
            category=str(item.get("category") or item.get("type") or "other"),
            amount=amount,
            label=str(item.get("label") or item.get("description") or "")[:120],
            source_key=str(item.get("source_key") or item.get("source") or ""),
            occurred_on=str(item.get("occurred_on") or "")[:10],
            created_at=str(item.get("created_at") or "")[:40],
            direction=str(item.get("direction") or ""),
            source=str(item.get("source") or ""),
            metadata=dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {},
        )

    def _has_source(self, source_key: str) -> bool:
        return any(
            str(item.get("source_key") or "") == source_key
            for item in self._state.get("events", [])
            if isinstance(item, dict)
        )

    def _find_source(self, source_key: str) -> WalletEvent | None:
        for item in self._state.get("events", []):
            if isinstance(item, dict) and str(item.get("source_key") or "") == source_key:
                return self._event_from_dict(item)
        return None

    def _append(
        self,
        category: str,
        amount: int,
        label: str,
        source_key: str,
        occurred_on: str,
        *,
        source: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> WalletEvent:
        existing = self._find_source(source_key)
        if existing is not None:
            return existing
        amount = int(amount)
        event = WalletEvent(
            event_id=uuid.uuid4().hex,
            category=str(category)[:40],
            amount=amount,
            label=str(label).strip()[:120],
            source_key=str(source_key)[:160],
            occurred_on=str(occurred_on)[:10],
            created_at=self._now().isoformat(),
            direction="income" if amount > 0 else "expense" if amount < 0 else "event",
            source=str(source or category)[:80],
            metadata=dict(metadata or {}),
        )
        self._state.setdefault("events", []).append(event.as_dict())
        self._state["balance"] = max(0, self.balance + amount)
        return event

    def _inventory_count(self, item_key: str) -> int:
        item_key = self._canonical_item_key(item_key)
        raw = self._state.setdefault("inventory", {})
        aliases = INVENTORY_KEY_ALIASES.get(item_key, (item_key,))
        # Alias values can coexist after an upgrade. They describe one slot,
        # not two stacks; use the largest value and canonicalize on the next
        # mutation rather than double-counting legacy data.
        return max(
            (max(0, int(raw.get(alias, 0) or 0)) for alias in aliases),
            default=0,
        )

    def _add_inventory(self, item_key: str, delta: int) -> None:
        item_key = self._canonical_item_key(item_key)
        raw = self._state.setdefault("inventory", {})
        aliases = INVENTORY_KEY_ALIASES.get(item_key, (item_key,))
        current = self._inventory_count(item_key)
        raw[item_key] = max(0, current + int(delta))
        # Remove only legacy aliases for this same item after the canonical
        # value has been written. This preserves old users' inventory exactly
        # while ensuring future reads and writes use one key.
        for alias in aliases[1:]:
            raw.pop(alias, None)

    def inventory_count(self, item_key: str) -> int:
        return self._inventory_count(str(item_key))

    @staticmethod
    def _canonical_item_key(item_key: str) -> str:
        candidate = str(item_key or "").strip()
        if candidate in ITEM_CATALOG:
            return candidate
        for canonical, aliases in INVENTORY_KEY_ALIASES.items():
            if candidate in aliases:
                return canonical
        return candidate

    def has_household(self, item_key: str) -> bool:
        return str(item_key) in self.owned_households

    def _record_life(self, event_key: str, *, increment: int = 1) -> None:
        collection = self._state.setdefault("life_collection", {})
        collection[event_key] = max(0, int(collection.get(event_key, 0) or 0) + increment)
        today = self._now().date().isoformat()
        daily = self._state.setdefault("daily_life", {}).setdefault(today, {})
        daily[event_key] = max(0, int(daily.get(event_key, 0) or 0) + increment)

    def _set_state(self, state: str, minutes: int) -> None:
        until = self._now() + timedelta(minutes=max(1, int(minutes)))
        self._state.setdefault("active_states", {})[state] = until.isoformat()

    def active_states(self) -> dict[str, str]:
        now = self._now()
        result: dict[str, str] = {}
        active = self._state.setdefault("active_states", {})
        for key, value in list(active.items()):
            try:
                if datetime.fromisoformat(str(value)) > now:
                    result[str(key)] = str(value)
                else:
                    active.pop(key, None)
            except (TypeError, ValueError):
                active.pop(key, None)
        return result

    def _grant_household_daily_supply(
        self,
        day: str,
        events: list[WalletEvent] | None = None,
    ) -> bool:
        """Grant one coffee-pot coffee per day, without changing real income."""
        if not self.has_household("coffee_pot"):
            return False
        daily = self._state.setdefault("daily_focus", {}).setdefault(day, {})
        supplies = daily.setdefault("supplies", {})
        household_supplies = daily.setdefault("household_supplies", {})
        source_key = f"household:coffee_pot:daily:{day}"
        if household_supplies.get("coffee_pot") or self._has_source(source_key):
            household_supplies["coffee_pot"] = True
            return False
        # The ordinary first-work coffee and the coffee-pot coffee are one
        # daily coffee allowance, not two simultaneous free coffees.
        if supplies.get("coffee"):
            household_supplies["coffee_pot"] = True
            return False
        self._add_inventory("coffee", 1)
        supplies["coffee"] = True
        household_supplies["coffee_pot"] = True
        event = self._append(
            "supply_reward",
            0,
            "咖啡壶每日补给：普通咖啡 ×1",
            source_key,
            day,
            source="household_supply",
            metadata={
                "item_key": "coffee",
                "household": "coffee_pot",
                "grant_quantity": 1,
                "daily_limit": 1,
            },
        )
        if events is not None:
            events.append(event)
        return True

    def ensure_daily_household_supply(self, day: str | None = None) -> bool:
        """Ensure today's coffee-pot allowance exists, at most once."""
        target = str(day or self._now().date().isoformat())[:10]
        return bool(self._atomic(lambda: self._grant_household_daily_supply(target)))

    def record_focus(self, seconds: int, *, started_at: datetime | None = None) -> dict[str, Any]:
        """把真实专注片段换成工资和日常补给，不篡改真实工作时间。"""
        seconds = max(0, int(seconds))
        now = self._now()
        start = started_at or now
        day = start.date().isoformat()

        def apply() -> dict[str, Any]:
            daily = self._state.setdefault("daily_focus", {}).setdefault(
                day,
                {
                    "focus_seconds": 0,
                    "focus_coins": 0,
                    "early_bird": False,
                    "first_started_at": "",
                    "supplies": {},
                },
            )
            supplies = daily.setdefault("supplies", {})
            old_seconds = max(0, int(daily.get("focus_seconds") or 0))
            credited_seconds = min(seconds, max(0, FOCUS_DAILY_CAP_SECONDS - old_seconds))
            daily["focus_seconds"] = old_seconds + credited_seconds
            if credited_seconds > 0 and not daily.get("first_started_at"):
                daily["first_started_at"] = start.isoformat()

            old_coins = max(0, int(daily.get("focus_coins") or 0))
            total_coins = (daily["focus_seconds"] * FOCUS_COINS_PER_HOUR) // 3600
            added_coins = max(0, total_coins - old_coins)
            daily["focus_coins"] = old_coins + added_coins
            events: list[WalletEvent] = []
            self._grant_household_daily_supply(day, events)

            if added_coins:
                events.append(self._append(
                    "salary", added_coins, "有效专注工资",
                    f"focus:{day}:{daily['focus_seconds']}", day,
                    source="focus_wage",
                    metadata={"focus_seconds": credited_seconds},
                ))

            def grant_supply(item_key: str, label: str, source_key: str, rule: str) -> None:
                if supplies.get(item_key):
                    return
                self._add_inventory(item_key, 1)
                supplies[item_key] = True
                events.append(self._append(
                    "supply_reward", 0, label, source_key, day,
                    source="daily_supply",
                    metadata={"item_key": item_key, "rule": rule, "free": True},
                ))

            if credited_seconds > 0:
                grant_supply("coffee", "开工补给：普通咖啡", f"supply:coffee:{day}", "first_formal_work")
            if seconds >= 40 * 60:
                grant_supply("milk_tea", "专注补给：奶茶", f"supply:milk-tea:{day}:{daily['focus_seconds']}", "continuous_focus_40m")
            if daily["focus_seconds"] >= 2 * 60 * 60:
                grant_supply("tea", "专注补给：茶", f"supply:tea:{day}", "daily_focus_2h")

            early_bird = False
            first_started_at = str(daily.get("first_started_at") or "")
            try:
                first_start = datetime.fromisoformat(first_started_at) if first_started_at else start
            except (TypeError, ValueError):
                first_start = start
            if (
                int(first_start.hour) < EARLY_BIRD_START_HOUR
                and daily["focus_seconds"] >= EARLY_BIRD_MIN_SECONDS
                and not bool(daily.get("early_bird"))
            ):
                daily["early_bird"] = True
                self._add_inventory("expensive_coffee", 1)
                early_bird = True
                events.append(self._append(
                    "early_bird", 0, "早鸟补给：昂贵咖啡",
                    f"early-bird:{day}", day,
                    source="early_bird_bonus",
                    metadata={"item_key": "expensive_coffee", "min_focus_seconds": EARLY_BIRD_MIN_SECONDS},
                ))
                self._record_life("early_bird")

            return {
                "events": [event.as_dict() for event in events],
                "coins": added_coins,
                "credited_seconds": credited_seconds,
                "early_bird": early_bird,
                "coffee_count": self.inventory_count("expensive_coffee"),
                "daily_supply": dict(supplies),
            }

        return self._atomic(apply)

    def daily_supply_status(self, day: str | None = None) -> dict[str, dict[str, Any]]:
        """Return today's free-supply checklist without treating food as hunger."""
        target = str(day or self._now().date().isoformat())[:10]
        daily = (self._state.get("daily_focus") or {}).get(target) or {}
        claimed = daily.get("supplies") if isinstance(daily.get("supplies"), dict) else {}
        household_supplies = (
            daily.get("household_supplies")
            if isinstance(daily.get("household_supplies"), dict)
            else {}
        )
        coffee_status = {
            "claimed": bool(claimed.get("coffee")),
            "rule": "当天第一次正式开工后免费 1 杯",
        }
        if self.has_household("coffee_pot"):
            coffee_status.update(
                {
                    "coffee_pot_enabled": True,
                    "coffee_pot_claimed": bool(
                        ((daily.get("household_supplies") or {}).get("coffee_pot"))
                        or self._has_source(f"household:coffee_pot:daily:{target}")
                    ),
                    "coffee_pot_rule": "每天补给普通咖啡 ×1，每天最多一杯",
                }
            )
        return {
            "coffee": coffee_status,
            "expensive_coffee": {"claimed": bool(daily.get("early_bird")), "rule": "首次有效工作从 10:00 前开始，并达到 20 分钟"},
            "milk_tea": {"claimed": bool(claimed.get("milk_tea")), "rule": "一次连续专注达到 40 分钟"},
            "cake": {"claimed": False, "rule": "想庆祝就使用；不需要完成特定 Todo"},
            "tea": {"claimed": bool(claimed.get("tea")), "rule": "当天累计专注达到 2 小时；好友敬茶另计"},
        }

    def record_income(
        self,
        label: str,
        amount: int,
        *,
        category: str = "windfall",
        source_key: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> WalletEvent | None:
        """登记一笔用户确认过的成果/外快，来源键保证幂等。"""
        amount = max(0, int(amount))
        clean_label = str(label).strip()[:120]
        clean_source = str(source_key).strip()[:160] or f"income:{uuid.uuid4().hex}"
        if not clean_label or amount <= 0 or self._has_source(clean_source):
            return None

        def apply() -> WalletEvent:
            return self._append(
                category, amount, clean_label, clean_source,
                self._now().date().isoformat(),
                source="achievement_income",
                metadata=metadata,
            )

        return self._atomic(apply)

    def pending_achievements(self) -> tuple[dict[str, Any], ...]:
        """Return submitted achievements that still need buddy witnesses."""

        return tuple(copy.deepcopy(item) for item in self._state.get("pending_achievements", []) if isinstance(item, dict))

    def monthly_achievement_count(self, month: str | None = None) -> int:
        target = str(month or self._now().date().isoformat())[:7]
        return sum(
            1
            for event in self.events
            if event.occurred_on.startswith(target)
            and event.category in {"windfall", "achievement_income"}
            and bool(event.metadata.get("achievement_witnessed"))
            and event.amount > 0
        )

    def monthly_achievement_submission_count(self, month: str | None = None) -> int:
        target = str(month or self._now().date().isoformat())[:7]
        return sum(
            1
            for item in self._state.get("pending_achievements", [])
            if isinstance(item, dict)
            and str(item.get("month") or "")[:7] == target
            and str(item.get("status") or "") != "deleted"
        )

    def register_achievement_income(
        self, kind: str, name: str, amount: int | None = None, note: str = "",
        *, witness_ids: Iterable[str] = (), witness_names: Iterable[str] = (),
    ) -> dict[str, Any] | None:
        """Submit a result for buddy witnessing; reward is fixed at 200 picks.

        ``witness_ids`` is optional for old local-only records. New cloud-backed
        submissions pass exactly two selected buddy IDs and the server remains
        authoritative for the actual reward settlement.
        """
        clean_kind = str(kind).strip()[:30] or "其他成果"
        clean_name = str(name).strip()[:90]
        # Keep the old positional argument for local-data compatibility, but
        # no longer let the submitter choose an arbitrary reward.
        clean_amount = ACHIEVEMENT_REWARD
        if not clean_name:
            return None
        month = self._now().date().isoformat()[:7]
        normalized_ids: list[str] = []
        for value in witness_ids or ():
            clean_id = str(value or "").strip()[:120]
            if clean_id and clean_id not in normalized_ids:
                normalized_ids.append(clean_id)
        normalized_ids = normalized_ids[:REQUIRED_ACHIEVEMENT_WITNESSES]
        normalized_names = [str(value or "").strip()[:60] for value in (witness_names or ())]

        def apply() -> dict[str, Any] | None:
            entries = self._state.setdefault("pending_achievements", [])
            if self.monthly_achievement_submission_count(month) >= MAX_MONTHLY_ACHIEVEMENT_SUBMISSIONS:
                return {"status": "submission_limit", "month": month}
            normalized = clean_name.casefold()
            for item in entries:
                if not isinstance(item, dict):
                    continue
                if str(item.get("month") or "") != month:
                    continue
                if str(item.get("name") or "").casefold() == normalized and str(item.get("kind") or "") == clean_kind:
                    if str(item.get("status") or "pending") in {"pending", "settled"}:
                        return None
            record = {
                "id": uuid.uuid4().hex,
                "kind": clean_kind,
                "name": clean_name,
                "amount": clean_amount,
                "note": str(note).strip()[:160],
                "month": month,
                "status": "pending",
                "required_witnesses": REQUIRED_ACHIEVEMENT_WITNESSES,
                "witnesses": [],
                "witness_slots": [
                    {
                        "id": witness_id,
                        "name": normalized_names[index] if index < len(normalized_names) else "搭子",
                        "status": "pending",
                    }
                    for index, witness_id in enumerate(normalized_ids)
                ],
                "replacement_round": 0,
                "submitted_at": self._now().isoformat(),
            }
            entries.append(record)
            return copy.deepcopy(record)

        return self._atomic(apply)

    def confirm_achievement(
        self, achievement_id: str, witness_id: str, witness_name: str = "",
    ) -> dict[str, Any] | None:
        """Record one invited (or legacy open) buddy witness."""
        return self.respond_achievement(achievement_id, witness_id, True, witness_name)

    def reject_achievement(
        self, achievement_id: str, witness_id: str, witness_name: str = "",
    ) -> dict[str, Any] | None:
        """Persist a witness rejection without silently treating it as pending."""
        return self.respond_achievement(achievement_id, witness_id, False, witness_name)

    def respond_achievement(
        self, achievement_id: str, witness_id: str, accepted: bool,
        witness_name: str = "",
    ) -> dict[str, Any] | None:
        """Apply one witness response and settle exactly once.

        Records created before manual invitations are still accepted through the
        legacy open-witness path so an upgrade never strands existing claims.
        New records with ``witness_slots`` only accept the selected IDs.
        """
        achievement_id = str(achievement_id).strip()[:80]
        witness_id = str(witness_id).strip()[:120]
        if not achievement_id or not witness_id or witness_id.casefold() in {"self", "me", "owner"}:
            return None

        def apply() -> dict[str, Any] | None:
            entries = self._state.setdefault("pending_achievements", [])
            record = next((item for item in entries if str(item.get("id") or "") == achievement_id), None)
            if not isinstance(record, dict) or str(record.get("status") or "pending") not in {"pending", "need_replacement"}:
                return None
            month = str(record.get("month") or self._now().date().isoformat()[:7])[:7]
            if self.monthly_achievement_count(month) >= MAX_MONTHLY_ACHIEVEMENTS:
                record["status"] = "monthly_limit"
                return {"status": "monthly_limit", "achievement": copy.deepcopy(record)}
            witnesses = record.setdefault("witnesses", [])
            slots = [slot for slot in record.get("witness_slots", []) if isinstance(slot, dict)]
            if slots:
                slot = next((slot for slot in slots if str(slot.get("id") or "") == witness_id), None)
                if slot is None:
                    return {"status": "not_invited", "achievement": copy.deepcopy(record)}
                if str(slot.get("status") or "pending") != "pending":
                    return {"status": "already_responded", "achievement": copy.deepcopy(record)}
                slot["status"] = "accepted" if accepted else "rejected"
                slot["responded_at"] = self._now().isoformat()
                if witness_name:
                    slot["name"] = str(witness_name).strip()[:60]
            elif not accepted:
                record["last_rejection"] = {"id": witness_id, "name": str(witness_name).strip()[:60] or "搭子"}
                record["status"] = "need_replacement"
                return {"status": "rejected", "achievement": copy.deepcopy(record)}
            if any(str(item.get("id") or "") == witness_id for item in witnesses if isinstance(item, dict)):
                return {"status": "duplicate_witness", "achievement": copy.deepcopy(record)}
            if not accepted:
                if slots and sum(str(slot.get("status")) == "rejected" for slot in slots) >= len(slots):
                    record["status"] = "need_replacement"
                elif slots:
                    record["status"] = "need_replacement"
                return {"status": "rejected", "achievement": copy.deepcopy(record)}
            witnesses.append({
                "id": witness_id,
                "name": str(witness_name).strip()[:60] or "搭子",
                "confirmed_at": self._now().isoformat(),
            })
            result: dict[str, Any] = {
                "status": "pending" if len(witnesses) < REQUIRED_ACHIEVEMENT_WITNESSES else "settled",
                "witness_count": len(witnesses),
                "achievement": copy.deepcopy(record),
            }
            if slots:
                accepted_count = sum(str(slot.get("status")) == "accepted" for slot in slots)
                if int(record.get("replacement_round") or 0) > 0:
                    accepted_count += len(witnesses)
            else:
                accepted_count = len(witnesses)
            if accepted_count >= REQUIRED_ACHIEVEMENT_WITNESSES:
                source_key = f"achievement:witnessed:{achievement_id}"
                event = self._append(
                    "windfall",
                    int(record.get("amount") or 0),
                    f"{record.get('kind') or '成果'}：{record.get('name') or '未命名成果'}",
                    source_key,
                    self._now().date().isoformat(),
                    source="achievement_witness",
                    metadata={
                        "achievement_id": achievement_id,
                        "kind": str(record.get("kind") or "其他成果"),
                        "note": str(record.get("note") or ""),
                        "witness_ids": [str(item.get("id") or "") for item in witnesses if isinstance(item, dict)],
                        "achievement_witnessed": True,
                    },
                )
                record["status"] = "settled"
                record["settled_at"] = self._now().isoformat()
                record["settled_event_id"] = event.event_id
                result["event"] = event.as_dict()
                result["achievement"] = copy.deepcopy(record)
            return result

        return self._atomic(apply)

    def replace_achievement_witnesses(
        self, achievement_id: str, witness_ids: Iterable[str], witness_names: Iterable[str] = (),
    ) -> dict[str, Any] | None:
        """Use the single allowed replacement round for a local claim."""
        clean_ids: list[str] = []
        for value in witness_ids or ():
            value = str(value or "").strip()[:120]
            if value and value not in clean_ids:
                clean_ids.append(value)
        if not clean_ids or len(clean_ids) > REQUIRED_ACHIEVEMENT_WITNESSES:
            return None
        names = [str(value or "").strip()[:60] for value in (witness_names or ())]

        def apply() -> dict[str, Any] | None:
            record = next((item for item in self._state.setdefault("pending_achievements", [])
                           if isinstance(item, dict) and str(item.get("id") or "") == str(achievement_id)), None)
            if not isinstance(record, dict) or int(record.get("replacement_round") or 0) >= 1:
                return None
            accepted = {str(item.get("id") or "") for item in record.get("witnesses", []) if isinstance(item, dict)}
            needed = max(0, REQUIRED_ACHIEVEMENT_WITNESSES - len(accepted))
            if len(clean_ids) != needed or accepted.intersection(clean_ids):
                return None
            previous = {str(slot.get("id") or "") for slot in record.get("witness_slots", []) if isinstance(slot, dict)}
            if previous.intersection(clean_ids):
                return None
            record["witness_slots"] = [
                {"id": value, "name": names[index] if index < len(names) else "搭子", "status": "pending"}
                for index, value in enumerate(clean_ids)
            ]
            record["replacement_round"] = 1
            record["status"] = "pending"
            return copy.deepcopy(record)

        return self._atomic(apply)

    def record_performance(
        self, label: str, *, amount: int = 2, source_key: str = "",
    ) -> WalletEvent | None:
        clean_source = str(source_key).strip()[:160] or f"performance:{uuid.uuid4().hex}"
        return self.record_income(
            label, amount, category="performance", source_key=clean_source,
        )

    def record_important_todo_completion(self, task_id: str, title: str) -> WalletEvent | None:
        """Deprecated compatibility hook; cake is now a free-use food."""
        del task_id, title
        return None
    def spend(
        self,
        label: str,
        amount: int,
        *,
        item_key: str = "",
        category: str = "spend",
        source_key: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> WalletEvent | None:
        """兼容旧调用；购买新商品应使用 purchase_item。"""
        amount = max(0, int(amount))
        clean_label = str(label).strip()[:120]
        if not clean_label or amount <= 0 or amount > self.balance:
            return None
        clean_source = str(source_key).strip() or f"spend:{uuid.uuid4().hex}"

        def apply() -> WalletEvent:
            event = self._append(
                category, -amount, clean_label, clean_source,
                self._now().date().isoformat(),
                source="purchase",
                metadata=metadata or ({"item_key": item_key} if item_key else {}),
            )
            if item_key:
                self._add_inventory(item_key, 1)
            return event

        return self._atomic(apply)

    def purchase_item(
        self, item_key: str, *, operation_key: str = "",
    ) -> WalletEvent | None:
        spec = ITEM_CATALOG.get(str(item_key))
        if not spec:
            return None
        if spec["kind"] == "household" and item_key not in ACTIVE_HOUSEHOLD_KEYS:
            return None
        if spec["kind"] == "household" and self.has_household(item_key):
            return None
        price = int(spec["price"])
        source_key = f"purchase:{operation_key}" if operation_key else f"purchase:{uuid.uuid4().hex}"
        existing = self._find_source(source_key)
        if existing is not None:
            return existing
        if price > self.balance:
            return None

        def apply() -> WalletEvent:
            event = self._append(
                "spend", -price, f"购买{spec['name']}", source_key,
                self._now().date().isoformat(),
                source="shop",
                metadata={"item_key": item_key, "kind": spec["kind"]},
            )
            if spec["kind"] == "household":
                self._state.setdefault("owned_households", []).append(item_key)
                if item_key == "coffee_pot":
                    self._grant_household_daily_supply(self._now().date().isoformat())
            else:
                self._add_inventory(item_key, 1)
            return event

        return self._atomic(apply)


    def active_food_scene(self) -> dict[str, Any] | None:
        """Return the current food-led scene without creating a second state machine."""
        scene = self._state.get("food_scene")
        if not isinstance(scene, dict):
            return None
        result = copy.deepcopy(scene)
        ends_at = str(result.get("ends_at") or "")
        if ends_at:
            try:
                result["expired"] = datetime.fromisoformat(ends_at) <= self._now()
            except (TypeError, ValueError):
                # A malformed persisted timestamp must never lock the
                # warehouse forever; treat the stale scene as finished.
                result["expired"] = True
        else:
            # Tea/companion scenes are intentionally open-ended in the data
            # model, but they still need a short visual lifetime. Without this
            # fallback, a crashed or restarted app could leave an old tea
            # scene active forever and make every warehouse item appear
            # unusable.
            try:
                started_at = datetime.fromisoformat(str(result.get("started_at") or ""))
                result["expired"] = (
                    started_at + timedelta(seconds=OPEN_FOOD_SCENE_LIFETIME_SECONDS)
                    <= self._now()
                )
            except (TypeError, ValueError):
                result["expired"] = True
        return result

    def update_active_food_scene_metadata(self, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Persist small runtime markers on the current food scene.

        The work timer owns pause/resume state; this helper only lets the
        scene remember a new episode baseline after an explicit resume.
        """

        if not isinstance(updates, dict) or not isinstance(self._state.get("food_scene"), dict):
            return None

        def apply() -> dict[str, Any] | None:
            scene = self._state.get("food_scene")
            if not isinstance(scene, dict):
                return None
            metadata = scene.setdefault("metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
                scene["metadata"] = metadata
            for key, value in updates.items():
                if isinstance(key, str) and key[:40]:
                    metadata[key[:40]] = value
            return copy.deepcopy(scene)

        return self._atomic(apply)

    def food_scene_start_error(
        self, item_key: str, *, consume_inventory: bool = True,
    ) -> str | None:
        """Return a stable reason before starting a food scene.

        The UI used to collapse both missing inventory and an active scene into
        one misleading message. Keeping this check in the economy core also
        makes the result consistent for the warehouse, supply cards and remote
        food events.
        """
        item_key = self._canonical_item_key(item_key)
        spec = ITEM_CATALOG.get(item_key)
        if not spec or spec.get("kind") != "consumable":
            return "invalid_item"
        if consume_inventory and self.inventory_count(item_key) <= 0:
            return "inventory"
        current = self.active_food_scene()
        if current and not current.get("expired"):
            return "active_scene"
        return None

    def food_scene_status(self) -> dict[str, Any] | None:
        scene = self.active_food_scene()
        if scene is None:
            return None
        ends_at = str(scene.get("ends_at") or "")
        remaining = None
        if ends_at and not scene.get("expired"):
            try:
                remaining = max(0, int((datetime.fromisoformat(ends_at) - self._now()).total_seconds()))
            except (TypeError, ValueError):
                remaining = None
        scene["remaining_seconds"] = remaining
        return scene

    def start_food_scene(
        self,
        item_key: str,
        *,
        duration_minutes: int | None = None,
        todo_id: str = "",
        todo_title: str = "",
        operation_key: str = "",
        consume_inventory: bool = True,
        source: str = "food_scene",
        scene_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Consume/receive one food and start its meaningful desktop scenario.

        This records a scene, not hunger, fullness or a numeric buff. A remote
        buddy event can set consume_inventory=False: the receiver experiences
        the scene but cannot turn a gift into leaderboard income.
        """
        item_key = self._canonical_item_key(item_key)
        spec = ITEM_CATALOG.get(item_key)
        if not spec or spec.get("kind") != "consumable":
            return None
        if self.food_scene_start_error(item_key, consume_inventory=consume_inventory) is not None:
            return None
        current = self.active_food_scene()

        default_minutes = spec.get("scene_minutes")
        minutes = default_minutes if duration_minutes is None else max(0, int(duration_minutes))
        now = self._now()
        ends_at = (
            (now + timedelta(minutes=minutes)).isoformat()
            if minutes and minutes > 0
            else ""
        )
        scene_id = uuid.uuid4().hex
        clean_todo = str(todo_title or "").strip()[:120]
        clean_source = str(source or "food_scene").strip()[:50]
        operation_source = (
            f"food-scene:{operation_key}" if str(operation_key).strip()
            else f"food-scene:{scene_id}"
        )

        def apply() -> dict[str, Any]:
            if current and current.get("expired"):
                self._state["food_scene"] = None
            if consume_inventory:
                self._add_inventory(item_key, -1)
            self._set_state(str(spec.get("state") or item_key), max(1, int(minutes or 1)))
            collection_key = str(spec.get("collection") or item_key)
            self._record_life(collection_key)
            scene = {
                "id": scene_id,
                "item_key": item_key,
                "name": str(spec.get("name") or item_key),
                "state": str(spec.get("state") or item_key),
                "scene_type": str(spec.get("scene_type") or "companion"),
                "started_at": now.isoformat(),
                "ends_at": ends_at,
                "duration_minutes": int(minutes or 0),
                "todo_id": str(todo_id or "")[:120],
                "todo_title": clean_todo,
                "deep_focus": item_key == "expensive_coffee",
                "source": clean_source,
                "metadata": {
                    str(key): value
                    for key, value in (scene_metadata or {}).items()
                    if str(key)[:40] and isinstance(key, str)
                },
            }
            self._state["food_scene"] = scene
            event = self._append(
                "item_use" if consume_inventory else "food_scene_received",
                0,
                f"{'使用' if consume_inventory else '收到'}{spec['name']}场景",
                operation_source,
                now.date().isoformat(),
                source=clean_source,
                metadata={
                    "item_key": item_key,
                    "scene_id": scene_id,
                    "scene_type": scene["scene_type"],
                    "duration_minutes": int(minutes or 0),
                    "todo_id": str(todo_id or "")[:120],
                    "todo_title": clean_todo,
                    "consume_inventory": bool(consume_inventory),
                },
            )
            return {
                "scene": copy.deepcopy(scene),
                "event": event.as_dict(),
                "item_key": item_key,
                "name": scene["name"],
                "feedback": {
                    "coffee": "喝都喝了，干半小时？",
                    "expensive_coffee": "今天喝贵的，认真干一场。",
                    "milk_tea": "歇会儿，工钱又不会跑。",
                    "cake": "这件事值得庆祝一下。",
                    "tea": "坐下来待一会儿，今天不用赶。",
                }.get(item_key, "六毛把这段生活记下来了。"),
            }

        return self._atomic(apply)

    def finish_food_scene(self, reason: str = "completed") -> dict[str, Any] | None:
        """Finish only the food scene; real focus seconds remain owned by FocusSession."""
        current = self.active_food_scene()
        if current is None:
            return None

        def apply() -> dict[str, Any]:
            scene = self._state.get("food_scene")
            self._state["food_scene"] = None
            return {**(copy.deepcopy(scene) if isinstance(scene, dict) else {}), "finish_reason": str(reason)[:40]}

        return self._atomic(apply)

    def grant_expensive_coffee_focus_reward(
        self, scene_id: str, *, threshold_seconds: int = 2 * 60 * 60,
    ) -> WalletEvent | None:
        """Grant one ordinary coffee after one uninterrupted expensive-coffee episode.

        The reward is a zero-income supply event: it changes inventory and
        writes an auditable ledger row, but cannot increase the wallet balance
        or leaderboard income.  The scene id is the idempotency key, so a
        repeated timer tick can never grant a second coffee.
        """

        clean_scene_id = str(scene_id or "").strip()[:120]
        if not clean_scene_id or int(threshold_seconds) <= 0:
            return None
        source_key = f"reward:expensive-coffee-2h:{clean_scene_id}"

        def apply() -> WalletEvent | None:
            if self._has_source(source_key):
                return None
            self._add_inventory("coffee", 1)
            return self._append(
                "supply_reward",
                0,
                "昂贵咖啡连续专注满 2 小时：普通咖啡 ×1",
                source_key,
                self._now().date().isoformat(),
                source="expensive_coffee_focus_reward",
                metadata={
                    "item_key": "coffee",
                    "source_item": "expensive_coffee",
                    "scene_id": clean_scene_id,
                    "threshold_seconds": int(threshold_seconds),
                    "grant_quantity": 1,
                },
            )

        return self._atomic(apply)

    def use_item(self, item_key: str) -> dict[str, Any] | None:
        item_key = self._canonical_item_key(item_key)
        spec = ITEM_CATALOG.get(str(item_key))
        if not spec or spec["kind"] != "consumable":
            return None
        if self.inventory_count(item_key) <= 0:
            return None

        def apply() -> dict[str, Any]:
            self._add_inventory(item_key, -1)
            state = str(spec.get("state") or "idle")
            minutes = 150 if item_key == "expensive_coffee" else 30 if item_key in {"coffee", "milk_tea"} else 20
            self._set_state(state, minutes)
            collection_key = str(spec.get("collection") or item_key)
            self._record_life(collection_key)
            event = self._append(
                "item_use", 0, f"使用{spec['name']}",
                f"use:{item_key}:{uuid.uuid4().hex}",
                self._now().date().isoformat(),
                source="item_usage",
                metadata={"item_key": item_key, "state": state},
            )
            return {
                "event": event.as_dict(),
                "item_key": item_key,
                "name": spec["name"],
                "state": state,
                "collection_count": int(self._state["life_collection"].get(collection_key, 0)),
                "feedback": {
                    "coffee": "普通喝一杯，今天也算照顾到六毛了。",
                    "expensive_coffee": "今天喝贵的。",
                    "milk_tea": "歇会儿，工钱又不会跑。",
                    "cake": "今天值得庆祝一下。",
                }.get(item_key, "六毛把这件事记下来了。"),
            }

        return self._atomic(apply)

    def record_gift_sent(self, recipient_id: str, recipient_label: str, *, price: int = 12) -> WalletEvent | None:
        """记录请搭子喝咖啡；这是支出，不增加任何人的排行榜收入。"""
        recipient_id = str(recipient_id).strip()[:80]
        recipient_label = str(recipient_label).strip()[:80] or "搭子"
        price = max(0, int(price))
        if not recipient_id or price <= 0 or self.balance < price:
            return None

        def apply() -> WalletEvent:
            event = self._append(
                "gift_sent",
                -price,
                f"请{recipient_label}家的六毛喝咖啡",
                f"gift-sent:{recipient_id}:{self._now().date().isoformat()}:{uuid.uuid4().hex}",
                self._now().date().isoformat(),
                source="buddy_gift",
                metadata={"recipient_id": recipient_id, "gift_item": "coffee"},
            )
            self._record_life("gift_sent")
            return event

        return self._atomic(apply)

    def record_gift_received(self, sender_id: str, sender_label: str) -> WalletEvent | None:
        """收到礼物只进库存，不增加余额或本月创收。"""
        sender_id = str(sender_id).strip()[:80]
        if not sender_id:
            return None

        def apply() -> WalletEvent:
            self._add_inventory("coffee", 1)
            self._record_life("gift_received")
            return self._append(
                "gift_received", 0, f"收到{str(sender_label).strip()[:80] or '搭子'}家的六毛送的咖啡",
                f"gift-received:{sender_id}:{uuid.uuid4().hex}",
                self._now().date().isoformat(),
                source="buddy_gift",
                metadata={"sender_id": sender_id, "item_key": "coffee"},
            )

        return self._atomic(apply)


    def record_food_gift_sent(
        self,
        recipient_id: str,
        recipient_label: str,
        item_key: str,
        *,
        operation_key: str = "",
    ) -> WalletEvent | None:
        """Charge the sender for a social food scene; gifts never count as income."""
        item_key = str(item_key).strip()
        spec = ITEM_CATALOG.get(item_key)
        if not spec or spec.get("kind") != "consumable":
            return None
        recipient_id = str(recipient_id).strip()[:80]
        recipient_label = str(recipient_label).strip()[:80] or "搭子"
        if not recipient_id:
            return None
        price = max(0, int(spec.get("price") or 0))
        source_key = (
            f"food-gift:{operation_key}" if str(operation_key).strip()
            else f"food-gift:{recipient_id}:{item_key}:{uuid.uuid4().hex}"
        )
        return self.spend(
            f"请{recipient_label}家的六毛{spec['name']}",
            price,
            category="gift_sent",
            source_key=source_key,
            metadata={
                "recipient_id": recipient_id,
                "gift_item": item_key,
                "leaderboard_income": False,
            },
        )

    def delete_manual_income(self, event_id: str) -> bool:
        """删除用户误登记的成果收入；系统工资、消费和礼物不可删除。"""
        event_id = str(event_id).strip()

        def apply() -> bool:
            events = self._state.setdefault("events", [])
            target = next((item for item in events if str(item.get("event_id")) == event_id), None)
            if not isinstance(target, dict):
                return False
            category = str(target.get("category") or "")
            amount = int(target.get("amount") or 0)
            if category not in MANUAL_INCOME_CATEGORIES or amount <= 0:
                return False
            events.remove(target)
            achievement_id = str((target.get("metadata") or {}).get("achievement_id") or "")
            if achievement_id:
                for achievement in self._state.setdefault("pending_achievements", []):
                    if isinstance(achievement, dict) and str(achievement.get("id") or "") == achievement_id:
                        achievement["status"] = "deleted"
                        break
            self._state["balance"] = max(0, self.balance - amount)
            return True

        return bool(self._atomic(apply))

    def monthly_income(self, month: str | None = None) -> int:
        return int(self.month_report(month).get("income") or 0)

    def month_report(self, month: str | None = None) -> dict[str, Any]:
        target = str(month or self._now().date().isoformat()[:7])[:7]
        totals: dict[str, int] = defaultdict(int)
        labels: list[str] = []
        item_counts: dict[str, int] = defaultdict(int)
        early_bird_count = 0
        for event in self.events:
            if not event.occurred_on.startswith(target):
                continue
            totals[event.category] += event.amount
            if event.category in {"windfall", "achievement_income"} and event.amount > 0:
                labels.append(event.label)
            if event.category in {"early_bird", "early_bird_bonus"}:
                early_bird_count += 1
            if event.category == "item_use":
                item_key = str(event.metadata.get("item_key") or "")
                if item_key:
                    item_counts[item_key] += 1
        income = sum(
            amount for category, amount in totals.items()
            if category in INCOME_CATEGORIES and amount > 0
        )
        expenses = abs(sum(
            amount for category, amount in totals.items()
            if category in EXPENSE_CATEGORIES and amount < 0
        ))
        salary = max(0, totals.get("salary", totals.get("focus_wage", 0)))
        windfall = max(0, totals.get("windfall", totals.get("achievement_income", 0)))
        return {
            "month": target,
            "salary": salary,
            "focus_wage": salary,
            "early_bird": max(0, totals.get("early_bird", totals.get("early_bird_bonus", 0))),
            "early_bird_count": early_bird_count,
            "performance": max(0, totals.get("performance", 0)),
            "windfall": windfall,
            "achievement_income": windfall,
            "social_reward": max(0, totals.get("social_reward", 0)),
            "special_reward": max(0, totals.get("special_reward", 0)),
            "income": income,
            "monthly_income": income,
            "expenses": expenses,
            "monthly_expense": expenses,
            "net": income - expenses,
            "windfall_labels": labels,
            "top_items": sorted(item_counts.items(), key=lambda item: (-item[1], item[0])),
            "balance": self.balance,
            "identity": self.current_identity(target, totals=totals, income=income, expenses=expenses),
        }

    def current_identity(
        self,
        month: str | None = None,
        *,
        totals: dict[str, int] | None = None,
        income: int | None = None,
        expenses: int | None = None,
    ) -> str:
        report = None
        if totals is None or income is None or expenses is None:
            report = self.month_report(month)
            totals = defaultdict(int, {
                "salary": int(report.get("salary") or 0),
                "windfall": int(report.get("windfall") or 0),
                "early_bird": int(report.get("early_bird") or 0),
            })
            income = int(report.get("income") or 0)
            expenses = int(report.get("expenses") or 0)
        if int(totals.get("windfall", 0)) > int(totals.get("salary", 0)) and int(totals.get("windfall", 0)) > 0:
            return "靠作品吃饭"
        if int(totals.get("early_bird", 0)) == 0:
            early_birds = sum(
                1 for event in self.events
                if event.category in {"early_bird", "early_bird_bonus"}
                and (not month or event.occurred_on.startswith(str(month)[:7]))
            )
        else:
            early_birds = int(totals.get("early_bird", 0))
        if early_birds >= 3:
            return "早鸟咖啡毛"
        if int(expenses or 0) > max(1, int(income or 0) // 2):
            return "花得挺快毛"
        if self.balance >= 100:
            return "荒野小财主"
        return "打工试用毛"

    def salary_comment(self, month: str | None = None) -> str:
        report = self.month_report(month)
        income = int(report["income"])
        expenses = int(report["expenses"])
        if int(report["windfall"]) > int(report["salary"]) and int(report["windfall"]) > 0:
            return "这回真靠作品吃饭了。"
        if int(report["early_bird_count"]) >= 3:
            return "早起还是有工钱的。"
        if expenses > income // 2 and expenses > 0:
            return "挣得不少，花得也挺快。"
        if income >= 100 or self.balance >= 100:
            return "哥们最近手头挺宽裕。"
        if income >= 30:
            return "还没暴富，但没摆烂。"
        return "这个月先挣到这儿。"

    def ledger_events(self, filter_key: str = "全部", limit: int = 80) -> list[WalletEvent]:
        allowed = {
            "收入": lambda e: e.amount > 0,
            "支出": lambda e: e.amount < 0,
            "工资": lambda e: e.category in {"salary", "focus_wage", "early_bird", "early_bird_bonus"},
            "成果": lambda e: e.category in {"windfall", "achievement_income", "performance"},
            "消费": lambda e: e.category in EXPENSE_CATEGORIES,
            "搭子互动": lambda e: e.category in {"social_reward", "gift_sent", "gift_received"},
            "特殊奖励": lambda e: e.category in {"special_reward", "reward"},
        }
        predicate = allowed.get(str(filter_key), lambda _event: True)
        return [event for event in reversed(self.events) if predicate(event)][:max(0, int(limit))]

    def recent_events(self, limit: int = 20) -> list[WalletEvent]:
        return list(self.ledger_events("全部", limit))

    def life_collection(self) -> dict[str, int]:
        return {
            str(key): max(0, int(value or 0))
            for key, value in (self._state.get("life_collection") or {}).items()
        }

    def today_summary(self) -> dict[str, Any]:
        today = self._now().date().isoformat()
        counts: dict[str, int] = defaultdict(int)
        for event in self.events:
            if event.occurred_on == today:
                counts[event.category] += event.amount
        return {
            "date": today,
            "income": sum(
                value for key, value in counts.items()
                if key in INCOME_CATEGORIES and value > 0
            ),
            "expenses": abs(sum(
                value for key, value in counts.items()
                if key in EXPENSE_CATEGORIES and value < 0
            )),
            "events": [event.as_dict() for event in reversed(self.events) if event.occurred_on == today][:20],
        }

    def inventory_rows(self) -> list[dict[str, Any]]:
        rows = []
        for key, spec in ITEM_CATALOG.items():
            count = self.inventory_count(key) if spec["kind"] == "consumable" else int(self.has_household(key))
            if count or spec["kind"] == "consumable":
                rows.append({
                    "item_key": key, "name": spec["name"], "kind": spec["kind"],
                    "quantity": count, "description": spec["description"],
                })
        return rows

    def _refresh_titles(self) -> None:
        counts = self.life_collection()
        earned = set(str(item) for item in self._state.get("titles", []) if str(item))
        rules = (
            ("喝贵的", counts.get("expensive_coffee", 0) >= 5),
            ("休息得明白", counts.get("milk_tea", 0) >= 10),
            ("庆祝一下", counts.get("cake", 0) >= 5),
            ("请客毛", counts.get("gift_sent", 0) >= 10),
            ("收到搭子咖啡", counts.get("gift_received", 0) >= 5),
        )
        for title, condition in rules:
            if condition:
                earned.add(title)
        self._state["titles"] = sorted(earned)

    def _safe_now(self) -> datetime:
        value = self._now()
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
