"""测试 Agent 状态缓存、异步聊天路由和完全离线时的陪伴能力。"""

from __future__ import annotations

import os
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ONEPIC_USE_DEMO_ASSETS", "1")

from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.ai import AIConnectionError
from onepic_desktop_pet.behavior import PetMood, PetState
from onepic_desktop_pet.chat import ChatDialog
from onepic_desktop_pet.chat_manager import (
    AgentConnectionState,
    AgentManager,
    ChatManager,
    OfflineDialogueManager,
)
from onepic_desktop_pet.companion import CompanionModel
from onepic_desktop_pet.config import PetSettings


class FakeCredentials:
    """避免测试访问系统凭据库。"""

    def get(self, _provider: str) -> str:
        return ""


class FakeService:
    """记录调用次数，并可模拟 AI 成功或掉线。"""

    def __init__(self, answer: str = "AI 六毛在这里。", error: str = "") -> None:
        self.answer = answer
        self.error = error
        self.calls = 0

    def reply(self, *_args, **_kwargs) -> str:
        self.calls += 1
        if self.error:
            raise AIConnectionError(self.error)
        return self.answer


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _offline_manager() -> OfflineDialogueManager:
    return OfflineDialogueManager(
        CompanionModel(PetMood()),
        lambda: "今日工作 42分钟 · 正在计时",
        lambda: 3,
        lambda: datetime(2026, 8, 12, 9, 30),
    )


def test_agent_checking_is_cached_and_chat_immediately_uses_offline_reply() -> None:
    """checking 不等于断开，也不能阻塞一条普通聊天。"""

    app = _app()
    settings = PetSettings(ai_provider="codex")
    agents = AgentManager(settings, FakeCredentials())
    service = FakeService()
    manager = ChatManager(settings, service, agents, _offline_manager())
    replies = QSignalSpy(manager.reply_ready)

    assert agents.status("codex").state is AgentConnectionState.CHECKING
    assert manager.submit("今天有点累", []) is True
    app.processEvents()

    reply = replies.at(0)[0]
    assert reply.mode == "offline"
    assert "喝口水" in reply.text
    assert service.calls == 0
    manager.shutdown()


def test_background_detection_updates_cache_once_without_chat_recheck(monkeypatch) -> None:
    """完整检测只由 AgentManager 启动一次，聊天本身不调用检测函数。"""

    app = _app()
    calls = []

    def fake_check(provider, *_args, **_kwargs):
        calls.append(provider)
        return "Codex 已连接。"

    monkeypatch.setattr(
        "onepic_desktop_pet.chat_manager.check_provider_connection",
        fake_check,
    )
    settings = PetSettings(ai_provider="codex")
    agents = AgentManager(settings, FakeCredentials())

    assert agents.start_background_check(("codex",), force=True)
    assert agents._thread is not None
    assert agents._thread.wait(2000)
    app.processEvents()
    assert calls == ["codex"]
    assert agents.status("codex").state is AgentConnectionState.CONNECTED

    service = FakeService()
    manager = ChatManager(settings, service, agents, _offline_manager())
    replies = []
    manager.reply_ready.connect(replies.append)
    assert manager.submit("请帮我分析论文结构", [])
    assert manager._thread is not None
    assert manager._thread.wait(2000)
    app.processEvents()
    assert calls == ["codex"]
    assert replies[0].mode == "ai"
    manager.shutdown()


def test_chatgpt_gui_without_codex_cli_is_not_routable_as_connected(monkeypatch) -> None:
    """中性 GUI 文案必须保留，但实际聊天不能误调用不存在的 Codex CLI。"""

    app = _app()
    message = "已检测到 ChatGPT（包含 Codex），但未检测到 Codex CLI。"
    monkeypatch.setattr(
        "onepic_desktop_pet.chat_manager.check_provider_connection",
        lambda *_args, **_kwargs: message,
    )
    settings = PetSettings(ai_provider="codex")
    agents = AgentManager(settings, FakeCredentials())

    assert agents.start_background_check(("codex",), force=True)
    assert agents._thread is not None
    assert agents._thread.wait(2000)
    app.processEvents()
    status = agents.status("codex")
    assert status.state is AgentConnectionState.DISCONNECTED
    assert status.detail == message
    agents.shutdown()


def test_connected_agent_failure_marks_error_and_next_message_skips_ai() -> None:
    """AI 调用异常后当次无缝降级，后续消息不反复调用坏连接。"""

    app = _app()
    settings = PetSettings(ai_provider="codex")
    agents = AgentManager(settings, FakeCredentials())
    agents.mark_runtime_success("codex")
    service = FakeService(error="Codex 请求超时。")
    manager = ChatManager(settings, service, agents, _offline_manager())
    replies = []
    manager.reply_ready.connect(replies.append)

    assert manager.submit("请帮我分析论文结构", []) is True
    assert manager._thread is not None
    assert manager._thread.wait(2000)
    app.processEvents()
    first = replies[0]
    assert first.mode == "offline"
    assert "离线模式" in first.text
    assert agents.status("codex").state is AgentConnectionState.ERROR

    assert manager.submit("今天工作很多", []) is True
    app.processEvents()
    assert replies[1].mode == "offline"
    assert service.calls == 1
    manager.shutdown()


def test_connected_agent_uses_async_ai_and_keeps_connected_cache() -> None:
    """缓存为 connected 时才启动 AI 线程，成功后保持连接。"""

    app = _app()
    settings = PetSettings(ai_provider="deepseek")
    agents = AgentManager(settings, FakeCredentials())
    agents.mark_runtime_success("deepseek")
    service = FakeService(answer="慢慢来，我陪你。")
    manager = ChatManager(settings, service, agents, _offline_manager())
    replies = []
    manager.reply_ready.connect(replies.append)

    assert manager.submit("有点紧张", []) is True
    assert manager._thread is not None
    assert manager._thread.wait(2000)
    app.processEvents()
    reply = replies[0]
    assert reply.mode == "ai"
    assert reply.text == "慢慢来，我陪你。"
    assert service.calls == 1
    assert agents.status("deepseek").state is AgentConnectionState.CONNECTED
    manager.shutdown()


def test_connected_agent_uses_ai_for_father_question() -> None:
    """在线时人物问题交给 AI，知识和角色设定由 AI 上下文共同处理。"""

    app = _app()
    settings = PetSettings(ai_provider="deepseek")
    agents = AgentManager(settings, FakeCredentials())
    agents.mark_runtime_success("deepseek")
    service = FakeService(answer="陈楚生，中国内地唱作人。按六毛的说法嘛——我爹。")
    manager = ChatManager(settings, service, agents, _offline_manager())
    replies = []
    manager.reply_ready.connect(replies.append)

    assert manager.submit("你认识陈楚生吗", []) is True
    assert manager._thread is not None
    assert manager._thread.wait(2000)
    app.processEvents()

    assert replies[0].mode == "ai"
    assert replies[0].text == "陈楚生，中国内地唱作人。按六毛的说法嘛——我爹。"
    assert service.calls == 1
    manager.shutdown()


def test_connected_agent_uses_ai_for_short_affection_message() -> None:
    app = _app()
    settings = PetSettings(ai_provider="deepseek")
    agents = AgentManager(settings, FakeCredentials())
    agents.mark_runtime_success("deepseek")
    service = FakeService(answer="嗯，六毛也喜欢你。")
    manager = ChatManager(settings, service, agents, _offline_manager())
    replies = []
    manager.reply_ready.connect(replies.append)

    assert manager.submit("我很爱你", []) is True
    assert manager._thread is not None
    assert manager._thread.wait(2000)
    app.processEvents()

    assert replies[0].mode == "ai"
    assert replies[0].text == "嗯，六毛也喜欢你。"
    assert service.calls == 1
    manager.shutdown()


def test_connected_agent_uses_ai_for_ambiguous_short_phrase() -> None:
    """裸的歌名/半句话不能被本地关键词规则改写成“我爹”。"""

    app = _app()
    settings = PetSettings(ai_provider="deepseek")
    agents = AgentManager(settings, FakeCredentials())
    agents.mark_runtime_success("deepseek")
    service = FakeService(answer="告诉我什么？")
    manager = ChatManager(settings, service, agents, _offline_manager())
    replies = []
    manager.reply_ready.connect(replies.append)

    assert manager.submit("有没有人告诉你", []) is True
    assert manager._thread is not None
    assert manager._thread.wait(2000)
    app.processEvents()

    assert replies[0].mode == "ai"
    assert replies[0].text == "告诉我什么？"
    assert service.calls == 1
    manager.shutdown()


def test_offline_dialogue_uses_time_work_pet_state_and_complex_hint() -> None:
    """完全没有 AI 时仍能回答本地上下文，复杂问题才显示恢复操作。"""

    offline = _offline_manager()

    assert "09:30" in offline.reply("现在几点").text
    assert "42分钟" in offline.reply("我今天工作多久了").text
    assert "专注星 3" in offline.reply("你的状态怎么样").text
    complex_reply = offline.reply("帮我写代码并分析这个项目的架构")
    assert complex_reply.mode == "offline"
    assert complex_reply.state is PetState.CURIOUS
    assert "现在是离线模式" in complex_reply.text
    assert complex_reply.show_recovery_actions is True


def test_recovery_buttons_only_emit_user_actions() -> None:
    """恢复按钮默认隐藏，显示后也只有用户点击才发出信号。"""

    app = _app()
    dialog = ChatDialog()
    reconnect = QSignalSpy(dialog.reconnect_requested)
    settings = QSignalSpy(dialog.settings_requested)

    assert not dialog.recovery_actions.isVisible()
    dialog.show()
    dialog.show_recovery_actions(True)
    app.processEvents()
    assert dialog.recovery_actions.isVisible()
    assert reconnect.count() == 0
    assert settings.count() == 0

    dialog.reconnect_button.click()
    dialog.go_to_settings_button.click()
    assert reconnect.count() == 1
    assert settings.count() == 1
    assert settings.at(0)[0] == "user_action"
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_pressing_enter_ten_times_only_submits_messages() -> None:
    """QDialog 的回车不得把 AI 设置按钮当成默认按钮并误触。"""

    app = _app()
    dialog = ChatDialog()
    messages = QSignalSpy(dialog.message_submitted)
    settings = QSignalSpy(dialog.settings_requested)
    dialog.show()
    dialog.input.setFocus()

    for index in range(10):
        dialog.input.setText(f"第 {index + 1} 条消息")
        QTest.keyClick(dialog.input, Qt.Key.Key_Return)
        app.processEvents()

    assert messages.count() == 10
    assert settings.count() == 0
    assert not dialog.settings_button.autoDefault()
    assert not dialog.go_to_settings_button.autoDefault()
    assert not dialog.reconnect_button.autoDefault()
    assert not dialog.send_button.autoDefault()
    dialog.close()
    dialog.deleteLater()
    app.processEvents()
