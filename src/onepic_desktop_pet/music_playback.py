"""实现指定歌曲的精确搜索、结果筛选、播放动作与媒体信息校验闭环。

每个音乐平台拥有独立 Provider Adapter。搜索成功、打开客户端或触发控件都不等于
播放成功；只有当前媒体标题和歌手与最终选中的歌曲同时匹配时才返回成功。Windows
Adapter 使用 UI Automation 定位“歌曲”结果，macOS 优先使用 Apple Events，并在
需要时使用已授权的 Accessibility。任何无法确认的情况都会返回明确失败码。
"""

from __future__ import annotations

import logging
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
    RESULT_NOT_FOUND = "RESULT_NOT_FOUND"
    PLAY_ACTION_FAILED = "PLAY_ACTION_FAILED"
    MEDIA_SESSION_TIMEOUT = "MEDIA_SESSION_TIMEOUT"
    TRACK_VERIFY_FAILED = "TRACK_VERIFY_FAILED"


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
    """一次完整点歌闭环的结果；成功必然意味着媒体信息已通过校验。"""

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


class ProviderSearchError(RuntimeError):
    """Provider 无法完成搜索或读取结果时使用的内部异常。"""


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
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.adapters = dict(adapters)
        self.track_reader = track_reader
        self.random_source = random_source or random.Random()
        self.sleep = sleep

    def play_random_artist(self, provider: str, artist: str) -> SongPlaybackResult:
        adapter = self.adapters.get(provider)
        title = ""
        self._debug("search", provider, title, artist)
        if adapter is None:
            return self._failed(provider, artist, MusicPlaybackError.SEARCH_FAILED)
        try:
            candidates = tuple(adapter.search("", artist))
        except Exception as exc:
            self._debug("search_failed", provider, title, artist, error=repr(exc))
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
            native_random = getattr(adapter, "play_random_artist", None)
            if callable(native_random):
                try:
                    if bool(native_random(artist)):
                        self._debug("started", provider, title, artist, selected_type="native_random")
                        return SongPlaybackResult(
                            True,
                            provider,
                            title,
                            artist,
                            f"正在播放{artist}的随机歌曲",
                            play_attempts=1,
                        )
                except Exception as exc:
                    self._debug("play_exception", provider, title, artist, error=repr(exc))
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
        try:
            played = bool(adapter.play(selected))
        except Exception as exc:
            self._debug("play_exception", provider, title, artist, error=repr(exc))
            played = False
        if not played:
            self._debug("play_action_failed", provider, title, artist)
            return self._failed(provider, artist, MusicPlaybackError.PLAY_ACTION_FAILED, selected=selected)

        current_title = current_artist = ""
        if self.track_reader is not None:
            try:
                self.sleep(0.25)
                current = self.track_reader(provider)
                if current is not None:
                    current_title = str(getattr(current, "title", "") or "")
                    current_artist = str(getattr(current, "artist", "") or "")
            except Exception as exc:
                self._debug("media_read_error", provider, title, artist, error=repr(exc))
        self._debug(
            "started",
            provider,
            title,
            artist,
            selected_title=selected.title,
            selected_artist=selected.artist,
            current_title=current_title,
            current_artist=current_artist,
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
            play_attempts=1,
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
    ) -> SongPlaybackResult:
        return SongPlaybackResult(
            False,
            provider,
            "",
            artist,
            _failure_message(code, random_artist=True),
            code,
            selected,
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
            raise ProviderSearchError("search box not exposed through UI Automation")
        try:
            search_box.SetFocus()
            search_box.SendKeys("{Ctrl}a{Del}")
            search_box.SendKeys(f"{artist} {title}".strip())
            search_box.SendKeys("{Enter}")
        except Exception as exc:
            raise ProviderSearchError("search input failed") from exc
        if not self._select_song_tab(window) and title:
            raise ProviderSearchError("song results tab not found")
        deadline = time.monotonic() + self.wait_seconds
        while time.monotonic() < deadline:
            candidates = self._song_candidates(window, title, artist)
            if candidates:
                return candidates
            time.sleep(0.35)
        return ()

    def play(self, candidate: SongCandidate) -> bool:
        control = candidate.native
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
            raise ProviderSearchError("uiautomation is unavailable") from exc
        return auto

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
        raise ProviderSearchError("player window not found")

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
    provider = "netease"
    window_pattern = ".*(网易云音乐|NetEase|CloudMusic).*"
    search_names = ("搜索", "搜索音乐、视频、播客、用户", "Search")
    play_button_names = ("播放", "播放全部", "Play")


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
