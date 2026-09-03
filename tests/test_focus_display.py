from __future__ import annotations

from copy import deepcopy
from datetime import datetime

import pytest

from onepic_desktop_pet.focus_display import (
    CrossDeviceDisplayDataError,
    get_cross_device_today_display_seconds,
)
from onepic_desktop_pet.focus_analytics import FocusAnalyticsStore
from onepic_desktop_pet.focus_segments import BEIJING_TIMEZONE


NOW = datetime(2026, 8, 31, 15, 0, tzinfo=BEIJING_TIMEZONE)


def _rows(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [
        {
            "user_id": "account-1",
            "segment_id": f"device-{index}",
            "session_id": f"session-{index}",
            "start_at": f"2026-08-31T{start}:00+08:00",
            "end_at": f"2026-08-31T{end}:00+08:00",
            "device_id": f"device-{index}",
        }
        for index, (start, end) in enumerate(pairs, start=1)
    ]


def test_cross_device_display_counts_overlap_once() -> None:
    assert (
        get_cross_device_today_display_seconds(
            "account-1", NOW, _rows(("09:00", "12:00"), ("10:00", "11:00"))
        )
        == 3 * 60 * 60
    )


def test_cross_device_display_counts_adjacent_and_disjoint_devices() -> None:
    adjacent = get_cross_device_today_display_seconds(
        "account-1", NOW, _rows(("09:00", "12:00"), ("11:00", "14:00"))
    )
    disjoint = get_cross_device_today_display_seconds(
        "account-1", NOW, _rows(("09:00", "12:00"), ("13:00", "14:00"))
    )
    assert adjacent == 5 * 60 * 60
    assert disjoint == 4 * 60 * 60


def test_cross_device_display_clips_to_beijing_today_and_now() -> None:
    rows = [
        {
            "user_id": "account-1",
            "segment_id": "cross-midnight",
            "session_id": "session",
            "start_at": "2026-08-30T23:30:00+08:00",
            "end_at": "2026-08-31T01:30:00+08:00",
        },
        {
            "user_id": "account-1",
            "segment_id": "after-now",
            "session_id": "session-2",
            "start_at": "2026-08-31T14:30:00+08:00",
            "end_at": None,
        },
    ]
    assert get_cross_device_today_display_seconds("account-1", NOW, rows) == 2 * 60 * 60


def test_cross_device_display_clips_a_closed_future_end_without_mutating_rows() -> None:
    """A timer/pause race must not make the whole account display stale."""

    rows = [
        {
            "user_id": "account-1",
            "segment_id": "future-end-race",
            "session_id": "session",
            "start_at": "2026-08-31T14:00:00+08:00",
            "end_at": "2026-08-31T16:00:00+08:00",
        }
    ]

    before = deepcopy(rows)
    assert get_cross_device_today_display_seconds("account-1", NOW, rows) == 60 * 60
    assert rows == before


def test_cross_device_display_includes_an_open_local_session_without_mutating_rows() -> None:
    rows = _rows(("09:00", "10:00"))
    before = deepcopy(rows)
    result = get_cross_device_today_display_seconds(
        "account-1",
        NOW,
        rows,
        active_session={
            "user_id": "account-1",
            "segment_id": "live-local",
            "session_id": "live",
            "start_at": "2026-08-31T14:00:00+08:00",
            "end_at": None,
        },
    )
    assert result == 2 * 60 * 60
    assert rows == before


def test_cross_device_display_uses_union_when_only_bucket_sums_round_down() -> None:
    """Derived hourly/daily truncation must not trigger a stale-cache fallback."""

    rows = [
        {
            "user_id": "account-1",
            "segment_id": "fractional-boundary",
            "session_id": "session",
            "start_at": "2026-08-31T09:00:00.100000+08:00",
            "end_at": "2026-08-31T12:00:00.300000+08:00",
        }
    ]

    assert (
        get_cross_device_today_display_seconds("account-1", NOW, rows)
        == 3 * 60 * 60
    )


def test_cross_device_display_rejects_malformed_or_foreign_payload() -> None:
    with pytest.raises(CrossDeviceDisplayDataError):
        get_cross_device_today_display_seconds("account-1", NOW, [{"start_at": "bad"}])
    with pytest.raises(CrossDeviceDisplayDataError):
        get_cross_device_today_display_seconds(
            "account-1",
            NOW,
            [{"user_id": "account-2", "start_at": "2026-08-31T09:00:00+08:00", "end_at": "2026-08-31T10:00:00+08:00"}],
        )


def test_cross_device_display_does_not_change_local_history_file(tmp_path) -> None:
    path = tmp_path / "focus.json"
    store = FocusAnalyticsStore(path=path, now_provider=lambda: NOW, persist=True)
    store.record_session(
        60 * 60,
        started_at=datetime(2026, 8, 31, 9, 0, tzinfo=BEIJING_TIMEZONE),
        completed=True,
        record_id="immutable-session",
    )
    before_bytes = path.read_bytes()
    before_state = deepcopy(store.snapshot())

    assert (
        get_cross_device_today_display_seconds(
            "account-1", NOW, store.focus_segments()
        )
        == 60 * 60
    )
    assert path.read_bytes() == before_bytes
    assert store.snapshot() == before_state
