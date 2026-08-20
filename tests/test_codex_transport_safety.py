"""验证 Codex 可执行文件解析、Windows shim 启动与错误分类。"""

import os
from pathlib import Path

import pytest

from onepic_desktop_pet.ai import (
    AIConnectionError,
    AIErrorKind,
    CodexCliCapabilities,
    _cli_command,
    ask_codex,
    resolve_codex_executable,
    user_message_for_ai_error,
)


def test_explicit_codex_path_wins_when_gui_path_is_empty(tmp_path, monkeypatch):
    executable = tmp_path / ("codex.cmd" if os.name == "nt" else "codex")
    executable.write_text("@echo off\n", encoding="utf-8")
    if os.name != "nt":
        executable.chmod(0o755)
    monkeypatch.setattr("onepic_desktop_pet.ai.find_codex_executable", lambda: None)
    assert resolve_codex_executable(executable) == executable


def test_windows_cmd_is_wrapped_by_cmd_exe(monkeypatch, tmp_path):
    executable = tmp_path / "folder with spaces" / "codex.cmd"
    command = _cli_command(executable, "--version")
    if __import__("os").name == "nt":
        assert command[1:3] == ["/d", "/s"]
        assert str(executable) in command


def test_missing_codex_is_local_executable_error_not_timeout(monkeypatch):
    monkeypatch.setattr("onepic_desktop_pet.ai.find_codex_executable", lambda: Path("codex.exe"))
    monkeypatch.setattr(
        "onepic_desktop_pet.ai._codex_cli_capabilities",
        lambda _path: CodexCliCapabilities(),
    )

    def missing(_command, **_kwargs):
        raise FileNotFoundError(2, "The system cannot find the file specified")

    monkeypatch.setattr("onepic_desktop_pet.ai.subprocess.run", missing)
    with pytest.raises(AIConnectionError) as caught:
        ask_codex("中国的首都是哪里？", [])
    assert caught.value.kind is AIErrorKind.LOCAL_EXECUTABLE_NOT_FOUND
    assert "离线陪伴" in user_message_for_ai_error(caught.value)
    assert "额度" not in user_message_for_ai_error(caught.value)
