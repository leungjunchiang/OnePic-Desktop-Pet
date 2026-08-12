"""统一管理本机音乐播放器发现、原生播放控制、媒体键回退和真实状态。

Windows 优先读取 Windows.Media.Control 的全局媒体 Sessions；只有目标客户端正在运行但没有
暴露可控制 Session 时才发送系统媒体键。macOS 的 Apple Music 与 Spotify 使用各自的
AppleScript adapter，QQ 音乐、网易云和酷狗在运行时使用系统媒体键作为基础控制回退。
本模块不把“找到安装文件”或“启动客户端”视为已连接，也不实现或伪造任何私人音乐 API。
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal

from .config import PetSettings
from .music import MUSIC_SERVICE_LABELS, find_music_client


PROVIDER_SESSION_MARKERS = {
    "qq": ("qqmusic", "tencent.qqmusic"),
    "netease": ("cloudmusic", "netease", "music.163"),
    "kugou": ("kugou", "kgmusic"),
    "apple": ("applemusic", "apple.music", "music.exe"),
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
    CONTROL_READY = "control_ready"
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

    @property
    def can_control(self) -> bool:
        return self.state in {
            MusicControlState.CONTROL_READY,
            MusicControlState.BASIC_ONLY,
        }


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
    ) -> None:
        self.settings = settings
        self.platform_name = platform_name or sys.platform
        self.windows_bridge = windows_bridge or WindowsGSMTCBridge()
        self.client_finder = client_finder
        self.process_checker = process_checker or self._default_process_checker
        self.command_runner = command_runner
        self.media_key_sender = media_key_sender or self._default_media_key_sender
        self._cache: dict[str, MusicProviderStatus] = {}

    def cached_status(self, provider: str) -> MusicProviderStatus:
        """返回缓存；没有缓存时只做安装检测，不声称已经建立控制。"""

        normalized = self._normalize(provider)
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
        self._cache[normalized] = status
        return status

    def inspect(self, provider: str, *, probe_control: bool = False) -> MusicProviderStatus:
        """刷新真实状态；macOS 只在用户主动刷新时探测 Apple Events 权限。"""

        normalized = self._normalize(provider)
        if self.platform_name == "win32":
            status = self._inspect_windows(normalized)
        elif self.platform_name == "darwin":
            status = self._inspect_macos(normalized, probe_control=probe_control)
        else:
            status = self.cached_status(normalized)
        self._cache[normalized] = status
        return status

    def control(self, provider: str, action: str) -> MusicControlResult:
        """执行基础播放命令；搜索歌曲不经过此入口。"""

        normalized = self._normalize(provider)
        if action == "status":
            status = self.inspect(normalized, probe_control=True)
            message = status.track.display_text() if status.track else status.message
            return MusicControlResult(status.can_control, normalized, action, message, status)
        if action not in {"play", "pause", "toggle", "next", "previous"}:
            status = self.inspect(normalized)
            return MusicControlResult(False, normalized, action, "不支持的音乐控制命令。", status)
        if self.platform_name == "win32":
            return self._control_windows(normalized, action)
        if self.platform_name == "darwin":
            return self._control_macos(normalized, action)
        status = self.inspect(normalized)
        return MusicControlResult(False, normalized, action, "当前系统暂不支持本机音乐控制。", status)

    def _inspect_windows(self, provider: str) -> MusicProviderStatus:
        detected = self._client(provider) is not None
        running = self._running(provider)
        label = MUSIC_SERVICE_LABELS[provider]
        try:
            sessions = self.windows_bridge.sessions()
        except Exception as exc:
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
        session = self._matching_session(provider, sessions)
        if session is not None:
            suffix = "已建立播放控制。" if session.controls else "已发现媒体 Session，但客户端未开放控制按钮。"
            state = MusicControlState.CONTROL_READY if session.controls else MusicControlState.APP_DETECTED
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
                MusicControlState.BASIC_ONLY,
                f"已检测到运行中的{label}，但没有可控制 Session；仅支持基础媒体键。",
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
        status = self._inspect_windows(provider)
        if status.state is MusicControlState.CONTROL_READY and status.session_id:
            try:
                success = self.windows_bridge.control(status.session_id, action)
            except Exception:
                success = False
            if success:
                refreshed = self._inspect_windows(provider)
                self._cache[provider] = refreshed
                return MusicControlResult(True, provider, action, self._action_message(action, refreshed), refreshed)
        if status.application_running and self.media_key_sender(action):
            fallback = MusicProviderStatus(
                provider,
                MusicControlState.BASIC_ONLY,
                f"{MUSIC_SERVICE_LABELS[provider]}未开放可用 Session；已发送系统媒体键，仅支持基础控制。",
                status.application_detected,
                True,
                track=status.track,
            )
            self._cache[provider] = fallback
            return MusicControlResult(True, provider, action, self._action_message(action, fallback), fallback, True)
        self._cache[provider] = status
        return MusicControlResult(False, provider, action, status.message, status)

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
        status = self._inspect_macos(provider, probe_control=False)
        if not status.application_running:
            self._cache[provider] = status
            return MusicControlResult(False, provider, action, status.message, status)
        if provider in {"apple", "spotify"}:
            result_type, output = self._run_macos_script(provider, action)
            if result_type == "ok":
                refreshed = self._inspect_macos(provider, probe_control=True)
                self._cache[provider] = refreshed
                return MusicControlResult(True, provider, action, self._action_message(action, refreshed), refreshed)
            if result_type == "permission":
                permission = self._mac_permission_status(provider, status.application_detected)
                self._cache[provider] = permission
                return MusicControlResult(False, provider, action, permission.message, permission)
        if self.media_key_sender(action):
            fallback = MusicProviderStatus(
                provider,
                MusicControlState.BASIC_ONLY,
                f"{MUSIC_SERVICE_LABELS[provider]}仅支持基础媒体键控制。",
                status.application_detected,
                True,
            )
            self._cache[provider] = fallback
            return MusicControlResult(True, provider, action, self._action_message(action, fallback), fallback, True)
        error = MusicProviderStatus(
            provider,
            MusicControlState.ERROR,
            f"未能控制{MUSIC_SERVICE_LABELS[provider]}，请检查播放器是否正在运行。",
            status.application_detected,
            status.application_running,
        )
        self._cache[provider] = error
        return MusicControlResult(False, provider, action, error.message, error)

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

    def _running(self, provider: str) -> bool:
        return any(self.process_checker(name) for name in PROVIDER_PROCESS_NAMES[provider])

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
        return provider if provider in MUSIC_SERVICE_LABELS else "netease"


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

    def __init__(self, manager: MusicProviderManager, provider: str, action: str, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.provider = provider
        self.action = action

    def run(self) -> None:
        try:
            result = self.manager.control(self.provider, self.action)
        except Exception as exc:
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
            self.settings.music_service,
            action,
            self,
        )
        self._thread.completed.connect(self._completed)
        self._thread.finished.connect(self._finished)
        self.busy_changed.emit(True)
        self._thread.start()
        return True

    def _completed(self, result: MusicControlResult) -> None:
        self.status_changed.emit(result.status)
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
