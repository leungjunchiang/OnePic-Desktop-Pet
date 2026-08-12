"""å®žçŽ°æŒ‡å®šæ­Œæ›²çš„ç²¾ç¡®æœç´¢ã€ç»“æžœç­›é€‰ã€æ’­æ”¾åŠ¨ä½œä¸Žåª’ä½“ä¿¡æ¯æ ¡éªŒé—­çŽ¯ã€‚

æ¯ä¸ªéŸ³ä¹å¹³å°æ‹¥æœ‰ç‹¬ç«‹ Provider Adapterã€‚æœç´¢æˆåŠŸã€æ‰“å¼€å®¢æˆ·ç«¯æˆ–è§¦å‘æŽ§ä»¶éƒ½ä¸ç­‰äºŽ
æ’­æ”¾æˆåŠŸï¼›åªæœ‰å½“å‰åª’ä½“æ ‡é¢˜å’Œæ­Œæ‰‹ä¸Žæœ€ç»ˆé€‰ä¸­çš„æ­Œæ›²åŒæ—¶åŒ¹é…æ—¶æ‰è¿”å›žæˆåŠŸã€‚Windows
Adapter ä½¿ç”¨ UI Automation å®šä½â€œæ­Œæ›²â€ç»“æžœï¼›ç½‘æ˜“äº‘ 3.x ä¸å…¬å¼€ Chromium æŽ§ä»¶æ ‘æ—¶ï¼Œ
ä½¿ç”¨ç»‘å®šç”¨æˆ· Default æ¡Œé¢çš„ DPI-aware æœ¬æœºäº¤äº’å›žé€€ã€‚macOS ä¼˜å…ˆä½¿ç”¨ Apple Eventsï¼Œ
å¹¶åœ¨éœ€è¦æ—¶ä½¿ç”¨å·²æŽˆæƒçš„ Accessibilityã€‚ä¸¥æ ¼ç‚¹æ­Œè¿”å›žæ˜Žç¡®å¤±è´¥ç ï¼›éšæœºæ­Œæ‰‹æ’­æ”¾åœ¨æ’­æ”¾
åŠ¨ä½œå·²æ‰§è¡Œä½†åª’ä½“ Session æš‚ä¸å¯è¯»æ—¶æ ‡è®°ä¸ºæœªéªŒè¯å¯åŠ¨ï¼Œä¸ä¼šå› æ­¤ä¸»åŠ¨åœæ­¢éŸ³ä¹ã€‚
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
    """ç‚¹æ­Œé—­çŽ¯ä¸­å¯ä»¥è¢«æ—¥å¿—ã€æµ‹è¯•å’Œç•Œé¢ç¨³å®šè¯†åˆ«çš„å¤±è´¥é˜¶æ®µã€‚"""

    SEARCH_FAILED = "SEARCH_FAILED"
    UI_AUTOMATION_UNAVAILABLE = "UI_AUTOMATION_UNAVAILABLE"
    RESULT_NOT_FOUND = "RESULT_NOT_FOUND"
    PLAY_ACTION_FAILED = "PLAY_ACTION_FAILED"
    MEDIA_SESSION_TIMEOUT = "MEDIA_SESSION_TIMEOUT"
    TRACK_VERIFY_FAILED = "TRACK_VERIFY_FAILED"


class MusicPlaybackOutcome(str, Enum):
    """åŸºç¡€éšæœºæ’­æ”¾æˆåŠŸåŽçš„å¯éªŒè¯ç¨‹åº¦ã€‚"""

    PLAYBACK_CONFIRMED = "PLAYBACK_CONFIRMED"
    PLAYBACK_STARTED_UNVERIFIED = "PLAYBACK_STARTED_UNVERIFIED"


class TrackSnapshot(Protocol):
    """æ ¡éªŒå™¨æ‰€éœ€çš„æœ€å°å½“å‰æ­Œæ›²ä¿¡æ¯ã€‚"""

    title: str
    artist: str


@dataclass(frozen=True)
class SongCandidate:
    """Provider Adapter ä»Žâ€œæ­Œæ›²â€ç»“æžœä¸­è¯»å–åˆ°çš„ä¸€ä¸ªå€™é€‰é¡¹ã€‚"""

    provider: str
    title: str
    artist: str
    result_type: str = "song"
    identifier: str = ""
    native: object | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class SongPlaybackResult:
    """ä¸€æ¬¡æ’­æ”¾ç»“æžœï¼›ä¸¥æ ¼ç‚¹æ­Œéœ€ç¡®è®¤ï¼Œéšæœºæ­Œæ‰‹æ’­æ”¾å…è®¸æ ‡è®°ä¸ºæœªéªŒè¯å¯åŠ¨ã€‚"""

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
    """Provider æ— æ³•å®Œæˆæœç´¢æˆ–è¯»å–ç»“æžœæ—¶ä½¿ç”¨çš„å†…éƒ¨å¼‚å¸¸ã€‚"""


class UIAutomationUnavailableError(ProviderSearchError):
    """Windows UIAutomation æ ¹èŠ‚ç‚¹ã€çª—å£æˆ–æŽ§ä»¶æ— æ³•è®¿é—®ã€‚"""


class MusicProviderAdapter(Protocol):
    """å„éŸ³ä¹å®¢æˆ·ç«¯å¿…é¡»ç‹¬ç«‹å®žçŽ°çš„æœ€å°ç‚¹æ­Œåè®®ã€‚"""

    provider: str

    def search(self, title: str, artist: str) -> Sequence[SongCandidate]: ...

    def play(self, candidate: SongCandidate) -> bool: ...


def _canonical(value: str) -> str:
    """ç”¨äºŽä¸¥æ ¼åŒ¹é…çš„ Unicode è§„èŒƒå½¢å¼ï¼Œä¿ç•™ç‰ˆæœ¬åŽç¼€ä»¥æ‹’ç» Live/Remixã€‚"""

    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _same_song(left: str, right: str) -> bool:
    return bool(_canonical(left)) and _canonical(left) == _canonical(right)


def _failure_message(code: MusicPlaybackError, *, random_artist: bool = False) -> str:
    messages = {
        MusicPlaybackError.SEARCH_FAILED: "æ­Œæ›²æœç´¢å¤±è´¥ï¼Œè¯·ç¡®è®¤æ’­æ”¾å™¨æ­£åœ¨è¿è¡Œå¹¶å…è®¸è¾…åŠ©åŠŸèƒ½ã€‚",
        MusicPlaybackError.UI_AUTOMATION_UNAVAILABLE: "æ’­æ”¾å™¨ç•Œé¢æš‚æ—¶æ— æ³•è®¿é—®ï¼Œè¯·åœ¨äº¤äº’å¼ Windows æ¡Œé¢ä¸­è¿è¡Œç½‘æ˜“äº‘éŸ³ä¹ã€‚",
        MusicPlaybackError.RESULT_NOT_FOUND: (
            "æ²¡æœ‰æ‰¾åˆ°è¿™ä½æ­Œæ‰‹çš„æ­Œæ›²ã€‚" if random_artist else "æ²¡æœ‰æ‰¾åˆ°è¿™é¦–æ­Œã€‚"
        ),
        MusicPlaybackError.PLAY_ACTION_FAILED: "å·²æ‰¾åˆ°æ­Œæ›²ï¼Œä½†æ’­æ”¾å™¨æ²¡æœ‰å¼€å§‹æ’­æ”¾ã€‚",
        MusicPlaybackError.MEDIA_SESSION_TIMEOUT: "æ’­æ”¾å™¨æ²¡æœ‰è¿”å›žå½“å‰æ­Œæ›²ä¿¡æ¯ï¼Œæš‚æ—¶æ— æ³•ç¡®è®¤æ˜¯å¦æ’­æ”¾æˆåŠŸã€‚",
        MusicPlaybackError.TRACK_VERIFY_FAILED: "å®žé™…æ’­æ”¾çš„ä¸æ˜¯ç›®æ ‡æ­Œæ›²ï¼Œå·²åœæ­¢é‡è¯•ã€‚",
    }
    return messages[code]


class ExactMusicPlaybackManager:
    """æ‰§è¡Œ search â†’ exact match â†’ play â†’ verifyï¼Œæœ€å¤šé‡è¯•ä¸€æ¬¡ç²¾ç¡®æ’­æ”¾åŠ¨ä½œã€‚"""

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
                    f"æ­£åœ¨æ’­æ”¾ï¼š{selected.artist}ã€Š{selected.title}ã€‹",
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
    """ç”¨äºŽé™ªä¼´åœºæ™¯çš„å®½æ¾éšæœºæ’­æ”¾é—­çŽ¯ã€‚

    ä¸Žç²¾ç¡®ç‚¹æ’­ä¸åŒï¼Œè¿™æ¡è·¯å¾„åªéœ€è¦æŠŠæ’­æ”¾å™¨å¸¦åˆ°ç›®æ ‡æ­Œæ‰‹çš„æ­Œæ›²åŒºåŸŸå¹¶
    å‘èµ·ä¸€æ¬¡çœŸå®žæ’­æ”¾åŠ¨ä½œã€‚åª’ä½“ Session ä»…ä½œä¸ºæ—¥å¿—å’Œå¯é€‰åé¦ˆï¼Œè¯»å–ä¸åˆ°
    å½“å‰æ­Œæ›²æ—¶ä¹Ÿä¸èƒ½é˜»æ­¢åŸºç¡€æ’­æ”¾ã€‚
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
        except UIAutomationUnavailableError as exc:
            self._debug("ui_automation_unavailable", provider, title, artist, error=str(exc))
            native_result = self._try_native_random(adapter, provider, artist)
            if native_result is not None:
                return native_result
            return self._failed(provider, artist, MusicPlaybackError.UI_AUTOMATION_UNAVAILABLE)
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
            # An adapter may expose a native â€œplay artist/randomâ€ action when
            # the client does not expose individual rows through automation.
            native_result = self._try_native_random(adapter, provider, artist)
            if native_result is not None:
                return native_róù¶‰žËkºwµçU…±ÑÉÕ”¤ì(€€€€€M¡½Ý]¥¹‘½Ü¡Ý¥¸°ä¤ì(€€€€€M•Ñ]¥¹‘½ÝA½Ì¡Ý¥¸±¹•Ü%¹ÑAÑÈ ´Ä¤°À°À°À°À°ÁàÄÌ¤ì(€€€€€M•Ñ½É•É½Õ¹‘]¥¹‘½Ü¡Ý¥¸¤ì(€€€€€Q¡É•…¹M±••À ØÀÀ¤ì(€€€€€I•ÐÈì¥˜ …•Ñ]¥¹‘½ÝI•Ð¡Ý¥¸±½ÕÐÈ¤¤ìÉ•ÍÕ±Ðô‰II=IñÉ•ÐôÀˆìÉ•ÑÕÉ¸ìô(€€€€€¥¹ÐÝ¥‘Ñ õÈ¹HµÈ¹0°¡•¥¡ÐõÈ¹µÈ¹Pì(€€€€€€¼¼ƒšBsžÒ‹š†–J3š¶3šnË–6‡ž&–vš2'žª_–>ž&§žB–?žÒƒš¾S’ú/–ºk’ö7¾ò3–ó–ºä€ÄÈÔ”¼ÄÔÀ”ƒ¦®c–"–Æ?Ž(€€€€€±¥¬¡È¹0¬¡¥¹Ð¤¡Ý¥‘Ñ ¨À¸ÌàÔ¤°È¹P¬¡¥¹Ð¤¡¡•¥¡Ð¨À¸ÀÐä¤¤ì(€€€€€Q¡É•…¹M±••À ÈÔÀ¤ì(€€€€€­•å‰‘}•Ù•¹Ð ÁàÄÄ°À°À±U%¹ÑAÑÈ¹i•É¼¤ì-•ä ÁàÐÄ¤ì­•å‰‘}•Ù•¹Ð ÁàÄÄ°À°È±U%¹ÑAÑÈ¹i•É¼¤ì-•ä ÁàÀà¤ì(€€€€€™½É•… ¡¡…È ¥¸ÅÕ•Éä¤-•ä ¡‰åÑ”¥¡…È¹Q½UÁÁ•É%¹Ù…É¥…¹Ð¡ ¤¤ì(€€€€€€¼¼ƒž²³’âš²‡–n{¢ö›–ÆWž’ëšBsžÒ‹–îë¢º»¾ò3ž²³’ê3š²‡–n{¢ö›¦'’â·¦š[šv‡š¶3š&/–îë¢º»–æÛ¢þo–—žîOšzs¦†×Ž(€€€€€-•ä ÁàÁ¤ìQ¡É•…¹M±••À ÄÄÀÀ¤ì-•ä ÁàÁ¤ìQ¡É•…¹M±••À ÐàÀÀ¤ì(€€€€€€¼¼ƒ–öO–&7–º‹š"ßž®¿¦š[šv‡–îë¢º»’âë¦f#š–kžR’âO¢úG¾òoš¾?’â«–>¿¢žš¶3šnË¢†3¦÷šb;ž†»š‚šr'¦f#š–kžRŽ(€€€€€‘½Õ‰±•mtÍ½¹I½ÝÌõìÀ¸ÔÄÐ°À¸Ôàä°À¸ØØÔ°À¸ÜÐÀ°À¸àÄÙôì(€€€€€¥¹ÐÍ½¹`õÈ¹0¬¡¥¹Ð¤¡Ý¥‘Ñ ¨À¸ÌÈ¤ì(€€€€€¥¹ÐÍ½¹dõÈ¹P¬¡¥¹Ð¤¡¡•¥¡Ð©Í½¹I½ÝÍmÁ¥­t¤ì(€€€€€±¥¬¡Í½¹`±Í½¹d¤ìQ¡É•…¹M±••À ÄÈÀ¤ì±¥¬¡Í½¹`±Í½¹d¤ìQ¡É•…¹M±••À ÄàÀÀ¤ì(€€€€€ÍÑÉ¥¹œÑ¥Ñ±”ôˆˆì(€€€€€‘•…‘±¥¹”õ…Ñ•Q¥µ”¹UÑ9½Ü¹‘‘M•½¹‘Ì Ü¤ì(€€€€€Ý¡¥±”¡…Ñ•Q¥µ”¹UÑ9½Üñ‘•…‘±¥¹”¤ì(€€€€€€€Ñ¥Ñ±”õQ¥Ñ±”¡Ý¥¸¤ì(€€€€€€€¥˜¡Ñ¥Ñ±”¹%¹‘•á=˜¡…ÉÑ¥ÍÐ±MÑÉ¥¹½µÁ…É¥Í½¸¹=É‘¥¹…±%¹½É•…Í”¤øôÀ¤‰É•…¬ì(€€€€€€€Q¡É•…¹M±••À ÌÔÀ¤ì(€€€€€ô(€€€€€M•Ñ]¥¹‘½ÝA½Ì¡Ý¥¸±¹•Ü%¹ÑAÑÈ ´È¤°À°À°À°À°ÁàÄÌ¤ì(€€€€€ÑÑ…¡Q¡É•…‘%¹ÁÕÐ¡ÕÉÉ•¹ÑQ¡É•…±Ñ…É•ÑQ¡É•…±™…±Í”¤ì(€€€€€ÑÑ…¡Q¡É•…‘%¹ÁÕÐ¡ÕÉÉ•¹ÑQ¡É•…±™½É•É½Õ¹‘Q¡É•…±™…±Í”¤ì(€€€€€É•ÍÕ±Ðô¡Ñ¥Ñ±”¹%¹‘•á=˜¡…ÉÑ¥ÍÐ±MÑÉ¥¹½µÁ…É¥Í½¸¹=É‘¥¹…±%¹½É•…Í”¤øôÀü‰A1eðˆè‰II=Iðˆ¤¬(€€€€€€€€‰Á¥‘}Ý¥¹‘½Üôˆ­Ý¥¸¹Q½%¹ÐØÐ ¤¬‰ñÑ¥Ñ±”ôˆ­Ñ¥Ñ±”¬‰ñÝ¥‘Ñ ôˆ­Ý¥‘Ñ ¬‰ñ¡•¥¡Ðôˆ­¡•¥¡Ð¬‰ñÁ¥¬ôˆ­Á¥¬ì(€€€ô¤ì(€€€Ñ¡É•…¹M•ÑÁ…ÉÑµ•¹ÑMÑ…Ñ”¡Á…ÉÑµ•¹ÑMÑ…Ñ”¹MQ¤ì(€€€Ñ¡É•…¹MÑ…ÉÐ ¤ìÑ¡É•…¹)½¥¸ ÌÀÀÀÀ¤ì(€€€É•ÑÕÉ¸É•ÍÕ±Ðì(€ô)ô( ì)‘µQåÁ”€‘Í½ÕÉ”(‘É•ÍÕ±Ðõm1¥±¥9•Ñ•…Í••™…Õ±Ñ•Í­Ñ½ÁtèéIÕ¸ ‘ÅÕ•Éä°‘…ÉÑ¥ÍÐ°‘Á¥¬¤)]É¥Ñ”µ=ÕÑÁÕÐ€‘É•ÍÕ±Ð)¥˜ ‘É•ÍÕ±Ð€µ¹½Ñ±¥­”€A1eð¨œ¥ì•á¥Ð€ÄÐô(œœœ(€€€€€€€€¤(()±…ÍÌ-Õ½Õ5ÕÍ¥‘…ÁÑ•È¡]¥¹‘½ÝÍU%ÕÑ½µ…Ñ¥½¹‘…ÁÑ•È¤è(€€€ÁÉ½Ù¥‘•È€ô€‰­Õ½Ôˆ(€€€Ý¥¹‘½Ý}Á…ÑÑ•É¸€ô€ˆ¸¨£¦ßž._¦~Ï’æAñ-Õ½Õñ-5ÕÍ¥Œ¤¸¨ˆ(€€€Í•…É¡}¹…µ•Ì€ô€ ‹šBsžÒˆˆ°€‹šBsžÒ‹¦~Ï’æ@ˆ°€‰M•…É ˆ¤(€€€Á±…å}‰ÕÑÑ½¹}¹…µ•Ì€ô€ ‹šJ·šRøˆ°€‹ž®/–6ÏšJ·šRøˆ°€‰A±…äˆ¤(()±…ÍÌÁÁ±•5ÕÍ¥]¥¹‘½ÝÍ‘…ÁÑ•È¡]¥¹‘½ÝÍU%ÕÑ½µ…Ñ¥½¹‘…ÁÑ•È¤è(€€€ÁÉ½Ù¥‘•È€ô€‰…ÁÁ±”ˆ(€€€Ý¥¹‘½Ý}Á…ÑÑ•É¸€ô€ˆ¸¨¡ÁÁ±”5ÕÍ¥ó¦~Ï’æ@¤¸¨ˆ(€€€Í•…É¡}¹…µ•Ì€ô€ ‹šBsžÒˆˆ°€‰M•…É ˆ¤(€€€Á±…å}‰ÕÑÑ½¹}¹…µ•Ì€ô€ ‹šJ·šRøˆ°€‹šJ·šRûš¶3šnÈˆ°€‰A±…äˆ°€‰A±…äÍ½¹œˆ¤(()±…ÍÌMÁ½Ñ¥™å]¥¹‘½ÝÍ‘…ÁÑ•È¡]¥¹‘½ÝÍU%ÕÑ½µ…Ñ¥½¹‘…ÁÑ•È¤è(€€€ÁÉ½Ù¥‘•È€ô€‰ÍÁ½Ñ¥™äˆ(€€€Ý¥¹‘½Ý}Á…ÑÑ•É¸€ô€ˆ¸©MÁ½Ñ¥™ä¸¨ˆ(€€€Í•…É¡}¹…µ•Ì€ô€ ‹šBsžÒˆˆ°€‰M•…É ˆ¤(€€€Í½¹}Ñ…‰}¹…µ•Ì€ô€ ‹š¶3šnÈˆ°€‰M½¹Ìˆ°€‰QÉ…­Ìˆ¤(€€€Á±…å}‰ÕÑÑ½¹}¹…µ•Ì€ô€ ‹šJ·šRøˆ°€‹šJ·šRûš¶3šnÈˆ°€‰A±…äˆ°€‰A±…äÍ½¹œˆ¤(()‘•˜}•Í…Á•}…ÁÁ±•ÍÉ¥ÁÐ¡Ù…±Õ”èÍÑÈ¤€´øÍÑÈè(€€€É•ÑÕÉ¸Ù…±Õ”¹É•Á±…” ‰qpˆ°€‰qqqpˆ¤¹É•Á±…” œˆœ°€qpˆœ¤(()±…ÍÌÁÁ±•5ÕÍ¥5…‘…ÁÑ•Èè(€€€€ˆˆ‹¦k¢þÁÁ±”Ù•¹ÑÌƒ–r£žR£š"ß¦~Ï’æC¢ÖšZg–êO’â·šBsžÒ‹–æÛšJ·šRûžÊûž†»š¶3šnËŽˆˆˆ((€€€ÁÉ½Ù¥‘•È€ô€‰…ÁÁ±”ˆ((€€€‘•˜}}¥¹¥Ñ}|¡Í•±˜°½µµ…¹‘}ÉÕ¹¹•Èè…±±…‰±•l¸¸¸°ÍÕ‰ÁÉ½•ÍÌ¹½µÁ±•Ñ•‘AÉ½•ÍÍt€ôÍÕ‰ÁÉ½•ÍÌ¹ÉÕ¸¤€´ø9½¹”è(€€€€€€€Í•±˜¹½µµ…¹‘}ÉÕ¹¹•È€ô½µµ…¹‘}ÉÕ¹¹•È((€€€‘•˜Í•…É ¡Í•±˜°Ñ¥Ñ±”èÍÑÈ°…ÉÑ¥ÍÐèÍÑÈ¤€´øM•ÅÕ•¹•mM½¹…¹‘¥‘…Ñ•tè(€€€€€€€ÅÕ•Éä€ô}•Í…Á•}…ÁÁ±•ÍÉ¥ÁÐ¡˜‰í…ÉÑ¥ÍÑôíÑ¥Ñ±•ôˆ¹ÍÑÉ¥À ¤¤(€€€€€€€ÍÉ¥ÁÐ€ô€ (€€€€€€€€€€€€Ñ•±°…ÁÁ±¥…Ñ¥½¸€‰5ÕÍ¥Œ‰q¸œ(€€€€€€€€€€€˜Í•Ð™½Õ¹‘QÉ…­ÌÑ¼Í•…É ±¥‰É…ÉäÁ±…å±¥ÍÐ€Ä™½È€‰íÅÕ•Éåôˆ½¹±äÍ½¹Íq¸œ(€€€€€€€€€€€€Í•Ð½ÕÑÁÕÑQ•áÐÑ¼€ˆ‰q¸œ(€€€€€€€€€€€€É•Á•…ÐÝ¥Ñ …¹‘¥‘…Ñ•QÉ…¬¥¸™½Õ¹‘QÉ…­Íq¸œ(€€€€€€€€€€€€Í•Ð½ÕÑÁÕÑQ•áÐÑ¼½ÕÑÁÕÑQ•áÐ€˜€¡¹…µ”½˜…¹‘¥‘…Ñ•QÉ…¬…ÌÑ•áÐ¤€˜Ñ…ˆ€˜€œ(€€€€€€€€€€€€œ¡…ÉÑ¥ÍÐ½˜…¹‘¥‘…Ñ•QÉ…¬…ÌÑ•áÐ¤€˜Ñ…ˆ€˜€¡Á•ÉÍ¥ÍÑ•¹Ð%½˜…¹‘¥‘…Ñ•QÉ…¬…ÌÑ•áÐ¤€˜±¥¹•™••‘q¸œ(€€€€€€€€€€€€•¹É•Á•…Ñq¹É•ÑÕÉ¸½ÕÑÁÕÑQ•áÑq¹•¹Ñ•±°œ(€€€€€€€€¤(€€€€€€€½µÁ±•Ñ•€ôÍ•±˜¹}ÉÕ¸¡ÍÉ¥ÁÐ¤(€€€€€€€…¹‘¥‘…Ñ•Ìè±¥ÍÑmM½¹…¹‘¥‘…Ñ•t€ômt(€€€€€€€™½È±¥¹”¥¸ÍÑÈ¡½µÁ±•Ñ•¹ÍÑ‘½ÕÐ½È€ˆˆ¤¹ÍÁ±¥Ñ±¥¹•Ì ¤è(€€€€€€€€€€€Á…ÉÑÌ€ô±¥¹”¹ÍÁ±¥Ð ‰qÐˆ¤(€€€€€€€€€€€¥˜±•¸¡Á…ÉÑÌ¤€øô€Ìè(€€€€€€€€€€€€€€€…¹‘¥‘…Ñ•Ì¹…ÁÁ•¹¡M½¹…¹‘¥‘…Ñ”¡Í•±˜¹ÁÉ½Ù¥‘•È°Á…ÉÑÍlÁt°Á…ÉÑÍlÅt°€‰Í½¹œˆ°Á…ÉÑÍlÉt¤¤(€€€€€€€É•ÑÕÉ¸…¹‘¥‘…Ñ•Ì((€€€‘•˜Á±…ä¡Í•±˜°…¹‘¥‘…Ñ”èM½¹…¹‘¥‘…Ñ”¤€´ø‰½½°è(€€€€€€€¥‘•¹Ñ¥™¥•È€ô}•Í…Á•}…ÁÁ±•ÍÉ¥ÁÐ¡…¹‘¥‘…Ñ”¹¥‘•¹Ñ¥™¥•È¤(€€€€€€€¥˜¹½Ð¥‘•¹Ñ¥™¥•Èè(€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€€€€€ÍÉ¥ÁÐ€ô€ (€€€€€€€€€€€€Ñ•±°…ÁÁ±¥…Ñ¥½¸€‰5ÕÍ¥Œ‰q¸œ(€€€€€€€€€€€˜Í•ÐÑ…É•ÑQÉ…¬Ñ¼™¥ÉÍÐÑÉ…¬½˜±¥‰É…ÉäÁ±…å±¥ÍÐ€ÄÝ¡½Í”Á•ÉÍ¥ÍÑ•¹Ð%¥Ì€‰í¥‘•¹Ñ¥™¥•Éô‰q¸œ(€€€€€€€€€€€€Á±…äÑ…É•ÑQÉ…­q¹•¹Ñ•±°œ(€€€€€€€€¤(€€€€€€€ÑÉäè(€€€€€€€€€€€Í•±˜¹}ÉÕ¸¡ÍÉ¥ÁÐ¤(€€€€€€€•á•ÁÐAÉ½Ù¥‘•ÉM•…É¡ÉÉ½Èè(€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€€€€€É•ÑÕÉ¸QÉÕ”((€€€‘•˜}ÉÕ¸¡Í•±˜°ÍÉ¥ÁÐèÍÑÈ¤€´øÍÕ‰ÁÉ½•ÍÌ¹½µÁ±•Ñ•‘AÉ½•ÍÌè(€€€€€€€ÑÉäè(€€€€€€€€€€€½µÁ±•Ñ•€ôÍ•±˜¹½µµ…¹‘}ÉÕ¹¹•È (€€€€€€€€€€€€€€€l‰½Í…ÍÉ¥ÁÐˆ°€ˆµ”ˆ°ÍÉ¥ÁÑt°(€€€€€€€€€€€€€€€…ÁÑÕÉ•}½ÕÑÁÕÐõQÉÕ”°(€€€€€€€€€€€€€€€Ñ•áÐõQÉÕ”°(€€€€€€€€€€€€€€€Ñ¥µ•½ÕÐôÄÀ°(€€€€€€€€€€€€€€€¡•¬õ…±Í”°(€€€€€€€€€€€€¤(€€€€€€€•á•ÁÐ€¡=MÉÉ½È°ÍÕ‰ÁÉ½•ÍÌ¹Q¥µ•½ÕÑáÁ¥É•¤…Ì•áŒè(€€€€€€€€€€€É…¥Í”AÉ½Ù¥‘•ÉM•…É¡ÉÉ½È ‰ÁÁ±”Ù•¹ÑÌÉ•ÅÕ•ÍÐ™…¥±•ˆ¤™É½´•áŒ(€€€€€€€¥˜½µÁ±•Ñ•¹É•ÑÕÉ¹½‘”€„ô€Àè(€€€€€€€€€€€É…¥Í”AÉ½Ù¥‘•ÉM•…É¡ÉÉ½È¡ÍÑÈ¡½µÁ±•Ñ•¹ÍÑ‘•ÉÈ½È€‰ÁÁ±”Ù•¹ÑÌÉ•ÅÕ•ÍÐ™…¥±•ˆ¤¤(€€€€€€€É•ÑÕÉ¸½µÁ±•Ñ•(()±…ÍÌ5…•ÍÍ¥‰¥±¥Ñå‘…ÁÑ•Èè(€€€€ˆˆ‹–r£–ÞËš:#šv•ÍÍ¥‰¥±¥Ñäƒš^Û–>«ž
ç–ï–B3š^Û–2–B¯žÊûž†»š¶3–B7–J3š¶3š&/žjš¶3šnËžîOšzsŽˆˆˆ((€€€ÁÉ½Ù¥‘•È€ô€ˆˆ(€€€…ÁÁ±¥…Ñ¥½¹}¹…µ”€ô€ˆˆ(€€€Í½¹}Ñ…‰}¹…µ•ÌèÑÕÁ±•mÍÑÈ°€¸¸¹t€ô€ ‹š¶3šnÈˆ°€‰M½¹Ìˆ°€‰QÉ…­Ìˆ¤(€€€Á±…å}‰ÕÑÑ½¹}¹…µ•ÌèÑÕÁ±•mÍÑÈ°€¸¸¹t€ô€ ‹šJ·šRøˆ°€‹šJ·šRûš¶3šnÈˆ°€‰A±…äˆ°€‰A±…äÍ½¹œˆ¤((€€€‘•˜}}¥¹¥Ñ}|¡Í•±˜°½µµ…¹‘}ÉÕ¹¹•Èè…±±…‰±•l¸¸¸°ÍÕ‰ÁÉ½•ÍÌ¹½µÁ±•Ñ•‘AÉ½•ÍÍt€ôÍÕ‰ÁÉ½•ÍÌ¹ÉÕ¸¤€´ø9½¹”è(€€€€€€€Í•±˜¹½µµ…¹‘}ÉÕ¹¹•È€ô½µµ…¹‘}ÉÕ¹¹•È((€€€‘•˜Í•…É ¡Í•±˜°Ñ¥Ñ±”èÍÑÈ°…ÉÑ¥ÍÐèÍÑÈ¤€´øM•ÅÕ•¹•mM½¹…¹‘¥‘…Ñ•tè(€€€€€€€ÅÕ•Éä€ôÕÉ±±¥ˆ¹Á…ÉÍ”¹ÅÕ½Ñ”¡˜‰í…ÉÑ¥ÍÑôíÑ¥Ñ±•ôˆ¤(€€€€€€€ÕÉ¤€ôÍ•±˜¹}Í•…É¡}ÕÉ¤¡ÅÕ•Éä¤(€€€€€€€ÑÉäè(€€€€€€€€€€€½Á•¹•€ôÍ•±˜¹½µµ…¹‘}ÉÕ¹¹•È (€€€€€€€€€€€€€€€l‰½Á•¸ˆ°€ˆµ„ˆ°Í•±˜¹…ÁÁ±¥…Ñ¥½¹}¹…µ”°ÕÉ¥t°(€€€€€€€€€€€€€€€…ÁÑÕÉ•}½ÕÑÁÕÐõQÉÕ”°(€€€€€€€€€€€€€€€Ñ•áÐõQÉÕ”°(€€€€€€€€€€€€€€€Ñ¥µ•½ÕÐôà°(€€€€€€€€€€€€€€€¡•¬õ…±Í”°(€€€€€€€€€€€€¤(€€€€€€€•á•ÁÐ€¡=MÉÉ½È°ÍÕ‰ÁÉ½•ÍÌ¹Q¥µ•½ÕÑáÁ¥É•¤…Ì•áŒè(€€€€€€€€€€€É…¥Í”AÉ½Ù¥‘•ÉM•…É¡ÉÉ½È ‰±¥•¹ÐÍ•…É ½Õ±¹½Ð‰”½Á•¹•ˆ¤™É½´•áŒ(€€€€€€€¥˜½Á•¹•¹É•ÑÕÉ¹½‘”€„ô€Àè(€€€€€€€€€€€É…¥Í”AÉ½Ù¥‘•ÉM•…É¡ÉÉ½È¡ÍÑÈ¡½Á•¹•¹ÍÑ‘•ÉÈ½È€‰±¥•¹ÐÍ•…É ½Õ±¹½Ð‰”½Á•¹•ˆ¤¤(€€€€€€€¥˜¹½ÐÑ¥Ñ±”è(€€€€€€€€€€€½µÁ±•Ñ•€ôÍ•±˜¹}ÉÕ¹}…•ÍÍ¥‰¥±¥Ñä¡Í•±˜¹}…ÉÑ¥ÍÑ}…¹‘¥‘…Ñ•Í}ÍÉ¥ÁÐ¡…ÉÑ¥ÍÐ¤¤(€€€€€€€€€€€…¹‘¥‘…Ñ•Ìè±¥ÍÑmM½¹…¹‘¥‘…Ñ•t€ômt(€€€€€€€€€€€™½È±¥¹”¥¸ÍÑÈ¡½µÁ±•Ñ•¹ÍÑ‘½ÕÐ½È€ˆˆ¤¹ÍÁ±¥Ñ±¥¹•Ì ¤è(€€€€€€€€€€€€€€€Á…ÉÑÌ€ô±¥¹”¹ÍÁ±¥Ð ‰qÐˆ¤(€€€€€€€€€€€€€€€¥˜±•¸¡Á…ÉÑÌ¤€øô€È…¹Á…ÉÑÍlÁt¹ÍÑÉ¥À ¤…¹}Í…µ•}Í½¹œ¡Á…ÉÑÍlÅt°…ÉÑ¥ÍÐ¤è(€€€€€€€€€€€€€€€€€€€…¹‘¥‘…Ñ•Ì¹…ÁÁ•¹¡M½¹…¹‘¥‘…Ñ”¡Í•±˜¹ÁÉ½Ù¥‘•È°Á…ÉÑÍlÁt°Á…ÉÑÍlÅt°€‰Í½¹œˆ¤¤(€€€€€€€€€€€É•ÑÕÉ¸…¹‘¥‘…Ñ•Ì(€€€€€€€ÍÉ¥ÁÐ€ôÍ•±˜¹}…•ÍÍ¥‰¥±¥Ñå}ÍÉ¥ÁÐ¡Ñ¥Ñ±”°…ÉÑ¥ÍÐ°Á±…äõ…±Í”¤(€€€€€€€½µÁ±•Ñ•€ôÍ•±˜¹}ÉÕ¹}…•ÍÍ¥‰¥±¥Ñä¡ÍÉ¥ÁÐ¤(€€€€€€€¥˜ÍÑÈ¡½µÁ±•Ñ•¹ÍÑ‘½ÕÐ½È€ˆˆ¤¹ÍÑÉ¥À ¤€ôô€‰5Q ˆè(€€€€€€€€€€€É•ÑÕÉ¸€¡M½¹…¹‘¥‘…Ñ”¡Í•±˜¹ÁÉ½Ù¥‘•È°Ñ¥Ñ±”°…ÉÑ¥ÍÐ°€‰Í½¹œˆ°˜‰íÑ¥Ñ±•õqÑí…ÉÑ¥ÍÑôˆ¤°¤(€€€€€€€É•ÑÕÉ¸€ ¤((€€€‘•˜Á±…ä¡Í•±˜°…¹‘¥‘…Ñ”èM½¹…¹‘¥‘…Ñ”¤€´ø‰½½°è(€€€€€€€½µÁ±•Ñ•€ôÍ•±˜¹}ÉÕ¹}…•ÍÍ¥‰¥±¥Ñä (€€€€€€€€€€€Í•±˜¹}…•ÍÍ¥‰¥±¥Ñå}ÍÉ¥ÁÐ¡…¹‘¥‘…Ñ”¹Ñ¥Ñ±”°…¹‘¥‘…Ñ”¹…ÉÑ¥ÍÐ°Á±…äõQÉÕ”¤(€€€€€€€€¤(€€€€€€€É•ÑÕÉ¸ÍÑÈ¡½µÁ±•Ñ•¹ÍÑ‘½ÕÐ½È€ˆˆ¤¹ÍÑÉ¥À ¤€ôô€‰A1eˆ((€€€‘•˜}ÉÕ¹}…•ÍÍ¥‰¥±¥Ñä¡Í•±˜°ÍÉ¥ÁÐèÍÑÈ¤€´øÍÕ‰ÁÉ½•ÍÌ¹½µÁ±•Ñ•‘AÉ½•ÍÌè(€€€€€€€ÑÉäè(€€€€€€€€€€€½µÁ±•Ñ•€ôÍ•±˜¹½µµ…¹‘}ÉÕ¹¹•È (€€€€€€€€€€€€€€€l‰½Í…ÍÉ¥ÁÐˆ°€ˆµ”ˆ°ÍÉ¥ÁÑt°(€€€€€€€€€€€€€€€…ÁÑÕÉ•}½ÕÑÁÕÐõQÉÕ”°(€€€€€€€€€€€€€€€Ñ•áÐõQÉÕ”°(€€€€€€€€€€€€€€€Ñ¥µ•½ÕÐôÄÈ°(€€€€€€€€€€€€€€€¡•¬õ…±Í”°(€€€€€€€€€€€€¤(€€€€€€€•á•ÁÐ€¡=MÉÉ½È°ÍÕ‰ÁÉ½•ÍÌ¹Q¥µ•½ÕÑáÁ¥É•¤…Ì•áŒè(€€€€€€€€€€€É…¥Í”AÉ½Ù¥‘•ÉM•…É¡ÉÉ½È ‰•ÍÍ¥‰¥±¥ÑäÉ•ÅÕ•ÍÐ™…¥±•ˆ¤™É½´•áŒ(€€€€€€€¥˜½µÁ±•Ñ•¹É•ÑÕÉ¹½‘”€„ô€Àè(€€€€€€€€€€€É…¥Í”AÉ½Ù¥‘•ÉM•…É¡ÉÉ½È¡ÍÑÈ¡½µÁ±•Ñ•¹ÍÑ‘•ÉÈ½È€‰•ÍÍ¥‰¥±¥ÑäÉ•ÅÕ•ÍÐ™…¥±•ˆ¤¤(€€€€€€€É•ÑÕÉ¸½µÁ±•Ñ•((€€€‘•˜}…•ÍÍ¥‰¥±¥Ñå}ÍÉ¥ÁÐ¡Í•±˜°Ñ¥Ñ±”èÍÑÈ°…ÉÑ¥ÍÐèÍÑÈ°€¨°Á±…äè‰½½°¤€´øÍÑÈè(€€€€€€€Í…™•}…ÁÀ€ô}•Í…Á•}…ÁÁ±•ÍÉ¥ÁÐ¡Í•±˜¹…ÁÁ±¥…Ñ¥½¹}¹…µ”¤(€€€€€€€Í…™•}Ñ¥Ñ±”€ô}•Í…Á•}…ÁÁ±•ÍÉ¥ÁÐ¡Ñ¥Ñ±”¤(€€€€€€€Í…™•}…ÉÑ¥ÍÐ€ô}•Í…Á•}…ÁÁ±•ÍÉ¥ÁÐ¡…ÉÑ¥ÍÐ¤(€€€€€€€Ñ…‰Ì€ô€ˆ°€ˆ¹©½¥¸¡˜œ‰í}•Í…Á•}…ÁÁ±•ÍÉ¥ÁÐ¡¹…µ”¥ôˆœ™½È¹…µ”¥¸Í•±˜¹Í½¹}Ñ…‰}¹…µ•Ì¤(€€€€€€€‰ÕÑÑ½¹Ì€ô€ˆ°€ˆ¹©½¥¸¡˜œ‰í}•Í…Á•}…ÁÁ±•ÍÉ¥ÁÐ¡¹…µ”¥ôˆœ™½È¹…µ”¥¸Í•±˜¹Á±…å}‰ÕÑÑ½¹}¹…µ•Ì¤(€€€€€€€…Ñ¥½¸€ô€‰ÑÉÕ”ˆ¥˜Á±…ä•±Í”€‰™…±Í”ˆ(€€€€€€€É•ÑÕÉ¸˜œœœ)Ñ•±°…ÁÁ±¥…Ñ¥½¸€‰íÍ…™•}…ÁÁôˆÑ¼…Ñ¥Ù…Ñ”)‘•±…ä€Ä¸Ô)Ñ•±°…ÁÁ±¥…Ñ¥½¸€‰MåÍÑ•´Ù•¹ÑÌˆ(€¥˜U$•±•µ•¹ÑÌ•¹…‰±•¥Ì™…±Í”Ñ¡•¸•ÉÉ½È€‰•ÍÍ¥‰¥±¥ÑäÁ•Éµ¥ÍÍ¥½¸É•ÅÕ¥É•ˆ(€Ñ•±°ÁÉ½•ÍÌ€‰íÍ…™•}…ÁÁôˆ(€€€Í•Ð™É½¹Ñµ½ÍÐÑ¼ÑÉÕ”(€€€Í•ÐÑ…‰9…µ•ÌÑ¼íííÑ…‰Íõõô(€€€Í•ÐÁ±…å9…µ•ÌÑ¼ííí‰ÕÑÑ½¹Íõõô(€€€ÑÉä(€€€€€É•Á•…ÐÝ¥Ñ ¥Ñ•µI•˜¥¸€¡•¹Ñ¥É”½¹Ñ•¹ÑÌ½˜™É½¹ÐÝ¥¹‘½Ü¤(€€€€€€€ÑÉä(€€€€€€€€€¥˜€¡¹…µ”½˜¥Ñ•µI•˜…ÌÑ•áÐ¤¥Ì¥¸Ñ…‰9…µ•ÌÑ¡•¸±¥¬¥Ñ•µI•˜(€€€€€€€•¹ÑÉä(€€€€€•¹É•Á•…Ð(€€€•¹ÑÉä(€€€‘•±…ä€Ä¸À(€€€É•Á•…ÐÝ¥Ñ Ñ¥Ñ±•±•µ•¹Ð¥¸€¡•¹Ñ¥É”½¹Ñ•¹ÑÌ½˜™É½¹ÐÝ¥¹‘½Ü¤(€€€€€ÑÉä(€€€€€€€¥˜€¡¹…µ”½˜Ñ¥Ñ±•±•µ•¹Ð…ÌÑ•áÐ¤¥Ì€‰íÍ…™•}Ñ¥Ñ±•ôˆÑ¡•¸(€€€€€€€€€Í•ÐÉ½Ý±•µ•¹ÐÑ¼Á…É•¹Ð½˜Ñ¥Ñ±•±•µ•¹Ð(€€€€€€€€€É•Á•…Ð€ØÑ¥µ•Ì(€€€€€€€€€€€Í•Ð…ÉÑ¥ÍÑ½Õ¹Ñ¼™…±Í”(€€€€€€€€€€€É•Á•…ÐÝ¥Ñ ¡¥±‘±•µ•¹Ð¥¸€¡•¹Ñ¥É”½¹Ñ•¹ÑÌ½˜É½Ý±•µ•¹Ð¤(€€€€€€€€€€€€€ÑÉä(€€€€€€€€€€€€€€€¥˜€¡¹…µ”½˜¡¥±‘±•µ•¹Ð…ÌÑ•áÐ¤¥Ì€‰íÍ…™•}…ÉÑ¥ÍÑôˆÑ¡•¸Í•Ð…ÉÑ¥ÍÑ½Õ¹Ñ¼ÑÉÕ”(€€€€€€€€€€€€€•¹ÑÉä(€€€€€€€€€€€•¹É•Á•…Ð(€€€€€€€€€€€¥˜…ÉÑ¥ÍÑ½Õ¹Ñ¡•¸(€€€€€€€€€€€€€¥˜í…Ñ¥½¹ôÑ¡•¸(€€€€€€€€€€€€€€€É•Á•…ÐÝ¥Ñ ¡¥±‘±•µ•¹Ð¥¸€¡•¹Ñ¥É”½¹Ñ•¹ÑÌ½˜É½Ý±•µ•¹Ð¤(€€€€€€€€€€€€€€€€€ÑÉä(€€€€€€€€€€€€€€€€€€€¥˜€¡¹…µ”½˜¡¥±‘±•µ•¹Ð…ÌÑ•áÐ¤¥Ì¥¸Á±…å9…µ•ÌÑ¡•¸(€€€€€€€€€€€€€€€€€€€€€±¥¬¡¥±‘±•µ•¹Ð(€€€€€€€€€€€€€€€€€€€€€É•ÑÕÉ¸€‰A1eˆ(€€€€€€€€€€€€€€€€€€€•¹¥˜(€€€€€€€€€€€€€€€€€•¹ÑÉä(€€€€€€€€€€€€€€€•¹É•Á•…Ð(€€€€€€€€€€€€€€€É•ÑÕÉ¸€‰5Q!}9=}A1e}	UQQ=8ˆ(€€€€€€€€€€€€€•¹¥˜(€€€€€€€€€€€€€É•ÑÕÉ¸€‰5Q ˆ(€€€€€€€€€€€•¹¥˜(€€€€€€€€€€€Í•ÐÉ½Ý±•µ•¹ÐÑ¼Á…É•¹Ð½˜É½Ý±•µ•¹Ð(€€€€€€€€€•¹É•Á•…Ð(€€€€€€€•¹¥˜(€€€€€•¹ÑÉä(€€€•¹É•Á•…Ð(€•¹Ñ•±°)•¹Ñ•±°)É•ÑÕÉ¸€‰9=}5Q ˆ(œœœ((€€€‘•˜}…ÉÑ¥ÍÑ}…¹‘¥‘…Ñ•Í}ÍÉ¥ÁÐ¡Í•±˜°…ÉÑ¥ÍÐèÍÑÈ¤€´øÍÑÈè(€€€€€€€€ˆˆ‹–r£š¶3šnËš‚ž¶û¦†×’â·¢¾ï–>[š¶3š&/žÊûž†»–2ç¦7¢†3žjš¶3–B7¾ò3’â7ž
ç–ï¦j?šrë¦†×¦v‹–žÒƒŽˆˆˆ((€€€€€€€Í…™•}…ÁÀ€ô}•Í…Á•}…ÁÁ±•ÍÉ¥ÁÐ¡Í•±˜¹…ÁÁ±¥…Ñ¥½¹}¹…µ”¤(€€€€€€€Í…™•}…ÉÑ¥ÍÐ€ô}•Í…Á•}…ÁÁ±•ÍÉ¥ÁÐ¡…ÉÑ¥ÍÐ¤(€€€€€€€Ñ…‰Ì€ô€ˆ°€ˆ¹©½¥¸¡˜œ‰í}•Í…Á•}…ÁÁ±•ÍÉ¥ÁÐ¡¹…µ”¥ôˆœ™½È¹…µ”¥¸Í•±˜¹Í½¹}Ñ…‰}¹…µ•Ì¤(€€€€€€€‰ÕÑÑ½¹Ì€ô€ˆ°€ˆ¹©½¥¸¡˜œ‰í}•Í…Á•}…ÁÁ±•ÍÉ¥ÁÐ¡¹…µ”¥ôˆœ™½È¹…µ”¥¸Í•±˜¹Á±…å}‰ÕÑÑ½¹}¹…µ•Ì¤(€€€€€€€É•ÑÕÉ¸˜œœœ)Ñ•±°…ÁÁ±¥…Ñ¥½¸€‰íÍ…™•}…ÁÁôˆÑ¼…Ñ¥Ù…Ñ”)‘•±…ä€Ä¸Ô)Ñ•±°…ÁÁ±¥…Ñ¥½¸€‰MåÍÑ•´Ù•¹ÑÌˆ(€¥˜U$•±•µ•¹ÑÌ•¹…‰±•¥Ì™…±Í”Ñ¡•¸•ÉÉ½È€‰•ÍÍ¥‰¥±¥ÑäÁ•Éµ¥ÍÍ¥½¸É•ÅÕ¥É•ˆ(€Ñ•±°ÁÉ½•ÍÌ€‰íÍ…™•}…ÁÁôˆ(€€€Í•ÐÑ…‰9…µ•ÌÑ¼íííÑ…‰Íõõô(€€€Í•ÐÁ±…å9…µ•ÌÑ¼ííí‰ÕÑÑ½¹Íõõô(€€€É•Á•…ÐÝ¥Ñ ¥Ñ•µI•˜¥¸€¡•¹Ñ¥É”½¹Ñ•¹ÑÌ½˜™É½¹ÐÝ¥¹‘½Ü¤(€€€€€ÑÉä(€€€€€€€¥˜€¡¹…µ”½˜¥Ñ•µI•˜…ÌÑ•áÐ¤¥Ì¥¸Ñ…‰9…µ•ÌÑ¡•¸±¥¬¥Ñ•µI•˜(€€€€€•¹ÑÉä(€€€•¹É•Á•…Ð(€€€‘•±…ä€Ä¸À(€€€Í•Ð½ÕÑÁÕÑQ•áÐÑ¼€ˆˆ(€€€É•Á•…ÐÝ¥Ñ …ÉÑ¥ÍÑ±•µ•¹Ð¥¸€¡•¹Ñ¥É”½¹Ñ•¹ÑÌ½˜™É½¹ÐÝ¥¹‘½Ü¤(€€€€€ÑÉä(€€€€€€€¥˜€¡¹…µ”½˜…ÉÑ¥ÍÑ±•µ•¹Ð…ÌÑ•áÐ¤¥Ì€‰íÍ…™•}…ÉÑ¥ÍÑôˆÑ¡•¸(€€€€€€€€€Í•ÐÉ½Ý±•µ•¹ÐÑ¼Á…É•¹Ð½˜…ÉÑ¥ÍÑ±•µ•¹Ð(€€€€€€€€€É•Á•…Ð€ØÑ¥µ•Ì(€€€€€€€€€€€Í•ÐÑ¥Ñ±•Q•áÐÑ¼€ˆˆ(€€€€€€€€€€€É•Á•…ÐÝ¥Ñ ¡¥±‘±•µ•¹Ð¥¸€¡•¹Ñ¥É”½¹Ñ•¹ÑÌ½˜É½Ý±•µ•¹Ð¤(€€€€€€€€€€€€€ÑÉä(€€€€€€€€€€€€€€€Í•Ð¡¥±‘9…µ”Ñ¼¹…µ”½˜¡¥±‘±•µ•¹Ð…ÌÑ•áÐ(€€€€€€€€€€€€€€€¥˜¡¥±‘9…µ”¥Ì¹½Ð€ˆˆ…¹¡¥±‘9…µ”¥Ì¹½Ð€‰íÍ…™•}…ÉÑ¥ÍÑôˆ…¹¡¥±‘9…µ”¥Ì¹½Ð¥¸Ñ…‰9…µ•Ì…¹¡¥±‘9…µ”¥Ì¹½Ð¥¸Á±…å9…µ•ÌÑ¡•¸(€€€€€€€€€€€€€€€€€Í•ÐÑ¥Ñ±•Q•áÐÑ¼¡¥±‘9…µ”(€€€€€€€€€€€€€€€€€•á¥ÐÉ•Á•…Ð(€€€€€€€€€€€€€€€•¹¥˜(€€€€€€€€€€€€€•¹ÑÉä(€€€€€€€€€€€•¹É•Á•…Ð(€€€€€€€€€€€¥˜Ñ¥Ñ±•Q•áÐ¥Ì¹½Ð€ˆˆÑ¡•¸(€€€€€€€€€€€€€Í•Ð½ÕÑÁÕÑQ•áÐÑ¼½ÕÑÁÕÑQ•áÐ€˜Ñ¥Ñ±•Q•áÐ€˜Ñ…ˆ€˜€‰íÍ…™•}…ÉÑ¥ÍÑôˆ€˜±¥¹•™••(€€€€€€€€€€€€€•á¥ÐÉ•Á•…Ð(€€€€€€€€€€€•¹¥˜(€€€€€€€€€€€Í•ÐÉ½Ý±•µ•¹ÐÑ¼Á…É•¹Ð½˜É½Ý±•µ•¹Ð(€€€€€€€€€•¹É•Á•…Ð(€€€€€€€•¹¥˜(€€€€€•¹ÑÉä(€€€•¹É•Á•…Ð(€€€É•ÑÕÉ¸½ÕÑÁÕÑQ•áÐ(€•¹Ñ•±°)•¹Ñ•±°(œœœ((€€€‘•˜}Í•…É¡}ÕÉ¤¡Í•±˜°ÅÕ•ÉäèÍÑÈ¤€´øÍÑÈè(€€€€€€€É…¥Í”9½Ñ%µÁ±•µ•¹Ñ•‘ÉÉ½È(()±…ÍÌEE5ÕÍ¥5…‘…ÁÑ•È¡5…•ÍÍ¥‰¥±¥Ñå‘…ÁÑ•È¤è(€€€ÁÉ½Ù¥‘•È€ô€‰ÅÄˆ(€€€…ÁÁ±¥…Ñ¥½¹}¹…µ”€ô€‰EE5ÕÍ¥Œˆ(€€€Á±…å}‰ÕÑÑ½¹}¹…µ•Ì€ô€ ‹šJ·šRøˆ°€‹šJ·šRûš¶3šnÈˆ°€‰A±…äˆ¤((€€€‘•˜}Í•…É¡}ÕÉ¤¡Í•±˜°ÅÕ•ÉäèÍÑÈ¤€´øÍÑÈè(€€€€€€€É•ÑÕÉ¸˜‰¡ÑÑÁÌè¼½ä¹ÅÄ¹½´½¸½ÉåÅÄ½Í•…É ýÜõíÅÕ•Éåô™ÐõÍ½¹œˆ(()±…ÍÌ9•Ñ•…Í•5ÕÍ¥5…‘…ÁÑ•È¡5…•ÍÍ¥‰¥±¥Ñå‘…ÁÑ•È¤è(€€€ÁÉ½Ù¥‘•È€ô€‰¹•Ñ•…Í”ˆ(€€€…ÁÁ±¥…Ñ¥½¹}¹…µ”€ô€‰9•Ñ•…Í•5ÕÍ¥Œˆ(€€€Á±…å}‰ÕÑÑ½¹}¹…µ•Ì€ô€ ‹šJ·šRøˆ°€‹ž®/–6ÏšJ·šRøˆ°€‰A±…äˆ¤((€€€‘•˜}Í•…É¡}ÕÉ¤¡Í•±˜°ÅÕ•ÉäèÍÑÈ¤€´øÍÑÈè(€€€€€€€É•ÑÕÉ¸˜‰¡ÑÑÁÌè¼½µÕÍ¥Œ¸ÄØÌ¹½´¼Œ½Í•…É ½´¼ýÌõíÅÕ•Éåô™ÑåÁ”ôÄˆ(()±…ÍÌ-Õ½Õ5ÕÍ¥5…‘…ÁÑ•È¡5…•ÍÍ¥‰¥±¥Ñå‘…ÁÑ•È¤è(€€€ÁÉ½Ù¥‘•È€ô€‰­Õ½Ôˆ(€€€…ÁÁ±¥…Ñ¥½¹}¹…µ”€ô€‰-Õ½Õ5ÕÍ¥Œˆ(€€€Á±…å}‰ÕÑÑ½¹}¹…µ•Ì€ô€ ‹šJ·šRøˆ°€‹ž®/–6ÏšJ·šRøˆ°€‰A±…äˆ¤((€€€‘•˜}Í•…É¡}ÕÉ¤¡Í•±˜°ÅÕ•ÉäèÍÑÈ¤€´øÍÑÈè(€€€€€€€É•ÑÕÉ¸˜‰¡ÑÑÁÌè¼½ÝÝÜ¹­Õ½Ô¹½´½åä½¡Ñµ°½Í•…É ¹¡Ñµ°Í•…É¡QåÁ”õÍ½¹œ™Í•…É¡-•å]½ÉõíÅÕ•Éåôˆ(()±…ÍÌMÁ½Ñ¥™å5…‘…ÁÑ•È¡5…•ÍÍ¥‰¥±¥Ñå‘…ÁÑ•È¤è(€€€ÁÉ½Ù¥‘•È€ô€‰ÍÁ½Ñ¥™äˆ(€€€…ÁÁ±¥…Ñ¥½¹}¹…µ”€ô€‰MÁ½Ñ¥™äˆ(€€€Í½¹}Ñ…‰}¹…µ•Ì€ô€ ‹š¶3šnÈˆ°€‰M½¹Ìˆ°€‰QÉ…­Ìˆ¤(€€€Á±…å}‰ÕÑÑ½¹}¹…µ•Ì€ô€ ‹šJ·šRøˆ°€‹šJ·šRûš¶3šnÈˆ°€‰A±…äˆ°€‰A±…äÍ½¹œˆ¤((€€€‘•˜}Í•…É¡}ÕÉ¤¡Í•±˜°ÅÕ•ÉäèÍÑÈ¤€´øÍÑÈè(€€€€€€€É•ÑÕÉ¸˜‰ÍÁ½Ñ¥™äéÍ•…É éíÅÕ•Éåôˆ(()‘•˜‰Õ¥±‘}ÁÉ½Ù¥‘•É}…‘…ÁÑ•ÉÌ (€€€Í•ÑÑ¥¹ÌèA•ÑM•ÑÑ¥¹Ì°(€€€€¨°(€€€Á±…Ñ™½Éµ}¹…µ”èÍÑÈð9½¹”€ô9½¹”°(€€€±¥•¹Ñ}™¥¹‘•Èè…±±…‰±•mmÍÑÈ°ÍÑÉt°A…Ñ ð9½¹•t€ô™¥¹‘}µÕÍ¥}±¥•¹Ð°(€€€½µµ…¹‘}ÉÕ¹¹•Èè…±±…‰±•l¸¸¸°ÍÕ‰ÁÉ½•ÍÌ¹½µÁ±•Ñ•‘AÉ½•ÍÍt€ôÍÕ‰ÁÉ½•ÍÌ¹ÉÕ¸°(¤€´ø‘¥ÑmÍÑÈ°5ÕÍ¥AÉ½Ù¥‘•É‘…ÁÑ•Étè(€€€€ˆˆ‹š2'–öO–&7žÎïžîšz¦ƒ’êS’â«–öóš¶“ž.³ž®/žjAÉ½Ù¥‘•È‘…ÁÑ•ËŽˆˆˆ((€€€Á±…Ñ™½É´€ôÁ±…Ñ™½Éµ}¹…µ”½ÈÍåÌ¹Á±…Ñ™½É´(€€€¥˜Á±…Ñ™½É´€ôô€‰Ý¥¸ÌÈˆè(€€€€€€€É•ÑÕÉ¸ì(€€€€€€€€€€€€‰ÅÄˆèEE5ÕÍ¥‘…ÁÑ•È¡Í•ÑÑ¥¹Ì°±¥•¹Ñ}™¥¹‘•Èõ±¥•¹Ñ}™¥¹‘•È¤°(€€€€€€€€€€€€‰¹•Ñ•…Í”ˆè9•Ñ•…Í•5ÕÍ¥‘…ÁÑ•È¡Í•ÑÑ¥¹Ì°±¥•¹Ñ}™¥¹‘•Èõ±¥•¹Ñ}™¥¹‘•È¤°(€€€€€€€€€€€€‰­Õ½Ôˆè-Õ½Õ5ÕÍ¥‘…ÁÑ•È¡Í•ÑÑ¥¹Ì°±¥•¹Ñ}™¥¹‘•Èõ±¥•¹Ñ}™¥¹‘•È¤°(€€€€€€€€€€€€‰…ÁÁ±”ˆèÁÁ±•5ÕÍ¥]¥¹‘½ÝÍ‘…ÁÑ•È¡Í•ÑÑ¥¹Ì°±¥•¹Ñ}™¥¹‘•Èõ±¥•¹Ñ}™¥¹‘•È¤°(€€€€€€€€€€€€‰ÍÁ½Ñ¥™äˆèMÁ½Ñ¥™å]¥¹‘½ÝÍ‘…ÁÑ•È¡Í•ÑÑ¥¹Ì°±¥•¹Ñ}™¥¹‘•Èõ±¥•¹Ñ}™¥¹‘•È¤°(€€€€€€€ô(€€€¥˜Á±…Ñ™½É´€ôô€‰‘…ÉÝ¥¸ˆè(€€€€€€€É•ÑÕÉ¸ì(€€€€€€€€€€€€‰ÅÄˆèEE5ÕÍ¥5…‘…ÁÑ•È¡½µµ…¹‘}ÉÕ¹¹•È¤°(€€€€€€€€€€€€‰¹•Ñ•…Í”ˆè9•Ñ•…Í•5ÕÍ¥5…‘…ÁÑ•È¡½µµ…¹‘}ÉÕ¹¹•È¤°(€€€€€€€€€€€€‰­Õ½Ôˆè-Õ½Õ5ÕÍ¥5…‘…ÁÑ•È¡½µµ…¹‘}ÉÕ¹¹•È¤°(€€€€€€€€€€€€‰…ÁÁ±”ˆèÁÁ±•5ÕÍ¥5…‘…ÁÑ•È¡½µµ…¹‘}ÉÕ¹¹•È¤°(€€€€€€€€€€€€‰ÍÁ½Ñ¥™äˆèMÁ½Ñ¥™å5…‘…ÁÑ•È¡½µµ…¹‘}ÉÕ¹¹•È¤°(€€€€€€€ô(€€€É•ÑÕÉ¸íô(()‘•˜ÁÉ½Ù¥‘•É}±…‰•°¡ÁÉ½Ù¥‘•ÈèÍÑÈ¤€´øÍÑÈè(€€€€ˆˆ‹’âëš^—–þ_–Þ—–ß–J3¢Â¢¾W¦v‹švÿ¢þS–n{ž¢Ï–ºkžj–æÏ–>Ã–B7žžÃŽˆˆˆ((€€€É•ÑÕÉ¸5UM%}MIY%}1	1L¹•Ð¡ÁÉ½Ù¥‘•È°ÁÉ½Ù¥‘•È¤