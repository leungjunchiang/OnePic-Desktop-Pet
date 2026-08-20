"""��֤��ë��������ժҪ��������Ϣ����ѡ��������Ĵ��ڡ�"""

from onepic_desktop_pet.ai import _conversation_text
from onepic_desktop_pet.chat_memory import ChatHistoryStore, ConversationMemory


def test_thirty_rounds_remain_verbatim_without_summary_or_clipping() -> None:
    memory = ConversationMemory()
    for index in range(30):
        memory.add("user", f"�� {index} ���û���������")
        memory.add("assistant", f"�� {index} ����ë�����ظ�")

    snapshot = memory.snapshot()
    assert snapshot.summary == ""
    assert len(snapshot.recent) == 60
    assert snapshot.recent[0] == ("user", "�� 0 ���û���������")
    assert snapshot.recent[-1] == ("assistant", "�� 29 ����ë�����ظ�")


def test_recent_messages_preserve_original_line_breaks() -> None:
    """�����ʮ����������ԭ�ģ�����Ϊ��ѹ������д�ո���С�"""

    memory = ConversationMemory()
    memory.add("user", "��һ��\n  �ڶ���")

    assert memory.recent == (("user", "��һ��\n  �ڶ���"),)


def test_older_rounds_roll_into_bounded_summary_and_normal_chat_keeps_latest_four_rounds() -> None:
    memory = ConversationMemory()
    for index in range(34):
        memory.add("user", f"�� {index} �֣���ϲ���������������ѹ���� {index}")
        memory.add("assistant", f"�� {index} �֣��һ����������һ��С����")

    snapshot = memory.snapshot()
    assert len(snapshot.recent) == 60
    assert snapshot.recent[0][1].startswith("�� 4 ��")
    assert "�� 0 ��" in snapshot.summary
    assert "�һ�����" in snapshot.summary
    assert len(snapshot.summary) <= 1800

    prompt = _conversation_text("�����ղŵĻ���", snapshot.as_history())
    assert "����Ի��ĳ���ժҪ" in prompt
    assert "�� 0 ��" in prompt
    assert "�� 4 �֣���ϲ����������" not in prompt
    assert "�� 30 �֣���ϲ����������" in prompt
    assert "�� 33 �֣��һ�����" in prompt


def test_bounded_memory_can_round_trip_through_local_file(tmp_path) -> None:
    path = tmp_path / "conversation-memory.json"
    memory = ConversationMemory(persist_path=path)
    memory.add("user", "�����˭")
    memory.add("assistant", "�ҵ���")

    restored = ConversationMemory(persist_path=path)
    assert restored.recent == (("user", "�����˭"), ("assistant", "�ҵ���"))
    assert "access_token" not in path.read_text(encoding="utf-8")


def test_memory_clear_removes_local_file(tmp_path) -> None:
    path = tmp_path / "conversation-memory.json"
    memory = ConversationMemory(persist_path=path)
    memory.add("user", "ֻ�ڱ�������")
    assert path.exists()
    memory.clear()
    assert not path.exists()


def test_chat_history_keeps_sessions_separate_and_round_trips(tmp_path) -> None:
    path = tmp_path / "chat-history.json"
    history = ChatHistoryStore(path)
    history.append("user", "����3��д����")
    history.append("assistant", "�Ѿ��Ž�����")
    first_id = history.current_session_id
    history.start_new_session()
    history.append("user", "�����Ⱥ�ˮ")

    restored = ChatHistoryStore(path)
    assert restored.current_messages() == (("user", "�����Ⱥ�ˮ"),)
    assert {session.session_id for session in restored.sessions()} == {
        first_id,
        restored.current_session_id,
    }
    first = restored.get(first_id)
    assert first is not None
    assert first.title == "����3��д����"
    assert first.messages[-1] == ("assistant", "�Ѿ��Ž�����")


def test_chat_history_clear_all_does_not_leave_a_local_file(tmp_path) -> None:
    path = tmp_path / "chat-history.json"
    history = ChatHistoryStore(path)
    history.append("user", "ֻ�����ڱ���")
    assert path.exists()

    history.clear_all()

    assert history.sessions() == ()
    assert history.current_messages() == ()
    assert not path.exists()


def test_chat_history_can_rename_edit_and_delete_one_message(tmp_path) -> None:
    history = ChatHistoryStore(tmp_path / "chat-history.json")
    history.append("user", "ԭ��������")
    history.append("assistant", "ԭ���Ļش�")
    session_id = history.current_session_id

    assert history.rename_session(session_id, "�����������")
    assert history.update_message(session_id, 0, "�༭�������")
    assert history.get(session_id).title == "�����������"
    assert history.get(session_id).messages == (
        ("user", "�༭�������"),
        ("assistant", "ԭ���Ļش�"),
    )
    assert history.delete_message(session_id, 1)
    assert history.get(session_id).messages == (("user", "�༭�������"),)
    assert not history.update_message(session_id, 4, "Խ��")


def test_chat_history_deleting_current_session_clears_only_chat_state(tmp_path) -> None:
    history = ChatHistoryStore(tmp_path / "chat-history.json")
    history.append("user", "��λᱻɾ��")
    session_id = history.current_session_id
    history.append("assistant", "ȷ��")

    assert history.delete_session(session_id)
    assert history.current_session_id == ""
    assert history.sessions() == ()
    assert history.current_messages() == ()

