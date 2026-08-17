"""验证六毛只在内存中保留长期摘要与最近三十轮完整对话。"""

from onepic_desktop_pet.ai import _conversation_text
from onepic_desktop_pet.chat_memory import ChatHistoryStore, ConversationMemory


def test_thirty_rounds_remain_verbatim_without_summary_or_clipping() -> None:
    memory = ConversationMemory()
    for index in range(30):
        memory.add("user", f"第 {index} 轮用户完整内容")
        memory.add("assistant", f"第 {index} 轮六毛完整回复")

    snapshot = memory.snapshot()
    assert snapshot.summary == ""
    assert len(snapshot.recent) == 60
    assert snapshot.recent[0] == ("user", "第 0 轮用户完整内容")
    assert snapshot.recent[-1] == ("assistant", "第 29 轮六毛完整回复")


def test_recent_messages_preserve_original_line_breaks() -> None:
    """最近三十轮属于完整原文，不能为了压缩而改写空格或换行。"""

    memory = ConversationMemory()
    memory.add("user", "第一行\n  第二行")

    assert memory.recent == (("user", "第一行\n  第二行"),)


def test_older_rounds_roll_into_bounded_summary_and_keep_latest_thirty() -> None:
    memory = ConversationMemory()
    for index in range(34):
        memory.add("user", f"第 {index} 轮：我喜欢安静工作，最近压力是 {index}")
        memory.add("assistant", f"第 {index} 轮：我会陪你先完成一个小步骤")

    snapshot = memory.snapshot()
    assert len(snapshot.recent) == 60
    assert snapshot.recent[0][1].startswith("第 4 轮")
    assert "第 0 轮" in snapshot.summary
    assert "我会陪你" in snapshot.summary
    assert len(snapshot.summary) <= 1800

    prompt = _conversation_text("继续刚才的话题", snapshot.as_history())
    assert "更早对话的长期摘要" in prompt
    assert "第 0 轮" in prompt
    assert "第 4 轮：我喜欢安静工作" in prompt
    assert "第 33 轮：我会陪你" in prompt


def test_bounded_memory_can_round_trip_through_local_file(tmp_path) -> None:
    path = tmp_path / "conversation-memory.json"
    memory = ConversationMemory(persist_path=path)
    memory.add("user", "你爹是谁")
    memory.add("assistant", "我爹。")

    restored = ConversationMemory(persist_path=path)
    assert restored.recent == (("user", "你爹是谁"), ("assistant", "我爹。"))
    assert "access_token" not in path.read_text(encoding="utf-8")


def test_memory_clear_removes_local_file(tmp_path) -> None:
    path = tmp_path / "conversation-memory.json"
    memory = ConversationMemory(persist_path=path)
    memory.add("user", "只在本机保存")
    assert path.exists()
    memory.clear()
    assert not path.exists()


def test_chat_history_keeps_sessions_separate_and_round_trips(tmp_path) -> None:
    path = tmp_path / "chat-history.json"
    history = ChatHistoryStore(path)
    history.append("user", "明天3点写论文")
    history.append("assistant", "已经放进待办")
    first_id = history.current_session_id
    history.start_new_session()
    history.append("user", "今天先喝水")

    restored = ChatHistoryStore(path)
    assert restored.current_messages() == (("user", "今天先喝水"),)
    assert {session.session_id for session in restored.sessions()} == {
        first_id,
        restored.current_session_id,
    }
    first = restored.get(first_id)
    assert first is not None
    assert first.title == "明天3点写论文"
    assert first.messages[-1] == ("assistant", "已经放进待办")


def test_chat_history_clear_all_does_not_leave_a_local_file(tmp_path) -> None:
    path = tmp_path / "chat-history.json"
    history = ChatHistoryStore(path)
    history.append("user", "只保存在本机")
    assert path.exists()

    history.clear_all()

    assert history.sessions() == ()
    assert history.current_messages() == ()
    assert not path.exists()

