"""验证跨设备日区间、状态保鲜和停止桥接不会把旧时间算入今日。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime

import pytest

from onepic_desktop_pet.focus_display import (
    CrossDeviceDisplayDataError,
    get_cross_device_today_display_seconds,
    live_projection_rows,
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


def test_sealed_and_live_same_interval_are_unioned_once() -> None:
    rows = [{
        "user_id": "account-1",
        "segment_id": "sealed-a",
        "session_id": "sealed",
        "start_at": "2026-08-31T09:00:00+08:00",
        "end_at": "2026-08-31T11:00:00+08:00",
        "device_id": "mac",
    }]
    assert get_cross_device_today_display_seconds(
        "account-1",
        NOW,
        rows,
        active_session={
            "user_id": "account-1",
            "segment_id": "live-a",
            "session_id": "live",
            "start_at": "2026-08-31T09:00:00+08:00",
            "end_at": None,
            "device_id": "mac",
        },
    ) == 6 * 60 * 60


def test_live_projection_unions_two_active_devices_without_double_counting() -> None:
    live_rows = live_projection_rows(
        "account-1",
        {
            "devices": [
                {
                    "device_id": "mac",
                    "session_id": "mac-session",
                    "start_at": "2026-08-31T09:00:00+08:00",
                    "live": True,
                },
                {
                    "device_id": "win",
                    "session_id": "win-session",
                    "start_at": "2026-08-31T10:00:00+08:00",
                    "live": True,
                },
            ]
        },
        now=NOW,
    )

    assert get_cross_device_today_display_seconds("account-1", NOW, live_rows) == 6 * 60 * 60


def test_live_projection_bridge_keeps_a_stopped_device_until_closed_fact_arrives() -> None:
    live_rows = live_projection_rows(
        "account-1",
        {
            "devices": [
                {
                    "device_id": "mac",
                    "session_id": "mac-session",
                    "start_at": "2026-08-31T09:00:00+08:00",
                    "end_at": "2026-08-31T11:00:00+08:00",
                    "working": False,
                },
                {
                    "device_id": "win",
                    "session_id": "win-session",
                    "start_at": "2026-08-31T10:00:00+08:00",
                    "live": True,
                },
            ]
        },
        now=NOW,
    )

    assert get_cross_device_today_display_seconds("account-1", NOW, live_rows) == 6 * 60 * 60


def test_stopped_live_projection_does_not_advance_after_both_devices_pause() -> None:
    """两台设备都暂停后，临时 projection 只能保留已结束区间。"""

    live_rows = live_projection_rows(
        "account-1",
        {
            "devices": [
                {
                    "device_id": "mac",
                    "session_id": "mac-session",
                    "start_at": "2026-08-31T09:00:00+08:00",
                    "end_at": "2026-08-31T11:00:00+08:00",
                    "working": False,
                },
                {
                    "device_id": "win",
                    "session_id": "win-session",
                    "start_at": "2026-08-31T10:00:00+08:00",
                    "end_at": "2026-08-31T12:00:00+08:00",
                    "working": False,
                },
            ]
        },
        now=NOW,
    )

    before = get_cross_device_today_display_seconds("account-1", NOW, live_rows)
    after = get_cross_device_today_display_seconds(
        "account-1",
        NOW.replace(hour=16),
        live_rows,
    )
    assert before == 3 * 60 * 60
    assert after == before


def test_cached_server_live_projection_drops_expired_activity_and_stop_bridges() -> None:
    """RPC 缓存不能让超过两分钟的远端状态重新变成新鲜状态。"""
    payload = {'server_timestamp':'2026-08-31T14:59:50+08:00', 'fresh_for_seconds':120,
               'devices': [
                   {'device_id':'old-live', 'session_id':'one', 'live':True,
                    'start_at':'2026-08-31T09:00:00+08:00', 'last_seen_at':'2026-08-31T14:57:00+08:00'},
                   {'device_id':'fresh-live', 'session_id':'two', 'live':True,
                    'start_at':'2026-08-31T14:00:00+08:00', 'last_seen_at':'2026-08-31T14:59:30+08:00'},
                   {'device_id':'old-stop', 'session_id':'three', 'working':False,
                    'start_at':'2026-08-31T12:00:00+08:00', 'end_at':'2026-08-31T14:57:00+08:00'},
                   {'device_id':'fresh-stop', 'session_id':'four', 'working':False,
                    'start_at':'2026-08-31T13:00:00+08:00', 'end_at':'2026-08-31T14:59:30+08:00'},
               ]}
    before = deepcopy(payload)
    rows = live_projection_rows('account-1', payload, now=NOW)
    assert {row.device_id for row in rows} == {'fresh-live', 'fresh-stop'}
    assert payload == before


def test_modern_live_projection_rejects_missing_or_invalid_freshness_evidence() -> None:
    """有新协议标记时，错误的保鲜时间不能把未知状态当作有效工作。"""
    payload = {'server_timestamp':'2026-08-31T14:59:50+08:00', 'fresh_for_seconds':120,
               'devices':[{'device_id':'mac','session_id':'s','live':True,
                           'start_at':'2026-08-31T09:00:00+08:00', 'last_seen_at':'not-a-time'}]}
    with pytest.raises(CrossDeviceDisplayDataError):
        live_projection_rows('account-1', payload, now=NOW)


def test_live_projection_rejects_foreign_or_duplicate_devices() -> None:
    with pytest.raises(CrossDeviceDisplayDataError):
        live_projection_rows(
            "account-1",
            {
                "devices": [
                    {
                        "user_id": "account-2",
                        "device_id": "mac",
                        "session_id": "session",
                        "start_at": "2026-08-31T09:00:00+08:00",
                        "live": True,
                    }
                ]
            },
            now=NOW,
        )
    with pytest.raises(CrossDeviceDisplayDataError):
        live_projection_rows(
            "account-1",
            {
                "devices": [
                    {
                        "device_id": "mac",
                        "session_id": "session-1",
                        "start_at": "2026-08-31T09:00:00+08:00",
                        "live": True,
                    },
                    {
                        "device_id": "mac",
                        "session_id": "session-2",
                        "start_at": "2026-08-31T10:00:00+08:00",
                        "live": True,
                    },
                ]
            },
            now=NOW,
        )


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
