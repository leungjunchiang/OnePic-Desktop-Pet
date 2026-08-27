from __future__ import annotations

from onepic_desktop_pet.login_rewards import (
    LoginRewardStore,
    login_reward_granted,
    login_streak_days,
)


def test_login_streak_uses_login_metric_not_focus_metric() -> None:
    assert login_streak_days({"streak_days": 6}) == 6
    assert login_streak_days({"current_streak_days": 6}) == 0
    assert login_reward_granted({"streak_days": 6, "reward_unlocked": False})


def test_login_reward_entitlement_is_permanent_and_account_scoped(tmp_path) -> None:
    first = LoginRewardStore(path=tmp_path / "login-rewards.json")
    assert first.grant("login-3-day") is True
    assert first.grant("login-3-day") is False

    restored = LoginRewardStore(path=tmp_path / "login-rewards.json")
    assert restored.is_unlocked("login-3-day")

    another_account = LoginRewardStore(path=tmp_path / "other-login-rewards.json")
    assert not another_account.is_unlocked("login-3-day")
