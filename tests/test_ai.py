"""测试 Lili 的 AI 提供方、响应解析与安全边界；测试不会访问真实网络。"""

from __future__ import annotations

import io
import json
import os
import sys
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
    ask_openai_responses,
    ask_compatible_api,
    check_provider_connection,
    codex_detection_message,
    find_codex_executable,
    launch_codex_gui,
    _cli_command,
    _cli_environment,
    _codex_app_server_command,
    _codex_exec_command,
    _macos_login_shell_path_value,
    _codex_model_override,
    _codex_turn_options,
    _codex_timeout_seconds,
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
    assert captured["timeout"] == 45


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


def test_codex_uses_ephemeral_read_only_session_and_prompt_argument(monkeypatch) -> None:
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
    assert command[-1] != "-"
    assert "这句话不能出现在命令参数里" in command[-1]
    assert captured["kwargs"].get("input") is None


def test_macos_lili_codex_call_is_isolated_and_uses_low_latency_model(monkeypatch) -> None:
    monkeypatch.setattr("onepic_desktop_pet.ai.sys.platform", "darwin")
    monkeypatch.delenv("LILI_CODEX_MODEL", raising=False)
    monkeypatch.delenv("LILI_CODEX_TIMEOUT_SECONDS", raising=False)
    command = _codex_exec_command(Path("/usr/local/bin/codex"), "测试消息")

    assert "--ignore-user-config" in command
    assert "--ephemeral" in command
    assert 'model="gpt-5.6-luna"' in command
    assert 'model_reasoning_effort="low"' in command
    assert command[-1] == "测试消息"
    assert _codex_model_override() == "gpt-5.6-luna"
    assert _codex_timeout_seconds() == 45


def test_windows_lili_codex_call_uses_low_latency_model(monkeypatch) -> None:
    monkeypatch.setattr("onepic_desktop_pet.ai.sys.platform", "win32")
    monkeypatch.delenv("LILI_CODEX_MODEL", raising=False)
    command = _codex_exec_command(Path("codex.exe"), "测试消息")

    assert "--ignore-user-config" in command
    assert "--ephemeral" in command
    assert 'model_provider="lili_http"' in command
    assert any(part.endswith("supports_websockets=false") for part in command)
    assert 'model="gpt-5.6-luna"' in command
    assert 'model_reasoning_effort="low"' in command
    assert _codex_model_override() == "gpt-5.6-luna"


def test_app_server_command_uses_cross_platform_stdio_transport(monkeypatch) -> None:
    monkeypatch.setattr("onepic_desktop_pet.ai.sys.platform", "win32")
    command = _codex_app_server_command(Path("codex.exe"))
    assert command[-2:] == ["--listen", "stdio://"]
    assert "mcp_servers={}" in command

    monkeypatch.setattr("onepic_desktop_pet.ai.sys.platform", "darwin")
    command = _codex_app_server_command(Path("/usr/local/bin/codex"))
    assert command[-2:] == ["--listen", "stdio://"]
    assert "--config" in command
    assert "--ignore-user-config" in command
    assert any(part.endswith("supports_websockets=false") for part in command)


def test_codex_turn_options_keep_daily_chat_fast_and_escalate_complex_questions(monkeypatch) -> None:
    monkeypatch.setattr("onepic_desktop_pet.ai.sys.platform", "win32")
    monkeypatch.delenv("LILI_CODEX_MODEL", raising=False)
    assert _codex_turn_options("今天干嘛？") == ("gpt-5.6-luna", "none")
    assert _codex_turn_options("请帮我做一个完整方案，分析系统设计和论文结构。") == (
        "gpt-5.6-terra",
        "low",
    )


def test_codex_exec_failure_is_logged_without_prompt_or_credentials(monkeypatch, caplog) -> None:
    def fake_run(_command, **_kwargs):
        return SimpleNamespace(returncode=2, stdout="", stderr="not logged in")

    monkeypatch.setattr("onepic_desktop_pet.ai.find_codex_executable", lambda: Path("codex"))
    monkeypatch.setattr("onepic_desktop_pet.ai.subprocess.run", fake_run)

    with pytest.raises(AIConnectionError, match="尚未登录或连接失败"):
        ask_codex("不要把这段完整提示写进日志", [])

    assert "exec failed" in caplog.text
    assert "不要把这段完整提示写进日志" not in caplog.text


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
    monkeypatch.setattr("onepic_desktop_pet.ai.find_codex_gui_app", lambda: Path("ChatGPT.exe"))
    monkeypatch.setattr("onepic_desktop_pet.ai.find_claude_executable", lambda: Path("claude.cmd"))

    def fake_run(command, **_kwargs):
        if "claude.cmd" in " ".join(str(part) for part in command):
            return SimpleNamespace(returncode=0, stdout=json.dumps({"loggedIn": True}), stderr="")
        return SimpleNamespace(returncode=0, stdout="Logged in using ChatGPT", stderr="")

    monkeypatch.setattr("onepic_desktop_pet.ai.subprocess.run", fake_run)
    assert check_provider_connection("codex", FakeCredentials()) == "Codex 已连接。"
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


def test_cli_environment_adds_graphical_app_missing_paths() -> None:
    """桌面应用启动时也应能发现 npm、Homebrew 或用户目录中的 CLI。"""

    path = _cli_environment()["PATH"]
    if sys.platform == "darwin":
        assert "/opt/homebrew/bin" in path
        assert str(Path.home() / ".local" / "bin") in path
    elif sys.platform == "win32":
        assert str(Path(os.environ.get("APPDATA", "")) / "npm") in path


def test_windows_cmd_cli_uses_command_processor() -> None:
    command = _cli_command(Path("claude.cmd"), "auth", "status")
    if sys.platform == "win32":
        assert command[1:4] == ["/d", "/s", "/c"]
        assert command[4:] == ["claude.cmd", "auth", "status"]
    else:
        assert command == ["claude.cmd", "auth", "status"]


def test_macos_codex_cli_uses_login_zsh_and_validates_absolute_path(
    monkeypatch, tmp_path
) -> None:
    """macOS 必须通过 /bin/zsh 找 CLI，再以绝对路径执行 --version。"""

    executable = tmp_path / "codex"
    executable.write_text("codex", encoding="utf-8")
    executable.chmod(0o755)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs.get("env", {})))
        if command[0:2] == ["/bin/zsh", "-lc"] and "__LILI_PATH__" in command[2]:
            return SimpleNamespace(returncode=0, stdout="__LILI_PATH__/usr/bin:/bin\n", stderr="")
        if command == ["/bin/zsh", "-lc", "command -v codex"]:
            return SimpleNamespace(returncode=0, stdout=f"{executable}\n", stderr="")
        if command == [str(executable), "--version"]:
            return SimpleNamespace(returncode=0, stdout="codex-cli 1.0", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr("onepic_desktop_pet.ai.sys.platform", "darwin")
    monkeypatch.setattr("onepic_desktop_pet.ai.subprocess.run", fake_run)
    find_codex_executable.cache_clear()

    assert find_codex_executable() == executable
    assert "__LILI_PATH__" in calls[0][0][2]
    assert [command for command, _env in calls[1:]] == [
        ["/bin/zsh", "-lc", "command -v codex"],
        [str(executable), "--version"],
    ]
    version_path = calls[2][1]["PATH"].split(os.pathsep)
    assert version_path[0] == str(executable.parent)
    assert find_codex_executable() == executable
    assert len(calls) == 3
    find_codex_executable.cache_clear()
    _macos_login_shell_path_value.cache_clear()


def test_macos_codex_login_status_uses_cached_absolute_path(monkeypatch, tmp_path) -> None:
    """连接验证和后续调用不得回退到 GUI PATH 中的裸 codex 命令。"""

    executable = tmp_path / "nvm" / "bin" / "codex"
    executable.parent.mkdir(parents=True)
    executable.write_text("codex", encoding="utf-8")
    executable.chmod(0o755)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs.get("env", {})))
        if command[0:2] == ["/bin/zsh", "-lc"] and "__LILI_PATH__" in command[2]:
            return SimpleNamespace(returncode=0, stdout="__LILI_PATH__\n", stderr="")
        if command == ["/bin/zsh", "-lc", "command -v codex"]:
            return SimpleNamespace(returncode=0, stdout=f"{executable}\n", stderr="")
        if command == [str(executable), "--version"]:
            return SimpleNamespace(returncode=0, stdout="codex-cli 1.0", stderr="")
        if command == [str(executable), "login", "status"]:
            return SimpleNamespace(returncode=0, stdout="Logged in", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr("onepic_desktop_pet.ai.sys.platform", "darwin")
    monkeypatch.setattr("onepic_desktop_pet.ai.find_codex_gui_app", lambda: Path("/Applications/ChatGPT.app"))
    monkeypatch.setattr("onepic_desktop_pet.ai.subprocess.run", fake_run)
    find_codex_executable.cache_clear()

    assert check_provider_connection("codex", FakeCredentials()) == "Codex 已连接。"
    assert calls[-1][0] == [str(executable), "login", "status"]
    assert calls[-1][1]["PATH"].split(os.pathsep)[0] == str(executable.parent)
    assert find_codex_executable() == executable
    find_codex_executable.cache_clear()
    _macos_login_shell_path_value.cache_clear()


def test_macos_codex_lookup_retries_user_shell_profiles(monkeypatch, tmp_path) -> None:
    executable = tmp_path / "nvm" / "bin" / "codex"
    executable.parent.mkdir(parents=True)
    executable.write_text("codex", encoding="utf-8")
    executable.chmod(0o755)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0:2] == ["/bin/zsh", "-lc"] and "__LILI_PATH__" in command[2]:
            return SimpleNamespace(returncode=0, stdout="__LILI_PATH__\n", stderr="")
        if command == ["/bin/zsh", "-lc", "command -v codex"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        if command[0:2] == ["/bin/zsh", "-lc"] and "~/.zshrc" in command[2]:
            return SimpleNamespace(returncode=0, stdout=f"{executable}\n", stderr="")
        if command == [str(executable), "--version"]:
            return SimpleNamespace(returncode=0, stdout="codex-cli 1.0", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr("onepic_desktop_pet.ai.sys.platform", "darwin")
    monkeypatch.setattr("onepic_desktop_pet.ai.subprocess.run", fake_run)
    find_codex_executable.cache_clear()

    assert find_codex_executable() == executable
    assert "__LILI_PATH__" in calls[0][2]
    assert calls[1] == ["/bin/zsh", "-lc", "command -v codex"]
    assert any("~/.zshrc" in command[-1] for command in calls)
    find_codex_executable.cache_clear()
    _macos_login_shell_path_value.cache_clear()


def test_openai_responses_api_preserves_context_without_token_in_payload(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse({"output_text": "六毛已经收到啦。"})

    monkeypatch.setattr("onepic_desktop_pet.ai.urllib.request.urlopen", fake_urlopen)
    answer = ask_openai_responses(
        "继续刚才的话题",
        [("summary", "用户在准备考试"), ("user", "我有点累")],
        "secret-token",
        "https://api.openai.com/v1",
        "gpt-4o-mini",
    )
    request = captured["request"]
    payload = json.loads(request.data.decode("utf-8"))
    assert answer == "六毛已经收到啦。"
    assert request.full_url == "https://api.openai.com/v1/responses"
    assert request.get_header("Authorization") == "Bearer secret-token"
    assert "secret-token" not in json.dumps(payload)
    assert "用户在准备考试" in payload["input"]
    assert captured["timeout"] == 30


def test_reset_conversation_forgets_persisted_codex_thread_without_touching_service_config(
    monkeypatch, tmp_path
) -> None:
    thread_path = tmp_path / "codex-app-server-thread.json"
    thread_path.write_text(
        '{"version": 1, "thread_id": "thr_saved"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "onepic_desktop_pet.ai._codex_thread_state_path",
        lambda: thread_path,
    )

    service = AIChatService(FakeCredentials())
    service.reset_conversation()

    assert not thread_path.exists()


def test_chatgpt_without_cli_uses_required_neutral_status(monkeypatch) -> None:
    """安装 ChatGPT GUI 但缺少 CLI 时，不得误报“未安装 Codex”。"""

    monkeypatch.setattr("onepic_desktop_pet.ai.find_codex_gui_app", lambda: Path("ChatGPT.app"))
    monkeypatch.setattr("onepic_desktop_pet.ai.find_codex_executable", lambda: None)
    expected = "已检测到 ChatGPT（包含 Codex），但未检测到 Codex CLI。"

    assert codex_detection_message() == expected
    assert check_provider_connection("codex", FakeCredentials()) == expected


def test_macos_gui_launch_uses_open_a_chatgpt(monkeypatch) -> None:
    calls = []

    monkeypatch.setattr("onepic_desktop_pet.ai.sys.platform", "darwin")
    monkeypatch.setattr("onepic_desktop_pet.ai.find_codex_gui_app", lambda: Path("/Applications/ChatGPT.app"))
    monkeypatch.setattr(
        "onepic_desktop_pet.ai.subprocess.Popen",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )

    assert launch_codex_gui() is True
    assert calls[0][0] == ["open", "-a", "ChatGPT"]

