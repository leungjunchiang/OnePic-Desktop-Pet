"""High-confidence Chinese todo extraction for the chat fast path.

The model is still responsible for ambiguous natural language.  This module
only handles explicit requests whose date/time can be understood without a
model call, so a phrase such as ``明天9点半提醒我改论文`` is saved even when
Codex is slow or temporarily unavailable.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Callable


DateProvider = Callable[[], datetime]


_CREATE_MARKERS = (
    "待办",
    "提醒我",
    "提醒一下",
    "提醒",
    "备忘",
    "记一下",
    "记得",
    "放进待办",
    "设置",
    "安排",
)
_QUERY_MARKERS = (
    "有什么",
    "哪些",
    "几项",
    "多少项",
    "查询",
    "看看",
    "查看",
)
_DATE_WORDS = ("今天", "明天", "后天")
_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _clean(value: str) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _date_value(text: str, current: date) -> str | None:
    match = re.search(r"(20\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*[日号]?", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
        except ValueError:
            return None
    match = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", text)
    if match:
        try:
            candidate = date(current.year, int(match.group(1)), int(match.group(2)))
        except ValueError:
            return None
        # A month/day without a year normally means the next upcoming date.
        if candidate < current:
            try:
                candidate = candidate.replace(year=current.year + 1)
            except ValueError:
                return None
        return candidate.isoformat()
    if "后天" in text:
        return "day_after_tomorrow"
    if "明天" in text:
        return "tomorrow"
    if "今天" in text:
        return "today"
    return None


def _number_value(value: str) -> int | None:
    """Parse the small Chinese number forms people use for clock times."""

    value = str(value or "").strip()
    if value.isdigit():
        return int(value)
    if not value or any(char not in _CHINESE_DIGITS and char != "十" for char in value):
        return None
    if "十" not in value:
        return _CHINESE_DIGITS.get(value)
    left, _, right = value.partition("十")
    tens = _CHINESE_DIGITS.get(left, 1) if left else 1
    ones = _CHINESE_DIGITS.get(right, 0) if right else 0
    return tens * 10 + ones


def _time_value(text: str) -> tuple[str | None, str]:
    """Return HH:MM and the exact matched phrase to remove from the title."""

    number = r"\d{1,2}|[零〇一二两三四五六七八九十]+"
    minute_number = r"\d{1,2}|[零〇一二两三四五六七八九十]+"
    pattern = re.compile(
        r"(?P<period>上午|早上|早晨|中午|下午|晚上|傍晚)?\s*"
        rf"(?P<hour>{number})\s*(?:点|时)\s*(?P<minute>半|{minute_number}分)?"
    )
    match = pattern.search(text)
    if not match:
        match = re.search(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})", text)
        if not match:
            return None, ""
        hour = int(match.group("hour"))
        minute = int(match.group("minute"))
        if hour > 23 or minute > 59:
            return None, ""
        return f"{hour:02d}:{minute:02d}", match.group(0)
    hour = _number_value(match.group("hour"))
    if hour is None:
        return None, ""
    raw_minute = match.group("minute") or "0"
    minute = 30 if raw_minute == "半" else _number_value(str(raw_minute).rstrip("分"))
    if minute is None:
        return None, ""
    if minute > 59:
        return None, ""
    period = match.group("period") or ""
    if period in {"下午", "晚上", "傍晚"} and hour < 12:
        hour += 12
    if period == "中午" and hour < 11:
        hour += 12
    if hour > 23:
        return None, ""
    return f"{hour:02d}:{minute:02d}", match.group(0)


def _strip_date_time(text: str, current: date) -> str:
    result = text
    result = re.sub(r"20\d{2}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}\s*[日号]?(?:的)?", " ", result)
    result = re.sub(r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]?(?:的)?", " ", result)
    result = re.sub(r"(?:今天|明天|后天)(?:早上|上午|中午|下午|晚上|傍晚)?(?:的)?", " ", result)
    _time, matched = _time_value(result)
    if matched:
        result = result.replace(matched, " ", 1)
    return result


def _extract_title(text: str, time_match: str, important: bool, current: date) -> str:
    result = text
    # Content after a colon is the task in forms such as “待办事项：10点起床”.
    has_task_separator = "：" in result or (
        ":" in result and not re.search(r"\d{1,2}:\d{2}", result)
    )
    if has_task_separator:
        left, right = re.split(r"[：:]", result, maxsplit=1)
        if any(marker in left for marker in ("待办", "备忘", "提醒", "设置", "安排")) and right.strip():
            result = right.strip()
    # Prefer the content after the explicit reminder verb.
    reminder_match = re.search(r"提醒(?:我|一下我|一下)?\s*(.+)$", result)
    if reminder_match:
        result = reminder_match.group(1)
    important_match = re.search(r"(?:最重要(?:的事情|的一件事)?|今日重点)\s*(?:是|为|：|:)?\s*(.+)$", result)
    if important and important_match:
        result = important_match.group(1)
    result = _strip_date_time(result, current)
    if time_match:
        result = result.replace(time_match, " ", 1)
    result = re.sub(
        r"^(?:(?:你能不能|能不能|可不可以|可以不可以)\s*)?"
        r"(?:(?:可以帮我|请|帮我|给我)\s*)?"
        r"(?:(?:用你的待办功能|使用待办功能)\s*)?"
        r"(?:(?:设置|安排|添加|加|记一下|备忘一下)(?:一个|一项)?\s*)?",
        "",
        result,
    )
    result = re.sub(r"(?:的)?待办事项(?:\s*[，,、：:]?\s*(?:即|就是)?\s*)?", " ", result)
    result = re.sub(r"^(?:即|就是|叫做)\s*", "", result)
    result = re.sub(r"(?:的)?(?:待办事项|待办|任务|备忘录)\s*$", "", result)
    result = re.sub(r"^(?:那个|这个|的)\s*", "", result)
    result = re.sub(r"[，。！？；,!?;。]+$", "", result)
    return _clean(result).strip("：:")[:240]


def parse_explicit_todo_request(
    message: str,
    *,
    now: DateProvider | None = None,
) -> dict[str, Any] | None:
    """Parse only an unambiguous create-todo request.

    Queries and vague conversational phrases return ``None`` and continue to
    the normal AI path.  The returned action is directly consumable by
    :class:`LocalActionExecutor`.
    """

    text = _clean(message)[:600]
    if not text or any(marker in text for marker in _QUERY_MARKERS):
        return None
    important = "最重要" in text or "今日重点" in text
    has_date = any(word in text for word in _DATE_WORDS) or bool(re.search(r"20\d{2}\s*[年./-].*?月.*?[日号]?|\d{1,2}\s*月\s*\d{1,2}\s*[日号]?", text))
    time_value, time_match = _time_value(text)
    explicit_create = any(marker in text for marker in _CREATE_MARKERS)
    # “明天10点起床” is a clear plan even without the word “待办”; do not
    # intercept ordinary “明天怎么样” chat.
    implicit_create = bool(has_date and time_value and re.search(r"起床|开会|上班|投稿|写|改|交|买|跑|整理|完成|学习|工作", text))
    if not explicit_create and not implicit_create and not important:
        return None
    current = (now or (lambda: datetime.now().astimezone()))().date()
    date_value = _date_value(text, current) or "today"
    title = _extract_title(text, time_match, important, current)
    if not title:
        return None
    return {
        "action": "create_todo",
        "tasks": [
            {
                "title": title,
                "date": date_value,
                "time": time_value,
                "reminder": bool(time_value or "提醒" in text),
                "important": important,
                "source": "chat-fast-path",
            }
        ],
    }
