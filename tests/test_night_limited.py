"""夜间限定造型时间窗与每日随机选择的回归测试。"""

from datetime import datetime

from onepic_desktop_pet.night_limited import (
    is_night_limited_window,
    night_limited_activity,
)


def test_night_limited_window_has_explicit_local_time_boundaries() -> None:
    assert not is_night_limited_window(datetime(2026, 8, 18, 0, 29, 59))
    assert is_night_limited_window(datetime(2026, 8, 18, 0, 30))
    assert is_night_limited_window(datetime(2026, 8, 18, 6, 29, 59))
    assert not is_night_limited_window(datetime(2026, 8, 18, 6, 30))


def test_night_limited_activity_is_stable_for_one_local_date() -> None:
    first = night_limited_activity(datetime(2026, 8, 18, 1, 2))
    second = night_limited_activity(datetime(2026, 8, 18, 5, 58))
    assert first == second == "night-study-limited"


def test_night_limited_activity_expires_outside_window() -> None:
    assert night_limited_activity(datetime(2026, 8, 18, 0, 29)) is None
    assert night_limited_activity(datetime(2026, 8, 18, 6, 30)) is None


def test_night_limited_activity_supports_a_future_random_pool() -> None:
    pool = ("night-study-limited", "night-thermos-limited", "night-reading-limited")
    assert night_limited_activity(datetime(2026, 8, 18, 2, 0), pool) in pool
    assert night_limited_activity(datetime(2026, 8, 18, 2, 0), pool) == (
        night_limited_activity(datetime(2026, 8, 18, 2, 0), pool)
    )
