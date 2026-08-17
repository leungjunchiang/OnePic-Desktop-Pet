"""统一管理本机播放器检测、内容解析、原生 Transport 控制和真实状态。

``MusicSongResolver`` 只负责搜索歌手、选择内容并让一首歌开始播放；
``MusicTransportController`` 只负责已经开始播放后的 Play/Pause/Next/Previous 和元数据。
Windows Transport 优先读取 Windows.Media.Control 的目标播放器 Sessions，短暂等待并重新获取
目标 Session 后才允许发送系统媒体键。macOS 的 Apple Music 与 Spotify 使用 Apple Events，
其他客户端仅在运行时使用媒体键回退。本模块不把安装或启动应用写成“已连接”，也不伪造
QQ 音乐、网易云或酷狗不存在的公开桌面 API。随机播放仍会按真实能力与本机成败历史自动
回退 Provider；歌曲一旦启动，之后的基础控制始终锁定该 Provider 的 Transport。
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping

from PySide6.QtCore import QObject, QThread, Signal

from .config import PetSettings
from .music import MUSIC_SERVICE_LABELS, find_music_client
from .music_playback import (
    BasicRandomArtistPlaybackManager,
    ExactMusicPlaybackManager,
    MusicPlaybackError,
    MusicPlaybackOutcome,
    MusicProviderAdapter,
    SongPlaybackResult,
    build_provider_adapters,
)


LOGGER = logging.getLogger(__name__)
SUPPORTED_PROVIDERS = ("qq", "netease", "kugou", "apple", "spotify")


PROVIDER_SESSION_MARKERS = {
    "qq": ("qqmusic", "tencent.qqmusic"),
    "netease": ("cloudmusic", "netease", "music.163"),
    "kugou": ("kugou", "kgmusic"),
    "apple": ("applemusic", "apple.music", "appleinc.applemusic"),
    "spotify": ("spotify",),
}

PROVIDER_PROCESS_NAMES = {
    "qq": ("QQMusic.exe", "QQMusic"),
    "netease": ("cloudmusic.exe", "NeteaseMusic", "网易云音乐"),
    "kugou": ("KuGou.exe", "KugouMusic", "酷狗音乐"),
    "apple": ("AppleMusic.exe", "Music"),
    "spotify": ("Spotify.exe", "Spotify"),
}


class MusicControlState(str, Enum):
    """播放器发现和控制能力，避免把安装检测误写成已连接。"""

    NOT_DETECTED = "not_detected"
    APP_DETECTED = "app_detected"
    APP_STARTED = "app_started"
    WAITING_SESSION = "waiting_session"
    CONTROL_READY = "control_ready"
    PLAYING = "playing"
    BASIC_ONLY = "basic_only"
    PERMISSION_REQUIRED = "permission_required"
    ERROR = "error"


@dataclass(frozen=True)
class TrackInfo:
    """播放器通过系统 Session 或 Apple Events 公开的当前媒体信息。"""

    title: str = ""
    artist: str = ""
    album: str = ""
    playback_status: str = "unknown"

    def display_text(self) -> str:
        if not self.title:
            return "当前播放器没有公开歌曲信息。"
        artist = f" · {self.artist}" if self.artist else ""
        return f"正在播放：{self.title}{artist}"


@dataclass(frozen=True)
class MusicProviderStatus:
    """一个平台当前的真实发现状态和播放控制等级。"""

    provider: str
    state: MusicControlState
    message: str
    application_detected: bool = False
    application_running: bool = False
    session_id: str = ""
    track: TrackInfo | None = None
    window_accessible: bool = False
    random_artist_capable: bool = False
    last_call_success: bool | None = None

    @property
    def can_control(self) -> bool:
        return self.state in {
            MusicControlState.CONTROL_READY,
            MusicControlState.PLAYING,
            MusicControlState.BASIC_ONLY,
        }

    @property
    def is_playing(self) -> bool:
        """仅在播放器公开明确的 playing 状态时返回真。"""

        return self.state is MusicControlState.PLAYING or bool(
            self.track and "playing" in self.track.playback_status.casefold()
        )


@dataclass(frozen=True)
class MusicState:
    """UI 唯一订阅的真实媒体状态，不把上一次按钮点击当成播放状态。"""

    player: str = ""
    playing: bool = False
    title: str = ""
    artist: str = ""
    updated_at: float = 0.0
    source: str = "platform-media-session"

    @classmethod
    def from_status(cls, status: MusicProviderStatus, *, source: str = "platform-media-session") -> "MusicState":
        track = status.track or TrackInfo()
        return cls(
            player=MUSIC_SERVICE_LABELS.get(status.provider, status.provider),
            playing=status.is_playing,
            title=track.title,
            artist=track.artist,
            updated_at=time.time(),
            source=source,
        )


@dataclass(frozen=True)
class MusicControlResult:
    """一次基础播放控制的结果，明确说明是否使用了媒体键回退。"""

    success: bool
    provider: str
    action: str
    message: str
    status: MusicProviderStatus
    used_fallback: bool = False


@dataclass(frozen=True)
class MusicProviderScore:
    """自动选择时一个 Provider 的可解释评分。"""

    provider: str
    score: int
    reasons: tuple[str, ...]
    status: MusicProviderStatus


@dataclass(frozen=True)
class ManagedMusicProvider:
    """把独立 Adapter 纳入统一的检测、启动、随机播放与曲目信息接口。"""

    manager: "MusicProviderManager"
    provider: str

    def detect(self) -> MusicProviderStatus:
        return self.manager.detect(self.provider)

    def is_running(self) -> bool:
        return self.manager.is_running(self.provider)

    def can_control(self) -> bool:
        return self.manager.can_control(self.provider)

    def launch_or_activate(self) -> bool:
        return self.manager.launch_or_activate(self.provider)

    def play_random_artist(self, artist: str) -> SongPlaybackResult:
        return self.manager.play_song(self.provider, "", artist, random_artist=True)

    def read_current_track(self) -> TrackInfo | None:
        return self.manager.current_track(self.provider)


@dataclass(frozen=True)
class WindowsSessionSnapshot:
    """GSMTC Session 的最小快照。"""

    source_id: str
    track: TrackInfo
    controls: frozenset[str]


class WindowsGSMTCBridge:
    """用 PyWinRT 调用 Windows 全局媒体 Session API。"""

    _METHODS = {
        "play": "try_play_async",
        "pause": "try_pause_async",
        "toggle": "try_toggle_play_pause_async",
        "next": "try_skip_next_async",
        "previous": "try_skip_previous_async",
    }

    def __init__(self, timeout_seconds: float = 4.0) -> None:
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def available() -> bool:
        try:
            from winrt.windows.media.control import (  # noqa: F401
                GlobalSystemMediaTransportControlsSessionManager,
            )
        except (ImportError, OSError):
            return False
        return True

    def sessions(self) -> tuple[WindowsSessionSnapshot, ...]:
        return tuple(self._run(self._sessions_async()))

    def control(self, source_id: str, action: str) -> bool:
        return bool(self._run(self._control_async(source_id, action)))

    def _run(self, coroutine):
        async def bounded():
            return await asyncio.wait_for(coroutine, timeout=self.timeout_seconds)

        return asyncio.run(bounded())

    @staticmethod
    async def _manager():
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager,
        )

        return await GlobalSystemMediaTransportControlsSessionManager.request_async()

    async def _sessions_async(self) -> list[WindowsSessionSnapshot]:
        manager = await self._manager()
        snapshots: list[WindowsSessionSnapshot] = []
        for session in manager.get_sessions():
            source_id = str(session.source_app_user_model_id or "")
            try:
                properties = await session.try_get_media_properties_async()
                playback = session.get_playback_info()
                controls = playback.controls
                enabled = frozenset(
                    action
                    for action, attribute in (
                        ("play", "is_play_enabled"),
                        ("pause", "is_pause_enabled"),
                        ("toggle", "is_play_pause_toggle_enabled"),
                        ("next", "is_next_enabled"),
                        ("previous", "is_previous_enabled"),
                    )
                    if bool(getattr(controls, attribute, False))
                )
                status_value = getattr(playback, "playback_status", None)
                status_name = getattr(status_value, "name", str(status_value or "unknown"))
                track = TrackInfo(
                    title=str(getattr(properties, "title", "") or ""),
                    artist=str(getattr(properties, "artist", "") or ""),
                    album=str(getattr(properties, "album_title", "") or ""),
                    playback_status=status_name.casefold(),
                )
            except Exception:
                enabled = frozenset()
                track = TrackInfo()
            snapshots.append(WindowsSessionSnapshot(source_id, track, enabled))
        return snapshots

    async def _control_async(self, source_id: str, action: str) -> bool:
        manager = await self._manager()
        method_name = self._METHODS.get(action)
        if method_name is None:
            return False
        for session in manager.get_sessions():
            if str(session.source_app_user_model_id or "") != source_id:
                continue
            method = getattr(session, method_name, None)
            return bool(await method()) if method is not None else False
        return False


class MusicTransportController:
    """只控制已经运行的播放器，不参与歌曲搜索或结果点击。

    Windows 路径严格按“目标 GSMTC Session → 短暂重取 Session → 系统媒体键”执行。
    如果应用尚未运行，会先启动并等待；媒体键只会在确认目标应用已经运行后使用。
    所有调用都由 :class:`MusicCommandThread` 执行，因此等待不会阻塞宠物界面。
    """

    def __init__(
        self,
        manager: "MusicProviderManager",
        *,
        wait_timeout_seconds: float = 1.8,
        poll_interval_seconds: float = 0.2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.manager = manager
        self.wait_timeout_seconds = max(0.0, wait_timeout_seconds)
        self.poll_interval_seconds = max(0.01, poll_interval_seconds)
        self.sleep = sleep

    def control(self, provider: str, action: str) -> MusicControlResult:
        """执行基础媒体命令；此入口永远不会调用选歌 Adapter。"""

        if self.manager.platform_name == "win32":
            return self._control_windows(provider, action)
        if self.manager.platform_name == "darwin":
            return self._control_macos(provider, action)
        status = self.manager.inspect(provider)
        return MusicControlResult(
            False,
            provider,
            action,
            "当前系统暂不支持本机音乐控制。",
            status,
        )

    def observe_after_selection(self, provider: str) -> MusicProviderStatus:
        """选歌动作结束后接管 Transport，尽可能等待 Session 和播放元数据。"""

        if self.manager.platform_name == "win32":
            status = self._wait_for_windows_session(provider)
        elif self.manager.platform_name == "darwin":
            status = self._wait_for_macos_running(provider, probe_control=True)
        else:
            status = self.manager.inspect(provider, probe_control=True)
        self.manager._cache[provider] = status
        return status

    def _control_windows(self, provider: str, action: str) -> MusicControlResult:
        status = self.manager._decorate_status(self.manager._inspect_windows(provider))
        launched = False
        if not status.application_running and status.application_detected:
            if self.manager.launch_or_activate(provider):
                launched = True
                status = self._wait_for_windows_session(provider)
            else:
                self.manager._cache[provider] = status
                return MusicControlResult(False, provider, action, status.message, status)
        elif status.application_running and not status.session_id:
            status = self._wait_for_windows_session(provider)

        if status.session_id and status.state in {
            MusicControlState.CONTROL_READY,
            MusicControlState.PLAYING,
        }:
            try:
                success = self.manager.windows_bridge.control(status.session_id, action)
            except Exception:
                success = False
            if success:
                refreshed = self.manager._decorate_status(
                    self.manager._inspect_windows(provider)
                )
                self.manager._cache[provider] = refreshed
                return MusicControlResult(
                    True,
                    provider,
                    action,
                    self.manager._action_message(action, refreshed),
                    refreshed,
                )

        # 系统媒体键无法指定接收者，只能在目标应用确实运行后作为最后回退。
        if status.application_running and self.manager.media_key_sender(action):
            fallback = self.manager._decorate_status(
                MusicProviderStatus(
                    provider,
                    MusicControlState.BASIC_ONLY,
                    f"{MUSIC_SERVICE_LABELS[provider]}已运行，但没有可控制的系统媒体 Session；"
                    "已使用基础媒体键，仅支持基础控制。",
                    status.application_detected,
                    True,
                    track=status.track,
                )
            )
            self.manager._cache[provider] = fallback
            return MusicControlResult(
                True,
                provider,
                action,
                self.manager._action_message(action, fallback),
                fallback,
                True,
            )

        if launched and not status.application_running and not status.session_id:
            status = replace(
                status,
                state=MusicControlState.APP_STARTED,
                message=(
                    f"{MUSIC_SERVICE_LABELS[provider]}已启动 · 正在等待应用初始化和播放控制。"
                ),
            )
        elif status.application_running and not status.session_id:
            status = replace(
                status,
                state=MusicControlState.WAITING_SESSION,
                message=(
                    f"{MUSIC_SERVICE_LABELS[provider]}已启动，但尚未建立系统媒体控制。"
                    "请先在播放器中播放任意歌曲一次。"
                ),
            )
        self.manager._cache[provider] = status
        return MusicControlResult(False, provider, action, status.message, status)

    def _wait_for_windows_session(self, provider: str) -> MusicProviderStatus:
        deadline = time.monotonic() + self.wait_timeout_seconds
        latest = self.manager._decorate_status(self.manager._inspect_windows(provider))
        while not latest.session_id and time.monotonic() < deadline:
            self.sleep(min(self.poll_interval_seconds, max(0.0, deadline - time.monotonic())))
            latest = self.manager._decorate_status(self.manager._inspect_windows(provider))
        if latest.session_id:
            return latest
        if latest.application_running:
            return replace(
                latest,
                state=MusicControlState.WAITING_SESSION,
                message=(
                    f"{MUSIC_SERVICE_LABELS[provider]}已启动 · 正在等待播放控制。"
                    "若一直没有响应，请先在播放器中播放任意歌曲一次。"
                ),
            )
        return latest

    def _control_macos(self, provider: str, action: str) -> MusicControlResult:
        status = self.manager._decorate_status(
            self.manager._inspect_macos(provider, probe_control=False)
        )
        if not status.application_running:
            if status.application_detected and self.manager.launch_or_activate(provider):
                status = self._wait_for_macos_running(provider, probe_control=False)
            if not status.application_running:
                if status.application_detected:
                    status = replace(
                        status,
                        state=MusicControlState.APP_STARTED,
                        message=(
                            f"{MUSIC_SERVICE_LABELS[provider]}已启动 · 正在等待应用初始化和播放控制。"
                        ),
                    )
                self.manager._cache[provider] = status
                return MusicControlResult(False, provider, action, status.message, status)
        if provider in {"apple", "spotify"}:
            result_type, _output = self.manager._run_macos_script(provider, action)
            if result_type == "ok":
                refreshed = self.manager._decorate_status(
                    self.manager._inspect_macos(provider, probe_control=True)
                )
                self.manager._cache[provider] = refreshed
                return MusicControlResult(
                    True,
                    provider,
                    action,
                    self.manager._action_message(action, refreshed),
                    refreshed,
                )
            if result_type == "permission":
                permission = self.manager._mac_permission_status(
                    provider, status.application_detected
                )
                self.manager._cache[provider] = permission
                return MusicControlResult(
                    False, provider, action, permission.message, permission
                )
        if self.manager.media_key_sender(action):
            fallback = self.manager._decorate_status(
                MusicProviderStatus(
                    provider,
                    MusicControlState.BASIC_ONLY,
                    f"{MUSIC_SERVICE_LABELS[provider]}仅支持基础媒体键控制。",
                    status.application_detected,
                    True,
                )
            )
            self.manager._cache[provider] = fallback
            return MusicControlResult(
                True,
                provider,
                action,
                self.manager._action_message(action, fallback),
                fallback,
                True,
            )
        error = MusicProviderStatus(
            provider,
            MusicControlState.ERROR,
            f"未能控制{MUSIC_SERVICE_LABELS[provider]}，请检查播放器是否正在运行。",
            status.application_detected,
            status.application_running,
        )
        self.manager._cache[provider] = error
        return MusicControlResult(False, provider, action, error.message, error)

    def _wait_for_macos_running(
        self,
        provider: str,
        *,
        probe_control: bool,
    ) -> MusicProviderStatus:
        deadline = time.monotonic() + self.wait_timeout_seconds
        latest = self.manager._decorate_status(
            self.manager._inspect_macos(provider, probe_control=probe_control)
        )
        while not latest.application_running and time.monotonic() < deadline:
            self.sleep(min(self.poll_interval_seconds, max(0.0, deadline - time.monotonic())))
            latest = self.manager._decorate_status(
                self.manager._inspect_macos(provider, probe_control=probe_control)
            )
        return latest


class MusicSongResolver:
    """只决定播放什么，并把成功启动的 Provider 交给 Transport 层。"""

    def __init__(self, manager: "MusicProviderManager") -> None:
        self.manager = manager

    def resolve(
        self,
        provider: str,
        title: str,
        artist: str,
        *,
        random_artist: bool = False,
    ) -> SongPlaybackResult:
        normalized = self.manager._normalize(provider)
        if random_artist:
            if normalized == "auto":
                return self.resolve_random_artist_auto(artist)
            result = self.manager.random_playback_manager.play_random_artist(
                normalized, artist
            )
            return self._handoff(result)
        if normalized == "auto":
            ranked = self.manager.ranked_providers()
            if not ranked:
                return SongPlaybackResult(
                    False,
                    "auto",
                    title,
                    artist,
                    "暂时没能找到可以播放的音乐软件，点击重试。",
                    MusicPlaybackError.SEARCH_FAILED,
                )
            normalized = ranked[0].provider
        return self._handoff(
            self.manager.playback_manager.play_song(normalized, title, artist)
        )

    def resolve_random_artist_auto(self, artist: str) -> SongPlaybackResult:
        """按评分逐个调用选歌 Adapter；单个 Provider 失败不会中断总流程。"""

        ranked = self.manager.ranked_providers(probe_control=True)
        attempted: list[str] = []
        failure_details: list[str] = []
        last_result: SongPlaybackResult | None = None
        for candidate in ranked:
            provider = candidate.provider
            attempted.append(provider)
            reason = "+".join(candidate.reasons) or "fallback"
            try:
                result = self.manager.random_playback_manager.play_random_artist(
                    provider, artist
                )
            except Exception as exc:
                LOGGER.exception(
                    "music_auto_select provider=%s score=%s reason=%s result=failed error=%r",
                    provider,
                    candidate.score,
                    reason,
                    exc,
                )
                result = SongPlaybackResult(
                    False,
                    provider,
                    "",
                    artist,
                    "播放器调用失败。",
                    MusicPlaybackError.PLAY_ACTION_FAILED,
                )
            if result.success:
                result = self._handoff(result, record_history=False)
                self.manager._record_provider_result(provider, True)
                label = MUSIC_SERVICE_LABELS[provider]
                outcome = result.outcome or MusicPlaybackOutcome.PLAYBACK_STARTED_UNVERIFIED
                message = (
                    f"已自动选择{label}，正在播放{artist}的歌曲。"
                    if outcome is MusicPlaybackOutcome.PLAYBACK_CONFIRMED
                    else f"已自动选择{label}并发起播放；当前歌曲信息暂时无法读取。"
                )
                LOGGER.debug(
                    "music_auto_select provider=%s score=%s reason=%s result=success outcome=%s",
                    provider,
                    candidate.score,
                    reason,
                    outcome.value,
                )
                return replace(
                    result,
                    message=message,
                    outcome=outcome,
                    attempted_providers=tuple(attempted),
                )
            error = result.error_code.value if result.error_code else "UNKNOWN"
            failure_details.append(f"{MUSIC_SERVICE_LABELS[provider]}：{error}")
            self.manager._record_provider_result(provider, False, error)
            LOGGER.debug(
                "music_auto_select provider=%s score=%s reason=%s result=failed error=%s",
                provider,
                candidate.score,
                reason,
                error,
            )
            last_result = result
        running = [
            provider
            for provider in attempted
            if self.manager.cached_status(provider).application_running
        ]
        failure_message = "暂时没能找到可以播放的音乐软件，点击重试。"
        if running:
            labels = "、".join(MUSIC_SERVICE_LABELS[provider] for provider in running)
            failure_message = (
                f"{labels}已启动，但自动选歌失败。"
                "如果你已在播放器中手动开始播放，播放/暂停、上一首和下一首仍可使用。"
            )
        if failure_details:
            failure_message += "\n失败阶段：" + "；".join(failure_details)
        return SongPlaybackResult(
            False,
            last_result.provider if last_result else "auto",
            "",
            artist,
            failure_message,
            last_result.error_code if last_result else MusicPlaybackError.SEARCH_FAILED,
            attempted_providers=tuple(attempted),
        )

    def _handoff(
        self,
        result: SongPlaybackResult,
        *,
        record_history: bool = True,
    ) -> SongPlaybackResult:
        if not result.success or result.provider not in SUPPORTED_PROVIDERS:
            return result
        self.manager.active_provider = result.provider
        status = self.manager.transport_controller.observe_after_selection(result.provider)
        if record_history:
            self.manager._record_provider_result(result.provider, True)
        track = status.track
        if track and result.requested_artist.casefold() in track.artist.casefold():
            return replace(
                result,
                current_title=track.title,
                current_artist=track.artist,
                outcome=MusicPlaybackOutcome.PLAYBACK_CONFIRMED,
            )
        return result


class MusicProviderManager:
    """按平台选择原生 adapter，并缓存每个平台的真实控制状态。"""

    def __init__(
        self,
        settings: PetSettings,
        *,
        platform_name: str | None = None,
        windows_bridge: WindowsGSMTCBridge | None = None,
        client_finder: Callable[[str, str], Path | None] = find_music_client,
        process_checker: Callable[[str], bool] | None = None,
        command_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        media_key_sender: Callable[[str], bool] | None = None,
        window_checker: Callable[[str], bool] | None = None,
        client_launcher: Callable[[str, Path], bool] | None = None,
        playback_adapters: Mapping[str, MusicProviderAdapter] | None = None,
        playback_verify_timeout: float = 5.0,
        playback_poll_interval: float = 0.55,
        playback_sleep: Callable[[float], None] | None = None,
        transport_wait_timeout: float = 1.8,
        transport_poll_interval: float = 0.2,
    ) -> None:
        self.settings = settings
        self.platform_name = platform_name or sys.platform
        self.windows_bridge = windows_bridge or WindowsGSMTCBridge()
        self.client_finder = client_finder
        self.process_checker = process_checker or self._default_process_checker
        self.command_runner = command_runner
        self.media_key_sender = media_key_sender or self._default_media_key_sender
        self.window_checker = window_checker or self._default_window_checker
        self.client_launcher = client_launcher
        self._cache: dict[str, MusicProviderStatus] = {}
        adapters = playback_adapters or build_provider_adapters(
            settings,
            platform_name=self.platform_name,
            client_finder=client_finder,
            command_runner=command_runner,
        )
        self._playback_adapters = dict(adapters)
        self.active_provider: str | None = None
        self.playback_manager = ExactMusicPlaybackManager(
            adapters,
            self.current_track,
            verify_timeout_seconds=playback_verify_timeout,
            poll_interval_seconds=playback_poll_interval,
            sleep=playback_sleep or time.sleep,
        )
        self.random_playback_manager = BasicRandomArtistPlaybackManager(
            adapters,
            self.current_track,
            verify_timeout_seconds=playback_verify_timeout,
            poll_interval_seconds=playback_poll_interval,
            sleep=playback_sleep or time.sleep,
        )
        self.transport_controller = MusicTransportController(
            self,
            wait_timeout_seconds=transport_wait_timeout,
            poll_interval_seconds=transport_poll_interval,
            sleep=playback_sleep or time.sleep,
        )
        self.song_resolver = MusicSongResolver(self)

    def cached_status(self, provider: str) -> MusicProviderStatus:
        """返回缓存；没有缓存时只做安装检测，不声称已经建立控制。"""

        normalized = self._normalize(provider)
        if normalized == "auto":
            current = self.active_provider
            if current:
                return self.cached_status(current)
            return MusicProviderStatus(
                "auto",
                MusicControlState.NOT_DETECTED,
                "音乐播放器将自动选择；尚未开始播放。",
            )
        cached = self._cache.get(normalized)
        if cached is not None:
            return cached
        detected = self._client(normalized) is not None
        label = MUSIC_SERVICE_LABELS[normalized]
        status = MusicProviderStatus(
            normalized,
            MusicControlState.APP_DETECTED if detected else MusicControlState.NOT_DETECTED,
            f"已检测到{label}应用；尚未验证播放控制。"
            if detected
            else f"未检测到{label}应用。",
            application_detected=detected,
        )
        status = self._decorate_status(status)
        self._cache[normalized] = status
        return status

    def inspect(self, provider: str, *, probe_control: bool = False) -> MusicProviderStatus:
        """刷新真实状态；macOS 只在用户主动刷新时探测 Apple Events 权限。"""

        normalized = self._normalize(provider)
        if normalized == "auto":
            return self.cached_status("auto")
        if self.platform_name == "win32":
            status = self._inspect_windows(normalized)
        elif self.platform_name == "darwin":
            status = self._inspect_macos(normalized, probe_control=probe_control)
        else:
            status = self.cached_status(normalized)
        status = self._decorate_status(status)
        self._cache[normalized] = status
        return status

    def detect(self, provider: str) -> MusicProviderStatus:
        """供统一 Provider API 使用的安装、运行、窗口与控制能力检测。"""

        return self.inspect(provider, probe_control=False)

    def provider(self, provider: str) -> ManagedMusicProvider:
        """返回带统一能力方法的 Provider facade，各平台播放细节仍由独立 Adapter 实现。"""

        normalized = self._normalize(provider)
        if normalized == "auto":
            raise ValueError("auto 不是单一 Provider")
        return ManagedMusicProvider(self, normalized)

    def detect_all(self, *, probe_control: bool = False) -> dict[str, MusicProviderStatus]:
        """一次刷新全部 Provider；Windows 复用同一份 GSMTC Session 快照。"""

        statuses: dict[str, MusicProviderStatus] = {}
        if self.platform_name == "win32":
            try:
                sessions = self.windows_bridge.sessions()
                session_error: Exception | None = None
            except Exception as exc:
                sessions = ()
                session_error = exc
            for provider in SUPPORTED_PROVIDERS:
                status = self._inspect_windows(
                    provider,
                    sessions=sessions,
                    session_error=session_error,
                )
                status = self._decorate_status(status)
                self._cache[provider] = status
                statuses[provider] = status
            return statuses
        for provider in SUPPORTED_PROVIDERS:
            statuses[provider] = self.inspect(provider, probe_control=probe_control)
        return statuses

    def ranked_providers(self, *, probe_control: bool = True) -> tuple[MusicProviderScore, ...]:
        """按真实可用性和本机成败历史排序，只返回当前有可能调用的播放器。"""

        statuses = self.detect_all(probe_control=probe_control)
        ranked = [
            self._score_provider(status)
            for status in statuses.values()
            if status.application_detected or status.application_running or bool(status.session_id)
        ]
        ranked.sort(key=lambda item: (-item.score, SUPPORTED_PROVIDERS.index(item.provider)))
        for item in ranked:
            LOGGER.debug(
                "music_auto_select provider=%s score=%s reason=%s",
                item.provider,
                item.score,
                "+".join(item.reasons) or "fallback",
            )
        return tuple(ranked)

    def auto_status_text(self) -> str:
        """返回设置页可直接显示的自动选择、当前播放器和检测摘要。"""

        statuses = {provider: self.cached_status(provider) for provider in SUPPORTED_PROVIDERS}
        detected = [
            MUSIC_SERVICE_LABELS[provider]
            for provider, status in statuses.items()
            if status.application_detected or status.application_running or status.session_id
        ]
        current = MUSIC_SERVICE_LABELS.get(self.active_provider or "", "尚未开始播放")
        detected_text = "、".join(detected) if detected else "暂未检测到播放器"
        lines = [
            "音乐播放器：自动选择",
            f"当前使用：{current}",
            f"已检测：{detected_text}",
        ]
        if self.active_provider:
            lines.append(self.provider_status_text(self.active_provider))
        else:
            lines.append("基础播放控制：尚未建立\n自动选歌：按 Provider 独立适配（实验性）")
        return "\n".join(lines)

    def provider_status_text(self, provider: str) -> str:
        """分开描述应用、Transport 与自动选歌能力，供设置页直接展示。"""

        status = self.cached_status(provider)
        label = MUSIC_SERVICE_LABELS.get(status.provider, status.provider)
        if not status.application_detected and not status.application_running:
            app_text = "未安装"
        elif status.application_running:
            app_text = "正在运行"
        else:
            app_text = "已安装"
        if status.state is MusicControlState.PLAYING:
            transport_text = "已建立播放控制 · 正在播放"
        elif status.state is MusicControlState.CONTROL_READY:
            transport_text = "已建立播放控制 · 可播放"
        elif status.state in {MusicControlState.APP_STARTED, MusicControlState.WAITING_SESSION}:
            transport_text = "已启动 · 正在等待播放控制"
        elif status.state is MusicControlState.BASIC_ONLY:
            transport_text = "仅支持基础媒体键"
        elif status.state is MusicControlState.PERMISSION_REQUIRED:
            transport_text = "需要 macOS Automation 权限"
        elif status.state is MusicControlState.ERROR:
            transport_text = "控制发生错误"
        else:
            transport_text = "尚未建立播放控制"
        selection_text = (
            "实验性 · 由该播放器独立 Adapter 完成"
            if status.random_artist_capable
            else "当前不可用"
        )
        lines = [
            label,
            f"应用状态：{app_text}",
            f"基础播放控制：{transport_text}",
            f"自动选歌：{selection_text}",
        ]
        if status.track and status.track.title:
            artist = f" - {status.track.artist}" if status.track.artist else ""
            lines.append(f"当前歌曲：{status.track.title}{artist}")
        return "\n".join(lines)

    def is_running(self, provider: str) -> bool:
        return self.inspect(provider).application_running

    def can_control(self, provider: str) -> bool:
        return self.inspect(provider, probe_control=True).can_control

    def read_current_track(self, provider: str = "auto") -> TrackInfo | None:
        return self.current_track(provider)

    def launch_or_activate(self, provider: str) -> bool:
        """显式播放流程可调用的客户端启动入口；单独启动不代表播放成功。"""

        normalized = self._normalize(provider)
        if normalized == "auto":
            ranked = self.ranked_providers(probe_control=False)
            normalized = ranked[0].provider if ranked else ""
        if not normalized:
            return False
        client = self._client(normalized)
        if client is None:
            return False
        try:
            if self.client_launcher is not None:
                launched = bool(self.client_launcher(normalized, client))
                if not launched:
                    return False
            elif self.platform_name == "darwin":
                subprocess.Popen(
                    ["open", "-a", str(client)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    [str(client)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except OSError:
            return False
        label = MUSIC_SERVICE_LABELS[normalized]
        self._cache[normalized] = self._decorate_status(
            MusicProviderStatus(
                normalized,
                MusicControlState.APP_STARTED,
                f"{label}已启动 · 正在等待播放控制。",
                application_detected=True,
            )
        )
        return True

    def control(self, provider: str, action: str) -> MusicControlResult:
        """执行基础播放命令；搜索歌曲不经过此入口。"""

        normalized = self._normalize(provider)
        if normalized == "auto":
            normalized = self._control_provider()
            if not normalized:
                status = MusicProviderStatus(
                    "auto",
                    MusicControlState.NOT_DETECTED,
                    "还没有正在播放的音乐软件，请先点击“随机听一首陈楚生”。",
                )
                return MusicControlResult(False, "auto", action, status.message, status)
        if action == "status":
            status = self.inspect(normalized, probe_control=True)
            message = status.track.display_text() if status.track else status.message
            return MusicControlResult(status.can_control, normalized, action, message, status)
        if action not in {"play", "pause", "toggle", "next", "previous"}:
            status = self.inspect(normalized)
            return MusicControlResult(False, normalized, action, "不支持的音乐控制命令。", status)
        return self.transport_controller.control(normalized, action)

    def play_song(
        self,
        provider: str,
        title: str,
        artist: str,
        *,
        random_artist: bool = False,
    ) -> SongPlaybackResult:
        """把内容选择交给 Song Resolver；基础控制不会经过此入口。"""

        return self.song_resolver.resolve(
            provider,
            title,
            artist,
            random_artist=random_artist,
        )

    def play_random_artist_auto(self, artist: str) -> SongPlaybackResult:
        """兼容公开 API；实际自动回退由 Song Resolver 执行。"""

        return self.song_resolver.resolve_random_artist_auto(artist)

    def current_track(self, provider: str) -> TrackInfo | None:
        """直接读取当前媒体信息供点歌校验使用，不复用可能过期的状态缓存。"""

        normalized = self._normalize(provider)
        if normalized == "auto":
            normalized = self.active_provider or ""
            if not normalized:
                return None
        if self.platform_name == "win32":
            sessions = self.windows_bridge.sessions()
            session = self._matching_session(normalized, sessions)
            return session.track if session is not None else None
        if self.platform_name == "darwin" and normalized in {"apple", "spotify"}:
            result_type, output = self._run_macos_script(normalized, "status")
            return self._parse_macos_track(output) if result_type == "ok" else None
        return None

    def _inspect_windows(
        self,
        provider: str,
        *,
        sessions: tuple[WindowsSessionSnapshot, ...] | None = None,
        session_error: Exception | None = None,
    ) -> MusicProviderStatus:
        detected = self._client(provider) is not None
        running = self._running(provider)
        label = MUSIC_SERVICE_LABELS[provider]
        if sessions is None and session_error is None:
            try:
                sessions = self.windows_bridge.sessions()
            except Exception as exc:
                sessions = ()
                session_error = exc
        if session_error is not None:
            exc = session_error
            if running:
                return MusicProviderStatus(
                    provider,
                    MusicControlState.BASIC_ONLY,
                    f"已检测到运行中的{label}；系统媒体 Session 不可用，仅支持基础媒体键。",
                    detected,
                    True,
                )
            message = f"已检测到{label}应用；尚未建立播放控制。" if detected else f"未检测到{label}应用。"
            state = MusicControlState.APP_DETECTED if detected else MusicControlState.NOT_DETECTED
            if not isinstance(exc, (ImportError, OSError)) and detected:
                message += " Windows 媒体控制暂时不可用。"
            return MusicProviderStatus(provider, state, message, detected, running)
        sessions = sessions or ()
        session = self._matching_session(provider, sessions)
        if session is not None:
            suffix = "已建立播放控制。" if session.controls else "已发现媒体 Session，但客户端未开放控制按钮。"
            state = (
                MusicControlState.PLAYING
                if session.controls and "playing" in session.track.playback_status.casefold()
                else MusicControlState.CONTROL_READY
                if session.controls
                else MusicControlState.APP_DETECTED
            )
            return MusicProviderStatus(
                provider,
                state,
                f"{label}：{suffix}",
                True,
                True,
                session.source_id,
                session.track,
            )
        if running:
            return MusicProviderStatus(
                provider,
                MusicControlState.WAITING_SESSION,
                f"{label}已启动 · 正在等待播放控制。",
                detected,
                True,
            )
        return MusicProviderStatus(
            provider,
            MusicControlState.APP_DETECTED if detected else MusicControlState.NOT_DETECTED,
            f"已检测到{label}应用；请先运行播放器并播放过媒体。" if detected else f"未检测到{label}应用。",
            detected,
            False,
        )

    def _control_windows(self, provider: str, action: str) -> MusicControlResult:
        """兼容旧内部入口，实际逻辑由 Transport Controller 唯一维护。"""

        return self.transport_controller._control_windows(provider, action)

    def _inspect_macos(self, provider: str, *, probe_control: bool) -> MusicProviderStatus:
        detected = self._client(provider) is not None
        running = self._running(provider)
        label = MUSIC_SERVICE_LABELS[provider]
        if not running:
            return MusicProviderStatus(
                provider,
                MusicControlState.APP_DETECTED if detected else MusicControlState.NOT_DETECTED,
                f"已检测到{label}应用；请先运行播放器。" if detected else f"未检测到{label}应用。",
                detected,
                False,
            )
        if provider in {"apple", "spotify"} and probe_control:
            result = self._run_macos_script(provider, "status")
            if result[0] == "permission":
                return self._mac_permission_status(provider, detected)
            if result[0] == "ok":
                return MusicProviderStatus(
                    provider,
                    MusicControlState.CONTROL_READY,
                    f"{label} 已建立 Apple Events 播放控制。",
                    detected,
                    True,
                    f"apple-events:{provider}",
                    self._parse_macos_track(result[1]),
                )
        if provider in {"apple", "spotify"}:
            return MusicProviderStatus(
                provider,
                MusicControlState.APP_DETECTED,
                f"已检测到运行中的{label}；点击播放控制时将验证 Automation 权限。",
                detected,
                True,
            )
        return MusicProviderStatus(
            provider,
            MusicControlState.BASIC_ONLY,
            f"已检测到运行中的{label}；该客户端没有稳定公开控制接口，仅支持基础媒体键。",
            detected,
            True,
        )

    def _control_macos(self, provider: str, action: str) -> MusicControlResult:
        """兼容旧内部入口，实际逻辑由 Transport Controller 唯一维护。"""

        return self.transport_controller._control_macos(provider, action)

    def _run_macos_script(self, provider: str, action: str) -> tuple[str, str]:
        target = "Music" if provider == "apple" else "Spotify"
        command = {
            "play": "play",
            "pause": "pause",
            "toggle": "playpause",
            "next": "next track",
            "previous": "previous track",
            "status": (
                'set t to current track\nreturn (player state as string) & tab & '
                '(name of t) & tab & (artist of t) & tab & (album of t)'
            ),
        }[action]
        script = f'tell application "{target}"\n{command}\nend tell'
        try:
            completed = self.command_runner(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "error", ""
        error = str(completed.stderr or "")
        if completed.returncode == 0:
            return "ok", str(completed.stdout or "").strip()
        lowered = error.casefold()
        if "-1743" in lowered or "not authorized" in lowered or "not permitted" in lowered:
            return "permission", error
        return "error", error

    @staticmethod
    def _parse_macos_track(output: str) -> TrackInfo:
        parts = output.split("\t")
        parts += [""] * (4 - len(parts))
        return TrackInfo(parts[1], parts[2], parts[3], parts[0].casefold())

    @staticmethod
    def _mac_permission_status(provider: str, detected: bool) -> MusicProviderStatus:
        return MusicProviderStatus(
            provider,
            MusicControlState.PERMISSION_REQUIRED,
            "需要 macOS Automation 权限：请在“系统设置 → 隐私与安全性 → 自动化”允许 Lili 控制音乐播放器。",
            detected,
            True,
        )

    @staticmethod
    def _matching_session(
        provider: str,
        sessions: tuple[WindowsSessionSnapshot, ...],
    ) -> WindowsSessionSnapshot | None:
        markers = PROVIDER_SESSION_MARKERS[provider]
        return next(
            (
                session
                for session in sessions
                if any(marker in session.source_id.casefold() for marker in markers)
            ),
            None,
        )

    def _client(self, provider: str) -> Path | None:
        custom = {
            "qq": self.settings.qq_music_path,
            "netease": self.settings.netease_music_path,
            "kugou": self.settings.kugou_music_path,
            "apple": self.settings.apple_music_path,
            "spotify": self.settings.spotify_music_path,
        }.get(provider, "")
        return self.client_finder(provider, custom)

    def _decorate_status(self, status: MusicProviderStatus) -> MusicProviderStatus:
        """补齐窗口、随机歌手能力与最近调用结果，避免各平台构造逻辑重复。"""

        if status.provider not in SUPPORTED_PROVIDERS:
            return status
        window_accessible = False
        if status.application_running:
            try:
                window_accessible = bool(self.window_checker(status.provider))
            except Exception:
                window_accessible = False
        history = self._history(status.provider)
        last_success = float(history.get("last_success_at", 0.0) or 0.0)
        last_failure = float(history.get("last_failure_at", 0.0) or 0.0)
        last_call_success: bool | None = None
        if last_success or last_failure:
            last_call_success = last_success >= last_failure
        capable = status.provider in self._playback_adapters and (
            status.application_detected or status.application_running
        )
        return replace(
            status,
            window_accessible=window_accessible,
            random_artist_capable=capable,
            last_call_success=last_call_success,
        )

    def _score_provider(self, status: MusicProviderStatus) -> MusicProviderScore:
        score = 0
        reasons: list[str] = []
        history = self._history(status.provider)
        if status.application_running:
            score += 40
            reasons.append("running")
        if status.can_control:
            score += 30
            reasons.append("controllable")
        if status.session_id:
            score += 15
            reasons.append("media_session")
        if status.window_accessible:
            score += 15
            reasons.append("window")
        if status.application_detected:
            score += 10
            reasons.append("installed")
        if status.random_artist_capable:
            score += 10
            reasons.append("artist_playback")
        if int(history.get("success_count", 0) or 0) > 0:
            score += 40
            reasons.append("last_success")
        if self.active_provider == status.provider:
            score += 60
            reasons.append("current_provider")
        if self.settings.music_service == status.provider:
            score += 20
            reasons.append("preferred")
        last_success = float(history.get("last_success_at", 0.0) or 0.0)
        last_failure = float(history.get("last_failure_at", 0.0) or 0.0)
        if last_failure > last_success:
            score -= 30
            reasons.append("recent_failure")
        last_error = str(history.get("last_error", ""))
        if last_error == MusicPlaybackError.UI_AUTOMATION_UNAVAILABLE.value:
            score -= 30
            reasons.append("ui_automation_unavailable")
        consecutive = int(history.get("consecutive_failures", 0) or 0)
        if consecutive > 1:
            score -= min(50, (consecutive - 1) * 10)
            reasons.append(f"failures_{consecutive}")
        return MusicProviderScore(status.provider, score, tuple(reasons), status)

    def _history(self, provider: str, *, create: bool = False) -> dict[str, object]:
        history = self.settings.music_provider_history
        entry = history.get(provider)
        if not isinstance(entry, dict):
            entry = {
                "success_count": 0,
                "failure_count": 0,
                "consecutive_failures": 0,
                "last_success_at": 0.0,
                "last_failure_at": 0.0,
                "last_error": "",
            }
            if create:
                history[provider] = entry
        return entry

    def _record_provider_result(self, provider: str, success: bool, error: str = "") -> None:
        entry = self._history(provider, create=True)
        now = time.time()
        if success:
            entry["success_count"] = int(entry.get("success_count", 0) or 0) + 1
            entry["consecutive_failures"] = 0
            entry["last_success_at"] = now
            entry["last_error"] = ""
        else:
            entry["failure_count"] = int(entry.get("failure_count", 0) or 0) + 1
            entry["consecutive_failures"] = int(entry.get("consecutive_failures", 0) or 0) + 1
            entry["last_failure_at"] = now
            entry["last_error"] = str(error)[:80]
        cached = self._cache.get(provider)
        if cached is not None:
            self._cache[provider] = self._decorate_status(cached)

    def _control_provider(self) -> str:
        """锁定实际播放平台；首次控制时尊重用户明确设置的优先播放器。"""

        if self.active_provider:
            return self.active_provider
        ranked = self.ranked_providers(probe_control=True)
        running = next(
            (
                item.provider
                for item in ranked
                if item.status.application_running and item.status.can_control
            ),
            "",
        )
        if running:
            return running
        preferred = self.settings.music_service
        if preferred in SUPPORTED_PROVIDERS:
            status = self.cached_status(preferred)
            if status.application_detected or status.application_running:
                return preferred
        # 默认“自动”没有当前播放 Session 时不擅自启动任意客户端。
        return ""

    def _running(self, provider: str) -> bool:
        return any(self.process_checker(name) for name in PROVIDER_PROCESS_NAMES[provider])

    def _default_window_checker(self, provider: str) -> bool:
        """检测 Provider 是否拥有可见顶层窗口；不激活窗口也不读取窗口文本内容。"""

        if self.platform_name != "win32":
            return False
        from .music import _windows_process_ids

        process_ids: set[int] = set()
        for name in PROVIDER_PROCESS_NAMES[provider]:
            if name.casefold().endswith(".exe"):
                process_ids.update(_windows_process_ids(name))
        if not process_ids:
            return False
        found = ctypes.c_bool(False)
        user32 = ctypes.windll.user32
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        @callback_type
        def callback(hwnd, _lparam):
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) in process_ids and user32.IsWindowVisible(hwnd):
                found.value = True
                return False
            return True

        try:
            user32.EnumWindows(callback, 0)
        except (AttributeError, OSError):
            return False
        return bool(found.value)

    def _default_process_checker(self, process_name: str) -> bool:
        if self.platform_name == "win32":
            from .music import _windows_process_ids

            return bool(_windows_process_ids(process_name))
        if self.platform_name == "darwin":
            try:
                completed = self.command_runner(
                    ["pgrep", "-x", process_name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                return False
            return completed.returncode == 0
        return False

    def _default_media_key_sender(self, action: str) -> bool:
        if self.platform_name == "win32":
            key = {"next": 0xB0, "previous": 0xB1}.get(action, 0xB3)
            try:
                ctypes.windll.user32.keybd_event(key, 0, 0, 0)
                ctypes.windll.user32.keybd_event(key, 0, 0x0002, 0)
            except (AttributeError, OSError):
                return False
            return True
        if self.platform_name == "darwin":
            return _send_macos_media_key(action)
        return False

    @staticmethod
    def _action_message(action: str, status: MusicProviderStatus) -> str:
        label = MUSIC_SERVICE_LABELS[status.provider]
        verbs = {
            "play": "播放",
            "pause": "暂停",
            "toggle": "播放/暂停",
            "next": "下一首",
            "previous": "上一首",
        }
        detail = status.track.display_text() if status.track and status.track.title else status.message
        return f"已向{label}发送“{verbs[action]}”。{detail}"

    @staticmethod
    def _normalize(provider: str) -> str:
        return provider if provider == "auto" or provider in MUSIC_SERVICE_LABELS else "auto"


def _send_macos_media_key(action: str) -> bool:
    """通过公开的 PyObjC Cocoa/Quartz 事件发送系统媒体键，不聚焦播放器窗口。"""

    key = {"next": 17, "previous": 18}.get(action, 16)
    try:
        from AppKit import NSEvent, NSSystemDefined
        from Quartz import CGEventPost, kCGHIDEventTap

        for flags in (0xA, 0xB):
            event = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                NSSystemDefined,
                (0, 0),
                0,
                0,
                0,
                None,
                8,
                (key << 16) | (flags << 8),
                -1,
            )
            CGEventPost(kCGHIDEventTap, event.CGEvent())
    except (ImportError, AttributeError, OSError):
        return False
    return True


class MusicCommandThread(QThread):
    """在线程中查询系统 Session 或发送 Apple Events，避免阻塞宠物动画。"""

    completed = Signal(object)

    def __init__(
        self,
        manager: MusicProviderManager,
        provider: str,
        action: str,
        parent=None,
        *,
        title: str = "",
        artist: str = "",
        random_artist: bool = False,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.provider = provider
        self.action = action
        self.title = title
        self.artist = artist
        self.random_artist = random_artist

    def run(self) -> None:
        try:
            if self.action == "play_song":
                result = self.manager.play_song(
                    self.provider,
                    self.title,
                    self.artist,
                    random_artist=self.random_artist,
                )
            else:
                result = self.manager.control(self.provider, self.action)
        except Exception as exc:
            if self.action == "play_song":
                result = SongPlaybackResult(
                    False,
                    self.provider,
                    self.title,
                    self.artist,
                    "歌曲搜索失败，请确认播放器正在运行并允许辅助功能。",
                    MusicPlaybackError.SEARCH_FAILED,
                )
            else:
                status = MusicProviderStatus(
                    self.provider,
                    MusicControlState.ERROR,
                    f"音乐控制遇到问题：{exc}",
                )
                result = MusicControlResult(False, self.provider, self.action, status.message, status)
        self.completed.emit(result)


class MusicController(QObject):
    """六毛界面的异步播放控制入口，同一时间只执行一个系统命令。"""

    result_ready = Signal(object)
    status_changed = Signal(object)
    state_changed = Signal(object)
    busy_changed = Signal(bool)

    def __init__(self, settings: PetSettings, manager: MusicProviderManager, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.manager = manager
        self._thread: MusicCommandThread | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def perform(self, action: str) -> bool:
        if self.busy:
            return False
        self._thread = MusicCommandThread(
            self.manager,
            "auto",
            action,
            self,
        )
        self._thread.completed.connect(self._completed)
        self._thread.finished.connect(self._finished)
        self.busy_changed.emit(True)
        self._thread.start()
        return True

    def refresh_status(self) -> bool:
        """后台读取 Windows GSMTC/macOS Apple Events 的当前媒体状态。"""

        return self.perform("status")

    def play_song(
        self,
        title: str,
        artist: str,
        *,
        random_artist: bool = False,
    ) -> bool:
        """在线程中执行精确点歌与媒体校验，避免阻塞宠物动画和拖动。"""

        if self.busy:
            return False
        self._thread = MusicCommandThread(
            self.manager,
            "auto" if random_artist else self.settings.music_service,
            "play_song",
            self,
            title=title,
            artist=artist,
            random_artist=random_artist,
        )
        self._thread.completed.connect(self._completed)
        self._thread.finished.connect(self._finished)
        self.busy_changed.emit(True)
        self._thread.start()
        return True

    def _completed(self, result: MusicControlResult | SongPlaybackResult) -> None:
        if isinstance(result, MusicControlResult):
            self.status_changed.emit(result.status)
            self.state_changed.emit(MusicState.from_status(result.status))
        elif result.success and result.provider in SUPPORTED_PROVIDERS:
            # Song Resolver 已把播放器交给 Transport；同步刷新 UI 的真实能力状态。
            status = self.manager.cached_status(result.provider)
            self.status_changed.emit(status)
            self.state_changed.emit(MusicState.from_status(status, source="lili-selection"))
        self.result_ready.emit(result)

    def _finished(self) -> None:
        thread = self._thread
        self._thread = None
        self.busy_changed.emit(False)
        if thread is not None:
            thread.deleteLater()

    def shutdown(self) -> None:
        """退出时等待最长 5.5 秒，避免仍在执行的系统线程被直接销毁。"""

        if self._thread is not None and self._thread.isRunning():
            self._thread.requestInterruption()
            self._thread.wait(5500)

