"""ÂÆûÁé∞ÊåáÂÆöÊ≠åÊõ≤ÁöÑÁ≤æÁ°ÆÊêúÁ¥¢„ÄÅÁªìÊûúÁ≠õÈÄâ„ÄÅÊí≠ÊîæÂä®‰Ωú‰∏éÂ™í‰Ωì‰ø°ÊÅØÊ†°È™åÈó≠ÁéØ„ÄÇ

ÊØè‰∏™Èü≥‰πêÂπ≥Âè∞Êã•ÊúâÁã¨Á´ã Provider Adapter„ÄÇÊêúÁ¥¢ÊàêÂäü„ÄÅÊâìÂºÄÂÆ¢Êà∑Á´ØÊàñËß¶ÂèëÊéß‰ª∂ÈÉΩ‰∏çÁ≠â‰∫é
Êí≠ÊîæÊàêÂäüÔºõÂè™ÊúâÂΩìÂâçÂ™í‰ΩìÊ†áÈ¢òÂíåÊ≠åÊâã‰∏éÊúÄÁªàÈÄâ‰∏≠ÁöÑÊ≠åÊõ≤ÂêåÊó∂ÂåπÈÖçÊó∂ÊâçËøîÂõûÊàêÂäü„ÄÇWindows
Adapter ‰ΩøÁî® UI Automation ÂÆö‰Ωç‚ÄúÊ≠åÊõ≤‚ÄùÁªìÊûúÔºåmacOS ‰ºòÂÖà‰ΩøÁî® Apple EventsÔºåÂπ∂Âú®
ÈúÄË¶ÅÊó∂‰ΩøÁî®Â∑≤ÊéàÊùÉÁöÑ Accessibility„ÄÇ‰ªª‰ΩïÊó†Ê≥ïÁ°ÆËÆ§ÁöÑÊÉÖÂÜµÈÉΩ‰ºöËøîÂõûÊòéÁ°ÆÂ§±Ë¥•Á†Å„ÄÇ
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
    """ÁÇπÊ≠åÈó≠ÁéØ‰∏≠ÂèØ‰ª•Ë¢´Êó•Âøó„ÄÅÊµãËØïÂíåÁïåÈù¢Á®≥ÂÆöËØÜÂà´ÁöÑÂ§±Ë¥•Èò∂ÊÆµ„ÄÇ"""

    SEARCH_FAILED = "SEARCH_FAILED"
    RESULT_NOT_FOUND = "RESULT_NOT_FOUND"
    PLAY_ACTION_FAILED = "PLAY_ACTION_FAILED"
    MEDIA_SESSION_TIMEOUT = "MEDIA_SESSION_TIMEOUT"
    TRACK_VERIFY_FAILED = "TRACK_VERIFY_FAILED"


class TrackSnapshot(Protocol):
    """Ê†°È™åÂô®ÊâÄÈúÄÁöÑÊúÄÂ∞èÂΩìÂâçÊ≠åÊõ≤‰ø°ÊÅØ„ÄÇ"""

    title: str
    artist: str


@dataclass(frozen=True)
class SongCandidate:
    """Provider Adapter ‰ªé‚ÄúÊ≠åÊõ≤‚ÄùÁªìÊûú‰∏≠ËØªÂèñÂà∞ÁöÑ‰∏Ä‰∏™ÂÄôÈÄâÈ°π„ÄÇ"""

    provider: str
    title: str
    artist: str
    result_type: str = "song"
    identifier: str = ""
    native: object | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class SongPlaybackResult:
    """‰∏ÄÊ¨°ÂÆåÊï¥ÁÇπÊ≠åÈó≠ÁéØÁöÑÁªìÊûúÔºõÊàêÂäüÂøÖÁÑ∂ÊÑèÂë≥ÁùÄÂ™í‰Ωì‰ø°ÊÅØÂ∑≤ÈÄöËøáÊ†°È™å„ÄÇ"""

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
    """Provider Êó†Ê≥ïÂÆåÊàêÊêúÁ¥¢ÊàñËØªÂèñÁªìÊûúÊó∂‰ΩøÁî®ÁöÑÂÜÖÈÉ®ÂºÇÂ∏∏„ÄÇ"""


class MusicProviderAdapter(Protocol):
    """ÂêÑÈü≥‰πêÂÆ¢Êà∑Á´ØÂøÖÈ°ªÁã¨Á´ãÂÆûÁé∞ÁöÑÊúÄÂ∞èÁÇπÊ≠åÂçèËÆÆ„ÄÇ"""

    provider: str

    def search(self, title: str, artist: str) -> Sequence[SongCandidate]: ...

    def play(self, candidate: SongCandidate) -> bool: ...


def _canonical(value: str) -> str:
    """Áî®‰∫é‰∏•Ê†ºÂåπÈÖçÁöÑ Unicode ËßÑËåÉÂΩ¢ÂºèÔºå‰øùÁïôÁâàÊú¨ÂêéÁºÄ‰ª•ÊãíÁªù Live/Remix„ÄÇ"""

    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _same_song(left: str, right: str) -> bool:
    return bool(_canonical(left)) and _canonical(left) == _canonical(right)


def _failure_message(code: MusicPlaybackError, *, random_artist: bool = False) -> str:
    messages = {
        MusicPlaybackError.SEARCH_FAILED: "Ê≠åÊõ≤ÊêúÁ¥¢Â§±Ë¥•ÔºåËØ∑Á°ÆËÆ§Êí≠ÊîæÂô®Ê≠£Âú®ËøêË°åÂπ∂ÂÖÅËÆ∏ËæÖÂä©ÂäüËÉΩ„ÄÇ",
        MusicPlaybackError.RESULT_NOT_FOUND: (
            "Ê≤°ÊúâÊâæÂà∞Ëøô‰ΩçÊ≠åÊâãÁöÑÊ≠åÊõ≤„ÄÇ" if random_artist else "Ê≤°ÊúâÊâæÂà∞ËøôÈ¶ñÊ≠å„ÄÇ"
        ),
        MusicPlaybackError.PLAY_ACTION_FAILED: "Â∑≤ÊâæÂà∞Ê≠åÊõ≤Ôºå‰ΩÜÊí≠ÊîæÂô®Ê≤°ÊúâÂºÄÂßãÊí≠Êîæ„ÄÇ",
        MusicPlaybackError.MEDIA_SESSION_TIMEOUT: "Êí≠ÊîæÂô®Ê≤°ÊúâËøîÂõûÂΩìÂâçÊ≠åÊõ≤‰ø°ÊÅØÔºåÊöÇÊó∂Êó†Ê≥ïÁ°ÆËÆ§ÊòØÂê¶Êí≠ÊîæÊàêÂäü„ÄÇ",
        MusicPlaybackError.TRACK_VERIFY_FAILED: "ÂÆûÈôÖÊí≠ÊîæÁöÑ‰∏çÊòØÁõÆÊ†áÊ≠åÊõ≤ÔºåÂ∑≤ÂÅúÊ≠¢ÈáçËØï„ÄÇ",
    }
    return messages[code]


class ExactMusicPlaybackManager:
    """ÊâßË°å search ‚Üí exact match ‚Üí play ‚Üí verifyÔºåÊúÄÂ§öÈáçËØï‰∏ÄÊ¨°Á≤æÁ°ÆÊí≠ÊîæÂä®‰Ωú„ÄÇ"""

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
                    f"Ê≠£Âú®Êí≠ÊîæÔºö{selected.artist}„Ää{selected.title}„Äã",
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


class WindowsUIAutomationAdapter:
    """Windows ÂÆ¢Êà∑Á´Ø Adapter Âü∫Á±ªÔºõÂ≠êÁ±ªÂøÖÈ°ªÊèê‰æõËá™Ë∫´Á™óÂè£„ÄÅÊêúÁ¥¢Ê°ÜÂíåÊí≠ÊîæÊåâÈíÆËØ≠‰πâ„ÄÇ"""

    provider = ""
    window_pattern = ".*"
    search_names: tuple[str, ...] = ()
    song_tab_names: tuple[str, ...] = ("Ê≠åÊõ≤", "Songs", "Tracks")
    play_button_names: tuple[str, ...] = ("Êí≠Êîæ", "Êí≠ÊîæÊ≠åÊõ≤", "Play", "Play song")

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
        if not self._select_song_tab(window):
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
                    ret◊M∂∂âûÀk∫wµÁAëïòÅ}—•—±ï}ô…Ωµ}…Ω‹°Õï±ò∞Å…Ω‹∞ÅÖ…—•Õ–ËÅÕ—»§Ä¥¯ÅÕ—»Ë(ÄÄÄÄÄÄÄÅ•ùπΩ…ïêÄÙÅÏ(ÄÄÄÄÄÄÄÄÄÄÄÅ}çÖπΩπ•çÖ∞°Ö…—•Õ–§∞(ÄÄÄÄÄÄÄÄÄÄÄÄ®°}çÖπΩπ•çÖ∞°πÖµî§ÅôΩ»ÅπÖµîÅ•∏ÅÕï±òπÕΩπù}—Öâ}πÖµïÃ§∞(ÄÄÄÄÄÄÄÄÄÄÄÄ®°}çÖπΩπ•çÖ∞°πÖµî§ÅôΩ»ÅπÖµîÅ•∏ÅÕï±òπ¡±ÖÂ}â’——Ωπ}πÖµïÃ§∞(ÄÄÄÄÄÄÄÄÄÄÄÄãö∂3ön»à∞(ÄÄÄÄÄÄÄÄÄÄÄÄâÕΩπúà∞(ÄÄÄÄÄÄÄÅÙ(ÄÄÄÄÄÄÄÅôΩ»ÅçΩπ—…Ω∞Å•∏ÅÕï±òπ}›Ö±¨°…Ω‹∞ÅµÖ·}ëï¡—†Ù–§Ë(ÄÄÄÄÄÄÄÄÄÄÄÅπÖµîÄÙÅÕ—»°ùï—Ö——»°çΩπ—…Ω∞∞Äâ9Öµîà∞Äàà§ÅΩ»Äàà§πÕ—…•¿†§(ÄÄÄÄÄÄÄÄÄÄÄÅçÖπΩπ•çÖ∞ÄÙÅ}çÖπΩπ•çÖ∞°πÖµî§(ÄÄÄÄÄÄÄÄÄÄÄÅ•òÅçÖπΩπ•çÖ∞ÅÖπêÅçÖπΩπ•çÖ∞ÅπΩ–Å•∏Å•ùπΩ…ïêÅÖπêÅ±ï∏°πÖµî§ÄÙÄ‡¿Ë(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ…ï—’…∏ÅπÖµî(ÄÄÄÄÄÄÄÅ…ï—’…∏Äàà((ÄÄÄÅÕ—Ö—•çµï—°Ωê(ÄÄÄÅëïòÅ}›Ö±¨°…ΩΩ–∞Ä®∞ÅµÖ·}ëï¡—†ËÅ•π–§Ë(ÄÄÄÄÄÄÄÅÕ—Öç¨ÄÙÅl°…ΩΩ–∞Ä¿•t(ÄÄÄÄÄÄÄÅÕïï∏ËÅÕï—m•π—tÄÙÅÕï–†§(ÄÄÄÄÄÄÄÅ›°•±îÅÕ—Öç¨Ë(ÄÄÄÄÄÄÄÄÄÄÄÅçΩπ—…Ω∞∞Åëï¡—†ÄÙÅÕ—Öç¨π¡Ω¿†§(ÄÄÄÄÄÄÄÄÄÄÄÅµÖ…≠ï»ÄÙÅ•ê°çΩπ—…Ω∞§(ÄÄÄÄÄÄÄÄÄÄÄÅ•òÅµÖ…≠ï»Å•∏ÅÕïï∏Ë(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅçΩπ—•π’î(ÄÄÄÄÄÄÄÄÄÄÄÅÕïï∏πÖëê°µÖ…≠ï»§(ÄÄÄÄÄÄÄÄÄÄÄÅÂ•ï±êÅçΩπ—…Ω∞(ÄÄÄÄÄÄÄÄÄÄÄÅ•òÅëï¡—†Ä¯ÙÅµÖ·}ëï¡—†Ë(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅçΩπ—•π’î(ÄÄÄÄÄÄÄÄÄÄÄÅ—…‰Ë(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅç°•±ë…ï∏ÄÙÅ—’¡±î°çΩπ—…Ω∞πï—°•±ë…ï∏†§§(ÄÄÄÄÄÄÄÄÄÄÄÅï·çï¡–Å·çï¡—•Ω∏Ë(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅç°•±ë…ï∏ÄÙÄ†§(ÄÄÄÄÄÄÄÄÄÄÄÅÕ—Öç¨πï·—ïπê†°ç°•±ê∞Åëï¡—†Ä¨Äƒ§ÅôΩ»Åç°•±êÅ•∏Å…ïŸï…Õïê°ç°•±ë…ï∏§§((ÄÄÄÅëïòÅ}ç’Õ—Ωµ}¡Ö—†°Õï±ò§Ä¥¯ÅÕ—»Ë(ÄÄÄÄÄÄÄÅ…ï—’…∏ÅÏ(ÄÄÄÄÄÄÄÄÄÄÄÄâ≈ƒàËÅÕï±òπÕï——•πùÃπ≈≈}µ’Õ•ç}¡Ö—†∞(ÄÄÄÄÄÄÄÄÄÄÄÄâπï—ïÖÕîàËÅÕï±òπÕï——•πùÃππï—ïÖÕï}µ’Õ•ç}¡Ö—†∞(ÄÄÄÄÄÄÄÄÄÄÄÄâ≠’ùΩ‘àËÅÕï±òπÕï——•πùÃπ≠’ùΩ’}µ’Õ•ç}¡Ö—†∞(ÄÄÄÄÄÄÄÄÄÄÄÄâÖ¡¡±îàËÅÕï±òπÕï——•πùÃπÖ¡¡±ï}µ’Õ•ç}¡Ö—†∞(ÄÄÄÄÄÄÄÄÄÄÄÄâÕ¡Ω—•ô‰àËÅÕï±òπÕï——•πùÃπÕ¡Ω—•ôÂ}µ’Õ•ç}¡Ö—†∞(ÄÄÄÄÄÄÄÅÙπùï–°Õï±òπ¡…ΩŸ•ëï»∞Äàà§(()ç±ÖÕÃÅEE5’Õ•çëÖ¡—ï»°]•πëΩ›ÕU%’—ΩµÖ—•ΩπëÖ¡—ï»§Ë(ÄÄÄÅ¡…ΩŸ•ëï»ÄÙÄâ≈ƒà(ÄÄÄÅ›•πëΩ›}¡Ö——ï…∏ÄÙÄà∏®°EG¶~œíÊAÒEE5’Õ•å§∏®à(ÄÄÄÅÕïÖ…ç°}πÖµïÃÄÙÄ†ãöBsû“ã¶~œíÊ@à∞ÄãöBsû“àà∞ÄâMïÖ…ç†à§(ÄÄÄÅ¡±ÖÂ}â’——Ωπ}πÖµïÃÄÙÄ†ãöJ∑öR¯à∞ÄãöJ∑öR˚ö∂3ön»à∞ÄâA±Ö‰à§(()ç±ÖÕÃÅ9ï—ïÖÕï5’Õ•çëÖ¡—ï»°]•πëΩ›ÕU%’—ΩµÖ—•ΩπëÖ¡—ï»§Ë(ÄÄÄÅ¡…ΩŸ•ëï»ÄÙÄâπï—ïÖÕîà(ÄÄÄÅ›•πëΩ›}¡Ö——ï…∏ÄÙÄà∏®£ûˆGöbOíÍG¶~œíÊAÒ9ï—ÖÕïÒ±Ω’ë5’Õ•å§∏®à(ÄÄÄÅÕïÖ…ç°}πÖµïÃÄÙÄ†ãöBsû“àà∞ÄãöBsû“ã¶~œíÊCé¢û¶äGéöJ∑ñ∫ãéûR£ö"‹à∞ÄâMïÖ…ç†à§(ÄÄÄÅ¡±ÖÂ}â’——Ωπ}πÖµïÃÄÙÄ†ãöJ∑öR¯à∞ÄãöJ∑öR˚ñ£¶†à∞ÄâA±Ö‰à§(()ç±ÖÕÃÅ-’ùΩ’5’Õ•çëÖ¡—ï»°]•πëΩ›ÕU%’—ΩµÖ—•ΩπëÖ¡—ï»§Ë(ÄÄÄÅ¡…ΩŸ•ëï»ÄÙÄâ≠’ùΩ‘à(ÄÄÄÅ›•πëΩ›}¡Ö——ï…∏ÄÙÄà∏®£¶ﬂû._¶~œíÊAÒ-’Ω’Ò-5’Õ•å§∏®à(ÄÄÄÅÕïÖ…ç°}πÖµïÃÄÙÄ†ãöBsû“àà∞ÄãöBsû“ã¶~œíÊ@à∞ÄâMïÖ…ç†à§(ÄÄÄÅ¡±ÖÂ}â’——Ωπ}πÖµïÃÄÙÄ†ãöJ∑öR¯à∞ÄãûÆ/ñ6œöJ∑öR¯à∞ÄâA±Ö‰à§(()ç±ÖÕÃÅ¡¡±ï5’Õ•ç]•πëΩ›ÕëÖ¡—ï»°]•πëΩ›ÕU%’—ΩµÖ—•ΩπëÖ¡—ï»§Ë(ÄÄÄÅ¡…ΩŸ•ëï»ÄÙÄâÖ¡¡±îà(ÄÄÄÅ›•πëΩ›}¡Ö——ï…∏ÄÙÄà∏®°¡¡±îÅ5’Õ•çÛ¶~œíÊ@§∏®à(ÄÄÄÅÕïÖ…ç°}πÖµïÃÄÙÄ†ãöBsû“àà∞ÄâMïÖ…ç†à§(ÄÄÄÅ¡±ÖÂ}â’——Ωπ}πÖµïÃÄÙÄ†ãöJ∑öR¯à∞ÄãöJ∑öR˚ö∂3ön»à∞ÄâA±Ö‰à∞ÄâA±Ö‰ÅÕΩπúà§(()ç±ÖÕÃÅM¡Ω—•ôÂ]•πëΩ›ÕëÖ¡—ï»°]•πëΩ›ÕU%’—ΩµÖ—•ΩπëÖ¡—ï»§Ë(ÄÄÄÅ¡…ΩŸ•ëï»ÄÙÄâÕ¡Ω—•ô‰à(ÄÄÄÅ›•πëΩ›}¡Ö——ï…∏ÄÙÄà∏©M¡Ω—•ô‰∏®à(ÄÄÄÅÕïÖ…ç°}πÖµïÃÄÙÄ†ãöBsû“àà∞ÄâMïÖ…ç†à§(ÄÄÄÅÕΩπù}—Öâ}πÖµïÃÄÙÄ†ãö∂3ön»à∞ÄâMΩπùÃà∞ÄâQ…Öç≠Ãà§(ÄÄÄÅ¡±ÖÂ}â’——Ωπ}πÖµïÃÄÙÄ†ãöJ∑öR¯à∞ÄãöJ∑öR˚ö∂3ön»à∞ÄâA±Ö‰à∞ÄâA±Ö‰ÅÕΩπúà§(()ëïòÅ}ïÕçÖ¡ï}Ö¡¡±ïÕç…•¡–°ŸÖ±’îËÅÕ—»§Ä¥¯ÅÕ—»Ë(ÄÄÄÅ…ï—’…∏ÅŸÖ±’îπ…ï¡±Öçî†âqpà∞Äâqqqpà§π…ï¡±Öçî†úàú∞Äùqpàú§(()ç±ÖÕÃÅ¡¡±ï5’Õ•ç5ÖçëÖ¡—ï»Ë(ÄÄÄÄààã¶k¢˛Å¡¡±îÅŸïπ—ÃÉñr£ûR£ö"ﬂ¶~œíÊC¢÷öZgñÍOí‚∑öBsû“ãñÊ€öJ∑öR˚û ˚ûÜªö∂3önÀéààà((ÄÄÄÅ¡…ΩŸ•ëï»ÄÙÄâÖ¡¡±îà((ÄÄÄÅëïòÅ}}•π•—}|°Õï±ò∞ÅçΩµµÖπë}…’ππï»ËÅÖ±±Öâ±ïl∏∏∏∞ÅÕ’â¡…ΩçïÕÃπΩµ¡±ï—ïëA…ΩçïÕÕtÄÙÅÕ’â¡…ΩçïÕÃπ…’∏§Ä¥¯Å9ΩπîË(ÄÄÄÄÄÄÄÅÕï±òπçΩµµÖπë}…’ππï»ÄÙÅçΩµµÖπë}…’ππï»((ÄÄÄÅëïòÅÕïÖ…ç†°Õï±ò∞Å—•—±îËÅÕ—»∞ÅÖ…—•Õ–ËÅÕ—»§Ä¥¯ÅMï≈’ïπçïmMΩπùÖπë•ëÖ—ïtË(ÄÄÄÄÄÄÄÅ≈’ï…‰ÄÙÅ}ïÕçÖ¡ï}Ö¡¡±ïÕç…•¡–°òâÌÖ…—•Õ—ÙÅÌ—•—±ïÙàπÕ—…•¿†§§(ÄÄÄÄÄÄÄÅÕç…•¡–ÄÙÄ†(ÄÄÄÄÄÄÄÄÄÄÄÄù—ï±∞ÅÖ¡¡±•çÖ—•Ω∏Äâ5’Õ•åâq∏ú(ÄÄÄÄÄÄÄÄÄÄÄÅòùÕï–ÅôΩ’πëQ…Öç≠ÃÅ—ºÅÕïÖ…ç†Å±•â…Ö…‰Å¡±ÖÂ±•Õ–ÄƒÅôΩ»ÄâÌ≈’ï…ÂÙàÅΩπ±‰ÅÕΩπùÕq∏ú(ÄÄÄÄÄÄÄÄÄÄÄÄùÕï–ÅΩ’—¡’—Qï·–Å—ºÄàâq∏ú(ÄÄÄÄÄÄÄÄÄÄÄÄù…ï¡ïÖ–Å›•—†ÅçÖπë•ëÖ—ïQ…Öç¨Å•∏ÅôΩ’πëQ…Öç≠Õq∏ú(ÄÄÄÄÄÄÄÄÄÄÄÄùÕï–ÅΩ’—¡’—Qï·–Å—ºÅΩ’—¡’—Qï·–ÄòÄ°πÖµîÅΩòÅçÖπë•ëÖ—ïQ…Öç¨ÅÖÃÅ—ï·–§ÄòÅ—ÖàÄòÄú(ÄÄÄÄÄÄÄÄÄÄÄÄú°Ö…—•Õ–ÅΩòÅçÖπë•ëÖ—ïQ…Öç¨ÅÖÃÅ—ï·–§ÄòÅ—ÖàÄòÄ°¡ï…Õ•Õ—ïπ–Å%ÅΩòÅçÖπë•ëÖ—ïQ…Öç¨ÅÖÃÅ—ï·–§ÄòÅ±•πïôïïëq∏ú(ÄÄÄÄÄÄÄÄÄÄÄÄùïπêÅ…ï¡ïÖ—qπ…ï—’…∏ÅΩ’—¡’—Qï·—qπïπêÅ—ï±∞ú(ÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅçΩµ¡±ï—ïêÄÙÅÕï±òπ}…’∏°Õç…•¡–§(ÄÄÄÄÄÄÄÅçÖπë•ëÖ—ïÃËÅ±•Õ—mMΩπùÖπë•ëÖ—ïtÄÙÅmt(ÄÄÄÄÄÄÄÅôΩ»Å±•πîÅ•∏ÅÕ—»°çΩµ¡±ï—ïêπÕ—ëΩ’–ÅΩ»Äàà§πÕ¡±•—±•πïÃ†§Ë(ÄÄÄÄÄÄÄÄÄÄÄÅ¡Ö…—ÃÄÙÅ±•πîπÕ¡±•–†âq–à§(ÄÄÄÄÄÄÄÄÄÄÄÅ•òÅ±ï∏°¡Ö…—Ã§Ä¯ÙÄÃË(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅçÖπë•ëÖ—ïÃπÖ¡¡ïπê°MΩπùÖπë•ëÖ—î°Õï±òπ¡…ΩŸ•ëï»∞Å¡Ö…—Õl¡t∞Å¡Ö…—Õl≈t∞ÄâÕΩπúà∞Å¡Ö…—Õl…t§§(ÄÄÄÄÄÄÄÅ…ï—’…∏ÅçÖπë•ëÖ—ïÃ((ÄÄÄÅëïòÅ¡±Ö‰°Õï±ò∞ÅçÖπë•ëÖ—îËÅMΩπùÖπë•ëÖ—î§Ä¥¯ÅâΩΩ∞Ë(ÄÄÄÄÄÄÄÅ•ëïπ—•ô•ï»ÄÙÅ}ïÕçÖ¡ï}Ö¡¡±ïÕç…•¡–°çÖπë•ëÖ—îπ•ëïπ—•ô•ï»§(ÄÄÄÄÄÄÄÅ•òÅπΩ–Å•ëïπ—•ô•ï»Ë(ÄÄÄÄÄÄÄÄÄÄÄÅ…ï—’…∏ÅÖ±Õî(ÄÄÄÄÄÄÄÅÕç…•¡–ÄÙÄ†(ÄÄÄÄÄÄÄÄÄÄÄÄù—ï±∞ÅÖ¡¡±•çÖ—•Ω∏Äâ5’Õ•åâq∏ú(ÄÄÄÄÄÄÄÄÄÄÄÅòùÕï–Å—Ö…ùï—Q…Öç¨Å—ºÅô•…Õ–Å—…Öç¨ÅΩòÅ±•â…Ö…‰Å¡±ÖÂ±•Õ–ÄƒÅ›°ΩÕîÅ¡ï…Õ•Õ—ïπ–Å%Å•ÃÄâÌ•ëïπ—•ô•ï…Ùâq∏ú(ÄÄÄÄÄÄÄÄÄÄÄÄù¡±Ö‰Å—Ö…ùï—Q…Öç≠qπïπêÅ—ï±∞ú(ÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅ—…‰Ë(ÄÄÄÄÄÄÄÄÄÄÄÅÕï±òπ}…’∏°Õç…•¡–§(ÄÄÄÄÄÄÄÅï·çï¡–ÅA…ΩŸ•ëï…MïÖ…ç°……Ω»Ë(ÄÄÄÄÄÄÄÄÄÄÄÅ…ï—’…∏ÅÖ±Õî(ÄÄÄÄÄÄÄÅ…ï—’…∏ÅQ…’î((ÄÄÄÅëïòÅ}…’∏°Õï±ò∞ÅÕç…•¡–ËÅÕ—»§Ä¥¯ÅÕ’â¡…ΩçïÕÃπΩµ¡±ï—ïëA…ΩçïÕÃË(ÄÄÄÄÄÄÄÅ—…‰Ë(ÄÄÄÄÄÄÄÄÄÄÄÅçΩµ¡±ï—ïêÄÙÅÕï±òπçΩµµÖπë}…’ππï»†(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅlâΩÕÖÕç…•¡–à∞Äàµîà∞ÅÕç…•¡—t∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅçÖ¡—’…ï}Ω’—¡’–ıQ…’î∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ—ï·–ıQ…’î∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ—•µïΩ’–Ùƒ¿∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅç°ïç¨ıÖ±Õî∞(ÄÄÄÄÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅï·çï¡–Ä°=M……Ω»∞ÅÕ’â¡…ΩçïÕÃπQ•µïΩ’—·¡•…ïê§ÅÖÃÅï·åË(ÄÄÄÄÄÄÄÄÄÄÄÅ…Ö•ÕîÅA…ΩŸ•ëï…MïÖ…ç°……Ω»†â¡¡±îÅŸïπ—ÃÅ…ï≈’ïÕ–ÅôÖ•±ïêà§Åô…Ω¥Åï·å(ÄÄÄÄÄÄÄÅ•òÅçΩµ¡±ï—ïêπ…ï—’…πçΩëîÄÑÙÄ¿Ë(ÄÄÄÄÄÄÄÄÄÄÄÅ…Ö•ÕîÅA…ΩŸ•ëï…MïÖ…ç°……Ω»°Õ—»°çΩµ¡±ï—ïêπÕ—ëï…»ÅΩ»Äâ¡¡±îÅŸïπ—ÃÅ…ï≈’ïÕ–ÅôÖ•±ïêà§§(ÄÄÄÄÄÄÄÅ…ï—’…∏ÅçΩµ¡±ï—ïê(()ç±ÖÕÃÅ5ÖçççïÕÕ•â•±•—ÂëÖ¡—ï»Ë(ÄÄÄÄààãñr£ñﬁÀö:#övÅççïÕÕ•â•±•—‰Éö^€ñ>´û
ÁñÔñB3ö^€ñ2ñBØû ˚ûÜªö∂3ñB7ñJ3ö∂3ö&/ûjö∂3önÀûÓOözséààà((ÄÄÄÅ¡…ΩŸ•ëï»ÄÙÄàà(ÄÄÄÅÖ¡¡±•çÖ—•Ωπ}πÖµîÄÙÄàà(ÄÄÄÅÕΩπù}—Öâ}πÖµïÃËÅ—’¡±ïmÕ—»∞Ä∏∏πtÄÙÄ†ãö∂3ön»à∞ÄâMΩπùÃà∞ÄâQ…Öç≠Ãà§(ÄÄÄÅ¡±ÖÂ}â’——Ωπ}πÖµïÃËÅ—’¡±ïmÕ—»∞Ä∏∏πtÄÙÄ†ãöJ∑öR¯à∞ÄãöJ∑öR˚ö∂3ön»à∞ÄâA±Ö‰à∞ÄâA±Ö‰ÅÕΩπúà§((ÄÄÄÅëïòÅ}}•π•—}|°Õï±ò∞ÅçΩµµÖπë}…’ππï»ËÅÖ±±Öâ±ïl∏∏∏∞ÅÕ’â¡…ΩçïÕÃπΩµ¡±ï—ïëA…ΩçïÕÕtÄÙÅÕ’â¡…ΩçïÕÃπ…’∏§Ä¥¯Å9ΩπîË(ÄÄÄÄÄÄÄÅÕï±òπçΩµµÖπë}…’ππï»ÄÙÅçΩµµÖπë}…’ππï»((ÄÄÄÅëïòÅÕïÖ…ç†°Õï±ò∞Å—•—±îËÅÕ—»∞ÅÖ…—•Õ–ËÅÕ—»§Ä¥¯ÅMï≈’ïπçïmMΩπùÖπë•ëÖ—ïtË(ÄÄÄÄÄÄÄÅ≈’ï…‰ÄÙÅ’…±±•àπ¡Ö…Õîπ≈’Ω—î°òâÌÖ…—•Õ—ÙÅÌ—•—±ïÙà§(ÄÄÄÄÄÄÄÅ’…§ÄÙÅÕï±òπ}ÕïÖ…ç°}’…§°≈’ï…‰§(ÄÄÄÄÄÄÄÅ—…‰Ë(ÄÄÄÄÄÄÄÄÄÄÄÅΩ¡ïπïêÄÙÅÕï±òπçΩµµÖπë}…’ππï»†(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅlâΩ¡ï∏à∞ÄàµÑà∞ÅÕï±òπÖ¡¡±•çÖ—•Ωπ}πÖµî∞Å’…•t∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅçÖ¡—’…ï}Ω’—¡’–ıQ…’î∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ—ï·–ıQ…’î∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ—•µïΩ’–Ù‡∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅç°ïç¨ıÖ±Õî∞(ÄÄÄÄÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅï·çï¡–Ä°=M……Ω»∞ÅÕ’â¡…ΩçïÕÃπQ•µïΩ’—·¡•…ïê§ÅÖÃÅï·åË(ÄÄÄÄÄÄÄÄÄÄÄÅ…Ö•ÕîÅA…ΩŸ•ëï…MïÖ…ç°……Ω»†âç±•ïπ–ÅÕïÖ…ç†ÅçΩ’±êÅπΩ–ÅâîÅΩ¡ïπïêà§Åô…Ω¥Åï·å(ÄÄÄÄÄÄÄÅ•òÅΩ¡ïπïêπ…ï—’…πçΩëîÄÑÙÄ¿Ë(ÄÄÄÄÄÄÄÄÄÄÄÅ…Ö•ÕîÅA…ΩŸ•ëï…MïÖ…ç°……Ω»°Õ—»°Ω¡ïπïêπÕ—ëï…»ÅΩ»Äâç±•ïπ–ÅÕïÖ…ç†ÅçΩ’±êÅπΩ–ÅâîÅΩ¡ïπïêà§§(ÄÄÄÄÄÄÄÅ•òÅπΩ–Å—•—±îË(ÄÄÄÄÄÄÄÄÄÄÄÅçΩµ¡±ï—ïêÄÙÅÕï±òπ}…’π}ÖççïÕÕ•â•±•—‰°Õï±òπ}Ö…—•Õ—}çÖπë•ëÖ—ïÕ}Õç…•¡–°Ö…—•Õ–§§(ÄÄÄÄÄÄÄÄÄÄÄÅçÖπë•ëÖ—ïÃËÅ±•Õ—mMΩπùÖπë•ëÖ—ïtÄÙÅmt(ÄÄÄÄÄÄÄÄÄÄÄÅôΩ»Å±•πîÅ•∏ÅÕ—»°çΩµ¡±ï—ïêπÕ—ëΩ’–ÅΩ»Äàà§πÕ¡±•—±•πïÃ†§Ë(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ¡Ö…—ÃÄÙÅ±•πîπÕ¡±•–†âq–à§(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ•òÅ±ï∏°¡Ö…—Ã§Ä¯ÙÄ»ÅÖπêÅ¡Ö…—Õl¡tπÕ—…•¿†§ÅÖπêÅ}ÕÖµï}ÕΩπú°¡Ö…—Õl≈t∞ÅÖ…—•Õ–§Ë(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅçÖπë•ëÖ—ïÃπÖ¡¡ïπê°MΩπùÖπë•ëÖ—î°Õï±òπ¡…ΩŸ•ëï»∞Å¡Ö…—Õl¡t∞Å¡Ö…—Õl≈t∞ÄâÕΩπúà§§(ÄÄÄÄÄÄÄÄÄÄÄÅ…ï—’…∏ÅçÖπë•ëÖ—ïÃ(ÄÄÄÄÄÄÄÅÕç…•¡–ÄÙÅÕï±òπ}ÖççïÕÕ•â•±•—Â}Õç…•¡–°—•—±î∞ÅÖ…—•Õ–∞Å¡±Ö‰ıÖ±Õî§(ÄÄÄÄÄÄÄÅçΩµ¡±ï—ïêÄÙÅÕï±òπ}…’π}ÖççïÕÕ•â•±•—‰°Õç…•¡–§(ÄÄÄÄÄÄÄÅ•òÅÕ—»°çΩµ¡±ï—ïêπÕ—ëΩ’–ÅΩ»Äàà§πÕ—…•¿†§ÄÙÙÄâ5Q àË(ÄÄÄÄÄÄÄÄÄÄÄÅ…ï—’…∏Ä°MΩπùÖπë•ëÖ—î°Õï±òπ¡…ΩŸ•ëï»∞Å—•—±î∞ÅÖ…—•Õ–∞ÄâÕΩπúà∞ÅòâÌ—•—±ïıq—ÌÖ…—•Õ—Ùà§∞§(ÄÄÄÄÄÄÄÅ…ï—’…∏Ä†§((ÄÄÄÅëïòÅ¡±Ö‰°Õï±ò∞ÅçÖπë•ëÖ—îËÅMΩπùÖπë•ëÖ—î§Ä¥¯ÅâΩΩ∞Ë(ÄÄÄÄÄÄÄÅçΩµ¡±ï—ïêÄÙÅÕï±òπ}…’π}ÖççïÕÕ•â•±•—‰†(ÄÄÄÄÄÄÄÄÄÄÄÅÕï±òπ}ÖççïÕÕ•â•±•—Â}Õç…•¡–°çÖπë•ëÖ—îπ—•—±î∞ÅçÖπë•ëÖ—îπÖ…—•Õ–∞Å¡±Ö‰ıQ…’î§(ÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅ…ï—’…∏ÅÕ—»°çΩµ¡±ï—ïêπÕ—ëΩ’–ÅΩ»Äàà§πÕ—…•¿†§ÄÙÙÄâA1eà((ÄÄÄÅëïòÅ}…’π}ÖççïÕÕ•â•±•—‰°Õï±ò∞ÅÕç…•¡–ËÅÕ—»§Ä¥¯ÅÕ’â¡…ΩçïÕÃπΩµ¡±ï—ïëA…ΩçïÕÃË(ÄÄÄÄÄÄÄÅ—…‰Ë(ÄÄÄÄÄÄÄÄÄÄÄÅçΩµ¡±ï—ïêÄÙÅÕï±òπçΩµµÖπë}…’ππï»†(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅlâΩÕÖÕç…•¡–à∞Äàµîà∞ÅÕç…•¡—t∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅçÖ¡—’…ï}Ω’—¡’–ıQ…’î∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ—ï·–ıQ…’î∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ—•µïΩ’–Ùƒ»∞(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅç°ïç¨ıÖ±Õî∞(ÄÄÄÄÄÄÄÄÄÄÄÄ§(ÄÄÄÄÄÄÄÅï·çï¡–Ä°=M……Ω»∞ÅÕ’â¡…ΩçïÕÃπQ•µïΩ’—·¡•…ïê§ÅÖÃÅï·åË(ÄÄÄÄÄÄÄÄÄÄÄÅ…Ö•ÕîÅA…ΩŸ•ëï…MïÖ…ç°……Ω»†âççïÕÕ•â•±•—‰Å…ï≈’ïÕ–ÅôÖ•±ïêà§Åô…Ω¥Åï·å(ÄÄÄÄÄÄÄÅ•òÅçΩµ¡±ï—ïêπ…ï—’…πçΩëîÄÑÙÄ¿Ë(ÄÄÄÄÄÄÄÄÄÄÄÅ…Ö•ÕîÅA…ΩŸ•ëï…MïÖ…ç°……Ω»°Õ—»°çΩµ¡±ï—ïêπÕ—ëï…»ÅΩ»ÄâççïÕÕ•â•±•—‰Å…ï≈’ïÕ–ÅôÖ•±ïêà§§(ÄÄÄÄÄÄÄÅ…ï—’…∏ÅçΩµ¡±ï—ïê((ÄÄÄÅëïòÅ}ÖççïÕÕ•â•±•—Â}Õç…•¡–°Õï±ò∞Å—•—±îËÅÕ—»∞ÅÖ…—•Õ–ËÅÕ—»∞Ä®∞Å¡±Ö‰ËÅâΩΩ∞§Ä¥¯ÅÕ—»Ë(ÄÄÄÄÄÄÄÅÕÖôï}Ö¡¿ÄÙÅ}ïÕçÖ¡ï}Ö¡¡±ïÕç…•¡–°Õï±òπÖ¡¡±•çÖ—•Ωπ}πÖµî§(ÄÄÄÄÄÄÄÅÕÖôï}—•—±îÄÙÅ}ïÕçÖ¡ï}Ö¡¡±ïÕç…•¡–°—•—±î§(ÄÄÄÄÄÄÄÅÕÖôï}Ö…—•Õ–ÄÙÅ}ïÕçÖ¡ï}Ö¡¡±ïÕç…•¡–°Ö…—•Õ–§(ÄÄÄÄÄÄÄÅ—ÖâÃÄÙÄà∞Äàπ©Ω•∏°òúâÌ}ïÕçÖ¡ï}Ö¡¡±ïÕç…•¡–°πÖµî•ÙàúÅôΩ»ÅπÖµîÅ•∏ÅÕï±òπÕΩπù}—Öâ}πÖµïÃ§(ÄÄÄÄÄÄÄÅâ’——ΩπÃÄÙÄà∞Äàπ©Ω•∏°òúâÌ}ïÕçÖ¡ï}Ö¡¡±ïÕç…•¡–°πÖµî•ÙàúÅôΩ»ÅπÖµîÅ•∏ÅÕï±òπ¡±ÖÂ}â’——Ωπ}πÖµïÃ§(ÄÄÄÄÄÄÄÅÖç—•Ω∏ÄÙÄâ—…’îàÅ•òÅ¡±Ö‰Åï±ÕîÄâôÖ±Õîà(ÄÄÄÄÄÄÄÅ…ï—’…∏Åòúúú)—ï±∞ÅÖ¡¡±•çÖ—•Ω∏ÄâÌÕÖôï}Ö¡¡ÙàÅ—ºÅÖç—•ŸÖ—î)ëï±Ö‰Äƒ∏‘)—ï±∞ÅÖ¡¡±•çÖ—•Ω∏ÄâMÂÕ—ï¥ÅŸïπ—Ãà(ÄÅ•òÅU$Åï±ïµïπ—ÃÅïπÖâ±ïêÅ•ÃÅôÖ±ÕîÅ—°ï∏Åï……Ω»ÄâççïÕÕ•â•±•—‰Å¡ï…µ•ÕÕ•Ω∏Å…ï≈’•…ïêà(ÄÅ—ï±∞Å¡…ΩçïÕÃÄâÌÕÖôï}Ö¡¡Ùà(ÄÄÄÅÕï–Åô…Ωπ—µΩÕ–Å—ºÅ—…’î(ÄÄÄÅÕï–Å—Öâ9ÖµïÃÅ—ºÅÌÌÌ—ÖâÕııÙ(ÄÄÄÅÕï–Å¡±ÖÂ9ÖµïÃÅ—ºÅÌÌÌâ’——ΩπÕııÙ(ÄÄÄÅ—…‰(ÄÄÄÄÄÅ…ï¡ïÖ–Å›•—†Å•—ïµIïòÅ•∏Ä°ïπ—•…îÅçΩπ—ïπ—ÃÅΩòÅô…Ωπ–Å›•πëΩ‹§(ÄÄÄÄÄÄÄÅ—…‰(ÄÄÄÄÄÄÄÄÄÅ•òÄ°πÖµîÅΩòÅ•—ïµIïòÅÖÃÅ—ï·–§Å•ÃÅ•∏Å—Öâ9ÖµïÃÅ—°ï∏Åç±•ç¨Å•—ïµIïò(ÄÄÄÄÄÄÄÅïπêÅ—…‰(ÄÄÄÄÄÅïπêÅ…ï¡ïÖ–(ÄÄÄÅïπêÅ—…‰(ÄÄÄÅëï±Ö‰Äƒ∏¿(ÄÄÄÅ…ï¡ïÖ–Å›•—†Å—•—±ï±ïµïπ–Å•∏Ä°ïπ—•…îÅçΩπ—ïπ—ÃÅΩòÅô…Ωπ–Å›•πëΩ‹§(ÄÄÄÄÄÅ—…‰(ÄÄÄÄÄÄÄÅ•òÄ°πÖµîÅΩòÅ—•—±ï±ïµïπ–ÅÖÃÅ—ï·–§Å•ÃÄâÌÕÖôï}—•—±ïÙàÅ—°ï∏(ÄÄÄÄÄÄÄÄÄÅÕï–Å…Ω›±ïµïπ–Å—ºÅ¡Ö…ïπ–ÅΩòÅ—•—±ï±ïµïπ–(ÄÄÄÄÄÄÄÄÄÅ…ï¡ïÖ–ÄÿÅ—•µïÃ(ÄÄÄÄÄÄÄÄÄÄÄÅÕï–ÅÖ…—•Õ—Ω’πêÅ—ºÅôÖ±Õî(ÄÄÄÄÄÄÄÄÄÄÄÅ…ï¡ïÖ–Å›•—†Åç°•±ë±ïµïπ–Å•∏Ä°ïπ—•…îÅçΩπ—ïπ—ÃÅΩòÅ…Ω›±ïµïπ–§(ÄÄÄÄÄÄÄÄÄÄÄÄÄÅ—…‰(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ•òÄ°πÖµîÅΩòÅç°•±ë±ïµïπ–ÅÖÃÅ—ï·–§Å•ÃÄâÌÕÖôï}Ö…—•Õ—ÙàÅ—°ï∏ÅÕï–ÅÖ…—•Õ—Ω’πêÅ—ºÅ—…’î(ÄÄÄÄÄÄÄÄÄÄÄÄÄÅïπêÅ—…‰(ÄÄÄÄÄÄÄÄÄÄÄÅïπêÅ…ï¡ïÖ–(ÄÄÄÄÄÄÄÄÄÄÄÅ•òÅÖ…—•Õ—Ω’πêÅ—°ï∏(ÄÄÄÄÄÄÄÄÄÄÄÄÄÅ•òÅÌÖç—•ΩπÙÅ—°ï∏(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ…ï¡ïÖ–Å›•—†Åç°•±ë±ïµïπ–Å•∏Ä°ïπ—•…îÅçΩπ—ïπ—ÃÅΩòÅ…Ω›±ïµïπ–§(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ—…‰(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ•òÄ°πÖµîÅΩòÅç°•±ë±ïµïπ–ÅÖÃÅ—ï·–§Å•ÃÅ•∏Å¡±ÖÂ9ÖµïÃÅ—°ï∏(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅç±•ç¨Åç°•±ë±ïµïπ–(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ…ï—’…∏ÄâA1eà(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅïπêÅ•ò(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅïπêÅ—…‰(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅïπêÅ…ï¡ïÖ–(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ…ï—’…∏Äâ5Q!}9=}A1e}	UQQ=8à(ÄÄÄÄÄÄÄÄÄÄÄÄÄÅïπêÅ•ò(ÄÄÄÄÄÄÄÄÄÄÄÄÄÅ…ï—’…∏Äâ5Q à(ÄÄÄÄÄÄÄÄÄÄÄÅïπêÅ•ò(ÄÄÄÄÄÄÄÄÄÄÄÅÕï–Å…Ω›±ïµïπ–Å—ºÅ¡Ö…ïπ–ÅΩòÅ…Ω›±ïµïπ–(ÄÄÄÄÄÄÄÄÄÅïπêÅ…ï¡ïÖ–(ÄÄÄÄÄÄÄÅïπêÅ•ò(ÄÄÄÄÄÅïπêÅ—…‰(ÄÄÄÅïπêÅ…ï¡ïÖ–(ÄÅïπêÅ—ï±∞)ïπêÅ—ï±∞)…ï—’…∏Äâ9=}5Q à(úúú((ÄÄÄÅëïòÅ}Ö…—•Õ—}çÖπë•ëÖ—ïÕ}Õç…•¡–°Õï±ò∞ÅÖ…—•Õ–ËÅÕ—»§Ä¥¯ÅÕ—»Ë(ÄÄÄÄÄÄÄÄààãñr£ö∂3önÀöÇû∂˚¶Ü◊í‚∑¢æÔñ>[ö∂3ö&/û ˚ûÜªñ2Á¶7¢Ü3ûjö∂3ñB7æÚ3í‚7û
ÁñÔ¶j?örÎ¶Ü◊¶vãñû“Ééààà((ÄÄÄÄÄÄÄÅÕÖôï}Ö¡¿ÄÙÅ}ïÕçÖ¡ï}Ö¡¡±ïÕç…•¡–°Õï±òπÖ¡¡±•çÖ—•Ωπ}πÖµî§(ÄÄÄÄÄÄÄÅÕÖôï}Ö…—•Õ–ÄÙÅ}ïÕçÖ¡ï}Ö¡¡±ïÕç…•¡–°Ö…—•Õ–§(ÄÄÄÄÄÄÄÅ—ÖâÃÄÙÄà∞Äàπ©Ω•∏°òúâÌ}ïÕçÖ¡ï}Ö¡¡±ïÕç…•¡–°πÖµî•ÙàúÅôΩ»ÅπÖµîÅ•∏ÅÕï±òπÕΩπù}—Öâ}πÖµïÃ§(ÄÄÄÄÄÄÄÅâ’——ΩπÃÄÙÄà∞Äàπ©Ω•∏°òúâÌ}ïÕçÖ¡ï}Ö¡¡±ïÕç…•¡–°πÖµî•ÙàúÅôΩ»ÅπÖµîÅ•∏ÅÕï±òπ¡±ÖÂ}â’——Ωπ}πÖµïÃ§(ÄÄÄÄÄÄÄÅ…ï—’…∏Åòúúú)—ï±∞ÅÖ¡¡±•çÖ—•Ω∏ÄâÌÕÖôï}Ö¡¡ÙàÅ—ºÅÖç—•ŸÖ—î)ëï±Ö‰Äƒ∏‘)—ï±∞ÅÖ¡¡±•çÖ—•Ω∏ÄâMÂÕ—ï¥ÅŸïπ—Ãà(ÄÅ•òÅU$Åï±ïµïπ—ÃÅïπÖâ±ïêÅ•ÃÅôÖ±ÕîÅ—°ï∏Åï……Ω»ÄâççïÕÕ•â•±•—‰Å¡ï…µ•ÕÕ•Ω∏Å…ï≈’•…ïêà(ÄÅ—ï±∞Å¡…ΩçïÕÃÄâÌÕÖôï}Ö¡¡Ùà(ÄÄÄÅÕï–Å—Öâ9ÖµïÃÅ—ºÅÌÌÌ—ÖâÕııÙ(ÄÄÄÅÕï–Å¡±ÖÂ9ÖµïÃÅ—ºÅÌÌÌâ’——ΩπÕııÙ(ÄÄÄÅ…ï¡ïÖ–Å›•—†Å•—ïµIïòÅ•∏Ä°ïπ—•…îÅçΩπ—ïπ—ÃÅΩòÅô…Ωπ–Å›•πëΩ‹§(ÄÄÄÄÄÅ—…‰(ÄÄÄÄÄÄÄÅ•òÄ°πÖµîÅΩòÅ•—ïµIïòÅÖÃÅ—ï·–§Å•ÃÅ•∏Å—Öâ9ÖµïÃÅ—°ï∏Åç±•ç¨Å•—ïµIïò(ÄÄÄÄÄÅïπêÅ—…‰(ÄÄÄÅïπêÅ…ï¡ïÖ–(ÄÄÄÅëï±Ö‰Äƒ∏¿(ÄÄÄÅÕï–ÅΩ’—¡’—Qï·–Å—ºÄàà(ÄÄÄÅ…ï¡ïÖ–Å›•—†ÅÖ…—•Õ—±ïµïπ–Å•∏Ä°ïπ—•…îÅçΩπ—ïπ—ÃÅΩòÅô…Ωπ–Å›•πëΩ‹§(ÄÄÄÄÄÅ—…‰(ÄÄÄÄÄÄÄÅ•òÄ°πÖµîÅΩòÅÖ…—•Õ—±ïµïπ–ÅÖÃÅ—ï·–§Å•ÃÄâÌÕÖôï}Ö…—•Õ—ÙàÅ—°ï∏(ÄÄÄÄÄÄÄÄÄÅÕï–Å…Ω›±ïµïπ–Å—ºÅ¡Ö…ïπ–ÅΩòÅÖ…—•Õ—±ïµïπ–(ÄÄÄÄÄÄÄÄÄÅ…ï¡ïÖ–ÄÿÅ—•µïÃ(ÄÄÄÄÄÄÄÄÄÄÄÅÕï–Å—•—±ïQï·–Å—ºÄàà(ÄÄÄÄÄÄÄÄÄÄÄÅ…ï¡ïÖ–Å›•—†Åç°•±ë±ïµïπ–Å•∏Ä°ïπ—•…îÅçΩπ—ïπ—ÃÅΩòÅ…Ω›±ïµïπ–§(ÄÄÄÄÄÄÄÄÄÄÄÄÄÅ—…‰(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÕï–Åç°•±ë9ÖµîÅ—ºÅπÖµîÅΩòÅç°•±ë±ïµïπ–ÅÖÃÅ—ï·–(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅ•òÅç°•±ë9ÖµîÅ•ÃÅπΩ–ÄààÅÖπêÅç°•±ë9ÖµîÅ•ÃÅπΩ–ÄâÌÕÖôï}Ö…—•Õ—ÙàÅÖπêÅç°•±ë9ÖµîÅ•ÃÅπΩ–Å•∏Å—Öâ9ÖµïÃÅÖπêÅç°•±ë9ÖµîÅ•ÃÅπΩ–Å•∏Å¡±ÖÂ9ÖµïÃÅ—°ï∏(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅÕï–Å—•—±ïQï·–Å—ºÅç°•±ë9Öµî(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅï·•–Å…ï¡ïÖ–(ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÅïπêÅ•ò(ÄÄÄÄÄÄÄÄÄÄÄÄÄÅïπêÅ—…‰(ÄÄÄÄÄÄÄÄÄÄÄÅïπêÅ…ï¡ïÖ–(ÄÄÄÄÄÄÄÄÄÄÄÅ•òÅ—•—±ïQï·–Å•ÃÅπΩ–ÄààÅ—°ï∏(ÄÄÄÄÄÄÄÄÄÄÄÄÄÅÕï–ÅΩ’—¡’—Qï·–Å—ºÅΩ’—¡’—Qï·–ÄòÅ—•—±ïQï·–ÄòÅ—ÖàÄòÄâÌÕÖôï}Ö…—•Õ—ÙàÄòÅ±•πïôïïê(ÄÄÄÄÄÄÄÄÄÄÄÄÄÅï·•–Å…ï¡ïÖ–(ÄÄÄÄÄÄÄÄÄÄÄÅïπêÅ•ò(ÄÄÄÄÄÄÄÄÄÄÄÅÕï–Å…Ω›±ïµïπ–Å—ºÅ¡Ö…ïπ–ÅΩòÅ…Ω›±ïµïπ–(ÄÄÄÄÄÄÄÄÄÅïπêÅ…ï¡ïÖ–(ÄÄÄÄÄÄÄÅïπêÅ•ò(ÄÄÄÄÄÅïπêÅ—…‰(ÄÄÄÅïπêÅ…ï¡ïÖ–(ÄÄÄÅ…ï—’…∏ÅΩ’—¡’—Qï·–(ÄÅïπêÅ—ï±∞)ïπêÅ—ï±∞(úúú((ÄÄÄÅëïòÅ}ÕïÖ…ç°}’…§°Õï±ò∞Å≈’ï…‰ËÅÕ—»§Ä¥¯ÅÕ—»Ë(ÄÄÄÄÄÄÄÅ…Ö•ÕîÅ9Ω—%µ¡±ïµïπ—ïë……Ω»(()ç±ÖÕÃÅEE5’Õ•ç5ÖçëÖ¡—ï»°5ÖçççïÕÕ•â•±•—ÂëÖ¡—ï»§Ë(ÄÄÄÅ¡…ΩŸ•ëï»ÄÙÄâ≈ƒà(ÄÄÄÅÖ¡¡±•çÖ—•Ωπ}πÖµîÄÙÄâEE5’Õ•åà(ÄÄÄÅ¡±ÖÂ}â’——Ωπ}πÖµïÃÄÙÄ†ãöJ∑öR¯à∞ÄãöJ∑öR˚ö∂3ön»à∞ÄâA±Ö‰à§((ÄÄÄÅëïòÅ}ÕïÖ…ç°}’…§°Õï±ò∞Å≈’ï…‰ËÅÕ—»§Ä¥¯ÅÕ—»Ë(ÄÄÄÄÄÄÄÅ…ï—’…∏Åòâ°——¡ÃËºΩ‰π≈ƒπçΩ¥Ω∏Ω…Â≈ƒΩÕïÖ…ç†˝‹ıÌ≈’ï…ÂÙô–ıÕΩπúà(()ç±ÖÕÃÅ9ï—ïÖÕï5’Õ•ç5ÖçëÖ¡—ï»°5ÖçççïÕÕ•â•±•—ÂëÖ¡—ï»§Ë(ÄÄÄÅ¡…ΩŸ•ëï»ÄÙÄâπï—ïÖÕîà(ÄÄÄÅÖ¡¡±•çÖ—•Ωπ}πÖµîÄÙÄâ9ï—ïÖÕï5’Õ•åà(ÄÄÄÅ¡±ÖÂ}â’——Ωπ}πÖµïÃÄÙÄ†ãöJ∑öR¯à∞ÄãûÆ/ñ6œöJ∑öR¯à∞ÄâA±Ö‰à§((ÄÄÄÅëïòÅ}ÕïÖ…ç°}’…§°Õï±ò∞Å≈’ï…‰ËÅÕ—»§Ä¥¯ÅÕ—»Ë(ÄÄÄÄÄÄÄÅ…ï—’…∏Åòâ°——¡ÃËºΩµ’Õ•å∏ƒÿÃπçΩ¥ºåΩÕïÖ…ç†Ω¥º˝ÃıÌ≈’ï…ÂÙô—Â¡îÙƒà(()ç±ÖÕÃÅ-’ùΩ’5’Õ•ç5ÖçëÖ¡—ï»°5ÖçççïÕÕ•â•±•—ÂëÖ¡—ï»§Ë(ÄÄÄÅ¡…ΩŸ•ëï»ÄÙÄâ≠’ùΩ‘à(ÄÄÄÅÖ¡¡±•çÖ—•Ωπ}πÖµîÄÙÄâ-’ùΩ’5’Õ•åà(ÄÄÄÅ¡±ÖÂ}â’——Ωπ}πÖµïÃÄÙÄ†ãöJ∑öR¯à∞ÄãûÆ/ñ6œöJ∑öR¯à∞ÄâA±Ö‰à§((ÄÄÄÅëïòÅ}ÕïÖ…ç°}’…§°Õï±ò∞Å≈’ï…‰ËÅÕ—»§Ä¥¯ÅÕ—»Ë(ÄÄÄÄÄÄÄÅ…ï—’…∏Åòâ°——¡ÃËºΩ››‹π≠’ùΩ‘πçΩ¥ΩÂ‰Ω°—µ∞ΩÕïÖ…ç†π°—µ∞çÕïÖ…ç°QÂ¡îıÕΩπúôÕïÖ…ç°-ïÂ]Ω…êıÌ≈’ï…ÂÙà(()ç±ÖÕÃÅM¡Ω—•ôÂ5ÖçëÖ¡—ï»°5ÖçççïÕÕ•â•±•—ÂëÖ¡—ï»§Ë(ÄÄÄÅ¡…ΩŸ•ëï»ÄÙÄâÕ¡Ω—•ô‰à(ÄÄÄÅÖ¡¡±•çÖ—•Ωπ}πÖµîÄÙÄâM¡Ω—•ô‰à(ÄÄÄÅÕΩπù}—Öâ}πÖµïÃÄÙÄ†ãö∂3ön»à∞ÄâMΩπùÃà∞ÄâQ…Öç≠Ãà§(ÄÄÄÅ¡±ÖÂ}â’——Ωπ}πÖµïÃÄÙÄ†ãöJ∑öR¯à∞ÄãöJ∑öR˚ö∂3ön»à∞ÄâA±Ö‰à∞ÄâA±Ö‰ÅÕΩπúà§((ÄÄÄÅëïòÅ}ÕïÖ…ç°}’…§°Õï±ò∞Å≈’ï…‰ËÅÕ—»§Ä¥¯ÅÕ—»Ë(ÄÄÄÄÄÄÄÅ…ï—’…∏ÅòâÕ¡Ω—•ô‰ÈÕïÖ…ç†ÈÌ≈’ï…ÂÙà(()ëïòÅâ’•±ë}¡…ΩŸ•ëï…}ÖëÖ¡—ï…Ã†(ÄÄÄÅÕï——•πùÃËÅAï—Mï——•πùÃ∞(ÄÄÄÄ®∞(ÄÄÄÅ¡±Ö—ôΩ…µ}πÖµîËÅÕ—»ÅÅ9ΩπîÄÙÅ9Ωπî∞(ÄÄÄÅç±•ïπ—}ô•πëï»ËÅÖ±±Öâ±ïmmÕ—»∞ÅÕ—…t∞ÅAÖ—†ÅÅ9ΩπïtÄÙÅô•πë}µ’Õ•ç}ç±•ïπ–∞(ÄÄÄÅçΩµµÖπë}…’ππï»ËÅÖ±±Öâ±ïl∏∏∏∞ÅÕ’â¡…ΩçïÕÃπΩµ¡±ï—ïëA…ΩçïÕÕtÄÙÅÕ’â¡…ΩçïÕÃπ…’∏∞(§Ä¥¯Åë•ç—mÕ—»∞Å5’Õ•çA…ΩŸ•ëï…ëÖ¡—ï…tË(ÄÄÄÄààãö2'ñˆOñ&7ûŒÔûÓöz¶ÉíÍSí‚´ñˆÛö∂ìû.≥ûÆ/ûjÅA…ΩŸ•ëï»ÅëÖ¡—ïÀéààà((ÄÄÄÅ¡±Ö—ôΩ…¥ÄÙÅ¡±Ö—ôΩ…µ}πÖµîÅΩ»ÅÕÂÃπ¡±Ö—ôΩ…¥(ÄÄÄÅ•òÅ¡±Ö—ôΩ…¥ÄÙÙÄâ›•∏Ã»àË(ÄÄÄÄÄÄÄÅ…ï—’…∏ÅÏ(ÄÄÄÄÄÄÄÄÄÄÄÄâ≈ƒàËÅEE5’Õ•çëÖ¡—ï»°Õï——•πùÃ∞Åç±•ïπ—}ô•πëï»ıç±•ïπ—}ô•πëï»§∞(ÄÄÄÄÄÄÄÄÄÄÄÄâπï—ïÖÕîàËÅ9ï—ïÖÕï5’Õ•çëÖ¡—ï»°Õï——•πùÃ∞Åç±•ïπ—}ô•πëï»ıç±•ïπ—}ô•πëï»§∞(ÄÄÄÄÄÄÄÄÄÄÄÄâ≠’ùΩ‘àËÅ-’ùΩ’5’Õ•çëÖ¡—ï»°Õï——•πùÃ∞Åç±•ïπ—}ô•πëï»ıç±•ïπ—}ô•πëï»§∞(ÄÄÄÄÄÄÄÄÄÄÄÄâÖ¡¡±îàËÅ¡¡±ï5’Õ•ç]•πëΩ›ÕëÖ¡—ï»°Õï——•πùÃ∞Åç±•ïπ—}ô•πëï»ıç±•ïπ—}ô•πëï»§∞(ÄÄÄÄÄÄÄÄÄÄÄÄâÕ¡Ω—•ô‰àËÅM¡Ω—•ôÂ]•πëΩ›ÕëÖ¡—ï»°Õï——•πùÃ∞Åç±•ïπ—}ô•πëï»ıç±•ïπ—}ô•πëï»§∞(ÄÄÄÄÄÄÄÅÙ(ÄÄÄÅ•òÅ¡±Ö—ôΩ…¥ÄÙÙÄâëÖ…›•∏àË(ÄÄÄÄÄÄÄÅ…ï—’…∏ÅÏ(ÄÄÄÄÄÄÄÄÄÄÄÄâ≈ƒàËÅEE5’Õ•ç5ÖçëÖ¡—ï»°çΩµµÖπë}…’ππï»§∞(ÄÄÄÄÄÄÄÄÄÄÄÄâπï—ïÖÕîàËÅ9ï—ïÖÕï5’Õ•ç5ÖçëÖ¡—ï»°çΩµµÖπë}…’ππï»§∞(ÄÄÄÄÄÄÄÄÄÄÄÄâ≠’ùΩ‘àËÅ-’ùΩ’5’Õ•ç5ÖçëÖ¡—ï»°çΩµµÖπë}…’ππï»§∞(ÄÄÄÄÄÄÄÄÄÄÄÄâÖ¡¡±îàËÅ¡¡±ï5’Õ•ç5ÖçëÖ¡—ï»°çΩµµÖπë}…’ππï»§∞(ÄÄÄÄÄÄÄÄÄÄÄÄâÕ¡Ω—•ô‰àËÅM¡Ω—•ôÂ5ÖçëÖ¡—ï»°çΩµµÖπë}…’ππï»§∞(ÄÄÄÄÄÄÄÅÙ(ÄÄÄÅ…ï—’…∏ÅÌÙ(()ëïòÅ¡…ΩŸ•ëï…}±Öâï∞°¡…ΩŸ•ëï»ËÅÕ—»§Ä¥¯ÅÕ—»Ë(ÄÄÄÄààãí‚Îö^óñ˛_ñﬁóñﬂñJ3¢¬¢æW¶vãövˇ¢˛Sñn{û¢œñ∫kûjñÊœñ>√ñB7ûû√éààà((ÄÄÄÅ…ï—’…∏Å5UM%}MIY%}1	1Lπùï–°¡…ΩŸ•ëï»∞Å¡…ΩŸ•ëï»§(