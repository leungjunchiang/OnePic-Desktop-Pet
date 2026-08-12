"""验证点歌必须经过精确结果匹配、播放动作和当前媒体信息校验。"""

from __future__ import annotations

import logging
import random

import pytest

from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.music_control import (
    MusicProviderManager,
    TrackInfo,
    WindowsSessionSnapshot,
)
from onepic_desktop_pet.music_playback import (
    BasicRandomArtistPlaybackManager,
    AppleMusicWindowsAdapter,
    ExactMusicPlaybackManager,
    KugouMusicAdapter,
    MusicPlaybackError,
    NeteaseMusicAdapter,
    QQMusicAdapter,
    SongCandidate,
    SpotifyWindowsAdapter,
    build_provider_adapters,
    UIAutomationUnavailableError,
)


class FakeAdapter:
    """返回可控搜索结果并记录真正被点击的候选项。"""

    provider = "qq"

    def __init__(self, candidates=(), *, play_success: bool = True, search_error: Exception | None = None):
        self.candidates = tuple(candidates)
        self.play_success = play_success
        self.search_error = search_error
        self.played: list[SongCandidate] = []

    def search(self, _title: str, _artist: str):
        if self.search_error is not None:
            raise self.search_error
        return self.candidates

    def play(self, candidate: SongCandidate) -> bool:
        self.played.append(candidate)
        return self.play_success


class UnavailableAdapter(FakeAdapter):
    def search(self, _title: str, _artist: str):
        raise UIAutomationUnavailableError("UI tree unavailable")


def test_random_artist_distinguishes_ui_automation_unavailable_from_empty_results() -> None:
    manager = BasicRandomArtistPlaybackManager({"qq": UnavailableAdapter()})

    result = manager.play_random_artist("qq", "陈楚生")

    assert result.success is False
    assert result.error_code is MusicPlaybackError.UI_AUTOMATION_UNAVAILABLE


def _manager(adapter: FakeAdapter, tracks, *, random_source=None) -> ExactMusicPlaybackManager:
    observed = iter(tracks)

    def read_track(_provider: str):
        return next(observed, None)

    return ExactMusicPlaybackManager(
        {adapter.provider: adapter},
        read_track,
        verify_timeout_seconds=0,
        sleep=lambda _seconds: None,
        random_source=random_source,
    )


def test_exact_match_rejects_artist_album_and_wrong_artist_results() -> None:
    correct = SongCandidate("qq", "有没有人告诉你", "陈楚生", "song", "track-1")
    adapter = FakeAdapter(
        (
            SongCandidate("qq", "陈楚生", "陈楚生", "artist", "artist-page"),
            SongCandidate("qq", "有没有人告诉你", "其他歌手", "song", "wrong-artist"),
            SongCandidate("qq", "有没有人告诉你", "陈楚生", "album", "album-page"),
            correct,
        )
    )
    manager = _manager(adapter, (TrackInfo("有没有人告诉你", "陈楚生"),))

    result = manager.play_song("qq", "有没有人告诉你", "陈楚生")

    assert result.success is True
    assert result.error_code is None
    assert result.selected == correct
    assert adapter.played == [correct]


@pytest.mark.parametrize(
    ("adapter", "expected"),
    (
        (FakeAdapter(search_error=RuntimeError("search unavailable")), MusicPlaybackError.SEARCH_FAILED),
        (FakeAdapter((SongCandidate("qq", "歌手主页", "陈楚生", "artist"),)), MusicPlaybackError.RESULT_NOT_FOUND),
        (
            FakeAdapter((SongCandidate("qq", "有没有人告诉你", "陈楚生"),), play_success=False),
            MusicPlaybackError.PLAY_ACTION_FAILED,
        ),
    ),
)
def test_failure_stages_are_not_collapsed_into_update_failed(adapter: FakeAdapter, expected) -> None:
    result = _manager(adapter, ()).play_song("qq", "有没有人告诉你", "陈楚生")

    assert result.success is False
    assert result.error_code is expected
    assert "更新失败" not in result.message


def test_media_session_timeout_retries_exact_play_once() -> None:
    candidate = SongCandidate("qq", "有没有人告诉你", "陈楚生")
    adapter = FakeAdapter((candidate,))

    result = _manager(adapter, (None, None)).play_song("qq", candidate.title, candidate.artist)

    assert result.error_code is MusicPlaybackError.MEDIA_SESSION_TIMEOUT
    assert result.play_attempts == 2
    assert adapter.played == [candidate, candidate]
    assert "没有返回当前歌曲信息" in result.message


def test_wrong_current_track_retries_then_returns_track_verify_failed(caplog) -> None:
    candidate = SongCandidate("qq", "有没有人告诉你", "陈楚生")
    adapter = FakeAdapter((candidate,))
    manager = _manager(
        adapter,
        (
            TrackInfo("原来队列里的歌", "其他歌手"),
            TrackInfo("还是错误的歌", "其他歌手"),
        ),
    )

    with caplog.at_level(logging.DEBUG, logger="onepic_desktop_pet.music_playback"):
        result = manager.play_song("qq", candidate.title, candidate.artist)

    assert result.error_code is MusicPlaybackError.TRACK_VERIFY_FAILED
    assert result.current_title == "还是错误的歌"
    assert adapter.played == [candidate, candidate]
    assert "实际播放的不是目标歌曲" in result.message
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "requestedTitle='有没有人告诉你'" in log_text
    assert "selected_title='有没有人告诉你'" in log_text
    assert "current_title='还是错误的歌'" in log_text
    assert "stage=verify_failed" in log_text


def test_random_artist_mode_only_chooses_confirmed_song_results() -> None:
    valid_a = SongCandidate("qq", "山楂花", "陈楚生", "song")
    valid_b = SongCandidate("qq", "经过", "陈楚生", "song")
    adapter = FakeAdapter(
        (
            SongCandidate("qq", "陈楚生", "陈楚生", "artist"),
            SongCandidate("qq", "歌单", "陈楚生", "playlist"),
            SongCandidate("qq", "别人的歌", "其他歌手", "song"),
            valid_a,
            valid_b,
        )
    )
    manager = _manager(
        adapter,
        (TrackInfo(valid_b.title, valid_b.artist),),
        random_source=random.Random(0),
    )

    result = manager.play_song("qq", "", "陈楚生", random_artist=True)

    assert result.success is True
    assert result.selected == valid_b
    assert adapter.played == [valid_b]


def test_every_windows_provider_has_an_independent_adapter() -> None:
    adapters = build_provider_adapters(PetSettings(), platform_name="win32")

    assert isinstance(adapters["qq"], QQMusicAdapter)
    assert isinstance(adapters["netease"], NeteaseMusicAdapter)
    assert isinstance(adapters["kugou"], KugouMusicAdapter)
    assert isinstance(adapters["apple"], AppleMusicWindowsAdapter)
    assert isinstance(adapters["spotify"], SpotifyWindowsAdapter)
    assert len({type(adapter) for adapter in adapters.values()}) == 5


class FakeBridge:
    def __init__(self, session: WindowsSessionSnapshot) -> None:
        self.session = session
        self.controls: list[tuple[str, str]] = []

    def sessions(self):
        return (self.session,)

    def control(self, source_id: str, action: str) -> bool:
        self.controls.append((source_id, action))
        return True


def test_specific_song_never_uses_global_media_key_as_play_success() -> None:
    candidate = SongCandidate("spotify", "有没有人告诉你", "陈楚生")
    adapter = FakeAdapter((candidate,))
    adapter.provider = "spotify"
    bridge = FakeBridge(
        WindowsSessionSnapshot(
            "Spotify.exe",
            TrackInfo(candidate.title, candidate.artist, playback_status="playing"),
            frozenset({"play"}),
        )
    )
    media_keys: list[str] = []
    manager = MusicProviderManager(
        PetSettings(music_service="spotify"),
        platform_name="win32",
        windows_bridge=bridge,
        client_finder=lambda _provider, _custom: None,
        process_checker=lambda _name: True,
        media_key_sender=lambda action: media_keys.append(action) or True,
        playback_adapters={"spotify": adapter},
        playback_verify_timeout=0,
        playback_sleep=lambda _seconds: None,
    )

    result = manager.play_song("spotify", candidate.title, candidate.artist)

    assert result.success is True
    assert media_keys == []
    assert bridge.controls == []
