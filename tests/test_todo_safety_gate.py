"""验证待办意图识别与写入许可门禁，防止聊天误修改本地 Todo。"""

from datetime import datetime

from onepic_desktop_pet.todo_nlp import (
    parse_explicit_todo_request,
    validate_todo_creation_intent,
)


NOW = lambda: datetime(2026, 8, 19, 12, 0)


def test_recall_and_date_statements_are_not_create_actions():
    messages = (
        "你还记得经过这首歌吗",
        "你知道陈楚生的白石洲吗",
        "你知道0713吗",
        "还记得我们昨天说的吗",
        "我明天要交论文",
        "明天14点有个会",
    )
    assert all(parse_explicit_todo_request(message, now=NOW) is None for message in messages)


def test_explicit_todo_and_reminder_requests_are_create_actions():
    todo = parse_explicit_todo_request("把明天14点开会加到待办", now=NOW)
    reminder = parse_explicit_todo_request("提醒我明天14点开会", now=NOW)
    remembered = parse_explicit_todo_request("记得提醒我8月21日13点退票", now=NOW)
    assert todo["tasks"][0]["title"] == "开会"
    assert todo["tasks"][0]["time"] == "14:00"
    assert reminder["tasks"][0]["title"] == "开会"
    assert remembered["tasks"][0]["title"] == "退票"


def test_explicit_todo_without_date_does_not_schedule_creation_day():
    action = parse_explicit_todo_request("新增待办：整理数据", now=NOW)
    assert action is not None
    assert action["tasks"][0]["date"] is None


def test_mixed_recall_sentence_only_extracts_explicit_operation():
    action = parse_explicit_todo_request(
        "你还记得经过这首歌吗？顺便把今晚听经过加到待办。",
        now=NOW,
    )
    assert action is not None
    assert action["tasks"][0]["title"] == "今晚听经过"


def test_write_gate_rejects_model_guess_without_user_authorization():
    result = validate_todo_creation_intent(
        "你还记得经过这首歌吗",
        {"action": "create_todo", "tasks": [{"title": "你还记得经过这首歌吗"}]},
    )
    assert result.allowed is False
    assert result.reason == "no-explicit-create-intent"


def test_write_gate_accepts_explicit_user_authorization():
    result = validate_todo_creation_intent(
        "把明天14点开会加到待办",
        {"action": "create_todo", "tasks": [{"title": "开会"}]},
    )
    assert result.allowed is True
