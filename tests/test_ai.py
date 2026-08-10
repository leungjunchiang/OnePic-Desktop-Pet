"""测试 Lili 的 AI 提供方、响应解析与安全边界；测试不会访问真实网络。"""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from onepic_desktop_pet.ai import (
    AIChatService,
    AIConnectionError,
    PROVIDER_PRESETS,
    _chat_endpoint,
    _parse_codex_jsonl,
    ask_claude,
    ask_codex,
    ask_compatible_api,
    check_provider_connection,
    provider_defaults,
    _models_endpoint,
)


class FakeCredentials:
    def __init__(self, token: str = "secret-token") -> None:
        self.token = token

    def get(self, _provider: str) -> str:
        return self.token


class FakeResponse:
    def __init__(self, data: dict) -> None:
        self.body = io.BytesIO(json.dumps(data).encode("utf-8"))
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.body.read()


def test_official_provider_presets_have_current_https_endpoints() -> None:
    assert provider_defaults("deepseek") == (
        "https://api.deepseek.com",
        "deepseek-v4-flash",
    )
    assert provider_defaults("kimi") == ("https://api.moonshot.cn/v1", "kimi-k3")
    assert PROVIDER_PRESETS["codex"].needs_token is False
    assert PROVIDER_PRESETS["claude"].needs_token is False


def test_chat_endpoint_rejects_plain_http() -> None:
    with pytest.raises(AIConnectionError, match="HTTPS"):
        _chat_endpoint("http://example.com/v1")
    assert _chat_endpoint("https://example.com/v1") == (
        "https://example.com/v1/chat/completions"
    )


def test_compatible_api_sends_bearer_token_without_returning_it(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(
            {"choices": [{"message": {"content": "慢慢来，我陪你。"}}]}
        )

    monkeypatch.setattr("onepic_desktop_pet.ai.urllib.request.urlopen", fake_urlopen)
    answer = ask_compatible_api(
        "deepseek",
        "今天有点累",
        [("assistant", "先歇一会。")],
        "secret-token",
        "https://api.deepseek.com",
        "deepseek-v4-flash",
    )

    request = captured["request"]
    payload = json.loads(request.data.decode("utf-8"))
    assert answer == "慢慢来，我陪你。"
    assert request.get_header("Authorization") == "Bearer secret-token"
    assert payload["thinking"] == {"type": "disabled"}
    assert "secret-token" not in json.dumps(payload)
    assert captured["timeout"] == 60


def test_codex_jsonl_parser_takes_last_agent_message() -> None:
    output = "\n".join(
        (
            json.dumps({"type": "thread.started"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "第一句"},
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "最终回复"},
                }
            ),
        )
    )
    assert _parse_codex_jsonl(output) == "最终回复"


def test_codex_uses_ephemeral_read_only_session_and_stdin(monkeypatch) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        output = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "联动正常"},
            }
        )
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(
        "onepic_desktop_pet.ai.find_codex_executable",
        lambda: Path("codex.exe"),
    )
    monkeypatch.setattr("onepic_desktop_pet.ai.subprocess.run", fake_run)

    assert ask_codex("这句话不能出现在命令参数里", []) == "联动正常"
    command = captured["command"]
    assert "--ephemeral" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[-1] == "-"
    assert "这句话不能出现在命令参数里" not in " ".join(command)
    assert "这句话不能出现在命令参数里" in captured["kwargs"]["input"]


def test_claude_uses_one_shot_no_tools_session_and_stdin(monkeypatch) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=json.dumps({"result": "巴布达"}), stderr="")

    monkeypatch.setattr("onepic_desktop_pet.ai.find_claude_executable", lambda: Path("claude.cmd"))
    monkeypatch.setattr("onepic_desktop_pet.ai.subprocess.run", fake_run)

    assert ask_claude("私密消息", []) == "巴布达"
    assert "--no-session-persistence" in captured["command"]
    assert captured["command"][captured["command"].index("--tools") + 1] == ""
    assert "私密消息" not in " ".join(captured["command"])
    assert "私密消息" in captured["kwargs"]["input"]


def test_service_requires_explicit_online_provider() -> None:
    service = AIChatService(FakeCredentials())
    with pytest.raises(AIConnectionError, match="离线"):
        service.reply("offline", "你好", [])


def test_codex_and_claude_connection_checks_verify_login(monkeypatch) -> None:
    monkeypatch.setattr("onepic_desktop_pet.ai.find_codex_executable", lambda: Path("codex.exe"))
    monkeypatch.setattr("onepic_desktop_pet.ai.find_claude_executable", lambda: Path("claude.cmd"))

    def fake_run(command, **_kwargs):
        if "claude.cmd" in str(command[0]):
            return SimpleNamespace(returncode=0, stdout=json.dumps({"loggedIn": True}), stderr="")
        return SimpleNamespace(returncode=0, stdout="Logged in using ChatGPT", stderr="")

    monkeypatch.setattr("onepic_desktop_pet.ai.subprocess.run", fake_run)
    assert "Codex 已安装并登录" in check_provider_connection("codex", FakeCredentials())
    assert "Claude Code 已安装并登录" in check_provider_connection("claude", FakeCredentials())


def test_api_connection_check_uses_read_only_models_endpoint(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse({"data": []})

    monkeypatch.setattr("onepic_desktop_pet.ai.urllib.request.urlopen", fake_urlopen)
    message = check_provider_connection(
        "deepseek", FakeCredentials(), "https://api.deepseek.com", "new-token"
    )
    assert "检测通过" in message
    assert captured["request"].full_url == "https://api.deepseek.com/models"
    assert captured["request"].method == "GET"
    assert captured["timeout"] == 15
    assert _models_endpoint("https://example.com/v1/chat/completions") == "https://example.com/v1/models"
