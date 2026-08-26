"""Interval facts and the single focus-time aggregation primitive.

The timer, study room and work report must not each maintain their own
counter.  This module deliberately knows nothing about Qt, persistence or
Supabase: callers give it raw focus intervals and it returns a projection for
the requested Beijing-calendar window.

Intervals are normalised to ``Asia/Shanghai`` for reporting.  A missing
``end_at`` means that the interval is still running and is evaluated against
``now`` without mutating the fact.  Invalid/negative intervals are ignored and
reported as data-quality errors; they are never interpreted as a cross-midnight
interval (which was the source of the classic 23:59/24-hour jump).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable


BEIJING_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")
INTERRUPTION_GRACE_SECONDS = 10 * 60


def as_beijing(value: datetime) -> datetime:
    """Return an aware datetime in the report timezone.

    Naive legacy values are explicitly interpreted as Beijing wall-clock
    values.  We never use the host machine timezone as part of a metric.
    """

    if value.tzinfo is None:
        value = value.replace(tzinfo=BEIJING_TIMEZONE)
    return value.astimezone(BEIJING_TIMEZONE)


@dataclass(frozen=True)
class FocusSegment:
    """One immutable piece of observed work.

    ``end_at`` is ``None`` only while the segment is active.  A segment may be
    split at a user pause, but timer checkpoints must not create new facts.
    """

    segment_id: str
    session_id: str
    start_at: datetime
    end_at: datetime | None = None
    device_id: str = ""
    completed: bool = False
    quality: int = 0
    task: str = ""
    interruptions: int = 0

    def normalized(self) -> "FocusSegment":
        return FocusSegment(
            segment_id=str(self.segment_id or ""),
            session_id=str(self.session_id or ""),
            start_at=as_beijing(self.start_at),
            end_at=as_beijing(self.end_at) if self.end_at is not None else None,
            device_id=str(self.device_id or ""),
            completed=bool(self.completed),
            quality=max(0, min(100, int(self.quality or 0))),
            task=str(self.task or "")[:120],
            interruptions=max(0, int(self.interruptions or 0)),
        )

    @property
    def is_open(self) -> bool:
        return self.end_at is None

    def effective_end(self, now: datetime) -> datetime:
        return as_beijing(self.end_at or now)

    def to_dict(self) -> dict[str, Any]:
        value = self.normalized()
        return {
            "segment_id": value.segment_id,
            "session_id": value.session_id,
            "start_at": value.start_at.isoformat(),
            "end_at": value.end_at.isoformat() if value.end_at is not None else None,
            "device_id": value.device_id,
            "completed": value.completed,
            "quality": value.quality,
            "task": value.task,
            "interruptions": value.interruptions,
        }


@dataclass(frozen=True)
class FocusAggregate:
    """Consistent metrics derived from one set of clipped union intervals."""

    start: datetime
    end: datetime
    total_seconds: int
    segment_count: int
    source_segment_count: int
    longest_seconds: int
    average_seconds: int
    hourly: tuple[dict[str, int | str], ...]
    daily: dict[str, int]
    intervals: tuple[dict[str, Any], ...]
    interruption_count: int
    quality_values: tuple[int, ...]
    errors: tuple[str, ...]

    @property
    def trusted(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "total_seconds": self.total_seconds,
            "segment_count": self.segment_count,
            "source_segment_count": self.source_segment_count,
            "longest_seconds": self.longest_seconds,
            "average_seconds": self.average_seconds,
            "hourly": [dict(item) for item in self.hourly],
            "daily": dict(self.daily),
            "intervals": [dict(item) for item in self.intervals],
            "interruption_count": self.interruption_count,
            "quality_values": list(self.quality_values),
            "errors": list(self.errors),
            "trusted": self.trusted,
        }


def segment_from_record(raw: dict[str, Any], index: int = 0) -> FocusSegment | None:
    """Convert legacy ``started_at + seconds`` rows to a focus fact.

    New rows may provide ``start_at/end_at``.  The old local ledger only has a
    duration, so its end is reconstructed once and then treated identically by
    the aggregator.  A negative duration is intentionally rejected.
    """

    if not isinstance(raw, dict):
        return None
    try:
        raw_start = raw.get("start_at", raw.get("started_at"))
        if isinstance(raw_start, datetime):
            start = raw_start
        else:
            start = datetime.fromisoformat(str(raw_start or "").replace("Z", "+00:00"))
        raw_end = raw.get("end_at", raw.get("ended_at"))
        if raw_end:
            end = raw_end if isinstance(raw_end, datetime) else datetime.fromisoformat(str(raw_end).replace("Z", "+00:00"))
        else:
            raw_seconds = raw.get("seconds", raw.get("duration_seconds"))
            if raw_seconds is None:
                end = None
            else:
                duration = int(raw_seconds)
                if duration < 0:
                    return FocusSegment(
                        segment_id=f"invalid:{index}",
                        session_id=str(raw.get("session_id") or ""),
                        start_at=as_beijing(start),
                        end_at=as_beijing(start) - timedelta(seconds=1),
                    )
                end = as_beijing(start) + timedelta(seconds=duration)
        explicit_record_id = str(raw.get("segment_id") or raw.get("record_id") or "").strip()
        record_id = explicit_record_id or f"legacy:{index}"
        # Current clients write ``<session-id>:<cumulative-seconds>``.  A
        # legacy row without an ID is one independent segment; do not group
        # all such rows under the shared ``legacy`` prefix.
        if raw.get("session_id"):
            session_id = str(raw.get("session_id"))
        elif explicit_record_id and ":" in explicit_record_id:
            session_id = explicit_record_id.split(":", 1)[0]
        else:
            session_id = record_id
        return FocusSegment(
            segment_id=record_id[:160],
            session_id=session_id[:160],
            start_at=start,
            end_at=end,
            device_id=str(raw.get("device_id") or ""),
            completed=bool(raw.get("completed")),
            quality=int(raw.get("quality", 0) or 0),
            task=str(raw.get("task_title") or raw.get("task") or raw.get("title") or ""),
            interruptions=int(raw.get("interruptions", 0) or 0),
        ).normalized()
    except (TypeError, ValueError, OverflowError):
        return None


def _merge_intervals(intervals: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged: list[list[datetime]] = []
    for start, end in sorted(intervals, key=lambda item: (item[0], item[1])):
        if end <= start:
            continue
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        elif end > merged[-1][1]:
            merged[-1][1] = end
    return [(item[0], item[1]) for item in merged]


def _clip_interval(
    start: datetime,
    end: datetime,
    window_start: datetime,
    window_end: datetime,
) -> tuple[datetime, datetime] | None:
    clipped_start = max(start, window_start)
    clipped_end = min(end, window_end)
    return (clipped_start, clipped_end) if clipped_end > clipped_start else None


def _seconds(start: datetime, end: datetime) -> int:
    # ``timedelta.seconds`` is deliberately forbidden here: a negative delta
    # wraps to nearly 24 hours.  ``total_seconds`` preserves the sign.
    return max(0, int((end - start).total_seconds()))


def _bucket_overlap_seconds(
    intervals: Iterable[tuple[datetime, datetime]],
    bucket_start: datetime,
    bucket_end: datetime,
) -> int:
    total = 0
    for start, end in intervals:
        clipped = _clip_interval(start, end, bucket_start, bucket_end)
        if clipped:
            total += _seconds(*clipped)
    return total


def aggregate_focus_time(
    segments: Iterable[FocusSegment | dict[str, Any]],
    start: datetime,
    end: datetime,
    *,
    now: datetime | None = None,
    interruption_grace_seconds: int = INTERRUPTION_GRACE_SECONDS,
) -> FocusAggregate:
    """Aggregate interval facts over ``[start, end)``.

    All duration values come from the union of clipped intervals.  Therefore
    overlapping records from two devices cannot inflate totals, and a segment
    crossing midnight naturally contributes to both calendar buckets.
    """

    window_start = as_beijing(start)
    window_end = as_beijing(end)
    moment = as_beijing(now or end)
    errors: list[str] = []
    valid: list[tuple[FocusSegment, datetime, datetime]] = []
    raw_count = 0
    for index, item in enumerate(segments):
        segment = item.normalized() if isinstance(item, FocusSegment) else segment_from_record(item, index)
        if segment is None:
            errors.append(f"invalid_segment:{index}")
            continue
        raw_count += 1
        effective_end = segment.effective_end(moment)
        if effective_end < segment.start_at:
            errors.append(f"negative_interval:{segment.segment_id or index}")
            continue
        clipped = _clip_interval(segment.start_at, effective_end, window_start, window_end)
        if clipped:
            valid.append((segment, clipped[0], clipped[1]))

    merged = _merge_intervals((item[1], item[2]) for item in valid)
    total = sum(_seconds(item[0], item[1]) for item in merged)
    longest = max((_seconds(item[0], item[1]) for item in merged), default=0)
    average = round(total / len(merged)) if merged else 0

    hourly: list[dict[str, int | str]] = []
    # Use the local calendar day containing the window start.  For a weekly or
    # monthly window the buckets are still the familiar 00:00–24:00 rhythm.
    cursor = window_start.replace(minute=0, second=0, microsecond=0)
    while cursor < window_end:
        bucket_end = min(cursor + timedelta(hours=1), window_end)
        hourly.append({
            "hour": cursor.hour,
            "label": f"{cursor.hour:02d}:00",
            "seconds": _bucket_overlap_seconds(merged, cursor, bucket_end),
        })
        cursor = bucket_end
    # The report UI expects exactly 24 hour rows for a day; for week/month it
    # also expects the hour-of-day chart, so collapse all calendar days.
    by_hour = [0] * 24
    for item in hourly:
        by_hour[int(item["hour"])] += int(item["seconds"])
    hourly = [
        {"hour": hour, "label": f"{hour:02d}:00", "seconds": value}
        for hour, value in enumerate(by_hour)
    ]

    daily: dict[str, int] = {}
    day_cursor = window_start.date()
    while day_cursor < window_end.date() or (day_cursor == window_end.date() and window_end.time() != time.min):
        day_start = datetime.combine(day_cursor, time.min, tzinfo=BEIJING_TIMEZONE)
        day_end = day_start + timedelta(days=1)
        daily[day_cursor.isoformat()] = _bucket_overlap_seconds(merged, day_start, day_end)
        day_cursor += timedelta(days=1)

    interval_dicts = tuple(
        {
            "date": item[0].date().isoformat(),
            "started_at": item[0].isoformat(),
            "ended_at": item[1].isoformat(),
            "seconds": _seconds(item[0], item[1]),
        }
        for item in merged
    )

    # Interruptions are pauses longer than the grace period within one
    # session.  This is diagnostic only; it never changes effective time.
    interruptions = 0
    by_session: dict[str, list[tuple[datetime, datetime]]] = {}
    for segment, clipped_start, clipped_end in valid:
        by_session.setdefault(segment.session_id or segment.segment_id, []).append((clipped_start, clipped_end))
    for intervals in by_session.values():
        ordered = sorted(intervals)
        previous_end: datetime | None = None
        for item_start, item_end in ordered:
            if previous_end is not None and (item_start - previous_end).total_seconds() > max(0, int(interruption_grace_seconds)):
                interruptions += 1
            previous_end = max(previous_end or item_end, item_end)

    quality_values = tuple(
        segment.quality for segment, clipped_start, clipped_end in valid
        if segment.quality > 0 and _seconds(clipped_start, clipped_end) > 0
    )
    hourly_sum = sum(int(item["seconds"]) for item in hourly)
    daily_sum = sum(daily.values())
    if hourly_sum != total:
        errors.append(f"hourly_mismatch:{hourly_sum}!={total}")
    if daily_sum != total:
        errors.append(f"daily_mismatch:{daily_sum}!={total}")
    if average > longest or longest > total:
        errors.append("metric_invariant_violation")

    return FocusAggregate(
        start=window_start,
        end=window_end,
        total_seconds=total,
        segment_count=len(merged),
        source_segment_count=raw_count,
        longest_seconds=longest,
        average_seconds=average,
        hourly=tuple(hourly),
        daily=daily,
        intervals=interval_dicts,
        interruption_count=interruptions,
        quality_values=quality_values,
        errors=tuple(errors),
    )


def calendar_window(period: str, moment: datetime) -> tuple[datetime, datetime, str]:
    """Return a Beijing-local half-open window for day/week/month."""

    current = as_beijing(moment)
    today = current.date()
    normalized = str(period or "day").strip().casefold()
    if normalized in {"week", "weekly", "本周", "周"}:
        start_date = today - timedelta(days=today.weekday())
        key = "week"
    elif normalized in {"month", "monthly", "月度", "月"}:
        start_date = today.replace(day=1)
        key = "month"
    else:
        start_date = today
        key = "day"
    if key == "month":
        next_month = (start_date.replace(day=28) + timedelta(days=4)).replace(day=1)
        end_date = next_month
    elif key == "week":
        end_date = start_date + timedelta(days=7)
    else:
        end_date = start_date + timedelta(days=1)
    return (
        datetime.combine(start_date, time.min, tzinfo=BEIJING_TIMEZONE),
        datetime.combine(end_date, time.min, tzinfo=BEIJING_TIMEZONE),
        key,
    )

