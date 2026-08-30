from __future__ import annotations

from datetime import datetime, timedelta, timezone

from onepic_desktop_pet.focus_segments import (
    BEIJING_TIMEZONE,
    FocusSegment,
    aggregate_focus_time,
    parse_focus_timestamp,
    segment_from_record,
)


def test_focus_timestamp_parser_preserves_absolute_and_beijing_wall_times() -> None:
    # A UTC value for 16:14 is midnight Beijing time on the following day.
    assert parse_focus_timestamp("2026-08-29T16:14:40Z").hour == 0
    # An explicit +08:00 value at midnight must remain in the midnight bucket.
    assert parse_focus_timestamp("2026-08-30T00:14:40+08:00").hour == 0
    # A Z suffix is not a local-time marker: 00:14 UTC is 08:14 Beijing.
    assert parse_focus_timestamp("2026-08-30T00:14:40Z").hour == 8
    # Legacy naive values retain their stated Beijing wall-clock time.
    assert parse_focus_timestamp("2026-08-30T00:14:40").hour == 0


def test_focus_timestamp_parser_places_midnight_fact_in_midnight_rhythm_bucket() -> None:
    segment = {
        "segment_id": "midnight",
        "session_id": "session",
        "start_at": "2026-08-29T16:14:40Z",
        "end_at": "2026-08-29T17:30:40Z",
    }
    parsed = segment_from_record(segment)
    assert parsed is not None
    result = aggregate_focus_time(
        [parsed],
        datetime(2026, 8, 30, tzinfo=BEIJING_TIMEZONE),
        datetime(2026, 8, 31, tzinfo=BEIJING_TIMEZONE),
    )
    assert result.total_seconds == 76 * 60
    assert result.hourly[0]["seconds"] == 45 * 60 + 20
    assert result.hourly[1]["seconds"] == 30 * 60 + 40
    assert result.hourly[8]["seconds"] == 0


def test_cross_midnight_uses_real_overlap_not_24_hour_wrap() -> None:
    segment = FocusSegment(
        "cross-night",
        "session",
        datetime(2026, 8, 25, 23, 40),
        datetime(2026, 8, 26, 0, 30),
    )
    day = aggregate_focus_time(
        [segment],
        datetime(2026, 8, 26),
        datetime(2026, 8, 27),
    )
    assert day.total_seconds == 30 * 60
    assert day.daily == {"2026-08-26": 30 * 60}
    assert not day.errors


def test_negative_interval_is_rejected_instead_of_becoming_23h59m() -> None:
    segment = FocusSegment(
        "bad",
        "session",
        datetime(2026, 8, 26, 10, 1),
        datetime(2026, 8, 26, 10, 0),
    )
    result = aggregate_focus_time(
        [segment], datetime(2026, 8, 26), datetime(2026, 8, 27)
    )
    assert result.total_seconds == 0
    assert result.longest_seconds == 0
    assert result.average_seconds == 0
    assert any(item.startswith("negative_interval:") for item in result.errors)


def test_overlapping_devices_are_counted_once_and_hour_buckets_reconcile() -> None:
    rows = [
        FocusSegment(
            "a", "a", datetime(2026, 8, 26, 9, 50), datetime(2026, 8, 26, 10, 20)
        ),
        FocusSegment(
            "b", "b", datetime(2026, 8, 26, 10, 10), datetime(2026, 8, 26, 10, 30)
        ),
    ]
    result = aggregate_focus_time(
        rows, datetime(2026, 8, 26), datetime(2026, 8, 27)
    )
    assert result.total_seconds == 40 * 60
    assert result.segment_count == 1
    assert result.longest_seconds == 40 * 60
    assert result.average_seconds == 40 * 60
    assert result.hourly[9]["seconds"] == 10 * 60
    assert result.hourly[10]["seconds"] == 30 * 60
    assert sum(int(item["seconds"]) for item in result.hourly) == result.total_seconds
    assert not result.errors


def test_open_segment_is_projected_to_now_without_persisting_an_end() -> None:
    now = datetime(2026, 8, 26, 10, 19, tzinfo=BEIJING_TIMEZONE)
    result = aggregate_focus_time(
        [
            FocusSegment(
                "live", "session", datetime(2026, 8, 26, 1, 50, tzinfo=timezone.utc)
            )
        ],
        datetime(2026, 8, 26, tzinfo=BEIJING_TIMEZONE),
        datetime(2026, 8, 27, tzinfo=BEIJING_TIMEZONE),
        now=now,
    )
    assert result.total_seconds == 29 * 60
    assert result.intervals[0]["started_at"].startswith("2026-08-26T09:50")
    assert result.intervals[0]["ended_at"].startswith("2026-08-26T10:19")


def test_utc_storage_is_bucketed_by_beijing_calendar() -> None:
    result = aggregate_focus_time(
        [
            FocusSegment(
                "utc", "session",
                datetime(2026, 8, 25, 16, 30, tzinfo=timezone.utc),
                datetime(2026, 8, 25, 17, 0, tzinfo=timezone.utc),
            )
        ],
        datetime(2026, 8, 26),
        datetime(2026, 8, 27),
    )
    assert result.total_seconds == 30 * 60
    assert result.intervals[0]["date"] == "2026-08-26"
