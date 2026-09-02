"""Read-only cross-device focus display projection.

This module is deliberately separate from the existing focus statistics and
sync paths.  It accepts the immutable interval facts that are already present
in the client, clips them to the current Beijing calendar day and returns the
union length.  It never writes a fact, updates a cache, calls an RPC or
changes a timer.  Callers can therefore use it for the two live display
surfaces without changing reports, weekly statistics or leaderboard values.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, time
from typing import Any

from .focus_segments import (
    BEIJING_TIMEZONE,
    FocusSegment,
    aggregate_focus_time,
    as_beijing,
    segment_from_record,
)


class CrossDeviceDisplayDataError(ValueError):
    """The read-only interval payload cannot be trusted for display."""


def _is_bucket_rounding_mismatch(error: str) -> bool:
    """Recognise aggregate-only bucket rounding diagnostics.

    ``aggregate_focus_time`` calculates the union total from each merged
    interval, then independently truncates every hour/day fragment to whole
    seconds.  A fractional-second interval crossing a bucket boundary can
    therefore produce e.g. ``13575!=13576`` even though the underlying union
    is valid.  This is a projection diagnostic, not a bad interval fact.
    """

    return error.startswith(("hourly_mismatch:", "daily_mismatch:"))


def _payload_rows(session_rows: Any) -> list[Any]:
    """Extract rows from the response shape used by ``lili_sync_focus_segments``."""

    if isinstance(session_rows, Mapping):
        if "segments" not in session_rows:
            raise CrossDeviceDisplayDataError("focus display payload missing segments")
        session_rows = session_rows.get("segments")
    if not isinstance(session_rows, (list, tuple)):
        raise CrossDeviceDisplayDataError("focus display payload is not a list")
    return list(session_rows)


def _normalise_rows(
    user_id: str,
    session_rows: Any,
) -> list[FocusSegment]:
    """Validate without mutating the supplied rows or FocusSegment objects."""

    account_id = str(user_id or "").strip()
    if not account_id:
        raise CrossDeviceDisplayDataError("focus display requires an account id")
    rows: list[FocusSegment] = []
    for index, raw in enumerate(_payload_rows(session_rows)):
        if isinstance(raw, FocusSegment):
            # FocusSegment.normalized() returns a new immutable value.
            try:
                rows.append(raw.normalized())
            except (TypeError, ValueError, OverflowError) as exc:
                raise CrossDeviceDisplayDataError(
                    f"invalid focus display interval:{index}"
                ) from exc
            continue
        if not isinstance(raw, Mapping):
            raise CrossDeviceDisplayDataError(f"invalid focus display row:{index}")
        row_user_id = str(raw.get("user_id") or "").strip()
        if row_user_id and row_user_id != account_id:
            raise CrossDeviceDisplayDataError(f"focus display account mismatch:{index}")
        parsed = segment_from_record(dict(raw), index)
        if parsed is None:
            raise CrossDeviceDisplayDataError(f"invalid focus display interval:{index}")
        rows.append(parsed)
    return rows


def get_cross_device_today_display_seconds(
    user_id: str,
    now: datetime,
    session_rows: Iterable[FocusSegment | Mapping[str, Any]] | Mapping[str, Any],
    *,
    active_session: FocusSegment | Mapping[str, Any] | None = None,
) -> int:
    """Return today's account-wide display seconds from immutable intervals.

    ``session_rows`` is normally the already-fetched server interval payload
    (or the account-scoped local copy of those facts).  The half-open window is
    Beijing local ``[00:00, now)``.  Overlapping intervals from two devices
    are counted once.  A current local session can be supplied separately and
    is clipped at ``now`` without persisting an end timestamp.

    The function is intentionally pure with respect to application state.  A
    malformed row, foreign-account row or invalid interval raises
    :class:`CrossDeviceDisplayDataError`; the UI caller must then keep its
    existing stable value.
    """

    moment = as_beijing(now)
    day_start = datetime.combine(moment.date(), time.min, tzinfo=BEIJING_TIMEZONE)
    rows = _normalise_rows(user_id, session_rows)
    if active_session is not None:
        active_rows = _normalise_rows(user_id, [active_session])
        rows.extend(active_rows)

    aggregate = aggregate_focus_time(rows, day_start, moment, now=moment)
    # Keep rejecting malformed, foreign, future, and otherwise invalid
    # intervals.  Only the derived bucket sums are tolerated here: the
    # display must use the union total, rather than falling back to a stale
    # cached value when subsecond truncation makes a bucket sum differ by a
    # second.  FocusSession and the shared aggregator remain untouched.
    errors = tuple(
        error
        for error in aggregate.errors
        if not _is_bucket_rounding_mismatch(error)
    )
    if errors:
        raise CrossDeviceDisplayDataError(
            "focus display interval validation failed: "
            + ",".join(errors[:4])
        )
    return max(0, int(aggregate.total_seconds))


__all__ = [
    "CrossDeviceDisplayDataError",
    "get_cross_device_today_display_seconds",
]
