"""One local-time implementation shared by todos, reminders and memories."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable


DateProvider = Callable[[], datetime]


def now_local(provider: DateProvider | None = None) -> datetime:
    """Return an aware local datetime; callers may inject a clock in tests."""

    value = provider() if provider else datetime.now().astimezone()
    return value if value.tzinfo is not None else value.astimezone()


def today_key(provider: DateProvider | None = None) -> str:
    return now_local(provider).date().isoformat()


def parse_date(value: str | date | datetime | None, provider: DateProvider | None = None) -> date:
    """Parse the small, deliberate date vocabulary used by AI actions."""

    current = now_local(provider).date()
    if value is None or not str(value).strip() or str(value).strip().casefold() in {"today", "今天"}:
        return current
    text = str(value).strip().casefold()
    if text in {"tomorrow", "明天"}:
        return current + timedelta(days=1)
    if text in {"day_after_tomorrow", "后天"}:
        return current + timedelta(days=2)
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip()[:10])


def parse_datetime(value: str | datetime, provider: DateProvider | None = None) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value).strip()
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if result.tzinfo is None:
        local = now_local(provider)
        result = result.replace(tzinfo=local.tzinfo)
    return result.astimezone(now_local(provider).tzinfo)


def format_clock(value: datetime | str | None) -> str:
    if value is None:
        return ""
    try:
        parsed = parse_datetime(value) if isinstance(value, str) else value
        return parsed.astimezone().strftime("%H:%M")
    except (TypeError, ValueError, OverflowError):
        return str(value)[:5]


def days_until(target: str | date | datetime, provider: DateProvider | None = None) -> int:
    """Return calendar-day distance in local time, never a UTC off-by-one."""

    target_date = parse_date(target, provider)
    return (target_date - now_local(provider).date()).days


def next_yearly_occurrence(month_day: str | date, provider: DateProvider | None = None) -> date:
    value = parse_date(month_day, provider) if not isinstance(month_day, date) else month_day
    current = now_local(provider).date()
    try:
        candidate = value.replace(year=current.year)
    except ValueError:  # Feb 29 in a non-leap year: use Feb 28 safely.
        candidate = date(current.year, 2, 28)
    if candidate < current:
        try:
            candidate = candidate.replace(year=current.year + 1)
        except ValueError:
            candidate = date(current.year + 1, 2, 28)
    return candidate


def format_duration(seconds: int) -> str:
    safe = max(0, int(seconds))
    minutes, _ = divmod(safe, 60)
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours}小时{minutes}分钟"
    if hours:
        return f"{hours}小时"
    if minutes:
        return f"{minutes}分钟"
    return "不足1分钟" if safe else "0分钟"

