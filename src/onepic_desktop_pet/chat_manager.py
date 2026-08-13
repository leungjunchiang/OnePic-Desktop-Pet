"""
本模块统一管理六毛的 Agent 连接缓存、异步 AI 请求与本地离线对话降级。

职责范围：
- 用 checking、connected、disconnected、error 四态缓存各 AI Agent 的连接结果；
- 在应用启动后后台检测 Codex、Claude Code、DeepSeek、Kimi 与兼容 API；
- 仅在显式刷新或低频重连时执行完整检测，不在每条聊天消息前重复探测；
- 把 AI 请求放入 QThread，失败、异常或超时后静默回到本地陪伴回复；
- 根据关键词、当前时间、工作时长和宠物状态生成不依赖网络的回复；
- 识别离线下无法可靠完成的复杂问题，并让界面显示手动重连和设置入口。

聊天内容和连接状态只保存在当前进程内存中，不写入磁盘。API 令牌仍由
CredentialStore 放入系统安全凭据库；本模块不会主动打开任何设置窗口。
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Iterable

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from .ai import (
    AIChatService,
    AIConnectionError,
    CredentialStore,
    PROVIDER_PRESETS,
    check_provider_connection,
)
from .behavior import PetState
from .companion import CompanionModel
from .config import PetSettings
from .liumao_worldview import worldview_response


class AgentConnectionState(str, Enum):
    """一个 Agent 在当前进程中的连接状态。"""

    CHECKING = "checking"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"


@dataclass(frozen=True)
class AgentStatus:
    """供聊天面板和设置页读取的单个 Agent 缓存快照。"""

    provider: str
    state: AgentConnectionState
    detail: str
    checked_at: datetime | None = None


@dataclass(frozen=True)
class ManagedChatReply:
    """ChatManager 返回给窗口的统一回复结果。"""

    text: str
    state: PetState
    mode: str
    show_recovery_actions: bool = False


class AgentDetectionThread(QThread):
    """依次检测一组提供方，并把每项结果发回主线程缓存。"""

    provider_checked = Signal(str, str, str)

    def __init__(
        self,
        providers: Iterable[str],
        credentials: CredentialStore,
        settings: PetSettings,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.providers = tuple(dict.fromkeys(providers))
        self.credentials = credentials
        self.settings = settings

    @staticmethod
    def _failure_state(message: str) -> AgentConnectionState:
        """把缺失/未登录归为断开，把网络和未知异常归为错误。"""

        disconnected_markers = (
            "未检测到",
            "没有找到",
            "没有填写",
            "没有保存",
            "尚未登录",
            "没有登录",
            "令牌无效",
        )
        if any(marker in message for marker in disconnected_markers):
            return AgentConnectionState.DISCONNECTED
        return AgentConnectionState.ERROR

    def run(self) -> None:
        for provider in self.providers:
            if self.isInterruptionRequested():
                return
            try:
                detail = check_provider_connection(
                    provider,
                    self.credentials,
                    self.settings.ai_base_url if provider == self.settings.ai_provider else "",
                )
            except AIConnectionError as exc:
                detail = str(exc)
                state = self._failure_state(detail)
            except Exception:
                detail = "后台检测遇到意外问题，稍后会自动重试。"
                state = AgentConnectionState.ERROR
            else:
                state = (
                    AgentConnectionState.DISCONNECTED
                    if "未检测到 Codex CLI" in detail
                    else AgentConnectionState.CONNECTED
                )
            self.provider_checked.emit(provider, state.value, detail)


class AgentManager(QObject):
    """后台检测、缓存并低频刷新所有 Agent 的状态。"""

    status_changed = Signal(str, str, str)
    detection_finished = Signal()

    RECONNECT_INTERVAL_MS = 5 * 60_000

    def __init__(
        self,
        settings: PetSettings,
        credentials: CredentialStore,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.credentials = credentials
        self._thread: AgentDetectionThread | None = None
        self._statuses: dict[str, AgentStatus] = {
            provider: AgentStatus(
                provider,
                AgentConnectionState.CONNECTED if provider == "offline" else AgentConnectionState.CHECKING,
                "纯离线模式随时可用。" if provider == "offline" else "正在后台检测…",
            )
            for provider in PROVIDER_PRESETS
        }
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setInterval(self.RECONNECT_INTERVAL_MS)
        self._reconnect_timer.timeout.connect(self.retry_inactive)
        self._reconnect_timer.start()

    @property
    def checking(self) -> bool:
        """返回当前是否已有完整检测线程运行。"""

        return self._thread is not None and self._thread.isRunning()

    def status(self, provider: str) -> AgentStatus:
        """读取缓存，不执行任何命令或网络请求。"""

        return self._statuses.get(
            provider,
            AgentStatus(provider, AgentConnectionState.DISCONNECTED, "未知的 AI 连接方式。"),
        )

    def start_background_check(
        self,
        providers: Iterable[str] | None = None,
        *,
        force: bool = False,
    ) -> bool:
        """异步检测指定提供方；运行中不会为聊天重复启动完整检测。"""

        if self.checking:
            return False
        selected = tuple(
            provider
            for provider in (providers or tuple(PROVIDER_PRESETS))
            if provider in PROVIDER_PRESETS and provider != "offline"
        )
        if not selected:
            return False
        now = datetime.now()
        for provider in selected:
            cached = self.status(provider)
            if not force and cached.state == AgentConnectionState.CONNECTED:
                continue
            self._set_status(provider, AgentConnectionState.CHECKING, "正在后台检测…", now)
        pending = tuple(
            provider for provider in selected if self.status(provider).state == AgentConnectionState.CHECKING
        )
        if not pending:
            return False
        self._thread = AgentDetectionThread(pending, self.credentials, self.settings, self)
        self._thread.provider_checked.connect(self._detection_result)
        self._thread.finished.connect(self._detection_thread_finished)
        self._thread.start()
        return True

    def reconnect_selected(self) -> bool:
        """响应用户按钮，只重查当前选择的 AI。"""

        provider = self.settings.ai_provider
        if provider == "offline":
            return False
        return self.start_background_check((provider,), force=True)

    def retry_inactive(self) -> None:
        """低频重连断开或出错的提供方，不影响已连接缓存。"""

        candidates = [
            provider
            for provider, status in self._statuses.items()
            if provider != "offline"
            and status.state in {AgentConnectionState.DISCONNECTED, AgentConnectionState.ERROR}
        ]
        if candidates:
            self.start_background_check(candidates)

    def mark_runtime_success(self, provider: str) -> None:
        """AI 调用成功后保持 connected，供下一条消息继续使用。"""

        label = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["offline"]).label
        self._set_status(provider, AgentConnectionState.CONNECTED, f"{label} 已连接。")

    def mark_runtime_error(self, provider: str, detail: str) -> None:
        """AI 调用失败后缓存 error；只影响后续路由，不弹设置页。"""

        self._set_status(
            provider,
            AgentConnectionState.ERROR,
            detail or "AI 暂时不可用，已自动切回离线陪伴。",
        )

    def mark_disconnected(self, provider: str, detail: str) -> None:
        """保存明确的缺失或未登录状态，不把中性检测文案误报为已连接。"""

        self._set_status(provider, AgentConnectionState.DISCONNECTED, detail)

    def _set_status(
        self,
        provider: str,
        state: AgentConnectionState,
        detail: str,
        checked_at: datetime | None = None,
    ) -> None:
        status = AgentStatus(provider, state, detail, checked_at or datetime.now())
        self._statuses[provider] = status
        self.status_changed.emit(provider, state.value, detail)

    def _detection_result(self, provider: str, state: str, detail: str) -> None:
        self._set_status(provider, AgentConnectionState(state), detail)

    def _detection_thread_finished(self) -> None:
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.deleteLater()
        self.detection_finished.emit()

    def shutdown(self) -> None:
        """退出时停止自动重连，并请求检测线程尽快结束。"""

        self._reconnect_timer.stop()
        if self._thread is not None and self._thread.isRunning():
            self._thread.requestInterruption()


class OfflineDialogueManager:
    """生成不依赖 AI 的完整陪伴对话，并识别复杂离线请求。"""

    COMPLEX_MARKERS = (
        "帮我写",
        "帮我做",
        "分析",
        "总结",
        "翻译",
        "写代码",
        "代码报错",
        "搜索",
        "查一下",
        "联网",
        "新闻",
        "天气",
        "股票",
        "法律",
        "诊断",
        "医疗",
        "方案",
        "论文",
    )

    def __init__(
        self,
        companion: CompanionModel,
        work_status: Callable[[], str],
        focus_stars: Callable[[], int] | None = None,
        now: Callable[[], datetime] | None = None,
        random_source: random.Random | None = None,
    ) -> None:
        self.companion = companion
        self.work_status = work_status
        self.focus_stars = focus_stars or (lambda: 0)
        self.now = now or datetime.now
        self.random = random_source or random.Random()

    def reply(self, message: str) -> ManagedChatReply:
        """优先处理本地上下文，再回退到现有陪伴关键词库。"""

        text = " ".join(message.split())[:1200]
        if self._is_complex(text):
            return ManagedChatReply(
                "现在是离线模式，等 AI 恢复后再帮你处理。你也可以先告诉我最着急的那一小步，六毛会继续陪你。",
                PetState.CURIOUS,
                "offline",
                True,
            )
        worldview = worldview_response(text, self.random)
        if worldview is not None:
            return ManagedChatReply(worldview.text, worldview.state, "offline")
        if any(marker in text for marker in ("几点", "现在时间", "当前时间", "星期几", "几号")):
            current = self.now()
            return ManagedChatReply(
                f"现在是 {current:%Y年%m月%d日 %H:%M}。先看看这一刻最值得完成的小事吧。",
                PetState.WAVE,
                "offline",
            )
        if any(marker in text for marker in ("工作多久", "专注多久", "计时多久", "今天工作")):
            return ManagedChatReply(self.work_status(), PetState.SIT, "offline")
        if any(marker in text for marker in ("你的状态", "你怎么样", "精力", "饱食度", "心情怎么样")):
            return ManagedChatReply(
                self.companion.status_text(self.focus_stars()),
                PetState.CURIOUS,
                "offline",
            )
        if any(marker in text for marker in ("陪我聊", "在干嘛", "无聊", "说点什么")):
            choices = (
                "六毛在桌面陪你呀。要不要说说今天最费劲的那一小段？",
                "我在认真待命。开心的、烦人的，或者没头没尾的小事都可以讲。",
                "巴布达！现在不用组织得很完整，想到什么就说什么。",
            )
            return ManagedChatReply(self.random.choice(choices), PetState.CURIOUS, "offline")
        reply = self.companion.reply_to(text)
        return ManagedChatReply(reply.text, reply.state, "offline")

    def _is_complex(self, text: str) -> bool:
        """保守识别需要外部知识、长推理或代码执行的请求。"""

        if any(marker in text for marker in self.COMPLEX_MARKERS):
            return True
        return len(text) > 220 and any(mark in text for mark in ("？", "?", "怎么", "为什么"))


class AIReplyThread(QThread):
    """在后台执行一次可能较慢的 AI 请求。"""

    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        service: AIChatService,
        provider: str,
        message: str,
        history: list[tuple[str, str]],
        base_url: str,
        model: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.provider = provider
        self.message = message
        self.history = history
        self.base_url = base_url
        self.model = model

    def run(self) -> None:
        try:
            answer = self.service.reply(
                self.provider,
                self.message,
                self.history,
                self.base_url,
                self.model,
            )
        except AIConnectionError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit("AI 连接遇到意外问题，已自动切回离线陪伴。")
        else:
            self.succeeded.emit(answer)


class ChatManager(QObject):
    """统一决定走缓存已连接的 AI，还是立即返回本地离线回复。"""

    reply_ready = Signal(object)
    busy_changed = Signal(bool)
    notice = Signal(str)

    def __init__(
        self,
        settings: PetSettings,
        service: AIChatService,
        agents: AgentManager,
        offline: OfflineDialogueManager,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.service = service
        self.agents = agents
        self.offline = offline
        self._thread: AIReplyThread | None = None
        self._pending_message = ""
        self._pending_provider = "offline"

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def submit(self, message: str, history: list[tuple[str, str]]) -> bool:
        """使用缓存状态路由消息；这里不会执行完整连接检测。"""

        if self.busy:
            self.notice.emit("上一句话还在路上，稍等我一下。")
            return False
        provider = self.settings.ai_provider
        status = self.agents.status(provider)
        if provider == "offline" or status.state != AgentConnectionState.CONNECTED:
            self.reply_ready.emit(self.offline.reply(message))
            return True
        self._pending_message = message
        self._pending_provider = provider
        self._thread = AIReplyThread(
            self.service,
            provider,
            message,
            history,
            self.settings.ai_base_url,
            self.settings.ai_model,
            self,
        )
        self._thread.succeeded.connect(self._ai_succeeded)
        self._thread.failed.connect(self._ai_failed)
        self._thread.finished.connect(self._thread_finished)
        self.busy_changed.emit(True)
        self._thread.start()
        return True

    def reconnect_now(self) -> bool:
        """只响应用户主动点击，不打开设置页。"""

        return self.agents.reconnect_selected()

    def _ai_succeeded(self, answer: str) -> None:
        self.agents.mark_runtime_success(self._pending_provider)
        state = PetState.SHY if any(word in answer for word in ("抱抱", "爱", "陪你")) else PetState.CURIOUS
        self.reply_ready.emit(ManagedChatReply(answer, state, "ai"))

    def _ai_failed(self, error: str) -> None:
        self.agents.mark_runtime_error(self._pending_provider, error)
        self.reply_ready.emit(self.offline.reply(self._pending_message))

    def _thread_finished(self) -> None:
        thread = self._thread
        self._thread = None
        self.busy_changed.emit(False)
        if thread is not None:
            thread.deleteLater()

    def shutdown(self) -> None:
        """退出时停止重连，并让正在运行的请求自然结束。"""

        self.agents.shutdown()
        if self._thread is not None and self._thread.isRunning():
            self._thread.requestInterruption()


def should_start_startup_detection() -> bool:
    """自动测试使用演示素材时跳过真实 Agent/网络探测。"""

    return os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1"

