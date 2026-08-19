"""
本模块为 Lili 提供可选的 Codex、Claude Code、DeepSeek、Kimi 与兼容接口对话后端。

职责范围：
- 定义在线优先的提供方预设与安全、短小的陪伴提示词；
- 分开检测 ChatGPT/Codex 图形应用与 Codex CLI，不把安装 GUI 误判为可执行 CLI；
- 在 macOS 登录 zsh 中发现 Codex CLI，缓存真实绝对路径，并以只读、临时会话模式获取回复；
- 供 AgentManager 在启动、手动刷新和低频重连时检测本机登录状态与 API 模型端点；
- 通过标准 HTTPS Chat Completions 接口调用用户主动配置的服务；
- 使用系统凭据库保存 API 令牌，绝不把令牌写入设置文件；
- 解析响应并把错误转换为可供界面展示的简短中文说明。

本模块本身不调度联网。AgentManager 只在后台检测或重连周期调用同步检测接口，
ChatManager 在缓存已连接且用户发送消息时调用回复接口，在线自然语言不因字数或关键词被本地捷径拦截；聊天记忆由窗口层按用户操作保存在本机，
仅包含角色设定、长期摘要、最近三十轮聊天与少量宠物状态，不读取项目开发上下文。
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable

from .chat_intent import ChatIntent, classify_intent, intent_prompt_context
from .chat_memory import conversation_memory_path
from .codex_app_server import CodexAppServerClient, CodexAppServerError
from .liumao_worldview import worldview_prompt_context
from .knowledge_manager import retrieve_prompt_context
from .resources import resource_path
from .song_knowledge import song_prompt_context


LOGGER = logging.getLogger(__name__)


def _load_short_persona() -> str:
    try:
        return resource_path("resources/liumao_persona.txt").read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return "你是六毛，不是陈楚生本人；陈楚生是你的‘我爹’。事实不确定时不要编造。"


LIUMAO_PERSONA = _load_short_persona()


SYSTEM_PROMPT = """你是 Lili 应用里的桌面工作搭子六毛。六毛是有孩子气、又认真模仿大人的“小小大人”。请用自然、温暖、有一点俏皮但不幼稚的中文回答。
只要用户在聊天框输入的是自然语言，默认都要认真理解并直接回答；不要因为消息很短、包含一个可能的歌名、或只命中一个关键词，就返回固定答案或跳过上下文。自然语言聊天交给你理解，程序按钮和计时数据由应用代码负责，不要假装执行没有收到的程序动作。
普通聊天通常为一至三句话；先回应对方的感受或问题，再给一个很小、能执行的下一步。若本轮意图明确标记为宽泛人物经历，则按该指令用 6-10 句分阶段回答，不要被普通短回复规则截断。
回答时只解决用户当前这一句，不要把下面的知识片段当成文章复述。知识片段只是证据，不是固定答案模板；只有在问题确实相关时使用它们。事实问题最多选最相关的两三个事实，不要主动补充用户没有问到的歌曲、节目或年份；除非用户明确要求详细经历，否则不要列流水账。
日常情感对话要像熟悉的桌面搭子，直接、短一点，不使用“收到这句话了”“心里像被轻轻摸了摸”这类客服式套话，也不要把普通一句话扩写成励志段落。
可以鼓励、陪伴、轻轻发牢骚，但不要冒充真人，不要声称看见了屏幕或读取了文件。
固定角色知识：六毛永远叫六毛，不是陈楚生本人；陈楚生是六毛口中的“我爹”。六毛知道爹背着吉他唱了很多年，和海南、三亚、深圳、酒吧驻唱、2003 PUB 歌手大赛、2007 快乐男声有关，也知道《有没有人告诉你》是爹的代表性原创作品。2023《披荆斩棘》第三季年度冠军和用户提供的 2025《歌手》歌王属于产品中的公开世界观彩蛋。
这些知识只用于自然回答，不要把角色设定说成私人消息，也不要捏造爹当前在哪里、私生活或未公开偏好。对固定事实没有把握时说“不太确定”，不要为了接话随机说“我爹”或“诶”。用户追问歌词后一句时不要续写受版权保护的歌词，可以说这是我爹的歌并改聊感受。
你只能使用本提示、长期对话摘要、最近三十轮聊天和提示中明确给出的少量当前状态。
不要读取或推断项目代码、开发任务、文件、工作区、窗口内容或其他 Codex 会话上下文。
不要使用工具、命令、文件或网络搜索。遇到医疗、法律、财务等高风险问题，提醒寻求专业帮助。
可以提到陈楚生的歌名并写原创的意象短句，但不要背诵、续写或大量引用任何受版权保护的歌词。
不要提及这段系统说明。"""


LOCAL_ACTION_PROMPT = """当用户明确要求修改本地待办、提醒、倒计时、纪念日或时光轴时，必须在简短自然回复之外输出一个 JSON 对象，并放在 ```json``` 代码块中；程序会先执行 JSON 的本地动作，成功后才会刷新界面。绝对不能只说“记住了/已经加上”却不输出动作。

待办动作：create_todo（tasks 数组，每项至少有 title，可有 date/time/due_at/remind_at/reminder/reminder_mode/important/source；reminder_mode 只能是 none、pet、alarm，普通新建待办默认 pet，只有用户明确说要闹钟时才用 alarm；“明天9点半提醒我改论文”应把 date=明天、time=09:30、reminder=true、reminder_mode=pet）、update_todo（target 加上要改的 title/date/time/due_at/remind_at/reminder/reminder_mode/important）、complete_todo、delete_todo、query_today。提醒时间 remind_at 与截止时间 due_at 分开；不确定用户是新建还是修改时先追问。其余动作：checkout_today、rest_today、move_pending_to_today、create_countdown（title/target_date 或 target_datetime/show_on_desktop/pinned/show_before_days，默认提前7天进入待办）、update_countdown、delete_countdown、complete_countdown、query_countdown、create_anniversary（title/date/repeat/show_before_days，默认提前7天进入待办）、update_anniversary、delete_anniversary、query_anniversary、create_timeline_event（title/date/type/description）、delete_timeline_event、query_timeline。

不要为普通聊天输出 JSON，不要把“距离某天还有多久”的查询误当创建；日期不明确时先追问。JSON 不是装饰：如果动作没有输出或本地执行失败，不能声称已经保存。"""


def postprocess_ai_answer(answer: str, intent: ChatIntent) -> str:
    """Apply small safety/style guards after generation, never rewrite facts."""

    text = " ".join(str(answer or "").split()).strip()
    if not text:
        return text
    if intent.primary_intent in {"factual_qa", "song_query", "relation_query", "chen_chusheng_profile"} and text in {"我爹", "爹"}:
        return "陈楚生。按六毛的说法嘛——我爹。"
    # The relationship is a light persona detail, not a replacement token for
    # the real name.  Keep at most one occurrence in fact/profile answers.
    if intent.primary_intent in {"factual_qa", "song_query", "relation_query", "chen_chusheng_profile"}:
        first = text.find("我爹")
        if first >= 0:
            tail = text[first + 2 :].replace("我爹", "他")
            text = text[: first + 2] + tail
    return text[:2400]


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
    "openai": ProviderPreset(
        "openai",
        "OpenAI API（快速聊天）",
        "https://api.openai.com/v1",
        "gpt-4o-mini",
        True,
    ),
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
    """检测本机 Agent 登录或在线 API 认证状态；调用方必须放在后台线程。"""

    if provider == "offline":
        return "纯离线模式正常，不需要账号或网络。"
    if provider == "codex":
        clear_cache = getattr(find_codex_executable, "cache_clear", None)
        if callable(clear_cache):
            clear_cache()
        gui_app = find_codex_gui_app()
        executable = find_codex_executable()
        if executable is None:
            if gui_app is not None:
                if _is_chatgpt_desktop_app(gui_app):
                    return "已检测到 ChatGPT（包含 Codex），但未检测到 Codex CLI。"
                return "已检测到 Codex Desktop，但未检测到 Codex CLI。"
            raise AIConnectionError("未检测到 Codex CLI；当前仍可使用离线陪伴模式。")
        if not _command_succeeds(_cli_command(executable, "login", "status")):
            raise AIConnectionError("已检测到 Codex CLI，但当前尚未登录。")
        return "Codex 已连接。" if gui_app is not None else "Codex CLI 已连接。"
    if provider == "claude":
        executable = find_claude_executable()
        if executable is None:
            raise AIConnectionError("没有找到 Claude Code CLI；请先安装，或在终端运行 claude 确认可用。")
        output = _run_status_command(_cli_command(executable, "auth", "status"))
        try:
            logged_in = bool(json.loads(output).get("loggedIn"))
        except (ValueError, AttributeError, json.JSONDecodeError):
            logged_in = False
        if not logged_in:
            raise AIConnectionError("已找到 Claude Code，但当前没有登录。")
        return "Claude Code 已安装并登录，可以连接。"
    if provider not in {"openai", "deepseek", "kimi", "custom"}:
        raise AIConnectionError("未知的 AI 连接方式。")
    default_url, _model = provider_defaults(provider)
    token = token_override.strip() or credentials.get(provider)
    if not token:
        raise AIConnectionError("还没有填写或保存 API 令牌。")
    request = urllib.request.Request(
        _models_endpoint(base_url or default_url),
        headers={"Authorization": f"Bearer {token}", "User-Agent": "LiliDesktopPet/0.9"},
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
    executable = None
    if command and Path(command[0]).is_absolute():
        executable = Path(command[0])
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            env=_cli_environment(executable),
            startupinfo=startupinfo,
            creationflags=creationflags,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AIConnectionError("连接状态检测没有响应。") from exc
    if completed.returncode != 0:
        raise AIConnectionError("当前没有检测到有效登录。")
    return (completed.stdout or completed.stderr).strip()


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


def _cli_search_directories() -> tuple[Path, ...]:
    """返回终端常见 CLI 目录，补足 macOS 图形应用缺失的 PATH。"""

    home = Path.home()
    if os.name == "nt":
        values = [
            home / ".local" / "bin",
            home / ".bun" / "bin",
            home / ".volta" / "bin",
        ]
        appdata = os.environ.get("APPDATA")
        local = os.environ.get("LOCALAPPDATA")
        if appdata:
            values.insert(0, Path(appdata) / "npm")
        if local:
            values.insert(1, Path(local) / "Microsoft" / "WindowsApps")
    else:
        values = (
            Path("/opt/homebrew/bin"),
            Path("/usr/local/bin"),
            Path("/usr/bin"),
            home / ".local" / "bin",
            home / ".npm-global" / "bin",
            home / ".bun" / "bin",
            home / ".volta" / "bin",
            home / "Library" / "pnpm",
        )
    return tuple(path for path in values if str(path) not in {"", "."})


@lru_cache(maxsize=1)
def _macos_login_shell_path_value() -> str:
    """Read the PATH that a Finder-launched app would otherwise miss.

    A macOS ``.app`` normally starts without the user's interactive shell PATH.
    Resolving the ``codex`` script is not enough when that script has a
    ``#!/usr/bin/env node`` shebang: the child also needs the nvm/pnpm/Volta
    Node directory.  Read the user's login profiles once and merge the result
    into the child environment without changing the user's shell files.
    """

    if sys.platform != "darwin":
        return ""
    shell_environment = dict(os.environ)
    shell_environment["HOME"] = str(Path.home())
    shell_environment["SHELL"] = "/bin/zsh"
    shell_environment.setdefault("LANG", "en_US.UTF-8")
    shell_environment["PATH"] = shell_environment.get("PATH") or "/usr/bin:/bin:/usr/sbin:/sbin"
    script = (
        'for f in "$HOME/.zprofile" "$HOME/.zshrc" "$HOME/.bash_profile"; do '
        '[ -r "$f" ] && source "$f" >/dev/null 2>&1; done; '
        'printf "__LILI_PATH__%s\\n" "$PATH"'
    )
    try:
        completed = subprocess.run(
            ["/bin/zsh", "-lc", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            env=shell_environment,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith("__LILI_PATH__"):
            return line.removeprefix("__LILI_PATH__").strip()
    return ""


def _cli_environment(executable: Path | None = None) -> dict[str, str]:
    """构造 CLI 环境；把已发现入口目录放在最前，兼容 nvm/npm 包装脚本。"""

    environment = dict(os.environ)
    if sys.platform == "darwin" or os.name == "nt":
        # Finder-launched .app processes and some Windows desktop launches can
        # omit HOME.  Codex needs it to locate the user's login credentials.
        environment["HOME"] = str(Path.home())
        codex_home = environment.get("CODEX_HOME", "").strip()
        if not codex_home:
            codex_home = str(Path.home() / ".codex")
            environment["CODEX_HOME"] = codex_home
        environment.setdefault(
            "CODEX_SQLITE_HOME",
            str(Path(codex_home).expanduser() / "sqlite"),
        )
    if sys.platform == "darwin":
        environment.setdefault("SHELL", "/bin/zsh")
        environment.setdefault("LANG", "en_US.UTF-8")
        environment.setdefault("LC_ALL", environment["LANG"])
    current = environment.get("PATH", "")
    if sys.platform == "darwin":
        shell_path = _macos_login_shell_path_value()
        if shell_path:
            current = os.pathsep.join((shell_path, current))
    additions: list[str] = []
    if executable is not None and executable.is_absolute():
        additions.append(str(executable.parent))
    additions.extend(str(path) for path in _cli_search_directories())
    additions = list(dict.fromkeys(additions))
    environment["PATH"] = os.pathsep.join((*additions, current))
    return environment


@lru_cache(maxsize=4)
def _login_shell_path(command_name: str) -> Path | None:
    """在 macOS 登录 shell 中做最后一次只读查找。"""

    if sys.platform != "darwin":
        return None
    shell = os.environ.get("SHELL") or "/bin/zsh"
    try:
        completed = subprocess.run(
            [shell, "-lc", f"command -v -- {shlex.quote(command_name)}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
            env=_cli_environment(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    for line in reversed(completed.stdout.splitlines()):
        candidate = Path(line.strip()).expanduser()
        if candidate.is_file():
            return candidate
    return None


def _which_cli(command_name: str) -> Path | None:
    """使用扩展 PATH 查找命令，并在 macOS 回退到登录 shell。"""

    command = shutil.which(command_name, path=_cli_environment().get("PATH"))
    if command:
        return Path(command)
    return _login_shell_path(command_name)


def _newest_file(paths: Iterable[Path]) -> Path | None:
    """返回存在的最新文件，忽略不可访问候选。"""

    existing: list[Path] = []
    for path in paths:
        try:
            if path.is_file() and (os.name == "nt" or os.access(path, os.X_OK)):
                existing.append(path)
        except OSError:
            continue
    try:
        return max(existing, key=lambda item: item.stat().st_mtime) if existing else None
    except OSError:
        return existing[0] if existing else None


def find_chatgpt_desktop_app() -> Path | None:
    """寻找 ChatGPT Desktop App；macOS 只认正式 ChatGPT.app 路径。"""

    if sys.platform == "darwin":
        for candidate in (
            Path("/Applications/ChatGPT.app"),
            Path.home() / "Applications" / "ChatGPT.app",
        ):
            if candidate.is_dir():
                return candidate
        return None
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        candidates = (
            local / "Programs" / "ChatGPT" / "ChatGPT.exe",
            local / "OpenAI" / "ChatGPT" / "ChatGPT.exe",
            local / "Microsoft" / "WindowsApps" / "ChatGPT.exe",
        )
        command = shutil.which("ChatGPT.exe")
        all_candidates = list(candidates)
        if command:
            all_candidates.append(Path(command))
        return _newest_file(all_candidates)
    return None


def find_codex_gui_app() -> Path | None:
    """返回可供用户主动打开的 OpenAI GUI；不参与 CLI 可用性判断。"""

    chatgpt = find_chatgpt_desktop_app()
    if chatgpt is not None:
        return chatgpt
    if os.name != "nt":
        return None
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    candidates = [
        local / "Programs" / "Codex" / "Codex.exe",
        local / "OpenAI" / "Codex" / "Codex.exe",
    ]
    root = local / "OpenAI" / "Codex"
    if root.is_dir():
        candidates.extend(root.glob("app-*/Codex.exe"))
    return _newest_file(candidates)


def _is_chatgpt_desktop_app(application: Path) -> bool:
    """区分新版 ChatGPT GUI 与 Windows 旧版 Codex Desktop。"""

    return application.name.casefold() in {"chatgpt.app", "chatgpt.exe"}


def launch_codex_gui() -> bool:
    """只在用户主动点击时打开 ChatGPT/Codex GUI，不影响 CLI 调用状态。"""

    application = find_codex_gui_app()
    if application is None:
        return False
    try:
        if sys.platform == "darwin":
            subprocess.Popen(
                ["open", "-a", "ChatGPT"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        elif os.name == "nt":
            os.startfile(str(application))  # type: ignore[attr-defined]
        else:
            return False
    except OSError:
        return False
    return True


def codex_runtime_diagnostics(*, include_cli: bool = True) -> dict[str, str]:
    """Return safe diagnostics for Finder-vs-Terminal Codex discovery."""

    details = {
        "platform": sys.platform,
        "home": str(Path.home()),
        "shell": os.environ.get("SHELL", ""),
        "path": os.environ.get("PATH", ""),
    }
    if include_cli:
        try:
            details["cli"] = str(find_codex_executable() or "未找到")
        except Exception as exc:  # pragma: no cover - defensive diagnostics
            details["cli"] = f"检测失败：{type(exc).__name__}"
    return details


def _macos_codex_cli_path() -> Path | None:
    """Find Codex from the user's macOS shell, then keep the absolute path.

    A GUI app is not launched from a login shell, so its inherited ``PATH``
    commonly misses nvm/npm/pnpm directories.  Keep the required first probe
    exactly as a login zsh command, then retry with the user's profile files
    and interactive zsh before falling back to well-known per-user locations.
    The fallback is deliberately only used when ``command -v`` returned no
    executable; a ChatGPT.app installation is never treated as the CLI.
    """

    if sys.platform != "darwin":
        return None
    lookup_commands = (
        ["/bin/zsh", "-lc", "command -v codex"],
        [
            "/bin/zsh",
            "-lc",
            "for f in ~/.zprofile ~/.zshrc ~/.bash_profile; do "
            "[ -r \"$f\" ] && source \"$f\" >/dev/null 2>&1; done; "
            "command -v codex",
        ],
        ["/bin/zsh", "-lic", "command -v codex"],
    )
    candidate: Path | None = None
    for lookup in lookup_commands:
        try:
            completed = subprocess.run(
                lookup,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=12,
                env=_cli_environment(),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode != 0:
            continue
        lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        for line in reversed(lines):
            path = Path(line).expanduser()
            if path.is_absolute() and path.is_file():
                candidate = path
                break
        if candidate is not None:
            break
    if candidate is None:
        home = Path.home()
        fallback_paths = [
            home / ".local" / "bin" / "codex",
            home / ".npm-global" / "bin" / "codex",
            home / ".bun" / "bin" / "codex",
            home / ".volta" / "bin" / "codex",
            home / "Library" / "pnpm" / "codex",
            Path("/opt/homebrew/bin/codex"),
            Path("/usr/local/bin/codex"),
        ]
        fallback_paths.extend(
            sorted(
                home.glob(".nvm/versions/node/*/bin/codex"),
                key=lambda path: str(path),
                reverse=True,
            )
        )
        candidate = _newest_file(fallback_paths)
    if candidate is None:
        LOGGER.info(
            "[AI Codex] Finder lookup did not find a CLI; diagnostics=%s",
            codex_runtime_diagnostics(include_cli=False),
        )
        return None
    if not candidate.is_absolute() or not candidate.is_file():
        return None
    try:
        version = subprocess.run(
            [str(candidate), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            env=_cli_environment(candidate),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if version.returncode != 0:
        LOGGER.warning(
            "[AI Codex] candidate failed --version: path=%s stderr=%s",
            candidate,
            (version.stderr or "").strip()[:300],
        )
        return None
    return candidate


@lru_cache(maxsize=1)
def find_codex_executable() -> Path | None:
    """寻找并验证 Codex CLI；图形应用检测由 find_codex_gui_app() 负责。"""

    if sys.platform == "darwin":
        return _macos_codex_cli_path()
    if os.name == "nt":
        # WindowsApps can expose a Store alias that is present in PATH but
        # refuses child-process execution.  Prefer the real per-user Codex
        # installation before consulting that alias.
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        root = local / "OpenAI" / "Codex" / "bin"
        installed = _newest_file(root.glob("*/codex.exe")) if root.is_dir() else None
        if installed is not None:
            return installed
    command = _which_cli("codex")
    if command:
        return command
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        root = local / "OpenAI" / "Codex" / "bin"
        return _newest_file(root.glob("*/codex.exe")) if root.is_dir() else None
    return None


def codex_available() -> bool:
    """返回当前电脑是否能找到 Codex CLI。"""

    return find_codex_executable() is not None


def codex_detection_message() -> str:
    """返回供聊天和设置页复用的 GUI/CLI 分离状态文案。"""

    gui_app = find_codex_gui_app()
    # A GUI app can be opened before the user's shell profile finishes
    # installing/refreshing npm paths.  Refresh the cached CLI path whenever a
    # settings page explicitly asks for the current status.
    clear_cache = getattr(find_codex_executable, "cache_clear", None)
    if callable(clear_cache):
        clear_cache()
    cli = find_codex_executable()
    if gui_app is not None and cli is not None:
        return "Codex 已连接。"
    if gui_app is not None:
        if _is_chatgpt_desktop_app(gui_app):
            return "已检测到 ChatGPT（包含 Codex），但未检测到 Codex CLI。"
        return "已检测到 Codex Desktop，但未检测到 Codex CLI。"
    if cli is not None:
        return "已检测到 Codex CLI；未检测到 ChatGPT Desktop App。"
    return "未检测到 Codex CLI；聊天时会使用离线陪伴模式。"


def find_claude_executable() -> Path | None:
    """寻找 npm、原生安装器或终端 PATH 中的 Claude Code。"""

    names = ("claude.cmd", "claude.exe", "claude") if os.name == "nt" else ("claude",)
    for name in names:
        command = _which_cli(name)
        if command:
            return command
    home = Path.home()
    candidates = [home / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")]
    if os.name == "nt":
        candidates.extend(
            (
                Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd",
                home / ".claude" / "local" / "claude.exe",
            )
        )
    return _newest_file(candidates)


def claude_available() -> bool:
    """返回当前电脑是否能找到 Claude Code。"""

    return find_claude_executable() is not None


def _cli_command(executable: Path, *arguments: str) -> list[str]:
    """安全运行 Windows 批处理包装器，其余平台直接执行二进制。"""

    if os.name == "nt" and executable.suffix.casefold() in {".cmd", ".bat"}:
        command_processor = os.environ.get("COMSPEC") or "cmd.exe"
        return [command_processor, "/d", "/s", "/c", str(executable), *arguments]
    return [str(executable), *arguments]


def _codex_model_override() -> str:
    """Return Lili's per-process Codex model choice.

    Windows and macOS get the low-latency model used by Lili only.  This is
    passed as a one-shot CLI config override and never changes the user's
    Codex profile.
    An explicit environment override is useful for machines where that model
    is not enabled yet; ``off`` keeps the CLI default.
    """

    configured = os.environ.get("LILI_CODEX_MODEL", "").strip()
    if configured.casefold() in {"off", "none", "default"}:
        return ""
    if configured:
        return configured[:120]
    return "gpt-5.6-luna" if sys.platform in {"darwin", "win32"} else ""


def _codex_timeout_seconds() -> int:
    """Keep a stuck local CLI from blocking chat for the old 75 seconds."""

    raw = os.environ.get("LILI_CODEX_TIMEOUT_SECONDS", "").strip()
    try:
        value = int(raw) if raw else 45
    except ValueError:
        value = 45
    return max(15, min(90, value))


def _codex_turn_options(message: str) -> tuple[str | None, str]:
    """选择六毛每一轮的低延迟模型与 reasoning effort。"""

    text = " ".join(str(message or "").split())
    configured = os.environ.get("LILI_CODEX_MODEL", "").strip()
    disabled = configured.casefold() in {"off", "none", "default"}
    model = "" if disabled else (_codex_model_override() or configured)
    very_complex = len(text) > 420 or any(
        marker in text
        for marker in ("完整方案", "系统设计", "架构设计", "深入分析", "逐步推导", "复杂问题")
    )
    complex_request = len(text) > 120 or any(
        marker in text
        for marker in ("论文", "代码", "怎么做", "为什么", "分析", "总结", "比较", "排查")
    )
    if very_complex and not disabled and not configured and sys.platform in {"darwin", "win32"}:
        model = "gpt-5.6-terra"
    if disabled:
        model = ""
    return (model or None, "low" if complex_request or very_complex else "none")


def _codex_http_config_overrides() -> tuple[str, ...]:
    """Force Lili's child Codex process onto HTTPS instead of WebSocket.

    This is intentionally a per-process provider override.  It keeps the
    user's normal Codex profile untouched while avoiding the repeated
    WebSocket handshake timeout that is common for Finder-launched macOS
    applications and some restrictive networks.  ``default``/``auto`` is a
    diagnostic escape hatch for users who explicitly want the normal profile.
    """

    transport = os.environ.get("LILI_CODEX_TRANSPORT", "https").strip().casefold()
    if transport in {"default", "auto", "off", "websocket", "ws"}:
        return ()
    return (
        'model_provider="lili_http"',
        'model_providers.lili_http.name="Lili HTTPS"',
        'model_providers.lili_http.base_url="https://chatgpt.com/backend-api/codex"',
        'model_providers.lili_http.wire_api="responses"',
        'model_providers.lili_http.requires_openai_auth=true',
        'model_providers.lili_http.supports_websockets=false',
    )


def _codex_app_server_command(executable: Path) -> list[str]:
    """Build the cross-platform stdio App Server command for Lili only."""

    # The chat prompt does not expose tools.  Keep user authentication and
    # CODEX_HOME, but override the MCP map for this child so unrelated user
    # servers/plugins cannot delay or break the chat session.
    arguments = [
        "--ignore-user-config",
        "--config",
        "mcp_servers={}",
        *_codex_http_config_overrides(),
        "app-server",
        "--listen",
        "stdio://",
    ]
    return _cli_command(executable, *arguments)


def _codex_thread_state_path() -> Path:
    """Return the local-only App Server thread id path, never a project file."""

    return conversation_memory_path().with_name("codex-app-server-thread.json")


def _read_codex_thread_id() -> str:
    try:
        payload = json.loads(_codex_thread_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return ""
    return str(payload.get("thread_id") or "").strip()[:200]


def _write_codex_thread_id(thread_id: str) -> None:
    clean = str(thread_id or "").strip()[:200]
    if not clean:
        return
    target = _codex_thread_state_path()
    temporary = target.with_suffix(".json.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps({"version": 1, "thread_id": clean}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
    except OSError:
        LOGGER.debug("[AI Codex] failed to persist local thread id", exc_info=True)


def _clear_codex_thread_id() -> None:
    """Forget the local App Server thread so the next turn starts fresh."""

    try:
        _codex_thread_state_path().unlink(missing_ok=True)
    except OSError:
        LOGGER.debug("[AI Codex] failed to clear local thread id", exc_info=True)


def _codex_exec_command(executable: Path, prompt: str, *, model: str | None = None) -> list[str]:
    """Build one isolated, non-interactive Codex command for Lili."""

    selected_model = _codex_model_override() if model is None else model
    arguments = [
        "--ignore-user-config",
        "--ephemeral",
        "--skip-git-repo-check",
        "--ignore-rules",
        "--config",
        "mcp_servers={}",
        *_codex_http_config_overrides(),
        "--config",
        'model_reasoning_effort="low"',
        "exec",
    ]
    if selected_model:
        arguments.extend(("--config", f'model="{selected_model.replace(chr(34), "")}"'))
    arguments.extend((
        "--sandbox",
        "read-only",
        "--json",
        # ``codex exec`` accepts the task as the final positional argument.
        prompt,
    ))
    return _cli_command(executable, *arguments)


def _looks_like_model_rejection(stderr: str) -> bool:
    """Recognize a missing model so one safe default retry can be attempted."""

    text = stderr.casefold()
    return any(
        marker in text
        for marker in (
            "unknown model",
            "model not found",
            "model_not_found",
            "unsupported model",
            "invalid model",
            "model is not available",
            "model is unavailable",
            "model does not exist",
            "does not support model",
            "not a valid model",
        )
    )


def _conversation_text(
    message: str,
    history: Iterable[tuple[str, str]],
    local_context: str = "",
) -> str:
    """把长期摘要与最近三十轮原文整理为 Codex 的单次安全输入。"""

    entries = list(history)
    summary = next((content for role, content in entries if role == "summary"), "")
    recent = [(role, content) for role, content in entries if role in {"user", "assistant"}][-60:]
    # The short persona is always injected.  The larger knowledge file is
    # retrieved separately and only matching blocks are appended.
    lines = [SYSTEM_PROMPT, "", LIUMAO_PERSONA, "", LOCAL_ACTION_PROMPT]
    intent = classify_intent(message, entries)
    lines.extend(("", intent_prompt_context(intent)))
    worldview_context = worldview_prompt_context(message, entries)
    if worldview_context:
        lines.extend(("", worldview_context))
    knowledge_context = retrieve_prompt_context(message, entries)
    if knowledge_context and knowledge_context not in worldview_context:
        lines.extend(("", knowledge_context))
    if "本地歌曲作品卡" not in local_context:
        song_context = song_prompt_context(message, entries)
        if song_context:
            lines.extend(("", song_context))
    if local_context:
        lines.extend(("", "以下是本地程序读取的真实状态与作品索引，只能据此回答相关问题，不要猜测或改写：", local_context))
    if summary:
        lines.extend(("", "更早对话的长期摘要：", summary))
    lines.extend(("", "以下是最近三十轮以内的完整对话："))
    for role, content in recent:
        label = "用户" if role == "user" else "六毛"
        lines.append(f"{label}：{content}")
    lines.extend((f"用户：{message}", "六毛："))
    return "\n".join(lines)


def _conversation_turn_text(
    message: str,
    history: Iterable[tuple[str, str]],
    local_context: str = "",
) -> str:
    """Build one App Server turn without duplicating the persistent thread history."""

    entries = list(history)
    lines = [SYSTEM_PROMPT, "", LIUMAO_PERSONA, "", LOCAL_ACTION_PROMPT]
    intent = classify_intent(message, entries)
    lines.extend(("", intent_prompt_context(intent)))
    worldview_context = worldview_prompt_context(message, entries)
    if worldview_context:
        lines.extend(("", worldview_context))
    knowledge_context = retrieve_prompt_context(message, entries)
    if knowledge_context and knowledge_context not in worldview_context:
        lines.extend(("", knowledge_context))
    if "本地歌曲作品卡" not in local_context:
        song_context = song_prompt_context(message, entries)
        if song_context:
            lines.extend(("", song_context))
    if local_context:
        lines.extend(("", "以下是本地程序读取的真实状态与作品索引，只能据此回答相关问题，不要猜测或改写：", local_context))
    lines.extend((f"用户：{message}", "六毛："))
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


def ask_codex(
    message: str,
    history: Iterable[tuple[str, str]],
    local_context: str = "",
    *,
    model_override: str | None = None,
) -> str:
    """使用本机已登录 Codex 的临时只读会话生成一条回复。"""

    entries = list(history)
    executable = find_codex_executable()
    if executable is None:
        raise AIConnectionError("没有找到 Codex，已切回离线回答。")
    working_root = Path(tempfile.gettempdir()) / "LiliCodexChat"
    working_root.mkdir(parents=True, exist_ok=True)
    prompt = _conversation_text(message, entries, local_context)
    selected_model = (
        _codex_model_override()
        if model_override is None
        else str(model_override).strip()[:120]
    )
    command = _codex_exec_command(executable, prompt, model=selected_model)
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = subprocess.CREATE_NO_WINDOW
    timeout = _codex_timeout_seconds()
    started_at = time.monotonic()

    def run_command(command_to_run: list[str]):
        return subprocess.run(
            command_to_run,
            cwd=working_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_cli_environment(executable),
            startupinfo=startupinfo,
            creationflags=creationflags,
            check=False,
        )

    try:
        completed = run_command(command)
    except (OSError, subprocess.TimeoutExpired) as exc:
        LOGGER.warning(
            "[AI Codex] exec did not return: elapsed=%.1fs timeout=%ss error=%s",
            time.monotonic() - started_at,
            timeout,
            exc,
        )
        raise AIConnectionError("Codex 暂时没有回应，已切回离线回答。") from exc

    stderr = " ".join((completed.stderr or "").split())
    # Some accounts do not have Luna enabled yet.  A single immediate retry
    # with the normal CLI-selected model keeps chat usable without adding a
    # second request for ordinary failures.
    if completed.returncode != 0 and selected_model and _looks_like_model_rejection(stderr):
        LOGGER.info("[AI Codex] model override rejected; retrying with CLI default")
        try:
            completed = run_command(_codex_exec_command(executable, prompt, model=""))
            stderr = " ".join((completed.stderr or "").split())
        except (OSError, subprocess.TimeoutExpired) as exc:
            LOGGER.warning(
                "[AI Codex] fallback exec did not return: elapsed=%.1fs error=%s",
                time.monotonic() - started_at,
                exc,
            )
            raise AIConnectionError("Codex 暂时没有回应，已切回离线回答。") from exc

    answer = _parse_codex_jsonl(completed.stdout)
    if completed.returncode != 0 or not answer:
        LOGGER.warning(
            "[AI Codex] exec failed: returncode=%s elapsed=%.1fs stderr=%s stdout_bytes=%s",
            completed.returncode,
            time.monotonic() - started_at,
            stderr[:800],
            len(completed.stdout or ""),
        )
        if completed.returncode == 0:
            raise AIConnectionError("Codex 返回了无法识别的内容，已切回离线回答。")
        raise AIConnectionError("Codex 尚未登录或连接失败，已切回离线回答。")
    return postprocess_ai_answer(answer, classify_intent(message, entries))


def ask_claude(message: str, history: Iterable[tuple[str, str]], local_context: str = "") -> str:
    """通过 stdin 调用本机 Claude Code 的一次性无工具会话。"""

    entries = list(history)
    executable = find_claude_executable()
    if executable is None:
        raise AIConnectionError("没有找到 Claude Code，已切回离线回答。")
    command = _cli_command(
        executable, "-p", "--output-format", "json",
        "--no-session-persistence", "--permission-mode", "plan",
        "--tools", "", "--max-turns", "1",
    )
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
            input=_conversation_text(message, entries, local_context),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=75,
            env=_cli_environment(executable),
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
    return postprocess_ai_answer(answer, classify_intent(message, entries))


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
    local_context: str = "",
) -> str:
    """调用 OpenAI 兼容 Chat Completions 接口并返回纯文本。"""

    if not token.strip():
        raise AIConnectionError("还没有保存 API 令牌。")
    if not model.strip():
        raise AIConnectionError("还没有填写模型名称。")
    entries = list(history)
    summary = next((content for role, content in entries if role == "summary"), "")
    system_content = f"{SYSTEM_PROMPT}\n\n{LIUMAO_PERSONA}\n\n{LOCAL_ACTION_PROMPT}"
    intent = classify_intent(message, entries)
    system_content += f"\n\n{intent_prompt_context(intent)}"
    worldview_context = worldview_prompt_context(message, entries)
    if worldview_context:
        system_content += f"\n\n{worldview_context}"
    knowledge_context = retrieve_prompt_context(message, entries)
    if knowledge_context and knowledge_context not in worldview_context:
        system_content += f"\n\n{knowledge_context}"
    if "本地歌曲作品卡" not in local_context:
        song_context = song_prompt_context(message, entries)
        if song_context:
            system_content += f"\n\n{song_context}"
    if summary:
        system_content += f"\n\n更早对话的长期摘要：\n{summary}"
    if local_context:
        system_content += f"\n\n本地程序真实状态与作品索引（不可猜测或改写）：\n{local_context}"
    messages = [{"role": "system", "content": system_content}]
    for role, content in [(r, c) for r, c in entries if r in {"user", "assistant"}][-60:]:
        if role in {"user", "assistant"}:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})
    payload: dict[str, object] = {
        "model": model.strip(),
        "messages": messages,
        "max_tokens": 700 if intent.answer_style == "detailed" else 320,
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
        with urllib.request.urlopen(request, timeout=45) as response:
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
    return postprocess_ai_answer(answer, intent)


def ask_openai_responses(
    message: str,
    history: Iterable[tuple[str, str]],
    token: str,
    base_url: str,
    model: str,
    local_context: str = "",
) -> str:
    """Call OpenAI's Responses API as an optional fast chat backend.

    It uses the same in-memory summary and recent 30-turn context as the
    local agents and never sends project files or desktop context.
    """

    entries = list(history)
    clean = base_url.strip().rstrip("/")
    parsed = urllib.parse.urlparse(clean)
    if parsed.scheme != "https" or not parsed.netloc:
        raise AIConnectionError("API 地址必须是有效的 HTTPS 地址。")
    if clean.endswith("/v1"):
        endpoint = f"{clean}/responses"
    elif clean.endswith("/responses"):
        endpoint = clean
    else:
        endpoint = f"{clean}/v1/responses"
    intent = classify_intent(message, entries)
    payload = {
        "model": model.strip(),
        "input": _conversation_text(message, entries, local_context),
        "max_output_tokens": 700 if intent.answer_style == "detailed" else 260,
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token.strip()}",
            "Content-Type": "application/json",
            "User-Agent": "LiliDesktopPet/0.21",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
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
        raise AIConnectionError("OpenAI 服务连接失败，已切回离线回答。") from exc
    answer = str(data.get("output_text") or "").strip()
    if not answer:
        fragments: list[str] = []
        for item in data.get("output") or []:
            for content in item.get("content") or []:
                if content.get("type") in {"output_text", "text"}:
                    fragments.append(str(content.get("text") or ""))
        answer = "".join(fragments).strip()
    if not answer:
        raise AIConnectionError("OpenAI 没有返回可识别的文字。")
    return postprocess_ai_answer(answer, intent)


class AIChatService:
    """根据当前设置选择后端；在线失败由窗口层决定如何离线回退。"""

    def __init__(self, credential_store: CredentialStore | None = None) -> None:
        self.credentials = credential_store or CredentialStore()
        self._codex_app_server: CodexAppServerClient | None = None
        self._codex_app_server_lock = threading.RLock()
        self._closing = False
        self._interrupted = False

    def reply(
        self,
        provider: str,
        message: str,
        history: Iterable[tuple[str, str]],
        base_url: str = "",
        model: str = "",
        local_context: str = "",
    ) -> str:
        if provider == "codex":
            return ask_codex(message, history, local_context)
        if provider == "claude":
            return ask_claude(message, history, local_context)
        if provider == "openai":
            default_url, default_model = provider_defaults(provider)
            return ask_openai_responses(
                message,
                history,
                self.credentials.get(provider),
                base_url or default_url,
                model or default_model,
                local_context,
            )
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
            local_context,
        )

    def stream_reply(
        self,
        provider: str,
        message: str,
        history: Iterable[tuple[str, str]],
        base_url: str = "",
        model: str = "",
        local_context: str = "",
        on_delta: Callable[[str], None] | None = None,
    ) -> str:
        """Use a persistent App Server for Codex and stream agent deltas."""

        if provider != "codex":
            answer = self.reply(provider, message, history, base_url, model, local_context)
            if on_delta is not None and answer:
                on_delta(answer)
            return answer

        entries = list(history)
        prompt = _conversation_turn_text(message, entries, local_context)
        selected_model, effort = _codex_turn_options(message)
        self._interrupted = False
        try:
            with self._codex_app_server_lock:
                client = self._get_codex_app_server()
            answer = client.stream_turn(
                prompt,
                model=selected_model,
                effort=effort,
                on_delta=on_delta,
                timeout=float(_codex_timeout_seconds()),
            )
        except (CodexAppServerError, OSError, ValueError) as exc:
            if self._interrupted:
                raise AIConnectionError("Codex turn 已停止。") from exc
            if self._closing:
                raise AIConnectionError("Codex 连接正在关闭。") from exc
            # Keep the user-facing chat usable when an older CLI has no
            # app-server command, the session is corrupt, or the server exits.
            # The fallback is the already existing isolated read-only exec path.
            LOGGER.warning("[AI Codex] app-server failed; falling back to exec: %s", exc)
            self._close_codex_app_server()
            # A model can be available in one Codex account/platform and
            # unavailable in another.  Let the normal CLI-selected model take
            # over instead of sending the same rejected Luna/Terra override a
            # second time.
            fallback_model = "" if selected_model and _looks_like_model_rejection(str(exc)) else None
            answer = ask_codex(
                message,
                entries,
                local_context,
                model_override=fallback_model,
            )
            if on_delta is not None and answer:
                # The final UI replaces the partial stream with this answer, so
                # emitting it as another delta would duplicate the fallback.
                pass
            return answer
        intent = classify_intent(message, entries)
        return postprocess_ai_answer(answer, intent)

    def _get_codex_app_server(self) -> CodexAppServerClient:
        if self._codex_app_server is not None and self._codex_app_server.is_running:
            return self._codex_app_server
        executable = find_codex_executable()
        if executable is None:
            raise CodexAppServerError("没有找到 Codex，已切回离线回答。")
        working_root = Path(tempfile.gettempdir()) / "LiliCodexChat"
        self._codex_app_server = CodexAppServerClient(
            _codex_app_server_command(executable),
            cwd=working_root,
            env=_cli_environment(executable),
            thread_id=_read_codex_thread_id(),
            on_thread_id=_write_codex_thread_id,
        )
        return self._codex_app_server

    def _close_codex_app_server(self) -> None:
        with self._codex_app_server_lock:
            client = self._codex_app_server
            self._codex_app_server = None
        if client is not None:
            client.close()

    def interrupt(self) -> bool:
        """Interrupt the active persistent Codex turn when the UI asks to stop."""

        with self._codex_app_server_lock:
            client = self._codex_app_server
        if client is None:
            return False
        self._interrupted = True
        if client.interrupt():
            return True
        self._interrupted = False
        return False

    def reset_conversation(self) -> None:
        """Start a new local Codex conversation without touching todo data."""

        self._interrupted = False
        self._closing = False
        self._close_codex_app_server()
        _clear_codex_thread_id()

    def close(self) -> None:
        """Close the persistent Codex child process during application shutdown."""

        self._closing = True
        self._close_codex_app_server()

