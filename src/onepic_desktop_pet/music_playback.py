"""实现指定歌曲的精确搜索、结果筛选、播放动作与媒体信息校验闭环。

每个音乐平台拥有独立 Provider Adapter。搜索成功、打开客户端或触发控件都不等于
播放成功；只有当前媒体标题和歌手与最终选中的歌曲同时匹配时才返回成功。Windows
Adapter 使用 UI Automation 定位“歌曲”结果；网易云 3.x 不公开 Chromium 控件树时，
使用绑定用户 Default 桌面的 DPI-aware 本机交互回退。macOS 优先使用 Apple Events，
并在需要时使用已授权的 Accessibility。严格点歌返回明确失败码；随机歌手播放在播放
动作已执行但媒体 Session 暂不可读时标记为未验证启动，不会因此主动停止音乐。
"""

from __future__ import annotations

import logging
import base64
import random
import subprocess
import sys
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

from .config import PetSettings
from .music import MUSIC_SERVICE_LABELS, find_music_client


LOGGER = logging.getLogger(__name__)


class MusicPlaybackError(str, Enum):
    """点歌闭环中可以被日志、测试和界面稳定识别的失败阶段。"""

    SEARCH_FAILED = "SEARCH_FAILED"
    UI_AUTOMATION_UNAVAILABLE = "UI_AUTOMATION_UNAVAILABLE"
    RESULT_NOT_FOUND = "RESULT_NOT_FOUND"
    PLAY_ACTION_FAILED = "PLAY_ACTION_FAILED"
    MEDIA_SESSION_TIMEOUT = "MEDIA_SESSION_TIMEOUT"
    TRACK_VERIFY_FAILED = "TRACK_VERIFY_FAILED"


class MusicPlaybackOutcome(str, Enum):
    """基础随机播放成功后的可验证程度。"""

    PLAYBACK_CONFIRMED = "PLAYBACK_CONFIRMED"
    PLAYBACK_STARTED_UNVERIFIED = "PLAYBACK_STARTED_UNVERIFIED"


class TrackSnapshot(Protocol):
    """校验器所需的最小当前歌曲信息。"""

    title: str
    artist: str


@dataclass(frozen=True)
class SongCandidate:
    """Provider Adapter 从“歌曲”结果中读取到的一个候选项。"""

    provider: str
    title: str
    artist: str
    result_type: str = "song"
    identifier: str = ""
    native: object | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class SongPlaybackResult:
    """一次播放结果；严格点歌需确认，随机歌手播放允许标记为未验证启动。"""

    success: bool
    provider: str
    requested_title: str
    requested_artist: str
    message: str
    error_code: MusicPlaybackError | None = None
    selected: SongCandidate | None = None
    current_title: str = ""
    current_artist: str = ""
    play_attempts: int = 0
    outcome: MusicPlaybackOutcome | None = None
    attempted_providers: tuple[str, ...] = ()


class ProviderSearchError(RuntimeError):
    """Provider 无法完成搜索或读取结果时使用的内部异常。"""


class UIAutomationUnavailableError(ProviderSearchError):
    """Windows UIAutomation 根节点、窗口或控件无法访问。"""


class MusicProviderAdapter(Protocol):
    """各音乐客户端必须独立实现的最小点歌协议。"""

    provider: str

    def search(self, title: str, artist: str) -> Sequence[SongCandidate]: ...

    def play(self, candidate: SongCandidate) -> bool: ...


def _canonical(value: str) -> str:
    """用于严格匹配的 Unicode 规范形式，保留版本后缀以拒绝 Live/Remix。"""

    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _same_song(left: str, right: str) -> bool:
    return bool(_canonical(left)) and _canonical(left) == _canonical(right)


def _failure_message(code: MusicPlaybackError, *, random_artist: bool = False) -> str:
    messages = {
        MusicPlaybackError.SEARCH_FAILED: "歌曲搜索失败，请确认播放器正在运行并允许辅助功能。",
        MusicPlaybackError.UI_AUTOMATION_UNAVAILABLE: "播放器界面暂时无法访问，请在交互式 Windows 桌面中运行网易云音乐。",
        MusicPlaybackError.RESULT_NOT_FOUND: (
            "没有找到这位歌手的歌曲。" if random_artist else "没有找到这首歌。"
        ),
        MusicPlaybackError.PLAY_ACTION_FAILED: "已找到歌曲，但播放器没有开始播放。",
        MusicPlaybackError.MEDIA_SESSION_TIMEOUT: "播放器没有返回当前歌曲信息，暂时无法确认是否播放成功。",
        MusicPlaybackError.TRACK_VERIFY_FAILED: "实际播放的不是目标歌曲，已停止重试。",
    }
    return messages[code]


class ExactMusicPlaybackManager:
    """执行 search → exact match → play → verify，最多重试一次精确播放动作。"""

    def __init__(
        self,
        adapters: Mapping[str, MusicProviderAdapter],
        track_reader: Callable[[str], TrackSnapshot | None],
        *,
        verify_timeout_seconds: float = 5.0,
        poll_interval_seconds: float = 0.55,
        sleep: Callable[[float], None] = time.sleep,
        random_source: random.Random | None = None,
    ) -> None:
        self.adapters = dict(adapters)
        self.track_reader = track_reader
        self.verify_timeout_seconds = max(0.0, verify_timeout_seconds)
        self.poll_interval_seconds = max(0.01, poll_interval_seconds)
        self.sleep = sleep
        self.random_source = random_source or random.Random()

    def play_song(
        self,
        provider: str,
        title: str,
        artist: str,
        *,
        random_artist: bool = False,
    ) -> SongPlaybackResult:
        adapter = self.adapters.get(provider)
        self._debug("search", provider, title, artist)
        if adapter is None:
            return self._failed(provider, title, artist, MusicPlaybackError.SEARCH_FAILED)
        try:
            candidates = tuple(adapter.search(title, artist))
        except Exception as exc:
            self._debug("search_failed", provider, title, artist, error=repr(exc))
            return self._failed(provider, title, artist, MusicPlaybackError.SEARCH_FAILED)

        valid = [
            candidate
            for candidate in candidates
            if candidate.result_type.casefold() == "song"
            and _same_song(candidate.artist, artist)
            and (random_artist or _same_song(candidate.title, title))
        ]
        if not valid:
            self._debug("result_not_found", provider, title, artist)
            return self._failed(
                provider,
                title,
                artist,
                MusicPlaybackError.RESULT_NOT_FOUND,
                random_artist=random_artist,
            )
        selected = self.random_source.choice(valid) if random_artist else valid[0]
        target_title = selected.title
        self._debug(
            "exact_match",
            provider,
            title,
            artist,
            selected_title=selected.title,
            selected_artist=selected.artist,
        )

        saw_media = False
        last_track: TrackSnapshot | None = None
        for attempt in (1, 2):
            try:
                played = bool(adapter.play(selected))
            except Exception as exc:
                played = False
                self._debug("play_exception", provider, title, artist, error=repr(exc))
            if not played:
                self._debug("play_action_failed", provider, title, artist, attempt=attempt)
                return self._failed(
                    provider,
                    title,
                    artist,
                    MusicPlaybackError.PLAY_ACTION_FAILED,
                    selected=selected,
                    attempts=attempt,
                )
            matched, observed, current = self._verify(provider, target_title, selected.artist)
            saw_media = saw_media or observed
            last_track = current or last_track
            if matched:
                current_title = str(getattr(current, "title", "") or "")
                current_artist = str(getattr(current, "artist", "") or "")
                self._debug(
                    "verified",
                    provider,
                    title,
                    artist,
                    selected_title=selected.title,
                    selected_artist=selected.artist,
                    current_title=current_title,
                    current_artist=current_artist,
                    attempt=attempt,
                )
                return SongPlaybackResult(
                    True,
                    provider,
                    title,
                    artist,
                    f"正在播放：{selected.artist}《{selected.title}》",
                    selected=selected,
                    current_title=current_title,
                    current_artist=current_artist,
                    play_attempts=attempt,
                    outcome=MusicPlaybackOutcome.PLAYBACK_CONFIRMED,
                )
            self._debug(
                "verify_retry" if attempt == 1 else "verify_failed",
                provider,
                title,
                artist,
                selected_title=selected.title,
                selected_artist=selected.artist,
                current_title=str(getattr(last_track, "title", "") or ""),
                current_artist=str(getattr(last_track, "artist", "") or ""),
                attempt=attempt,
            )

        code = (
            MusicPlaybackError.TRACK_VERIFY_FAILED
            if saw_media
            else MusicPlaybackError.MEDIA_SESSION_TIMEOUT
        )
        self._debug(
            "failed",
            provider,
            title,
            artist,
            error_code=code.value,
            selected_title=selected.title,
            selected_artist=selected.artist,
            current_title=str(getattr(last_track, "title", "") or ""),
            current_artist=str(getattr(last_track, "artist", "") or ""),
        )
        return self._failed(
            provider,
            title,
            artist,
            code,
            selected=selected,
            current=last_track,
            attempts=2,
        )

    def _verify(
        self,
        provider: str,
        title: str,
        artist: str,
    ) -> tuple[bool, bool, TrackSnapshot | None]:
        deadline = time.monotonic() + self.verify_timeout_seconds
        observed = False
        current: TrackSnapshot | None = None
        while True:
            try:
                current = self.track_reader(provider)
            except Exception as exc:
                self._debug("media_read_error", provider, title, artist, error=repr(exc))
                current = None
            if current is not None and (current.title or current.artist):
                observed = True
                if _same_song(current.title, title) and _same_song(current.artist, artist):
                    return True, True, current
            if time.monotonic() >= deadline:
                return False, observed, current
            self.sleep(min(self.poll_interval_seconds, max(0.0, deadline - time.monotonic())))

    @staticmethod
    def _debug(stage: str, provider: str, title: str, artist: str, **values: object) -> None:
        details = " ".join(f"{key}={value!r}" for key, value in values.items())
        LOGGER.debug(
            "music_playback stage=%s provider=%s requestedTitle=%r requestedArtist=%r %s",
            stage,
            provider,
            title,
            artist,
            details,
        )

    @staticmethod
    def _failed(
        provider: str,
        title: str,
        artist: str,
        code: MusicPlaybackError,
        *,
        random_artist: bool = False,
        selected: SongCandidate | None = None,
        current: TrackSnapshot | None = None,
        attempts: int = 0,
    ) -> SongPlaybackResult:
        return SongPlaybackResult(
            False,
            provider,
            title,
            artist,
            _failure_message(code, random_artist=random_artist),
            code,
            selected,
            str(getattr(current, "title", "") or ""),
            str(getattr(current, "artist", "") or ""),
            attempts,
        )


class BasicRandomArtistPlaybackManager:
    """用于陪伴场景的宽松随机播放闭环。

    与精确点播不同，这条路径只需要把播放器带到目标歌手的歌曲区域并
    发起一次真实播放动作。媒体 Session 仅作为日志和可选反馈，读取不到
    当前歌曲时也不能阻止基础播放。
    """

    def __init__(
        self,
        adapters: Mapping[str, MusicProviderAdapter],
        track_reader: Callable[[str], TrackSnapshot | None] | None = None,
        *,
        random_source: random.Random | None = None,
        verify_timeout_seconds: float = 5.0,
        poll_interval_seconds: float = 0.55,
        action_attempts: int = 2,
        retry_delay_seconds: float = 0.65,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.adapters = dict(adapters)
        self.track_reader = track_reader
        self.random_source = random_source or random.Random()
        self.verify_timeout_seconds = max(0.0, verify_timeout_seconds)
        self.poll_interval_seconds = max(0.01, poll_interval_seconds)
        self.action_attempts = max(1, int(action_attempts))
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)
        self.sleep = sleep

    def play_random_artist(self, provider: str, artist: str) -> SongPlaybackResult:
        adapter = self.adapters.get(provider)
        title = ""
        self._debug("search", provider, title, artist)
        if adapter is None:
            return self._failed(provider, artist, MusicPlaybackError.SEARCH_FAILED)
        candidates: tuple[SongCandidate, ...] = ()
        search_error: Exception | None = None
        for search_attempt in range(1, self.action_attempts + 1):
            try:
                candidates = tuple(adapter.search("", artist))
                self._debug(
                    "search_attempt",
                    provider,
                    title,
                    artist,
                    attempt=search_attempt,
                    candidate_count=len(candidates),
                )
                search_error = None
                if candidates or search_attempt == self.action_attempts:
                    break
                self._debug("search_empty_retry", provider, title, artist, attempt=search_attempt)
            except (UIAutomationUnavailableError, ProviderSearchError) as exc:
                search_error = exc
                self._debug("search_error", provider, title, artist, attempt=search_attempt, error=repr(exc))
                if search_attempt == self.action_attempts:
                    break
            except Exception as exc:
                self._debug("search_failed", provider, title, artist, attempt=search_attempt, error=repr(exc))
                return self._failed(provider, artist, MusicPlaybackError.SEARCH_FAILED)
            self.sleep(self.retry_delay_seconds)

        if search_error is not None:
            # Windows UI Automation and the PowerShell bridge can fail after
            # the client was found. Let adapters with a native random action
            # try that path before giving up.
            native_result = self._try_native_random(adapter, provider, artist)
            if native_result is not None:
                return native_result
            if isinstance(search_error, UIAutomationUnavailableError) or isinstance(adapter, WindowsUIAutomationAdapter):
                return self._failed(provider, artist, MusicPlaybackError.UI_AUTOMATION_UNAVAILABLE)
            return self._failed(provider, artist, MusicPlaybackError.SEARCH_FAILED)

        # Song rows are preferred, but an artist page/playlist returned by an
        # adapter is also a valid target for random playback.
        song_candidates = [
            candidate
            for candidate in candidates
            if candidate.result_type.casefold() == "song"
            and _same_song(candidate.artist, artist)
        ]
        selectable = song_candidates or [
            candidate
            for candidate in candidates
            if candidate.result_type.casefold() in {"artist", "album", "playlist"}
        ]
        if not selectable:
            # An adapter may expose a native “play artist/random” action when
            # the client does not expose individual rows through automation.
            native_result = self._try_native_random(adapter, provider, artist)
            if native_result is not None:
                return native_result
            self._debug("result_not_found", provider, title, artist)
            return self._failed(provider, artist, MusicPlaybackError.RESULT_NOT_FOUND)
        selected = self.random_source.choice(selectable)
        self._debug(
            "random_match",
            provider,
            title,
            artist,
            selected_title=selected.title,
            selected_artist=selected.artist,
            selected_type=selected.result_type,
        )
        current_title = current_artist = ""
        current_status = ""
        play_attempts = 0
        current: TrackSnapshot | None = None
        explicit_not_playing = False
        for play_attempt in range(1, self.action_attempts + 1):
            play_attempts = play_attempt
            try:
                played = bool(adapter.play(selected))
            except Exception as exc:
                self._debug("play_exception", provider, title, artist, attempt=play_attempt, error=repr(exc))
                played = False
            self._debug("play_attempt", provider, title, artist, attempt=play_attempt, accepted=played)
            if not played:
                if play_attempt < self.action_attempts:
                    self.sleep(self.retry_delay_seconds)
                    continue
                self._debug("play_action_failed", provider, title, artist, attempts=play_attempt)
                return self._failed(provider, artist, MusicPlaybackError.PLAY_ACTION_FAILED, selected=selected)

            if self.track_reader is None:
                break
            current = self._wait_for_playing(provider, artist)
            current_status = str(getattr(current, "playback_status", "") or "")
            if "playing" in current_status.casefold() or not current_status:
                break
            explicit_not_playing = True
            if play_attempt < self.action_attempts:
                self._debug("playback_not_started_retry", provider, title, artist, attempt=play_attempt, playback_status=current_status)
                self.sleep(self.retry_delay_seconds)
                continue
            self._debug("playback_not_started", provider, title, artist, attempts=play_attempt, playback_status=current_status)
        if explicit_not_playing and "playing" not in current_status.casefold():
            return self._failed(
                provider,
                artist,
                MusicPlaybackError.PLAY_ACTION_FAILED,
                selected=selected,
                current=current,
                attempts=play_attempts,
            )
        if current is not None:
            current_title = str(getattr(current, "title", "") or "")
            current_artist = str(getattr(current, "artist", "") or "")
        confirmed = bool(_canonical(artist)) and _canonical(artist) in _canonical(current_artist)
        outcome = (
            MusicPlaybackOutcome.PLAYBACK_CONFIRMED
            if confirmed
            else MusicPlaybackOutcome.PLAYBACK_STARTED_UNVERIFIED
        )
        self._debug(
            "started",
            provider,
            title,
            artist,
            selected_title=selected.title,
            selected_artist=selected.artist,
            current_title=current_title,
            current_artist=current_artist,
            playback_status=current_status,
            outcome=outcome.value,
        )
        return SongPlaybackResult(
            True,
            provider,
            title,
            artist,
            f"正在播放{artist}的随机歌曲" + (f"：{selected.title}" if selected.title else ""),
            selected=selected,
            current_title=current_title,
            current_artist=current_artist,
            play_attempts=play_attempts or 1,
            outcome=outcome,
        )

    def _wait_for_playing(self, provider: str, artist: str) -> TrackSnapshot | None:
        """等待目标播放器进入 Playing；读不到 Session 不能阻止已发出的播放动作。

        UI Automation 只负责把播放器带到歌曲区域并触发播放。播放是否真正启动，
        优先观察该 Provider 的媒体 Session 的 playback_status；标题和歌手只用于
        可选的歌曲确认，不再作为“已开始播放”的硬门槛。
        """

        deadline = time.monotonic() + self.verify_timeout_seconds
        latest: TrackSnapshot | None = None
        while True:
            try:
                latest = self.track_reader(provider) if self.track_reader else None
            except Exception as exc:
                self._debug("media_read_error", provider, "", artist, error=repr(exc))
                latest = None
            status = str(getattr(latest, "playback_status", "") or "").casefold()
            title = str(getattr(latest, "title", "") or "")
            current_artist = str(getattr(latest, "artist", "") or "")
            if latest is not None and (status == "playing" or "playing" in status):
                self._debug(
                    "playback_started",
                    provider,
                    "",
                    artist,
                    playback_status=status,
                    current_title=title,
                    current_artist=current_artist,
                )
                return latest
            if time.monotonic() >= deadline:
                self._debug(
                    "playback_unverified",
                    provider,
                    "",
                    artist,
                    playback_status=status,
                    current_title=title,
                    current_artist=current_artist,
                )
                return latest
            self.sleep(min(self.poll_interval_seconds, max(0.0, deadline - time.monotonic())))

    def _try_native_random(
        self,
        adapter: MusicProviderAdapter,
        provider: str,
        artist: str,
    ) -> SongPlaybackResult | None:
        """UI 树不可读时调用 Provider 自己的歌手随机播放能力。"""

        native_random = getattr(adapter, "play_random_artist", None)
        if not callable(native_random):
            return None
        try:
            if not bool(native_random(artist)):
                return None
        except Exception as exc:
            self._debug("play_exception", provider, "", artist, error=repr(exc))
            return None
        self._debug("started", provider, "", artist, selected_type="native_random")
        return SongPlaybackResult(
            True,
            provider,
            "",
            artist,
            f"正在播放{artist}的随机歌曲",
            play_attempts=1,
            outcome=MusicPlaybackOutcome.PLAYBACK_STARTED_UNVERIFIED,
        )

    @staticmethod
    def _debug(stage: str, provider: str, title: str, artist: str, **values: object) -> None:
        details = " ".join(f"{key}={value!r}" for key, value in values.items())
        LOGGER.debug(
            "music_playback stage=%s provider=%s requestedTitle=%r requestedArtist=%r %s",
            stage,
            provider,
            title,
            artist,
            details,
        )

    @staticmethod
    def _failed(
        provider: str,
        artist: str,
        code: MusicPlaybackError,
        *,
        selected: SongCandidate | None = None,
        current: TrackSnapshot | None = None,
        attempts: int = 0,
    ) -> SongPlaybackResult:
        return SongPlaybackResult(
            False,
            provider,
            "",
            artist,
            _failure_message(code, random_artist=True),
            code,
            selected,
            str(getattr(current, "title", "") or ""),
            str(getattr(current, "artist", "") or ""),
            attempts,
        )

class WindowsUIAutomationAdapter:
    """Windows 客户端 Adapter 基类；子类必须提供自身窗口、搜索框和播放按钮语义。"""

    provider = ""
    window_pattern = ".*"
    search_names: tuple[str, ...] = ()
    song_tab_names: tuple[str, ...] = ("歌曲", "Songs", "Tracks")
    play_button_names: tuple[str, ...] = ("播放", "播放歌曲", "Play", "Play song")

    def __init__(
        self,
        settings: PetSettings,
        *,
        client_finder: Callable[[str, str], Path | None] = find_music_client,
        process_launcher: Callable[..., object] = subprocess.Popen,
        wait_seconds: float = 8.0,
    ) -> None:
        self.settings = settings
        self.client_finder = client_finder
        self.process_launcher = process_launcher
        self.wait_seconds = wait_seconds

    def search(self, title: str, artist: str) -> Sequence[SongCandidate]:
        auto = self._automation()
        if auto is None:
            return self._powershell_search(title, artist)
        client = self.client_finder(self.provider, self._custom_path())
        if client is None:
            raise ProviderSearchError("music client not installed")
        try:
            self.process_launcher(
                [str(client)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise ProviderSearchError("music client could not be launched") from exc
        window = self._wait_for_window(auto)
        search_box = self._find_search_box(window)
        if search_box is None:
            raise UIAutomationUnavailableError("search box not exposed through UI Automation")
        try:
            search_box.SetFocus()
            search_box.SendKeys("{Ctrl}a{Del}")
            search_box.SendKeys(f"{artist} {title}".strip())
            search_box.SendKeys("{Enter}")
        except Exception as exc:
            raise UIAutomationUnavailableError("search input failed") from exc
        if not self._select_song_tab(window) and title:
            raise UIAutomationUnavailableError("song results tab not found")
        deadline = time.monotonic() + self.wait_seconds
        while time.monotonic() < deadline:
            candidates = self._song_candidates(window, title, artist)
            if candidates:
                return candidates
            time.sleep(0.35)
        return ()

    def play(self, candidate: SongCandidate) -> bool:
        control = candidate.native
        if isinstance(control, tuple) and control and control[0] == "powershell":
            return self._powershell_play(candidate)
        if control is None:
            return False
        row = self._matching_row(control, candidate.artist)
        if row is None:
            return False
        wanted = {_canonical(name) for name in self.play_button_names}
        for child in self._walk(row, max_depth=5):
            if "Button" not in str(getattr(child, "ControlTypeName", "")):
                continue
            if _canonical(str(getattr(child, "Name", "") or "")) in wanted:
                try:
                    child.Click()
                    return True
                except Exception:
                    continue
        try:
            control.DoubleClick()
            return True
        except Exception:
            return False

    @staticmethod
    def _automation():
        try:
            import uiautomation as auto
        except (ImportError, OSError) as exc:
            # The packaged app may run without the optional Python wrapper.
            # Windows' built-in UIAutomationClient is used as a fallback.
            return None
        return auto

    def _powershell_search(self, title: str, artist: str) -> Sequence[SongCandidate]:
        client = self.client_finder(self.provider, self._custom_path())
        if client is None:
            raise ProviderSearchError("music client not installed")
        script = r'''
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
try { $proc=Start-Process -FilePath $exe -PassThru } catch { Write-Output ("UI|root=0|error=start_process"); exit 10 }
function Desc($root) {
  $all = @($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition))
  return $all
}
$root=[System.Windows.Automation.AutomationElement]::RootElement
$walker=[System.Windows.Automation.TreeWalker]::ControlViewWalker
$deadline=(Get-Date).AddSeconds(12); $win=$null
$wins=@()
while((Get-Date) -lt $deadline -and $null -eq $win) {
  try { $wins=@($root.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)) } catch { Write-Output "UI|root=0|error=find_windows"; exit 10 }
  foreach($candidate in $wins) {
    if (($candidate.Current.Name -match $pattern) -or ($candidate.Current.ClassName -match $pattern)) { $win=$candidate; break }
  }
  if($null -eq $win){ Start-Sleep -Milliseconds 350 }
}
if($null -eq $win){ Write-Output ("UI|root=1|topLevel=" + $wins.Count + "|window=0|pid=" + $proc.Id); exit 10 }
$items=Desc $win
Write-Output ("META|pid=" + $win.Current.ProcessId + "|handle=" + $win.Current.NativeWindowHandle + "|title=" + $win.Current.Name + "|root=1|topLevel=" + $wins.Count + "|controls=" + $items.Count)
$edit=$items | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit } | Select-Object -First 1
if($null -eq $edit){ Write-Output "UI|searchBox=0"; exit 11 }
Write-Output "UI|searchBox=1"
try { $vp=$edit.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern); $vp.SetValue($artist); $edit.SetFocus() } catch { exit 12 }
[System.Windows.Forms.SendKeys]::SendWait('{ENTER}'); Start-Sleep -Seconds 2
$items=Desc $win
Write-Output ("UI|searchResultsControls=" + $items.Count)
$artistElements=@($items | Where-Object { $_.Current.Name -eq $artist })
$out=@()
foreach($ae in $artistElements){
  $row=$ae
  for($i=0;$i -lt 6 -and $null -ne $row;$i++){
    $children=Desc $row | Where-Object { $_.Current.Name -and $_.Current.Name -ne $artist }
    $title=$children | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::ListItem -or $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::DataItem } | Select-Object -First 1
    if($null -ne $title){ $out += ($title.Current.Name + "`t" + $artist); break }
    try { $row=$walker.GetParent($row) } catch { break }
  }
}
$out | Select-Object -Unique
Write-Output ("UI|candidateCount=" + $out.Count)
'''
        script = (
            "$exe=" + self._ps_literal(str(client)) + "; $pattern=" + self._ps_literal(self.window_pattern)
            + "; $artist=" + self._ps_literal(artist) + ";\n" + script
        )
        completed = self._run_powershell(script)
        self._log_powershell_output(completed.stdout, provider=self.provider, stage="search")
        if completed.returncode in {10, 11, 12}:
            raise UIAutomationUnavailableError("UIAutomation root/window/search controls unavailable")
        if completed.returncode != 0:
            raise UIAutomationUnavailableError(
                str(completed.stderr or "UIAutomation search failed")
            )
        candidates = []
        for line in str(completed.stdout or "").splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2 and parts[0].strip():
                candidates.append(SongCandidate(self.provider, parts[0].strip(), parts[1].strip(), "song", native=("powershell", parts[0].strip(), artist)))
        return tuple(candidates)

    def _powershell_play(self, candidate: SongCandidate) -> bool:
        script = r'''
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root=[System.Windows.Automation.AutomationElement]::RootElement
$walker=[System.Windows.Automation.TreeWalker]::ControlViewWalker
function Desc($root) { @($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)) }
$win=$null
foreach($candidate in @($root.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition))) { if(($candidate.Current.Name -match $pattern) -or ($candidate.Current.ClassName -match $pattern)){ $win=$candidate; break } }
if($null -eq $win){ Write-Output "UI|root=1|window=0"; exit 10 }
$titleElement=Desc $win | Where-Object { $_.Current.Name -eq $title } | Select-Object -First 1
if($null -eq $titleElement){ Write-Output "UI|titleControl=0"; exit 11 }
$row=$titleElement
for($i=0;$i -lt 6;$i++){
  $children=Desc $row
  if(@($children | Where-Object { $_.Current.Name -eq $artist }).Count -gt 0){
    $button=$children | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.IsEnabled } | Select-Object -First 1
    if($null -ne $button){ try { $ip=$button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern); $ip.Invoke(); Write-Output ("PLAY|control=" + $button.Current.Name + "|title=" + $title + "|artist=" + $artist); 'PLAYED'; exit 0 } catch {} }
  }
  try { $row=$walker.GetParent($row) } catch { break }
}
exit 12
'''
        script = (
            "$pattern=" + self._ps_literal(self.window_pattern) + "; $title=" + self._ps_literal(candidate.title)
            + "; $artist=" + self._ps_literal(candidate.artist) + ";\n" + script
        )
        completed = self._run_powershell(script)
        self._log_powershell_output(completed.stdout, provider=self.provider, stage="play")
        return completed.returncode == 0 and "PLAYED" in str(completed.stdout or "")

    @staticmethod
    def _log_powershell_output(output: str | None, *, provider: str, stage: str) -> None:
        for line in str(output or "").splitlines():
            if line.startswith(("META|", "UI|", "PLAY|")):
                LOGGER.debug("music_playback ui_automation provider=%s stage=%s %s", provider, stage, line)

    @staticmethod
    def _ps_literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    @staticmethod
    def _run_powershell(script: str) -> subprocess.CompletedProcess:
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        try:
            kwargs = {
                "capture_output": True,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "timeout": 25,
                "check": False,
            }
            if sys.platform == "win32":
                # Do not flash a console window while the worker performs
                # the interactive client handoff.
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            return subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
                **kwargs,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UIAutomationUnavailableError("PowerShell UIAutomation request failed") from exc

    def _wait_for_window(self, auto):
        deadline = time.monotonic() + self.wait_seconds
        window = auto.WindowControl(searchDepth=1, RegexName=self.window_pattern)
        while time.monotonic() < deadline:
            try:
                if window.Exists(0, 0):
                    return window
            except TypeError:
                if window.Exists():
                    return window
            time.sleep(0.3)
        raise UIAutomationUnavailableError("player window not found")

    def _find_search_box(self, window):
        wanted = {_canonical(name) for name in self.search_names}
        fallback = None
        for control in self._walk(window, max_depth=7):
            if "Edit" not in str(getattr(control, "ControlTypeName", "")):
                continue
            fallback = fallback or control
            name = _canonical(str(getattr(control, "Name", "") or ""))
            if not wanted or any(item in name for item in wanted):
                return control
        return fallback

    def _select_song_tab(self, window) -> bool:
        wanted = {_canonical(name) for name in self.song_tab_names}
        deadline = time.monotonic() + min(4.0, self.wait_seconds)
        while time.monotonic() < deadline:
            for control in self._walk(window, max_depth=8):
                if _canonical(str(getattr(control, "Name", "") or "")) not in wanted:
                    continue
                try:
                    control.Click()
                    time.sleep(0.45)
                    return True
                except Exception:
                    continue
            time.sleep(0.25)
        return False

    def _song_candidates(self, window, title: str, artist: str) -> tuple[SongCandidate, ...]:
        candidates: list[SongCandidate] = []
        for control in self._walk(window, max_depth=10):
            name = str(getattr(control, "Name", "") or "")
            if title and not _same_song(name, title):
                continue
            if not title or self._matching_row(control, artist) is not None:
                row = self._matching_row(control, artist)
                if row is None:
                    continue
                resolved_title = name if title else self._title_from_row(row, artist)
                if resolved_title:
                    candidates.append(
                        SongCandidate(self.provider, resolved_title, artist, "song", native=control)
                    )
        unique: dict[tuple[str, str], SongCandidate] = {}
        for candidate in candidates:
            unique.setdefault((_canonical(candidate.title), _canonical(candidate.artist)), candidate)
        return tuple(unique.values())

    def _matching_row(self, control, artist: str):
        current = control
        for _ in range(6):
            try:
                current = current.GetParentControl()
            except Exception:
                return None
            if current is None:
                return None
            descendants = tuple(self._walk(current, max_depth=4))
            names = [str(getattr(item, "Name", "") or "") for item in descendants]
            nonempty = [name for name in names if name.strip()]
            if len(nonempty) <= 60 and any(_same_song(name, artist) for name in nonempty):
                return current
        return None

    def _title_from_row(self, row, artist: str) -> str:
        ignored = {
            _canonical(artist),
            *(_canonical(name) for name in self.song_tab_names),
            *(_canonical(name) for name in self.play_button_names),
            "歌曲",
            "song",
        }
        for control in self._walk(row, max_depth=4):
            name = str(getattr(control, "Name", "") or "").strip()
            canonical = _canonical(name)
            if canonical and canonical not in ignored and len(name) <= 80:
                return name
        return ""

    @staticmethod
    def _walk(root, *, max_depth: int):
        stack = [(root, 0)]
        seen: set[int] = set()
        while stack:
            control, depth = stack.pop()
            marker = id(control)
            if marker in seen:
                continue
            seen.add(marker)
            yield control
            if depth >= max_depth:
                continue
            try:
                children = tuple(control.GetChildren())
            except Exception:
                children = ()
            stack.extend((child, depth + 1) for child in reversed(children))

    def _custom_path(self) -> str:
        return {
            "qq": self.settings.qq_music_path,
            "netease": self.settings.netease_music_path,
            "kugou": self.settings.kugou_music_path,
            "apple": self.settings.apple_music_path,
            "spotify": self.settings.spotify_music_path,
        }.get(self.provider, "")


class QQMusicAdapter(WindowsUIAutomationAdapter):
    provider = "qq"
    window_pattern = ".*(QQ音乐|QQMusic).*"
    search_names = ("搜索音乐", "搜索", "Search")
    play_button_names = ("播放", "播放歌曲", "Play")


class NeteaseMusicAdapter(WindowsUIAutomationAdapter):
    """网易云 Adapter；新版 Chromium UI 不公开控件时使用真实桌面 DPI 回退。"""

    provider = "netease"
    window_pattern = ".*(网易云音乐|NetEase|CloudMusic).*"
    search_names = ("搜索", "搜索音乐、视频、播客、用户", "Search")
    play_button_names = ("播放", "播放全部", "Play")

    def play_random_artist(self, artist: str) -> bool:
        client = self.client_finder(self.provider, self._custom_path())
        if client is None:
            return False
        try:
            self.process_launcher(
                [str(client)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return False
        # 首条搜索建议会进入陈楚生专辑/歌曲列表；直接随机双击可见歌曲行，
        # 避免“下一首”逸出目标队列，也不依赖 Chromium 未公开的内部控件树。
        # The first click can arrive while the Chromium surface is still
        # loading. Retry the complete native action once, inside the worker
        # thread, so the user does not need to click Lili twice.
        for attempt in range(1, 3):
            random_index = random.randint(0, 4)
            try:
                completed = self._run_powershell(
                    self._netease_default_desktop_script(str(client), artist, random_index)
                )
            except UIAutomationUnavailableError as exc:
                LOGGER.debug(
                    "music_playback provider=%s stage=native_random attempt=%s error=%r",
                    self.provider,
                    attempt,
                    exc,
                )
                completed = None
            if completed is not None:
                self._log_powershell_output(
                    completed.stdout,
                    provider=self.provider,
                    stage=f"native_random_attempt_{attempt}",
                )
                if completed.returncode == 0 and "PLAYED|" in str(completed.stdout or ""):
                    return True
            if attempt < 2:
                time.sleep(0.65)
        return False

    @classmethod
    def _netease_default_desktop_script(
        cls,
        client: str,
        artist: str,
        random_index: int,
    ) -> str:
        # Windows 11/网易云 3.x 会把真正窗口放在用户的 Default Desktop；
        # 后台 Agent 或沙盒进程的 AutomationElement.RootElement 因此可能为空。
        query = "chenchusheng" if _canonical(artist) == _canonical("陈楚生") else artist
        return (
            "$exe=" + cls._ps_literal(client)
            + ";$artist=" + cls._ps_literal(artist)
            + ";$query=" + cls._ps_literal(query)
            + ";$pick=" + str(max(0, min(4, int(random_index))))
            + r''';
$source=@'
using System;
using System.Text;
using System.Runtime.InteropServices;
using System.Threading;
public static class LiliNeteaseDefaultDesktop {
  [DllImport("user32.dll", SetLastError=true)] static extern IntPtr OpenDesktop(string n,uint f,bool i,uint a);
  [DllImport("user32.dll", SetLastError=true)] static extern bool SetThreadDesktop(IntPtr h);
  [DllImport("user32.dll")] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr c);
  [DllImport("user32.dll")] static extern bool EnumDesktopWindows(IntPtr d, Callback c, IntPtr l);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h,out uint p);
  [DllImport("user32.dll")] static extern int GetClassName(IntPtr h,StringBuilder s,int n);
  [DllImport("user32.dll")] static extern int GetWindowText(IntPtr h,StringBuilder s,int n);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h,out Rect r);
  [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h,int c);
  [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h,IntPtr a,int x,int y,int w,int z,uint f);
  [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
  [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] static extern bool AttachThreadInput(uint a,uint b,bool attach);
  [DllImport("user32.dll")] static extern bool SetCursorPos(int x,int y);
  [DllImport("user32.dll")] static extern void mouse_event(uint f,uint x,uint y,uint d,UIntPtr e);
  [DllImport("user32.dll")] static extern void keybd_event(byte v,byte s,uint f,UIntPtr e);
  delegate bool Callback(IntPtr h,IntPtr l);
  struct Rect { public int L,T,R,B; }
  static void Key(byte v) { keybd_event(v,0,0,UIntPtr.Zero); keybd_event(v,0,2,UIntPtr.Zero); }
  static void Click(int x,int y) { SetCursorPos(x,y); mouse_event(2,0,0,0,UIntPtr.Zero); mouse_event(4,0,0,0,UIntPtr.Zero); }
  static string Title(IntPtr h) { var b=new StringBuilder(512); GetWindowText(h,b,b.Capacity); return b.ToString(); }
  public static string Run(string query,string artist,int pick) {
    string result="";
    var thread=new Thread(()=>{
      var desk=OpenDesktop("Default",0,false,0x01ff);
      if(desk==IntPtr.Zero || !SetThreadDesktop(desk)) { result="ERROR|desktop="+Marshal.GetLastWin32Error(); return; }
      SetThreadDpiAwarenessContext(new IntPtr(-4));
      IntPtr win=IntPtr.Zero;
      var deadline=DateTime.UtcNow.AddSeconds(12);
      while(win==IntPtr.Zero && DateTime.UtcNow<deadline) {
        EnumDesktopWindows(desk,(h,l)=>{
          uint pid; GetWindowThreadProcessId(h,out pid);
          var c=new StringBuilder(128); GetClassName(h,c,c.Capacity);
          if(IsWindowVisible(h) && c.ToString()=="OrpheusBrowserHost") { win=h; return false; }
          return true;
        },IntPtr.Zero);
        if(win==IntPtr.Zero) Thread.Sleep(350);
      }
      if(win==IntPtr.Zero) { result="ERROR|window=0"; return; }
      IntPtr previousWindow=GetForegroundWindow();
      uint targetPid, foregroundPid;
      uint targetThread=GetWindowThreadProcessId(win,out targetPid);
      uint foregroundThread=GetWindowThreadProcessId(GetForegroundWindow(),out foregroundPid);
      uint currentThread=GetCurrentThreadId();
      AttachThreadInput(currentThread,targetThread,true);
      AttachThreadInput(currentThread,foregroundThread,true);
      ShowWindow(win,9);
      // The player must be briefly active for keyboard/mouse input, but do
      // not make it topmost. The previous foreground app is restored after
      // the click sequence so the command behaves like a background action.
      SetWindowPos(win,IntPtr.Zero,0,0,0,0,0x17);
      SetForegroundWindow(win);
      Thread.Sleep(600);
      Rect r; if(!GetWindowRect(win,out r)) { result="ERROR|rect=0"; return; }
      int width=r.R-r.L, height=r.B-r.T;
      // 搜索框和歌曲卡片均按窗口物理像素比例定位，兼容 125%/150% 高分屏。
      Click(r.L+(int)(width*0.385), r.T+(int)(height*0.049));
      Thread.Sleep(250);
      keybd_event(0x11,0,0,UIntPtr.Zero); Key(0x41); keybd_event(0x11,0,2,UIntPtr.Zero); Key(0x08);
      foreach(char ch in query) Key((byte)Char.ToUpperInvariant(ch));
      // 第一次回车展示搜索建议，第二次回车选中首条歌手建议并进入结果页。
      Key(0x0d); Thread.Sleep(1100); Key(0x0d); Thread.Sleep(4800);
      // 当前客户端首条建议为陈楚生专辑；每个可见歌曲行都明确标有陈楚生。
      double[] songRows={0.514,0.589,0.665,0.740,0.816};
      int songX=r.L+(int)(width*0.32);
      int songY=r.T+(int)(height*songRows[pick]);
      // 点击动作只负责把客户端带到播放状态；窗口标题不能可靠地代表当前歌曲。
      // 播放是否真正启动由 Python 侧的目标 GSMTC Session 轮询确认。
      Click(songX,songY); Thread.Sleep(120); Click(songX,songY); Thread.Sleep(900);
      string title=Title(win);
      bool titleArtistMatch=title.IndexOf(artist,StringComparison.OrdinalIgnoreCase)>=0;
      SetWindowPos(win,IntPtr.Zero,0,0,0,0,0x17);
      AttachThreadInput(currentThread,targetThread,false);
      AttachThreadInput(currentThread,foregroundThread,false);
      bool restored=false;
      if(previousWindow!=IntPtr.Zero && previousWindow!=win) {
        uint previousPid;
        uint previousThread=GetWindowThreadProcessId(previousWindow,out previousPid);
        AttachThreadInput(currentThread,previousThread,true);
        restored=SetForegroundWindow(previousWindow);
        AttachThreadInput(currentThread,previousThread,false);
      }
      result="PLAYED|pid_window="+win.ToInt64()+"|title="+title+
        "|titleArtistMatch="+titleArtistMatch+"|restoredForeground="+restored+
        "|width="+width+"|height="+height+"|pick="+pick;
    });
    thread.SetApartmentState(ApartmentState.STA);
    thread.Start(); thread.Join(30000);
    return result;
  }
}
'@;
Add-Type $source
$result=[LiliNeteaseDefaultDesktop]::Run($query,$artist,$pick)
Write-Output $result
if($result -notlike 'PLAYED|*'){ exit 14 }
'''
        )


class KugouMusicAdapter(WindowsUIAutomationAdapter):
    provider = "kugou"
    window_pattern = ".*(酷狗音乐|KuGou|KGMusic).*"
    search_names = ("搜索", "搜索音乐", "Search")
    play_button_names = ("播放", "立即播放", "Play")


class AppleMusicWindowsAdapter(WindowsUIAutomationAdapter):
    provider = "apple"
    window_pattern = ".*(Apple Music|音乐).*"
    search_names = ("搜索", "Search")
    play_button_names = ("播放", "播放歌曲", "Play", "Play song")


class SpotifyWindowsAdapter(WindowsUIAutomationAdapter):
    provider = "spotify"
    window_pattern = ".*Spotify.*"
    search_names = ("搜索", "Search")
    song_tab_names = ("歌曲", "Songs", "Tracks")
    play_button_names = ("播放", "播放歌曲", "Play", "Play song")


def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


class AppleMusicMacAdapter:
    """通过 Apple Events 在用户音乐资料库中搜索并播放精确歌曲。"""

    provider = "apple"

    def __init__(self, command_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
        self.command_runner = command_runner

    def search(self, title: str, artist: str) -> Sequence[SongCandidate]:
        query = _escape_applescript(f"{artist} {title}".strip())
        script = (
            'tell application "Music"\n'
            f'set foundTracks to search library playlist 1 for "{query}" only songs\n'
            'set outputText to ""\n'
            'repeat with candidateTrack in foundTracks\n'
            'set outputText to outputText & (name of candidateTrack as text) & tab & '
            '(artist of candidateTrack as text) & tab & (persistent ID of candidateTrack as text) & linefeed\n'
            'end repeat\nreturn outputText\nend tell'
        )
        completed = self._run(script)
        candidates: list[SongCandidate] = []
        for line in str(completed.stdout or "").splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                candidates.append(SongCandidate(self.provider, parts[0], parts[1], "song", parts[2]))
        return candidates

    def play(self, candidate: SongCandidate) -> bool:
        identifier = _escape_applescript(candidate.identifier)
        if not identifier:
            return False
        script = (
            'tell application "Music"\n'
            f'set targetTrack to first track of library playlist 1 whose persistent ID is "{identifier}"\n'
            'play targetTrack\nend tell'
        )
        try:
            self._run(script)
        except ProviderSearchError:
            return False
        return True

    def _run(self, script: str) -> subprocess.CompletedProcess:
        try:
            completed = self.command_runner(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProviderSearchError("Apple Events request failed") from exc
        if completed.returncode != 0:
            raise ProviderSearchError(str(completed.stderr or "Apple Events request failed"))
        return completed


class MacAccessibilityAdapter:
    """在已授权 Accessibility 时只点击同时包含精确歌名和歌手的歌曲结果。"""

    provider = ""
    application_name = ""
    song_tab_names: tuple[str, ...] = ("歌曲", "Songs", "Tracks")
    play_button_names: tuple[str, ...] = ("播放", "播放歌曲", "Play", "Play song")

    def __init__(self, command_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
        self.command_runner = command_runner

    def search(self, title: str, artist: str) -> Sequence[SongCandidate]:
        query = urllib.parse.quote(f"{artist} {title}")
        uri = self._search_uri(query)
        try:
            opened = self.command_runner(
                ["open", "-a", self.application_name, uri],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProviderSearchError("client search could not be opened") from exc
        if opened.returncode != 0:
            raise ProviderSearchError(str(opened.stderr or "client search could not be opened"))
        if not title:
            completed = self._run_accessibility(self._artist_candidates_script(artist))
            candidates: list[SongCandidate] = []
            for line in str(completed.stdout or "").splitlines():
                parts = line.split("\t")
                if len(parts) >= 2 and parts[0].strip() and _same_song(parts[1], artist):
                    candidates.append(SongCandidate(self.provider, parts[0], parts[1], "song"))
            return candidates
        script = self._accessibility_script(title, artist, play=False)
        completed = self._run_accessibility(script)
        if str(completed.stdout or "").strip() == "MATCH":
            return (SongCandidate(self.provider, title, artist, "song", f"{title}\t{artist}"),)
        return ()

    def play(self, candidate: SongCandidate) -> bool:
        completed = self._run_accessibility(
            self._accessibility_script(candidate.title, candidate.artist, play=True)
        )
        return str(completed.stdout or "").strip() == "PLAYED"

    def _run_accessibility(self, script: str) -> subprocess.CompletedProcess:
        try:
            completed = self.command_runner(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProviderSearchError("Accessibility request failed") from exc
        if completed.returncode != 0:
            raise ProviderSearchError(str(completed.stderr or "Accessibility request failed"))
        return completed

    def _accessibility_script(self, title: str, artist: str, *, play: bool) -> str:
        safe_app = _escape_applescript(self.application_name)
        safe_title = _escape_applescript(title)
        safe_artist = _escape_applescript(artist)
        tabs = ", ".join(f'"{_escape_applescript(name)}"' for name in self.song_tab_names)
        buttons = ", ".join(f'"{_escape_applescript(name)}"' for name in self.play_button_names)
        action = "true" if play else "false"
        return f'''
tell application "{safe_app}" to activate
delay 1.5
tell application "System Events"
  if UI elements enabled is false then error "Accessibility permission required"
  tell process "{safe_app}"
    set frontmost to true
    set tabNames to {{{tabs}}}
    set playNames to {{{buttons}}}
    try
      repeat with itemRef in (entire contents of front window)
        try
          if (name of itemRef as text) is in tabNames then click itemRef
        end try
      end repeat
    end try
    delay 1.0
    repeat with titleElement in (entire contents of front window)
      try
        if (name of titleElement as text) is "{safe_title}" then
          set rowElement to parent of titleElement
          repeat 6 times
            set artistFound to false
            repeat with childElement in (entire contents of rowElement)
              try
                if (name of childElement as text) is "{safe_artist}" then set artistFound to true
              end try
            end repeat
            if artistFound then
              if {action} then
                repeat with childElement in (entire contents of rowElement)
                  try
                    if (name of childElement as text) is in playNames then
                      click childElement
                      return "PLAYED"
                    end if
                  end try
                end repeat
                return "MATCH_NO_PLAY_BUTTON"
              end if
              return "MATCH"
            end if
            set rowElement to parent of rowElement
          end repeat
        end if
      end try
    end repeat
  end tell
end tell
return "NO_MATCH"
'''

    def _artist_candidates_script(self, artist: str) -> str:
        """在歌曲标签页中读取歌手精确匹配行的歌名，不点击随机页面元素。"""

        safe_app = _escape_applescript(self.application_name)
        safe_artist = _escape_applescript(artist)
        tabs = ", ".join(f'"{_escape_applescript(name)}"' for name in self.song_tab_names)
        buttons = ", ".join(f'"{_escape_applescript(name)}"' for name in self.play_button_names)
        return f'''
tell application "{safe_app}" to activate
delay 1.5
tell application "System Events"
  if UI elements enabled is false then error "Accessibility permission required"
  tell process "{safe_app}"
    set tabNames to {{{tabs}}}
    set playNames to {{{buttons}}}
    repeat with itemRef in (entire contents of front window)
      try
        if (name of itemRef as text) is in tabNames then click itemRef
      end try
    end repeat
    delay 1.0
    set outputText to ""
    repeat with artistElement in (entire contents of front window)
      try
        if (name of artistElement as text) is "{safe_artist}" then
          set rowElement to parent of artistElement
          repeat 6 times
            set titleText to ""
            repeat with childElement in (entire contents of rowElement)
              try
                set childName to name of childElement as text
                if childName is not "" and childName is not "{safe_artist}" and childName is not in tabNames and childName is not in playNames then
                  set titleText to childName
                  exit repeat
                end if
              end try
            end repeat
            if titleText is not "" then
              set outputText to outputText & titleText & tab & "{safe_artist}" & linefeed
              exit repeat
            end if
            set rowElement to parent of rowElement
          end repeat
        end if
      end try
    end repeat
    return outputText
  end tell
end tell
'''

    def _search_uri(self, query: str) -> str:
        raise NotImplementedError


class QQMusicMacAdapter(MacAccessibilityAdapter):
    provider = "qq"
    application_name = "QQMusic"
    play_button_names = ("播放", "播放歌曲", "Play")

    def _search_uri(self, query: str) -> str:
        return f"https://y.qq.com/n/ryqq/search?w={query}&t=song"


class NeteaseMusicMacAdapter(MacAccessibilityAdapter):
    provider = "netease"
    application_name = "NeteaseMusic"
    play_button_names = ("播放", "立即播放", "Play")

    def _search_uri(self, query: str) -> str:
        return f"https://music.163.com/#/search/m/?s={query}&type=1"


class KugouMusicMacAdapter(MacAccessibilityAdapter):
    provider = "kugou"
    application_name = "KugouMusic"
    play_button_names = ("播放", "立即播放", "Play")

    def _search_uri(self, query: str) -> str:
        return f"https://www.kugou.com/yy/html/search.html#searchType=song&searchKeyWord={query}"


class SpotifyMacAdapter(MacAccessibilityAdapter):
    provider = "spotify"
    application_name = "Spotify"
    song_tab_names = ("歌曲", "Songs", "Tracks")
    play_button_names = ("播放", "播放歌曲", "Play", "Play song")

    def _search_uri(self, query: str) -> str:
        return f"spotify:search:{query}"


def build_provider_adapters(
    settings: PetSettings,
    *,
    platform_name: str | None = None,
    client_finder: Callable[[str, str], Path | None] = find_music_client,
    command_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict[str, MusicProviderAdapter]:
    """按当前系统构造五个彼此独立的 Provider Adapter。"""

    platform = platform_name or sys.platform
    if platform == "win32":
        return {
            "qq": QQMusicAdapter(settings, client_finder=client_finder),
            "netease": NeteaseMusicAdapter(settings, client_finder=client_finder),
            "kugou": KugouMusicAdapter(settings, client_finder=client_finder),
            "apple": AppleMusicWindowsAdapter(settings, client_finder=client_finder),
            "spotify": SpotifyWindowsAdapter(settings, client_finder=client_finder),
        }
    if platform == "darwin":
        return {
            "qq": QQMusicMacAdapter(command_runner),
            "netease": NeteaseMusicMacAdapter(command_runner),
            "kugou": KugouMusicMacAdapter(command_runner),
            "apple": AppleMusicMacAdapter(command_runner),
            "spotify": SpotifyMacAdapter(command_runner),
        }
    return {}


def provider_label(provider: str) -> str:
    """为日志工具和调试面板返回稳定的平台名称。"""

    return MUSIC_SERVICE_LABELS.get(provider, provider)
