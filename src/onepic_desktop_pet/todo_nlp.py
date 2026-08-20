"""High-confidence Chinese todo extraction for the chat fast path.

The model is still responsible for ambiguous natural language.  This module
only handles explicit requests whose date/time can be understood without a
model call, so a phrase such as ``明天9点半提醒我改论文`` is saved even when
Codex is slow or temporarily unavailable.
Todo writes are additionally checked at the executor boundary so recall
questions and date-only statements cannot mutate local storage.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from dataclasses import dataclass
from typing import Any, Callable


DateProvider = Callable[[], datetime]


_EXPLICIT_CREATE_MARKERS = (
    "加到待办", "加入待办", "放到待办", "放进待办", "记到待办",
    "建个待办", "创建待办", "新增待办", "添加待办", "加一个待办",
    "加个待办", "设置提醒", "提醒我", "提醒一下我", "帮我记一下",
    "帮我记下",
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


@dataclass(frozen=True)
class TodoCreationPermission:
    """Last-mile authorization result for a Todo database write."""

    allowed: bool
    reason: str


_RECALL_DENIAL_PATTERNS = (
    re.compile(r"你.*记得.*吗"),
    re.compile(r"还记得.*吗"),
    re.compile(r"记不记得"),
    re.compile(r"知不知道"),
    re.compile(r"你知道.*吗"),
    re.compile(r"记得什么"),
    re.compile(r"记得谁"),
)


def _has_explicit_create(text: str) -> bool:
    """Recognize direct Todo/reminder operations, never bare ``记得``."""

    if any(marker in text for marker in _EXPLICIT_CREATE_MARKERS):
        return True
    if "最重要的事情" in text or "今日重点" in text:
        return True
    if "待办事项" in text and re.search(r"[：:]", text):
        return True
    return bool(re.search(r"(?:帮我\s*)?(?:设|设置|建|创建|新增|添加|加|安排).{0,16}待办", text))


def _is_recall_question(text: str) -> bool:
    return any(pattern.search(text) for pattern in _RECALL_DENIAL_PATTERNS)


def _explicit_operation_segment(text: str) -> str:
    """Keep only the clause that actually authorizes the write."""

    suffix = re.search(
        r"(?:把|将)?\s*[^。！？!?；;]+?(?:加到待办|加入待办|放到待办|放进待办|记到待办)",
        text,
    )
    if suffix:
        segment = suffix.group(0)
        return re.sub(r"^(?:顺便|然后|再|并且)\s*", "", segment).strip()
    return text


def validate_todo_creation_intent(
    user_text: str | None,
    parsed_action: dict[str, Any] | None = None,
) -> TodoCreationPermission:
    """Authorize a Todo write immediately before the storage call.

    ``None`` is reserved for trusted programmatic actions.  Chat actions must
    carry the original user text and a direct operation phrase; dates or
    planning verbs alone are never authorization.
    """

    if user_text is None:
        return TodoCreationPermission(True, "trusted-programmatic-action")
    text = _clean(user_text)[:600]
    if not text or not _has_explicit_create(text):
        return TodoCreationPermission(False, "no-explicit-create-intent")
    tasks = (parsed_action or {}).get("tasks")
    if tasks is not None and (
        not isinstance(tasks, list)
        or not any(isinstance(task, dict) and str(task.get("title") or "").strip() for task in tasks)
    ):
        return TodoCreationPermission(False, "missing-todo-title")
    # A recall question remains chat unless the same sentence contains a
    # separate direct operation, e.g. “顺便把今晚听歌加到待办”.
    if _is_recall_question(text) and not _has_explicit_create(text):
        return TodoCreationPermission(False, "recall-question")
    return TodoCreationPermission(True, "explicit-create-intent")


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
    if not match:
        # Accept the compact forms people commonly type in a chat, such as
        # “8.19 13:00” or “8/19 13:00”.  These are event dates, not the
        # date on which the Todo was created.
        match = re.search(r"(\d{1,2})\s*[./-]\s*(\d{1,2})\s*(?:日|号)?", text)
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
    result = re.sub(r"\d{1,2}\s*[./-]\s*\d{1,2}\s*(?:日|号)?(?:的)?", " ", result)
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
        r"(?:(?:可以帮我|请|帮我|给我|六毛[，,]?\s*)\s*)?"
        r"(?:(?:用你的待办功能|使用待办功能)\s*)?"
        r"(?:(?:设置|安排|添加|加|记一下|记下|备忘一下)(?:一个|一项|个)?\s*)?"
        r"",
        "",
        result,
    )
    if re.search(r"(?:加到|加入|放到|放进|记到)\s*(?:我的)?待办", result):
        result = re.sub(r"^(?:把|将)\s*", "", result)
    result = re.sub(
        r"(?:加到|加入|放到|放进|记到)\s*(?:我的)?待办(?:事项)?\s*$",
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
    operation_text = _explicit_operation_segment(text)
    has_date = any(word in operation_text for word in _DATE_WORDS) or bool(re.search(r"20\d{2}\s*[年./-].*?月.*?[日号]?|\d{1,2}\s*月\s*\d{1,2}\s*[日号]?", operation_text))
    time_value, time_match = _time_value(operation_text)
    explicit_create = _has_explicit_create(text)
    # A date/time and a planning verb are not permission to write data.  Only
    # an explicit Todo/reminder operation reaches the local action executor.
    if not explicit_create:
        return None
    permission = validate_todo_creation_intent(text)
    if not permission.allowed:
        return None
    current = (now or (lambda: datetime.now().astimezone()))().date()
    date_value = _date_value(operation_text, current) or "today"
    title = _extract_title(operation_text, time_match, important, current)
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
