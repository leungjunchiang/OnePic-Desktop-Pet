"""Local-first wallet and salary rules for the 六毛打工经济系统.

The wallet is deliberately a ledger instead of a mutable balance-only file.
Focus time is capped per day, every event has a stable local id, and the
same event id can be uploaded to Supabase without creating duplicate income.
The local ledger remains authoritative when the user is offline.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from .local_data import local_data_path, read_json, write_json_atomic


FOCUS_DAILY_CAP_SECONDS = 8 * 60 * 60
FOCUS_COINS_PER_HOUR = 6
EARLY_BIRD_START_HOUR = 10
EARLY_BIRD_MIN_SECONDS = 30 * 60


@dataclass(frozen=True)
class WalletEvent:
    event_id: str
    category: str
    amount: int
    label: str
    source_key: str
    occurred_on: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "category": self.category,
            "amount": self.amount,
            "label": self.label,
            "source_key": self.source_key,
            "occurred_on": self.occurred_on,
        }


class EconomyLedger:
    """Persist wallet income, expenses and early-bird inventory locally."""

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
            "version": 1,
            "balance": 0,
            "events": [],
            "daily_focus": {},
            "inventory": {"昂贵咖啡": 0},
        }
        if self._persist:
            self._load()

    @property
    def balance(self) -> int:
        return int(self._state.get("balance") or 0)

    @property
    def inventory(self) -> dict[str, int]:
        raw = self._state.get("inventory") or {}
        return {str(key): max(0, int(value)) for key, value in raw.items()}

    @property
    def events(self) -> tuple[WalletEvent, ...]:
        return tuple(self._event_from_dict(item) for item in self._state.get("events", []) if isinstance(item, dict))

    def _load(self) -> None:
        data = read_json(self.path, {})
        if not isinstance(data, dict):
            return
        self._state.update(data)
        self._state["events"] = [item for item in data.get("events", []) if isinstance(item, dict)]
        self._state["daily_focus"] = data.get("daily_focus") if isinstance(data.get("daily_focus"), dict) else {}
        self._state["inventory"] = data.get("inventory") if isinstance(data.get("inventory"), dict) else {"昂贵咖啡": 0}
        self._state["balance"] = int(data.get("balance") or 0)

    def _save(self) -> None:
        if self._persist:
            write_json_atomic(self.path, self._state)

    @staticmethod
    def _event_from_dict(item: dict[str, Any]) -> WalletEvent:
        return WalletEvent(
            event_id=str(item.get("event_id") or ""),
            category=str(item.get("category") or "other"),
            amount=int(item.get("amount") or 0),
            label=str(item.get("label") or "")[:120],
            source_key=str(item.get("source_key") or ""),
            occurred_on=str(item.get("occurred_on") or "")[:10],
        )

    def _has_source(self, source_key: str) -> bool:
        return any(str(item.get("source_key") or "") == source_key for item in self._state.get("events", []))

    def _append(self, category: str, amount: int, label: str, source_key: str, occurred_on: str) -> WalletEvent:
        event = WalletEvent(
            event_id=uuid.uuid4().hex,
            category=str(category)[:32],
            amount=int(amount),
            label=str(label)[:120],
            source_key=str(source_key)[:160],
            occurred_on=str(occurred_on)[:10],
        )
        self._state.setdefault("events", []).append(event.as_dict())
        self._state["balance"] = self.balance + event.amount
        self._save()
        return event

    def record_focus(self, seconds: int, *, started_at: datetime | None = None) -> dict[str, Any]:
        """Credit one real focus segment, respecting the daily cap."""

        seconds = max(0, int(seconds))
        now = self._now()
        day = (started_at or now).date().isoformat()
        daily = self._state.setdefault("daily_focus", {}).setdefault(
            day,
            {"focus_seconds": 0, "focus_coins": 0, "early_bird": False},
        )
        old_seconds = max(0, int(daily.get("focus_seconds") or 0))
        credited_seconds = min(seconds, max(0, FOCUS_DAILY_CAP_SECONDS - old_seconds))
        daily["focus_seconds"] = old_seconds + credited_seconds
        old_coins = max(0, int(daily.get("focus_coins") or 0))
        total_coins = (daily["focus_seconds"] * FOCUS_COINS_PER_HOUR) // 3600
        added_coins = max(0, total_coins - old_coins)
        daily["focus_coins"] = old_coins + added_coins
        events: list[WalletEvent] = []
        if added_coins:
            events.append(self._append("salary", added_coins, "有效专注工资", f"focus:{day}:{daily['focus_seconds']}", day))
        early_bird = False
        start = started_at or now
        if (
            int(start.hour) < EARLY_BIRD_START_HOUR
            and seconds >= EARLY_BIRD_MIN_SECONDS
            and not bool(daily.get("early_bird"))
        ):
            daily["early_bird"] = True
            inventory = self._state.setdefault("inventory", {})
            inventory["昂贵咖啡"] = max(0, int(inventory.get("昂贵咖啡") or 0)) + 1
            early_bird = True
            events.append(self._append("early_bird", 0, "早鸟奖励：昂贵咖啡", f"early-bird:{day}", day))
        self._save()
        return {
            "events": [event.as_dict() for event in events],
            "coins": added_coins,
            "credited_seconds": credited_seconds,
            "early_bird": early_bird,
            "coffee_count": self.inventory.get("昂贵咖啡", 0),
        }

    def record_income(self, label: str, amount: int, *, category: str = "windfall", source_key: str = "") -> WalletEvent | None:
        """Register user-confirmed income such as paper稿费 exactly once."""

        amount = max(0, int(amount))
        clean_label = str(label).strip()[:120]
        clean_source = str(source_key).strip()[:160] or f"income:{uuid.uuid4().hex}"
        if not clean_label or amount <= 0 or self._has_source(clean_source):
            return None
        return self._append(category, amount, clean_label, clean_source, self._now().date().isoformat())

    def record_performance(self, label: str, *, amount: int = 2, source_key: str = "") -> WalletEvent | None:
        """Give a small completion bonus without rewarding raw idle time."""

        clean_source = str(source_key).strip()[:160] or f"performance:{uuid.uuid4().hex}"
        return self.record_income(label, amount, category="performance", source_key=clean_source)

    def spend(self, label: str, amount: int, *, item_key: str = "") -> WalletEvent | None:
        """Record a purchase without allowing the wallet to go negative."""

        amount = max(0, int(amount))
        if not label.strip() or amount <= 0 or amount > self.balance:
            return None
        event = self._append("spend", -amount, str(label).strip()[:120], f"spend:{uuid.uuid4().hex}", self._now().date().isoformat())
        if item_key:
            inventory = self._state.setdefault("inventory", {})
            inventory[item_key] = max(0, int(inventory.get(item_key) or 0)) + 1
            self._save()
        return event

    def month_report(self, month: str | None = None) -> dict[str, Any]:
        """Return a salary-slip-shaped aggregate for YYYY-MM."""

        target = str(month or self._now().date().isoformat()[:7])[:7]
        totals: dict[str, int] = defaultdict(int)
        labels: list[str] = []
        for event in self.events:
            if not event.occurred_on.startswith(target):
                continue
            totals[event.category] += event.amount
            if event.category == "windfall":
                labels.append(event.label)
        income = sum(value for key, value in totals.items() if key != "spend" and value > 0)
        expenses = abs(min(0, totals.get("spend", 0)))
        return {
            "month": target,
            "salary": max(0, totals.get("salary", 0)),
            "early_bird": max(0, totals.get("early_bird", 0)),
            "performance": max(0, totals.get("performance", 0)),
            "windfall": max(0, totals.get("windfall", 0)),
            "income": income,
            "expenses": expenses,
            "net": income - expenses,
            "windfall_labels": labels,
            "balance": self.balance,
        }

    def recent_events(self, limit: int = 20) -> list[WalletEvent]:
        return list(self.events[-max(0, int(limit)) :][::-1])

