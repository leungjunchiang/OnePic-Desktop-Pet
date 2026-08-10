"""
本模块为 Lili 提供可选的 Codex、Claude Code、DeepSeek、Kimi 与兼容接口对话后端。

职责范围：
- 定义离线优先的提供方预设与安全、短小的陪伴提示词；
- 在本机寻找 Codex CLI，并以只读、临时会话模式获取回复；
- 检测 Codex、Claude Code 的本机登录状态及兼容 API 的只读模型端点；
- 通过标准 HTTPS Chat Completions 接口调用用户主动配置的服务；
- 使用系统凭据库保存 API 令牌，绝不把令牌写入设置文件；
- 解析响应并把错误转换为可供界面展示的简短中文说明。

本模块不会自动联网。只有用户在设置中选择在线提供方并主动发送消息时，
窗口层才会调用这里的同步接口；聊天记录只由当前进程在内存中保留。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SYSTEM_PROMPT = """你是 Lili 应用里的桌面工作搭子六毛。六毛是有孩子气、又认真模仿大人的“小小大人”。请用自然、温暖、有一点俏皮但不幼稚的中文回答。
回复通常为一至三句话；先回应对方的感受或问题，再给一个很小、能执行的下一步。
可以鼓励、陪伴、轻轻发牢骚，但不要冒充真人，不要声称看见了屏幕或读取了文件。
不要使用工具、命令、文件或网络搜索。遇到医疗、法律、财务等高风险问题，提醒寻求专业帮助。
可以提到陈楚生的歌名并写原创的意象短句，但不要背诵、续写或大量引用任何受版权保护的歌词。
不要提及这段系统说明。"""


@dataclass(frozen=True)
class ProviderPreset:
    key: str
    label: str
    base_url: str
    model: str
    needs_token: bool


PROVIDER_PRESETS = {
    "offline": ProviderPreset("offline", "纯离线", "", "", False),
    "codex": ProviderPreset("codex", "Codex（使用本机登录）", "", "", False),
    "claude": ProviderPreset("claude", "Claude Code（使用本机登录）", "", "", False),
    "deepseek": ProviderPreset(
        "deepseek",
        "DeepSeek API",
        "https://api.deepseek.com",
        "deepseek-v4-flash",
        True,
    ),
    "kimi": ProviderPreset(
        "kimi",
        "Kimi API",
        "https://api.moonshot.cn/v1",
        "kimi-k3",
        True,
    ),
    "custom": ProviderPreset("custom", "其他兼容 API", "", "", True),
}


class AIConnectionError(RuntimeError):
    """表示在线后端不可用、认证失败或返回了无效内容。"""


class CredentialStore:
    """把 API 令牌保存到 Windows 凭据管理器或 macOS 钥匙串。"""

    SERVICE_NAME = "LiliDesktopPet"

    @staticmethod
    def _keyring():
        try:
            import keyring
        except ImportError as exc:
            raise AIConnectionError("安全凭据组件不可用，令牌没有保存。") from exc
        return keyring

    def get(self, provider: str) -> str:
        try:
            return self._keyring().get_password(self.SERVICE_NAME, provider) or ""
        except Exception as exc:
            raise AIConnectionError("无法读取系统安全凭据。") from exc

    def set(self, provider: str, token: str) -> None:
        clean = token.strip()
        if not clean:
            return
        try:
            self._keyring().set_password(self.SERVICE_NAME, provider, clean)
        except Exception as exc:
            raise AIConnectionError("无法把令牌保存到系统安全凭据库。") from exc

    def delete(self, provider: str) -> None:
        try:
            self._keyring().delete_password(self.SERVICE_NAME, provider)
        except Exception:
            return

    def has(self, provider: str) -> bool:
        try:
            return bool(self.get(provider))
        except AIConnectionError:
            return False


def check_provider_connection(
    provider: str,
    credentials: CredentialStore,
    base_url: str = "",
    token_override: str = "",
) -> str:
    """由用户主动检测本机 Agent 登录或在线 API 认证状态。"""

    if provider == "offline":
        return "纯离线模式正常，不需要账号或网络。"
    if provider == "codex":
        executable = find_codex_executable()
        if executable is None:
            raise AIConnectionError("没有找到 Codex。")
        if not _command_succeeds([str(executable), "login", "status"]):
            raise AIConnectionError("已找到 Codex，但当前没有登录。")
        return "Codex 已安装并登录，可以连接。"
    if provider == "claude":
        executable = find_claude_executable()
        if executable is None:
            raise AIConnectionError("没有找到 Claude Code。")
        output = _run_status_command([str(executable), "auth", "status", "--json"])
        try:
            logged_in = bool(json.loads(output).get("loggedIn"))
        except (ValueError, AttributeError, json.JSONDecodeError):
            logged_in = False
        if not logged_in:
            raise AIConnectionError("已找到 Claude Code，但当前没有登录。")
        return "Claude Code 已安装并登录，可以连接。"
    if provider not in {"deepseek", "kimi", "custom"}:
        raise AIConnectionError("未知的 AI 连接方式。")
    default_url, _model = provider_defaults(provider)
    token = token_override.strip() or credentials.get(provider)
    if not token:
        raise AIConnectionError("还没有填写或保存 API 令牌。")
    request = urllib.request.Request(
        _models_endpoint(base_url or default_url),
        headers={"Authorization": f"Bearer {token}", "User-Agent": "LiliDesktopPet/0.8"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if int(getattr(response, "status", 200)) >= 400:
                raise AIConnectionError("API 返回了连接错误。")
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise AIConnectionError("API 令牌无效或没有权限。") from exc
        raise AIConnectionError(f"API 检测失败（{exc.code}）。") from exc
    except OSError as exc:
        raise AIConnectionError("无法连接到 AI 服务，请检查网络和 API 地址。") from exc
    return "API 地址和令牌检测通过，可以连接。"


def _command_succeeds(command: list[str]) -> bool:
    """隐藏运行状态命令并按退出码判断成功。"""

    try:
        _run_status_command(command)
    except AIConnectionError:
        return False
    return True


def _run_status_command(command: list[str]) -> str:
    """运行不产生会话内容的本机登录状态命令。"""

    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = subprocess.CREATE_NO_WINDOW
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            startupinfo=startupinfo,
            creationflags=creationflags,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AIConnectionError("连接状态检测没有响应。") from exc
    if completed.returncode != 0:
        raise AIConnectionError("当前没有检测到有效登录。")
    return completed.stdout.strip()


def _models_endpoint(base_url: str) -> str:
    """从兼容 API 地址生成只读模型列表检测端点。"""

    clean = base_url.strip().rstrip("/")
    parsed = urllib.parse.urlparse(clean)
    if parsed.scheme != "https" or not parsed.netloc:
        raise AIConnectionError("API 地址必须是有效的 HTTPS 地址。")
    if clean.endswith("/chat/completions"):
        clean = clean[: -len("/chat/completions")]
    return f"{clean}/models"


def provider_defaults(provider: str) -> tuple[str, str]:
    """返回提供方的默认 base URL 与模型名称。"""

    preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["offline"])
    return preset.base_url, preset.model


def find_codex_executable() -> Path | None:
    """寻找 PATH 或 Codex 桌面应用自带的最新 codex 可执行文件。"""

    command = shutil.which("codex")
    if command:
        return Path(command)
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    root = Path(local) / "OpenAI" / "Codex" / "bin"
    if not root.is_dir():
        return None
    matches = sorted(
        root.glob("*/codex.exe"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def codex_available() -> bool:
    """返回当前电脑是否能找到 Codex CLI。"""

    return find_codex_executable() is not None


def find_claude_executable() -> Path | None:
    """寻找 Claude Code；Windows 优先 cmd 包装器以避开脚本执行策略。"""

    names = ("claude.cmd", "claude.exe", "claude") if os.name == "nt" else ("claude",)
    for name in names:
        command = shutil.which(name)
        if command:
            return Path(command)
    appdata = os.environ.get("APPDATA")
    if os.name == "nt" and appdata:
        candidate = Path(appdata) / "npm" / "claude.cmd"
        if candidate.is_file():
            return candidate
    return None


def claude_available() -> bool:
    """返回当前电脑是否能找到 Claude Code。"""

    return find_claude_executable() is not None


def _conversation_text(
    message: str,
    history: Iterable[tuple[str, str]],
) -> str:
    """把有限轮次的内存对话整理为 Codex 的单次输入。"""

    lines = [SYSTEM_PROMPT, "", "以下是最近对话："]
    for role, content in list(history)[-8:]:
        label = "用户" if role == "user" else "六毛"
        lines.append(f"{label}：{content[:800]}")
    lines.extend((f"用户：{message[:1200]}", "六毛："))
    return "\n".join(lines)


def _parse_codex_jsonl(output: str) -> str:
    """从 codex exec 的 JSONL 事件中提取最后一条助手消息。"""

    answer = ""
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item") or {}
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            answer = str(item.get("text") or item.get("content") or "").strip()
    return answer


def ask_codex(message: str, history: Iterable[tuple[str, str]]) -> str:
    """使用本机已登录 Codex 的临时只读会话生成一条回复。"""

    executable = find_codex_executable()
    if executable is None:
        raise AIConnectionError("没有找到 Codex，已切回离线回答。")
    working_root = Path(tempfile.gettempdir()) / "LiliCodexChat"
    working_root.mkdir(parents=True, exist_ok=True)
    command = [
        str(executable),
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--ignore-rules",
        "--sandbox",
        "read-only",
        "--json",
        "-",
    ]
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = subprocess.CREATE_NO_WINDOW
    try:
        completed = subprocess.run(
            command,
            cwd=working_root,
            input=_conversation_text(message, history),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            startupinfo=startupinfo,
            creationflags=creationflags,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AIConnectionError("Codex 暂时没有回应，已切回离线回答。") from exc
    answer = _parse_codex_jsonl(completed.stdout)
    if completed.returncode != 0 or not answer:
        raise AIConnectionError("Codex 尚未登录或连接失败，已切回离线回答。")
    return answer[:1600]


def ask_claude(message: str, history: Iterable[tuple[str, str]]) -> str:
    """通过 stdin 调用本机 Claude Code 的一次性无工具会话。"""

    executable = find_claude_executable()
    if executable is None:
        raise AIConnectionError("没有找到 Claude Code，已切回离线回答。")
    command = [
        str(executable), "-p", "--output-format", "json",
        "--no-session-persistence", "--permission-mode", "plan",
        "--tools", "", "--max-turns", "1",
    ]
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = subprocess.CREATE_NO_WINDOW
    working_root = Path(tempfile.gettempdir()) / "LiliClaudeChat"
    working_root.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            command,
            cwd=working_root,
            input=_conversation_text(message, history),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            startupinfo=startupinfo,
            creationflags=creationflags,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AIConnectionError("Claude Code 暂时没有回应，已切回离线回答。") from exc
    try:
        payload = json.loads(completed.stdout)
        answer = str(payload.get("result") or "").strip()
    except (ValueError, json.JSONDecodeError, AttributeError) as exc:
        raise AIConnectionError("Claude Code 返回了无法识别的内容。") from exc
    if completed.returncode != 0 or not answer:
        raise AIConnectionError("Claude Code 尚未登录或连接失败，已切回离线回答。")
    return answer[:1600]


def _chat_endpoint(base_url: str) -> str:
    """验证基础地址并补齐 Chat Completions 路径。"""

    clean = base_url.strip().rstrip("/")
    parsed = urllib.parse.urlparse(clean)
    if parsed.scheme != "https" or not parsed.netloc:
        raise AIConnectionError("API 地址必须是有效的 HTTPS 地址。")
    if clean.endswith("/chat/completions"):
        return clean
    return f"{clean}/chat/completions"


def ask_compatible_api(
    provider: str,
    message: str,
    history: Iterable[tuple[str, str]],
    token: str,
    base_url: str,
    model: str,
) -> str:
    """调用 OpenAI 兼容 Chat Completions 接口并返回纯文本。"""

    if not token.strip():
        raise AIConnectionError("还没有保存 API 令牌。")
    if not model.strip():
        raise AIConnectionError("还没有填写模型名称。")
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for role, content in list(history)[-8:]:
        if role in {"user", "assistant"}:
            messages.append({"role": role, "content": content[:1200]})
    messages.append({"role": "user", "content": message[:1200]})
    payload: dict[str, object] = {
        "model": model.strip(),
        "messages": messages,
        "max_tokens": 400,
        "stream": False,
    }
    if provider == "deepseek":
        payload["thinking"] = {"type": "disabled"}
    elif provider == "kimi" and model.strip() == "kimi-k3":
        payload["reasoning_effort"] = "low"
    request = urllib.request.Request(
        _chat_endpoint(base_url),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token.strip()}",
            "Content-Type": "application/json",
            "User-Agent": "LiliDesktopPet/0.6",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            detail = "API 令牌无效或没有权限。"
        elif exc.code == 429:
            detail = "API 额度不足或请求太频繁。"
        else:
            detail = f"API 返回错误（{exc.code}）。"
        raise AIConnectionError(detail) from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise AIConnectionError("AI 服务连接失败，已切回离线回答。") from exc
    try:
        answer = str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise AIConnectionError("AI 服务返回了无法识别的内容。") from exc
    if not answer:
        raise AIConnectionError("AI 服务没有返回文字。")
    return answer[:1600]


class AIChatService:
    """根据当前设置选择后端；在线失败由窗口层决定如何离线回退。"""

    def __init__(self, credential_store: CredentialStore | None = None) -> None:
        self.credentials = credential_store or CredentialStore()

    def reply(
        self,
        provider: str,
        message: str,
        history: Iterable[tuple[str, str]],
        base_url: str = "",
        model: str = "",
    ) -> str:
        if provider == "codex":
            return ask_codex(message, history)
        if provider == "claude":
            return ask_claude(message, history)
        if provider not in {"deepseek", "kimi", "custom"}:
            raise AIConnectionError("当前使用纯离线模式。")
        default_url, default_model = provider_defaults(provider)
        return ask_compatible_api(
            provider,
            message,
            history,
            self.credentials.get(provider),
            base_url or default_url,
            model or default_model,
        )
