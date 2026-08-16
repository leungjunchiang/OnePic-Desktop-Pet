from __future__ import annotations

from datetime import datetime

from onepic_desktop_pet.todo_nlp import parse_explicit_todo_request


NOW = lambda: datetime(2026, 8, 17, 12, 0)


def _task(message: str) -> dict:
    action = parse_explicit_todo_request(message, now=NOW)
    assert action is not None
    assert action["action"] == "create_todo"
    return action["tasks"][0]


def test_explicit_tomorrow_half_hour_request_extracts_title_and_time() -> None:
    task = _task("明天9点半提醒我修改论文")
    assert task["title"] == "修改论文"
    assert task["date"] == "tomorrow"
    assert task["time"] == "09:30"
    assert task["reminder"] is True


def test_chinese_number_afternoon_request_is_saved() -> None:
    task = _task("下午三点提醒我跑回归")
    assert task["title"] == "跑回归"
    assert task["time"] == "15:00"


def test_evening_chinese_number_request_is_saved() -> None:
    task = _task("晚上八点提醒我洗衣服")
    assert task["title"] == "洗衣服"
    assert task["time"] == "20:00"


def test_todo_colon_form_drops_date_time_and_marker_words() -> None:
    task = _task("明天的待办事项：10点起床")
    assert task["title"] == "起床"
    assert task["date"] == "tomorrow"
    assert task["time"] == "10:00"


def test_long_explicit_form_drops_action_prefixes() -> None:
    task = _task("请用你的待办功能设置一个2026年8月17日的待办事项，即10:00起床")
    assert task["title"] == "起床"
    assert task["date"] == "2026-08-17"
    assert task["time"] == "10:00"


def test_important_task_is_marked() -> None:
    task = _task("今天最重要的事情是把机制部分写完")
    assert task["title"] == "把机制部分写完"
    assert task["important"] is True
    assert task["date"] == "today"


def test_explicit_date_is_preserved() -> None:
    task = _task("设置2026年8月20日9点提醒我提交材料")
    assert task["title"] == "提交材料"
    assert task["date"] == "2026-08-20"
    assert task["time"] == "09:00"


def test_plain_ambiguous_chat_is_not_intercepted() -> None:
    assert parse_explicit_todo_request("明天怎么样", now=NOW) is None
    assert parse_explicit_todo_request("有没有人告诉你", now=NOW) is None


def test_query_about_todos_is_not_create_action() -> None:
    assert parse_explicit_todo_request("我明天还有什么待办？", now=NOW) is None
