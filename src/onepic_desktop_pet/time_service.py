"""统一北京时间业务日历；旧本地无偏移时间按北京墙钟，服务端无偏移时间按 UTC。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Callable


DateProvider = Callable[[], datetime]
# Reuse the existing equivalent fixed-offset zone. Modern business dates have no DST;
# this also works on Windows installations without an IANA tzdata package.
BEIJING_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")


def to_beijing(value: datetime) -> datetime:
    """带偏移值保留同一时刻；旧无偏移本地数据明确按北京时间解释。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=BEIJING_TIMEZONE)
    return value.astimezone(BEIJING_TIMEZONE)


def now_beijing(provider: DateProvider | None = None) -> datetime:
    return to_beijing(provider()) if provider else datetime.now(BEIJING_TIMEZONE)


def parse_timestamp(value, *, naive_tz=BEIJING_TIMEZONE) -> datetime | None:
    """解析绝对时间；来源必须明确约定历史 naive 值，坏值不伪造时间。"""
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=naive_tz)
        return parsed.astimezone(BEIJING_TIMEZONE)
    except (TypeError, ValueError, OverflowError):
        return None


def parse_server_datetime(value) -> datetime | None:
    return parse_timestamp(value, naive_tz=timezone.utc)


def beijing_day_start(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time(), BEIJING_TIMEZONE)


def now_local(provider: DateProvider | None = None) -> datetime:
    """兼容旧调用名；所有业务页面使用北京时间，忽略电脑系统时区。"""
    return now_beijing(provider)


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
        return to_beijing(value).date()
    if isinstance(value, date):
        return value
    if "T" in str(value) or " " in str(value).strip():
        stamp = parse_timestamp(value)
        if stamp is not None:
            return stamp.date()
    return date.fromisoformat(str(value).strip()[:10])


def parse_datetime(value: str | datetime, provider: DateProvider | None = None) -> datetime:
    result = parse_timestamp(value)
    if result is None:
        raise ValueError("无效时间")
    return result


def format_clock(value: datetime | str | None) -> str:
    if value is None:
        return ""
    try:
        parsed = parse_timestamp(value)
        return parsed.strftime("%H:%M") if parsed is not None else ""
    except (TypeError, ValueError, OverflowError):
        return ""


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
