"""
鏈ā鍧楃粺涓€绠＄悊鍏瘺鐨?Agent 杩炴帴缂撳瓨銆佸紓姝?AI 璇锋眰涓庢湰鍦扮绾垮璇濋檷绾с€?
鑱岃矗鑼冨洿锛?- 鐢?checking銆乧onnected銆乨isconnected銆乪rror 鍥涙€佺紦瀛樺悇 AI Agent 鐨勮繛鎺ョ粨鏋滐紱
- 鍦ㄥ簲鐢ㄥ惎鍔ㄥ悗鍚庡彴妫€娴?Codex銆丆laude Code銆丏eepSeek銆並imi 涓庡吋瀹?API锛?- 浠呭湪鏄惧紡鍒锋柊鎴栦綆棰戦噸杩炴椂鎵ц瀹屾暣妫€娴嬶紝涓嶅湪姣忔潯鑱婂ぉ娑堟伅鍓嶉噸澶嶆帰娴嬶紱
- 鎶?AI 璇锋眰鏀惧叆 QThread锛屽け璐ャ€佸紓甯告垨瓒呮椂鍚庨潤榛樺洖鍒版湰鍦伴櫔浼村洖澶嶏紱
- 鏍规嵁鍏抽敭璇嶃€佸綋鍓嶆椂闂淬€佸伐浣滄椂闀垮拰瀹犵墿鐘舵€佺敓鎴愪笉渚濊禆缃戠粶鐨勫洖澶嶏紱
- 璇嗗埆绂荤嚎涓嬫棤娉曞彲闈犲畬鎴愮殑澶嶆潅闂锛屽苟璁╃晫闈㈡樉绀烘墜鍔ㄩ噸杩炲拰璁剧疆鍏ュ彛銆?
鑱婂ぉ鍐呭鍜岃繛鎺ョ姸鎬佸彧淇濆瓨鍦ㄥ綋鍓嶈繘绋嬪唴瀛樹腑锛屼笉鍐欏叆纾佺洏銆侫PI 浠ょ墝浠嶇敱
CredentialStore 鏀惧叆绯荤粺瀹夊叏鍑嵁搴擄紱鏈ā鍧椾笉浼氫富鍔ㄦ墦寮€浠讳綍璁剧疆绐楀彛銆?"""

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
from .liumao_worldview import story_response, worldview_response


class AgentConnectionState(str, Enum):
    """涓€涓?Agent 鍦ㄥ綋鍓嶈繘绋嬩腑鐨勮繛鎺ョ姸鎬併€?""

    CHECKING = "checking"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"


@dataclass(frozen=True)
class AgentStatus:
    """渚涜亰澶╅潰鏉垮拰璁剧疆椤佃鍙栫殑鍗曚釜 Agent 缂撳瓨蹇収銆?""

    provider: str
    state: AgentConnectionState
    detail: str
    checked_at: datetime | None = None


@dataclass(frozen=True)
class ManagedChatReply:
    """ChatManager 杩斿洖缁欑獥鍙ｇ殑缁熶竴鍥炲缁撴灉銆?""

    text: str
    state: PetState
    mode: str
    show_recovery_actions: bool = False


class AgentDetectionThread(QThread):
    """渚濇妫€娴嬩竴缁勬彁渚涙柟锛屽苟鎶婃瘡椤圭粨鏋滃彂鍥炰富绾跨▼缂撳瓨銆?""

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
        """鎶婄己澶?鏈櫥褰曞綊涓烘柇寮€锛屾妸缃戠粶鍜屾湭鐭ュ紓甯稿綊涓洪敊璇€?""

        disconnected_markers = (
            "鏈娴嬪埌",
            "娌℃湁鎵惧埌",
            "娌℃湁濉啓",
            "娌℃湁淇濆瓨",
            "灏氭湭鐧诲綍",
            "娌℃湁鐧诲綍",
            "浠ょ墝鏃犳晥",
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
                detail = "鍚庡彴妫€娴嬮亣鍒版剰澶栭棶棰橈紝绋嶅悗浼氳嚜鍔ㄩ噸璇曘€?
                state = AgentConnectionState.ERROR
            else:
                state = (
                    AgentConnectionState.DISCONNECTED
                    if "鏈娴嬪埌 Codex CLI" in detail
                    else AgentConnectionState.CONNECTED
                )
            self.provider_checked.emit(provider, state.value, detail)


class AgentManager(QObject):
    """鍚庡彴妫€娴嬨€佺紦瀛樺苟浣庨鍒锋柊鎵€鏈?Agent 鐨勭姸鎬併€?""

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
                "绾绾挎ā寮忛殢鏃跺彲鐢ㄣ€? if provider == "offline" else "姝ｅ湪鍚庡彴妫€娴嬧€?,
            )
            for provider in PROVIDER_PRESETS
        }
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setInterval(self.RECONNECT_INTERVAL_MS)
        self._reconnect_timer.timeout.connect(self.retry_inactive)
        self._reconnect_timer.start()

    @property
    def checking(self) -> bool:
        """杩斿洖褰撳墠鏄惁宸叉湁瀹屾暣妫€娴嬬嚎绋嬭繍琛屻€?""

        return self._thread is not None and self._thread.isRunning()

    def status(self, provider: str) -> AgentStatus:
        """璇诲彇缂撳瓨锛屼笉鎵ц浠讳綍鍛戒护鎴栫綉缁滆姹傘€?""

        return self._statuses.get(
            provider,
            AgentStatus(provider, AgentConnectionState.DISCONNECTED, "鏈煡鐨?AI 杩炴帴鏂瑰紡銆?),
        )

    def start_background_check(
        self,
        providers: Iterable[str] | None = None,
        *,
        force: bool = False,
    ) -> bool:
        """寮傛妫€娴嬫寚瀹氭彁渚涙柟锛涜繍琛屼腑涓嶄細涓鸿亰澶╅噸澶嶅惎鍔ㄥ畬鏁存娴嬨€?""

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
            self._set_status(provider, AgentConnectionState.CHECKING, "姝ｅ湪鍚庡彴妫€娴嬧€?, now)
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
        """鍝嶅簲鐢ㄦ埛鎸夐挳锛屽彧閲嶆煡褰撳墠閫夋嫨鐨?AI銆?""

        provider = self.settings.ai_provider
        if provider == "offline":
            return False
        return self.start_background_check((provider,), force=True)

    def retry_inactive(self) -> None:
        """浣庨閲嶈繛鏂紑鎴栧嚭閿欑殑鎻愪緵鏂癸紝涓嶅奖鍝嶅凡杩炴帴缂撳瓨銆?""

        candidates = [
            provider
            for provider, status in self._statuses.items()
            if provider != "offline"
            and status.state in {AgentConnectionState.DISCONNECTED, AgentConnectionState.ERROR}
        ]
        if candidates:
            self.start_background_check(candidates)

    def mark_runtime_success(self, provider: str) -> None:
        """AI 璋冪敤鎴愬姛鍚庝繚鎸?connected锛屼緵涓嬩竴鏉℃秷鎭户缁娇鐢ㄣ€?""

        label = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["offline"]).label
        self._set_status(provider, AgentConnectionState.CONNECTED, f"{label} 宸茶繛鎺ャ€?)

    def mark_runtime_error(self, provider: str, detail: str) -> None:
        """AI 璋冪敤澶辫触鍚庣紦瀛?error锛涘彧褰卞搷鍚庣画璺敱锛屼笉寮硅缃〉銆?""

        self._set_status(
            provider,
            AgentConnectionState.ERROR,
            detail or "AI 鏆傛椂涓嶅彲鐢紝宸茶嚜鍔ㄥ垏鍥炵绾块櫔浼淬€?,
        )

    def mark_disconnected(self, provider: str, detail: str) -> None:
        """淇濆瓨鏄庣‘鐨勭己澶辨垨鏈櫥褰曠姸鎬侊紝涓嶆妸涓€ф娴嬫枃妗堣鎶ヤ负宸茶繛鎺ャ€?""

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
        """閫€鍑烘椂鍋滄鑷姩閲嶈繛锛屽苟璇锋眰妫€娴嬬嚎绋嬪敖蹇粨鏉熴€?""

        self._reconnect_timer.stop()
        if self._thread is not None and self._thread.isRunning():
            self._thread.requestInterruption()


class OfflineDialogueManager:
    """鐢熸垚涓嶄緷璧?AI 鐨勫畬鏁撮櫔浼村璇濓紝骞惰瘑鍒鏉傜绾胯姹傘€?""

    COMPLEX_MARKERS = (
        "甯垜鍐?,
        "甯垜鍋?,
        "鍒嗘瀽",
        "鎬荤粨",
        "缈昏瘧",
        "鍐欎唬鐮?,
        "浠ｇ爜鎶ラ敊",
        "鎼滅储",
        "鏌ヤ竴涓?,
        "鑱旂綉",
        "鏂伴椈",
        "澶╂皵",
        "鑲＄エ",
        "娉曞緥",
        "璇婃柇",
        "鍖荤枟",
        "鏂规",
        "璁烘枃",
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

    def reply(
        self,
        message: str,
        history: Iterable[tuple[str, str]] = (),
    ) -> ManagedChatReply:
        """浼樺厛澶勭悊鏈湴涓婁笅鏂囷紝鍐嶅洖閫€鍒扮幇鏈夐櫔浼村叧閿瘝搴撱€?""

        text = " ".join(message.split())[:1200]
        if self._is_complex(text):
            return ManagedChatReply(
                "鐜板湪鏄绾挎ā寮忥紝绛?AI 鎭㈠鍚庡啀甯綘澶勭悊銆備綘涔熷彲浠ュ厛鍛婅瘔鎴戞渶鐫€鎬ョ殑閭ｄ竴灏忔锛屽叚姣涗細缁х画闄綘銆?,
                PetState.CURIOUS,
                "offline",
                True,
            )
        worldview = worldview_response(text, self.random, history)
        if worldview is not None:
            return ManagedChatReply(worldview.text, worldview.state, "offline")
        story = story_response(text, self.random, history)
        if story is not None:
            return ManagedChatReply(story.text, story.state, "offline")
        if any(marker in text for marker in ("鍑犵偣", "鐜板湪鏃堕棿", "褰撳墠鏃堕棿", "鏄熸湡鍑?, "鍑犲彿")):
            current = self.now()
            return ManagedChatReply(
                f"鐜板湪鏄?{current:%Y骞?m鏈?d鏃?%H:%M}銆傚厛鐪嬬湅杩欎竴鍒绘渶鍊煎緱瀹屾垚鐨勫皬浜嬪惂銆?,
                PetState.WAVE,
                "offline",
            )
        if any(marker in text for marker in ("宸ヤ綔澶氫箙", "涓撴敞澶氫箙", "璁℃椂澶氫箙", "浠婂ぉ宸ヤ綔")):
            return ManagedChatReply(self.work_status(), PetState.SIT, "offline")
        if any(marker in text for marker in ("浣犵殑鐘舵€?, "浣犳€庝箞鏍?, "绮惧姏", "楗遍搴?, "蹇冩儏鎬庝箞鏍?)):
            return ManagedChatReply(
                self.companion.status_text(self.focus_stars()),
                PetState.CURIOUS,
                "offline",
            )
        if any(marker in text for marker in ("闄垜鑱?, "鍦ㄥ共鍢?, "鏃犺亰", "璇寸偣浠€涔?)):
            choices = (
                "鍏瘺鍦ㄦ闈㈤櫔浣犲憖銆傝涓嶈璇磋浠婂ぉ鏈€璐瑰姴鐨勯偅涓€灏忔锛?,
                "鎴戝湪璁ょ湡寰呭懡銆傚紑蹇冪殑銆佺儲浜虹殑锛屾垨鑰呮病澶存病灏剧殑灏忎簨閮藉彲浠ヨ銆?,
                "宸村竷杈撅紒鐜板湪涓嶇敤缁勭粐寰楀緢瀹屾暣锛屾兂鍒颁粈涔堝氨璇翠粈涔堛€?,
            )
            return ManagedChatReply(self.random.choice(choices), PetState.CURIOUS, "offline")
        reply = self.companion.reply_to(text)
        return ManagedChatReply(reply.text, reply.state, "offline")

    @staticmethod
    def should_handle_locally(message: str) -> bool:
        """Return whether the offline fallback has a concise local match.

        This helper is retained for callers that explicitly request local
        handling.  ``ChatManager`` deliberately does not use it to bypass an
        available online model.
        """

        text = " ".join(str(message or "").split())[:80]
        if not text:
            return True
        markers = (
            "鐖变綘", "寰堢埍浣?, "鍠滄浣?, "鎯充綘", "鎶辨姳", "浜蹭翰",
            "璋㈣阿", "鎰熻阿", "浣犲ソ", "鍡?, "鏃╀笂濂?, "鏃╁畨",
            "鏅氬畨", "鍐嶈", "鎷滄嫓", "鏈夋病鏈変汉鍛婅瘔浣?, "鏈夋病鏈変汉鍛婅瘔鎴?,
        )
        return any(marker in text for marker in markers)

    def _is_complex(self, text: str) -> bool:
        """淇濆畧璇嗗埆闇€瑕佸閮ㄧ煡璇嗐€侀暱鎺ㄧ悊鎴栦唬鐮佹墽琛岀殑璇锋眰銆?""

        if any(marker in text for marker in self.COMPLEX_MARKERS):
            return True
        return len(text) > 220 and any(mark in text for mark in ("锛?, "?", "鎬庝箞", "涓轰粈涔?))


class AIReplyThread(QThread):
    """鍦ㄥ悗鍙版墽琛屼竴娆″彲鑳借緝鎱㈢殑 AI 璇锋眰銆?""

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
            self.failed.emit("AI 杩炴帴閬囧埌鎰忓闂锛屽凡鑷姩鍒囧洖绂荤嚎闄即銆?)
        else:
            self.succeeded.emit(answer)


class ChatManager(QObject):
    """缁熶竴鍐冲畾璧扮紦瀛樺凡杩炴帴鐨?AI锛岃繕鏄珛鍗宠繑鍥炴湰鍦扮绾垮洖澶嶃€?""

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
        self._pending_history: list[tuple[str, str]] = []
        self._pending_provider = "offline"

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def submit(self, message: str, history: list[tuple[str, str]]) -> bool:
        """Submit natural language to AI when available.

        Short messages are not treated as deterministic commands.  They may
        depend on context, may be ambiguous, and should be understood by the
        same language layer as longer messages.  Local worldview/story rules
        remain available inside ``OfflineDialogueManager`` as the fallback.
        """

        if self.busy:
            self.notice.emit("涓婁竴鍙ヨ瘽杩樺湪璺笂锛岀◢绛夋垜涓€涓嬨€?)
            return False
        # Do not intercept online chat with keyword, worldview, story, or
        # short-social shortcuts.  The model receives the recent conversation,
        # current work state, and only the small knowledge snippets selected by
        # the AI context builder.
        provider = self.settings.ai_provider
        status = self.agents.status(provider)
        if provider == "offline" or status.state != AgentConnectionState.CONNECTED:
            self.reply_ready.emit(self.offline.reply(message, history))
            return True
        self._pending_message = message
        self._pending_history = list(history)
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
        """鍙搷搴旂敤鎴蜂富鍔ㄧ偣鍑伙紝涓嶆墦寮€璁剧疆椤点€?""

        return self.agents.reconnect_selected()

    def _ai_succeeded(self, answer: str) -> None:
        self.agents.mark_runtime_success(self._pending_provider)
        state = PetState.SHY if any(word in answer for word in ("鎶辨姳", "鐖?, "闄綘")) else PetState.CURIOUS
        self.reply_ready.emit(ManagedChatReply(answer, state, "ai"))

    def _ai_failed(self, error: str) -> None:
        self.agents.mark_runtime_error(self._pending_provider, error)
        self.reply_ready.emit(self.offline.reply(self._pending_message, self._pending_history))

    def _thread_finished(self) -> None:
        thread = self._thread
        self._thread = None
        self.busy_changed.emit(False)
        if thread is not None:
            thread.deleteLater()

    def shutdown(self) -> None:
        """閫€鍑烘椂鍋滄閲嶈繛锛屽苟璁╂鍦ㄨ繍琛岀殑璇锋眰鑷劧缁撴潫銆?""

        self.agents.shutdown()
        if self._thread is not None and self._thread.isRunning():
            self._thread.requestInterruption()


def should_start_startup_detection() -> bool:
    """鑷姩娴嬭瘯浣跨敤婕旂ず绱犳潗鏃惰烦杩囩湡瀹?Agent/缃戠粶鎺㈡祴銆?""

    return os.environ.get("ONEPIC_USE_DEMO_ASSETS") != "1"

