"""测试 Codex App Server 的 stdio 生命周期与增量事件协议。"""

from __future__ import annotations

import json
import queue
from pathlib import Path

from onepic_desktop_pet.codex_app_server import CodexAppServerClient


class _FakeStream:
    def __init__(self) -> None:
        self.lines: queue.Queue[str] = queue.Queue()
        self.closed = False

    def readline(self) -> str:
        if self.closed:
            return ""
        value = self.lines.get()
        return value

    def close(self) -> None:
        self.closed = True
        self.lines.put("")


class _FakeStdin:
    def __init__(self, owner: "_FakeProcess") -> None:
        self.owner = owner

    def write(self, payload: str) -> None:
        message = json.loads(payload)
        self.owner.messages.append(message)
        method = message.get("method")
        request_id = message.get("id")
        if method == "initialize":
            self.owner.emit({"id": request_id, "result": {"userAgent": "fake"}})
        elif method == "thread/start":
            self.owner.thread_id = "thr_fake"
            thread = {"id": "thr_fake"}
            if self.owner.start_provider:
                thread["modelProvider"] = self.owner.start_provider
            self.owner.emit(
                {"id": request_id, "result": {"thread": thread}}
            )
        elif method == "thread/resume":
            self.owner.thread_id = "thr_saved"
            thread = {"id": "thr_saved"}
            if self.owner.resume_provider:
                thread["modelProvider"] = self.owner.resume_provider
            self.owner.emit(
                {"id": request_id, "result": {"thread": thread}}
            )
        elif method == "turn/start":
            self.owner.emit(
                {"id": request_id, "result": {"turn": {"id": "turn_fake"}}}
            )
            self.owner.emit(
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": self.owner.thread_id,
                        "turnId": "turn_fake",
                        "delta": "首字",
                    },
                }
            )
            self.owner.emit(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": self.owner.thread_id,
                        "turnId": "turn_fake",
                        "item": {"type": "agentMessage", "text": "首字完整回复"},
                    },
                }
            )
            self.owner.emit(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": self.owner.thread_id,
                        "turn": {"id": "turn_fake", "status": "completed"},
                    },
                }
            )

    def flush(self) -> None:
        return

    def close(self) -> None:
        self.owner.stdout.close()


class _FakeProcess:
    def __init__(self) -> None:
        self.stdout = _FakeStream()
        self.stderr = _FakeStream()
        self.stdin = _FakeStdin(self)
        self.messages: list[dict] = []
        self.thread_id = ""
        self.resume_provider = ""
        self.start_provider = ""
        self.returncode = None

    def emit(self, message: dict) -> None:
        self.stdout.lines.put(json.dumps(message) + "\n")

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.returncode = -9
        self.stdout.close()


def test_app_server_initializes_once_and_streams_multiple_turns(monkeypatch, tmp_path: Path) -> None:
    process = _FakeProcess()
    monkeypatch.setattr(
        "onepic_desktop_pet.codex_app_server.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )
    saved: list[str] = []
    client = CodexAppServerClient(
        ["codex", "app-server"],
        cwd=tmp_path,
        thread_id="",
        on_thread_id=saved.append,
    )

    first: list[str] = []
    assert client.stream_turn("第一句", on_delta=first.append) == "首字完整回复"
    assert first == ["首字"]
    assert saved == ["thr_fake"]

    # The process and thread remain alive; the second message is only a new turn.
    assert client.stream_turn("第二句", on_delta=first.append) == "首字完整回复"
    methods = [message.get("method") for message in process.messages]
    assert methods.count("initialize") == 1
    assert methods.count("thread/start") == 1
    assert methods.count("turn/start") == 2
    client.close()


def test_app_server_can_resume_saved_thread(monkeypatch, tmp_path: Path) -> None:
    process = _FakeProcess()
    monkeypatch.setattr(
        "onepic_desktop_pet.codex_app_server.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )
    client = CodexAppServerClient(
        ["codex", "app-server"],
        cwd=tmp_path,
        thread_id="thr_saved",
    )
    assert client.stream_turn("恢复", on_delta=lambda _delta: None) == "首字完整回复"
    methods = [message.get("method") for message in process.messages]
    assert "thread/resume" in methods
    assert "thread/start" not in methods
    client.close()


def test_app_server_replaces_resumed_thread_when_provider_changed(monkeypatch, tmp_path: Path) -> None:
    process = _FakeProcess()
    process.resume_provider = "openai"
    process.start_provider = "lili_http"
    monkeypatch.setattr(
        "onepic_desktop_pet.codex_app_server.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )
    saved: list[str] = []
    invalidated: list[bool] = []
    client = CodexAppServerClient(
        ["codex", "app-server"],
        cwd=tmp_path,
        thread_id="thr_saved",
        on_thread_id=saved.append,
        on_thread_invalidated=lambda: invalidated.append(True),
        desired_provider="lili_http",
        desired_transport="https",
    )

    assert client.stream_turn("恢复", on_delta=lambda _delta: None) == "首字完整回复"
    methods = [message.get("method") for message in process.messages]
    assert methods.count("thread/resume") == 1
    assert methods.count("thread/start") == 1
    assert saved == ["thr_fake"]
    assert invalidated == [True]
    assert client.thread_id == "thr_fake"
    client.close()

