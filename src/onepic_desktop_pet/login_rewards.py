"""Account-scoped login reward state and compatibility helpers.

The login streak returned by the server is a different metric from the
focus/work streak shown in the reports.  Keep its parsing in one place and
persist the resulting entitlement separately from the currently equipped
outfit.  An entitlement is permanent once granted; the current streak may
later reset without taking the wardrobe item away.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .local_data import account_local_data_path, read_json, write_json_atomic


LOGIN_REWARD_STREAK_THRESHOLD = 3
LOGIN_REWARD_KEY = "login-3-day"
_STREAK_KEYS = (
    "streak_days",
    "login_streak_days",
    "continuous_login_days",
    "login_streak",
)


def login_streak_days(payload: Any) -> int:
    """Return the canonical login streak from a login-RPC payload.

    ``current_streak_days`` is intentionally not accepted here: that field is
    used by the focus report and must not unlock an account login reward.
    """

    if not isinstance(payload, dict):
        return 0
    for key in _STREAK_KEYS:
        if key not in payload:
            continue
        try:
            return max(0, int(payload.get(key) or 0))
        except (TypeError, ValueError, OverflowError):
            continue
    return 0


def login_reward_granted(payload: Any) -> bool:
    """Return whether a login payload proves the three-day reward is owned."""

    if not isinstance(payload, dict):
        return False
    return bool(payload.get("reward_unlocked")) or bool(
        payload.get("newly_unlocked")
    ) or login_streak_days(payload) >= LOGIN_REWARD_STREAK_THRESHOLD


class LoginRewardStore:
    """Small account-scoped permanent entitlement cache."""

    def __init__(
        self,
        account_id: str | None = None,
        *,
        persist: bool = True,
        path: Path | None = None,
    ) -> None:
        self.account_id = str(account_id or "").strip()
        self.persist = bool(persist)
        self.path = path or account_local_data_path(
            "login-rewards.json", self.account_id
        )
        self._unlocked: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self.persist:
            return
        raw = read_json(self.path, {})
        if not isinstance(raw, dict):
            return
        values = raw.get("unlocked_entitlements")
        if not isinstance(values, list):
            values = raw.get("unlocked_outfits")
        if isinstance(values, list):
            self._unlocked = {
                str(value).strip()[:80]
                for value in values
                if str(value).strip()
            }

    def is_unlocked(self, key: str) -> bool:
        return str(key or "").strip() in self._unlocked

    def grant(self, key: str) -> bool:
        """Grant an entitlement once and persist it atomically."""

        clean = str(key or "").strip()[:80]
        if not clean or clean in self._unlocked:
            return False
        self._unlocked.add(clean)
        if self.persist:
            try:
                write_json_atomic(
                    self.path,
                    {"version": 1, "unlocked_entitlements": sorted(self._unlocked)},
                )
            except OSError:
                # The in-memory entitlement remains valid for this session;
                # a later login can retry the cache write without affecting
                # the UI callback or the Qt event loop.
                pass
        return True
