"""
六毛聊天使用的 Codex App Server stdio 客户端。

本模块只负责跨平台 JSONL 传输、一次初始化、thread 生命周期、turn 流式事件
和中断；模型选择、提示词与失败回退仍由 ai.py 负责。客户端不复制登录令牌，
认证继续由 Codex CLI 使用本机已有的登录状态完成。
App Server stderr is bounded and redacted before debug logging; user-facing
transport failures are classified by the owning AI service.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping


LOGGER = logging.getLogger(__name__)


class CodexAppServerError(RuntimeError):
    """表示 App Server 没有完成协议握手、请求或 turn。"""


class CodexAppServerClient:
    """保持一个 Codex App Server 进程，并在同一 thread 上连续发送 turns。"""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        thread_id: str = "",
        on_thread_id: Callable[[str], None] | None = None,
        desired_provider: str = "",
        desired_transport: str = "",
        client_version: str = "0.22.70",
    ) -> None:
        self.command = list(command)
        self.cwd = Path(cwd)
        self.env = dict(env or os.environ)
        self.thread_id = str(thread_id or "").strip()
        self.on_thread_id = on_thread_id
        self.desired_provider = str(desired_provider or "").strip()
        self.desired_transport = str(desired_transport or "").strip()
        self.client_version = client_version
        self._process: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._notifications: queue.Queue[dict[str, Any]] = queue.Queue()
        self._next_request_id = 1
        self._write_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._turn_lock = threading.Lock()
        self._closed = False
        self._reader_error = ""
        self._active_turn_id = ""

    @property
    def is_running(self) -> bool:
        """返回长驻 App Server 是否仍在运行。"""

        process = self._process
        return process is not None and process.poll() is None

    def ensure_ready(self) -> None:
        """Start and handshake the persistent server without generating text."""

        self._ensure_ready()

    def stream_turn(
        self,
        text: str,
        *,
        model: str | None = None,
        effort: str | None = None,
        on_delta: Callable[[str], None] | None = None,
        timeout: float = 90.0,
    ) -> str:
        """发送一轮文本并在收到增量时回调，返回最终 agentMessage。"""

        clean_text = str(text or "").strip()
        if not clean_text:
            raise CodexAppServerError("不能向 Codex 发送空消息。")
        with self._turn_lock:
            self._ensure_ready()
            params: dict[str, Any] = {
                "threadId": self.thread_id,
                "input": [{"type": "text", "text": clean_text}],
                "cwd": str(self.cwd),
                "approvalPolicy": "never",
                "sandboxPolicy": {
                    "type": "readOnly",
                    "access": {"type": "fullAccess"},
                },
                "personality": "friendly",
            }
            if model:
                params["model"] = str(model)[:120]
            if effort:
                params["effort"] = str(effort)[:20]
            started = self._request("turn/start", params, timeout=min(timeout, 30.0))
            turn = started.get("turn") or {}
            turn_id = str(turn.get("id") or "").strip()
            if not turn_id:
                raise CodexAppServerError("Codex 没有返回有效的 turn。")
            self._active_turn_id = turn_id
            try:
                return self._read_turn_events(
                    turn_id,
                    on_delta=on_delta,
                    timeout=max(15.0, float(timeout)),
                )
            except CodexAppServerError as exc:
                # A timed-out turn must not leave the persistent process in an
                # unknown state.  Ask the server to stop it before the service
                # closes the client and falls back to the isolated CLI path.
                if "超时" in str(exc):
                    self.interrupt()
                raise
            finally:
                self._active_turn_id = ""

    def interrupt(self) -> bool:
        """请求中断当前 turn；没有活跃 turn 时不发送无效请求。"""

        turn_id = self._active_turn_id
        if not turn_id or not self.thread_id or not self.is_running:
            return False
        try:
            self._request(
                "turn/interrupt",
                {"threadId": self.thread_id, "turnId": turn_id},
                timeout=5.0,
            )
        except CodexAppServerError:
            return False
        return True

    def close(self) -> None:
        """关闭长驻进程，退出时最多等待很短时间，避免卡住桌面程序。"""

        with self._state_lock:
            self._closed = True
            process = self._process
            self._process = None
            pending = list(self._pending.values())
            self._pending.clear()
            active_turn_id = self._active_turn_id
            if active_turn_id:
                self._notifications.put(
                    {
                        "method": "error",
                        "params": {
                            "threadId": self.thread_id,
                            "turnId": active_turn_id,
                            "error": {"message": "App Server 已关闭。"},
                        },
                    }
                )
        if process is None:
            return
        for waiter in pending:
            waiter.put({"error": {"message": "App Server 已关闭。"}})
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=1.5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=1.5)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def _ensure_ready(self) -> None:
        with self._state_lock:
            if self.is_running and self.thread_id:
                return
            if self._closed:
                self._closed = False
            self._start_process()
            self._request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "lili_desktop_pet",
                        "title": "Lili Desktop Pet",
                        "version": self.client_version,
                    }
                },
                timeout=20.0,
            )
            self._send_notification("initialized", {})
            self._start_or_resume_thread()

    def _start_process(self) -> None:
        if self.is_running:
            return
        self.cwd.mkdir(parents=True, exist_ok=True)
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = subprocess.CREATE_NO_WINDOW
        try:
            self._process = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                env=self.env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
        except (OSError, ValueError) as exc:
            self._process = None
            raise CodexAppServerError("Codex App Server 启动失败。") from exc
        self._reader_error = ""
        self._reader_thread = threading.Thread(
            target=self._read_stdout,
            name="lili-codex-app-server",
            daemon=True,
        )
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            name="lili-codex-app-server-stderr",
            daemon=True,
        )
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        process = self._process
        stream = process.stdout if process is not None else None
        if stream is None:
            return
        try:
            for raw_line in iter(stream.readline, ""):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    LOGGER.debug("[AI Codex] ignored non-JSON App Server output")
                    continue
                if not isinstance(message, dict):
                    continue
                request_id = message.get("id")
                if isinstance(request_id, int) and "method" not in message:
                    waiter = self._pending.get(request_id)
                    if waiter is not None:
                        waiter.put(message)
                else:
                    self._notifications.put(message)
        except (OSError, ValueError):
            pass
        finally:
            if not self._closed and self._process is not None:
                self._reader_error = "Codex App Server 连接已断开。"
            error = self._reader_error or "Codex App Server 没有返回数据。"
            for waiter in list(self._pending.values()):
                waiter.put({"error": {"message": error}})

    def _drain_stderr(self) -> None:
        process = self._process
        stream = process.stderr if process is not None else None
        if stream is None:
            return
        try:
            for raw_line in iter(stream.readline, ""):
                # Never log prompts, tokens, or the full server output.  This is
                # only a bounded diagnostic useful when the child exits.
                compact = " ".join(raw_line.split())
                compact = re.sub(r"(?i)command\s*\[[^\]]*\]", "Command [REDACTED]", compact)
                compact = re.sub(r"(?i)(prompt|system\s+prompt)\s*[:=].*$", r"\1=<redacted>", compact)
                compact = re.sub(
                    r"(?i)(authorization|api[_ -]?key|token|access[_ -]?token)\s*[:=]\s*\S+",
                    r"\1=<redacted>",
                    compact,
                )
                if compact:
                    LOGGER.debug("[AI Codex] app-server: %s", compact[:300])
        except (OSError, ValueError):
            return

    def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        self._write_message({"method": method, "params": params})

    def _request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float,
    ) -> dict[str, Any]:
        with self._state_lock:
            process = self._process
            if process is None or process.stdin is None or process.poll() is not None:
                raise CodexAppServerError("Codex App Server 尚未连接。")
            request_id = self._next_request_id
            self._next_request_id += 1
            waiter: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[request_id] = waiter
            try:
                self._write_message(
                    {"id": request_id, "method": method, "params": params or {}}
                )
            except (OSError, ValueError) as exc:
                self._pending.pop(request_id, None)
                raise CodexAppServerError("无法向 Codex App Server 发送请求。") from exc
        try:
            message = waiter.get(timeout=max(1.0, float(timeout)))
        except queue.Empty as exc:
            raise CodexAppServerError(f"Codex App Server 请求超时：{method}。") from exc
        finally:
            self._pending.pop(request_id, None)
        error = message.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or "Codex App Server 返回错误。")
            raise CodexAppServerError(detail[:500])
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    def _write_message(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise OSError("App Server stdin is closed")
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            process.stdin.write(payload + "\n")
            process.stdin.flush()

    def _start_or_resume_thread(self) -> None:
        common: dict[str, Any] = {
            "cwd": str(self.cwd),
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "personality": "friendly",
            "serviceName": "lili_desktop_pet",
        }
        if self.thread_id:
            try:
                result = self._request(
                    "thread/resume",
                    {"threadId": self.thread_id, "personality": "friendly"},
                    timeout=20.0,
                )
            except CodexAppServerError:
                # A deleted, archived, or incompatible saved id should not make
                # the chat permanently unusable.  Start a fresh persistent thread.
                self.thread_id = ""
            else:
                if self._thread_matches_desired_provider(result):
                    self._accept_thread(result)
                    return
                LOGGER.warning(
                    "[AI Codex] discarding resumed thread with provider=%s; desired=%s transport=%s",
                    self._thread_provider(result) or "unknown",
                    self.desired_provider or "unknown",
                    self.desired_transport or "unknown",
                )
                # Only forget the local pointer.  The user's Codex history is
                # intentionally left untouched; a fresh thread is safer than
                # sending a turn through a stale provider configuration.
                self.thread_id = ""
        result = self._request("thread/start", common, timeout=20.0)
        if not self._thread_matches_desired_provider(result):
            raise CodexAppServerError(
                "Codex 新 thread 使用了不兼容的 model provider。"
            )
        self._accept_thread(result)

    def _thread_provider(self, result: dict[str, Any]) -> str:
        """Read provider metadata across current and older App Server shapes."""

        thread = result.get("thread") or {}
        candidates: list[Any] = []
        if isinstance(thread, dict):
            candidates.extend(
                thread.get(key)
                for key in ("modelProvider", "model_provider", "provider")
            )
        candidates.extend(
            result.get(key)
            for key in ("modelProvider", "model_provider", "provider")
        )
        for candidate in candidates:
            if isinstance(candidate, dict):
                candidate = candidate.get("id") or candidate.get("name") or candidate.get("key")
            value = str(candidate or "").strip()
            if value:
                return value
        return ""

    def _thread_matches_desired_provider(self, result: dict[str, Any]) -> bool:
        """Reject an explicitly mismatched provider, tolerate older metadata."""

        if not self.desired_provider:
            return True
        actual = self._thread_provider(result)
        # Some older servers omit provider metadata from thread/start and
        # thread/resume responses.  The local v2 state check still protects
        # those servers; only an explicit server-side mismatch is fatal.
        return not actual or actual.casefold() == self.desired_provider.casefold()

    def _accept_thread(self, result: dict[str, Any]) -> None:
        thread = result.get("thread") or {}
        thread_id = str(thread.get("id") or "").strip()
        if not thread_id:
            raise CodexAppServerError("Codex 没有返回有效的 thread。")
        self.thread_id = thread_id
        if self.on_thread_id is not None:
            try:
                self.on_thread_id(thread_id)
            except Exception:
                LOGGER.debug("[AI Codex] failed to persist thread id", exc_info=True)

    def _read_turn_events(
        self,
        turn_id: str,
        *,
        on_delta: Callable[[str], None] | None,
        timeout: float,
    ) -> str:
        started_at = time.monotonic()
        parts: list[str] = []
        final_text = ""
        while True:
            remaining = timeout - (time.monotonic() - started_at)
            if remaining <= 0:
                raise CodexAppServerError("Codex App Server 回答超时。")
            try:
                event = self._notifications.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                continue
            method = str(event.get("method") or "")
            params = event.get("params") or {}
            if not isinstance(params, dict):
                continue
            if method == "item/agentMessage/delta":
                if not self._matches_turn(params, turn_id):
                    continue
                delta = str(params.get("delta") or "")
                if delta:
                    parts.append(delta)
                    if on_delta is not None:
                        on_delta(delta)
                continue
            if method == "item/completed":
                if not self._matches_turn(params, turn_id):
                    continue
                item = params.get("item") or {}
                if str(item.get("type") or "") in {"agentMessage", "agent_message"}:
                    final_text = str(item.get("text") or item.get("content") or "").strip()
                continue
            if method == "error" and self._matches_turn(params, turn_id):
                error = params.get("error") or {}
                raise CodexAppServerError(
                    str(error.get("message") or "Codex turn 失败。")[:500]
                )
            if method != "turn/completed" or not self._matches_turn(params, turn_id):
                continue
            turn = params.get("turn") or {}
            status = str(turn.get("status") or "")
            if status != "completed":
                error = turn.get("error") or {}
                detail = str(error.get("message") or "Codex turn 没有完成。")
                raise CodexAppServerError(detail[:500])
            if not final_text:
                final_text = "".join(parts).strip()
            if not final_text:
                raise CodexAppServerError("Codex 没有返回文字。")
            # A compatible server may omit deltas.  Do not leave the UI blank.
            if not parts and on_delta is not None:
                on_delta(final_text)
            return final_text

    def _matches_turn(self, params: dict[str, Any], turn_id: str) -> bool:
        event_thread = str(params.get("threadId") or "").strip()
        event_turn = str(params.get("turnId") or "").strip()
        nested_turn = params.get("turn") or {}
        if not event_turn and isinstance(nested_turn, dict):
            event_turn = str(nested_turn.get("id") or "").strip()
        if event_thread and event_thread != self.thread_id:
            return False
        return not event_turn or event_turn == turn_id

