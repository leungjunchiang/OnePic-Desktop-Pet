from __future__ import annotations

from datetime import datetime

from onepic_desktop_pet.chat_memory import ChatHistoryStore, ConversationMemory
from onepic_desktop_pet.diary import DailyCompanionStats
from onepic_desktop_pet.economy import EconomyLedger
from onepic_desktop_pet.time_memory import TimeMemory


def test_account_switch_does_not_reuse_personal_local_data(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    now = lambda: datetime(2026, 8, 22, 12, 0)

    memory = TimeMemory(account_id="account-a", now_provider=now, persist=True)
    memory.todos.add("只属于账号 A 的待办")
    assert len(memory.todos.items) == 1
    memory.switch_account("account-b")
    assert memory.todos.items == ()
    memory.alarms.add("只属于账号 B 的闹钟", "2026-08-22T13:00:00")
    memory.switch_account("account-a")
    assert [item.title for item in memory.todos.items] == ["只属于账号 A 的待办"]
    assert memory.alarms.items == ()

    diary = DailyCompanionStats(account_id="account-a", now_provider=now, persist=True)
    diary.record_touch()
    diary.switch_account("account-b")
    assert diary.snapshot()["touches"] == 0

    economy = EconomyLedger(account_id="account-a", now_provider=now, persist=True)
    economy.record_focus(60 * 60, started_at=now())
    assert economy.events
    economy.switch_account("account-b")
    assert all(event.source != "focus_wage" for event in economy.events)

    memory_chat = ConversationMemory(account_scoped=True, account_id="account-a")
    memory_chat.add("user", "只属于账号 A 的聊天")
    memory_chat.switch_account("account-b")
    assert memory_chat.recent == ()
    history = ChatHistoryStore(account_scoped=True, account_id="account-a")
    history.append("user", "只属于账号 A 的历史")
    history.switch_account("account-b")
    assert history.sessions() == ()
