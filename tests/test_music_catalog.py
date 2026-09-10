"""验证六毛曲库、洗牌袋和跨平台音乐唤起降级。"""

import random

from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.music import (
    CatalogMusicService,
    SongEntry,
    ShuffleBag,
    artist_collection_url,
    chen_artist_url,
    identify_music_service,
    load_song_catalog,
    music_search_url,
    open_chen_artist_page,
    open_music_url,
    resolve_artist_music_service,
    song_deep_link,
)


def test_catalog_contains_real_platform_metadata() -> None:
    songs = load_song_catalog()
    assert len(songs) >= 10
    assert any(song.netease_song_id for song in songs)
    assert any(song.qq_song_mid for song in songs)
    assert any(song.apple_music_url.startswith("https://music.apple.com/") for song in songs)


def test_shuffle_bag_does_not_repeat_within_one_round() -> None:
    songs = tuple(SongEntry(str(index), f"歌{index}") for index in range(5))
    bag = ShuffleBag(random_source=random.Random(7))
    picked = [bag.next(songs).id for _ in range(len(songs))]
    assert len(set(picked)) == len(songs)


def test_deep_links_use_real_ids_and_canonical_apple_url() -> None:
    song = SongEntry(
        "song-1",
        "白石洲",
        netease_song_id="2112804681",
        qq_song_mid="0019f9U01clD4c",
        apple_music_url="https://music.apple.com/cn/song/example",
    )
    assert song_deep_link("netease", song) == "orpheus://song/2112804681/?autoplay=1"
    assert song_deep_link("qq", song) == "qqmusic://qq.com/media/playSonglist?p=0019f9U01clD4c"
    assert song_deep_link("apple", song) == song.apple_music_url


def test_windows_url_open_does_not_precheck_registry() -> None:
    opened: list[str] = []
    assert open_music_url(
        "orpheus://song/66525/?autoplay=1",
        platform_name="win32",
        startfile=opened.append,
    )
    assert opened == ["orpheus://song/66525/?autoplay=1"]


def test_catalog_prefers_deep_link_and_does_not_claim_confirmed_playback() -> None:
    song = SongEntry("song-1", "白石洲", netease_song_id="2112804681")
    opened: list[str] = []
    browser: list[str] = []
    service = CatalogMusicService(
        PetSettings(music_service="auto"),
        songs=(song,),
        opener=lambda url: opened.append(url) or True,
        browser_opener=lambda url: browser.append(url) or True,
    )

    result = service.play_random_song()

    assert result.success is True
    assert result.provider == "netease"
    assert result.confirmed is False
    assert result.fallback_used is False
    assert opened == ["orpheus://song/2112804681/?autoplay=1"]
    assert browser == []
    assert "正在播放" not in result.message


def test_catalog_falls_back_to_official_web_url_when_scheme_fails() -> None:
    song = SongEntry(
        "song-1",
        "白石洲",
        netease_song_id="2112804681",
        qq_song_mid="0019f9U01clD4c",
    )
    attempted: list[str] = []
    web: list[str] = []
    service = CatalogMusicService(
        PetSettings(music_service="netease"),
        songs=(song,),
        opener=lambda url: attempted.append(url) or False,
        browser_opener=lambda url: web.append(url) or True,
    )

    result = service.play_random_song()

    assert result.success is True
    assert result.fallback_used is True
    assert attempted == ["orpheus://song/2112804681/?autoplay=1", "qqmusic://qq.com/media/playSonglist?p=0019f9U01clD4c"]
    assert web[0].startswith("https://music.163.com/")
    assert music_search_url("netease", "白石洲") in web


def test_deep_link_exception_still_reaches_web_fallback() -> None:
    song = SongEntry("song-1", "白石洲", netease_song_id="2112804681")
    web: list[str] = []

    def broken_opener(_url: str) -> bool:
        raise OSError("scheme handler unavailable")

    service = CatalogMusicService(
        PetSettings(music_service="netease"),
        songs=(song,),
        opener=broken_opener,
        browser_opener=lambda url: web.append(url) or True,
    )

    result = service.play_random_song()

    assert result.success is True
    assert result.fallback_used is True
    assert web[0].startswith("https://music.163.com/")


def test_catalog_persists_only_shuffle_state_in_settings() -> None:
    settings = PetSettings(music_service="netease")
    song = SongEntry("song-1", "白石洲", netease_song_id="2112804681")
    service = CatalogMusicService(settings, songs=(song,), opener=lambda _url: True)
    service.play_random_song()
    assert settings.music_recent_history == ["song-1"]
    assert settings.music_shuffle_bag == []


def test_collection_fallback_is_an_official_artist_page() -> None:
    assert artist_collection_url("netease") == "https://music.163.com/#/artist?id=2124"
    assert artist_collection_url("apple").startswith("https://music.apple.com/")


def test_artist_shortcut_uses_canonical_web_destinations() -> None:
    assert chen_artist_url("netease") == "https://music.163.com/#/artist?id=2124"
    assert chen_artist_url("qq").endswith("/singer/002PZBgg1S9xPX")
    assert chen_artist_url("apple").endswith("/930912184")
    assert chen_artist_url("kugou").endswith("/435-0-0-all.html")
    assert chen_artist_url("qishui") == "https://www.qishui.com/"


def test_artist_shortcut_detects_handler_and_allows_manual_override(monkeypatch) -> None:
    assert identify_music_service("CloudMusic.exe") == "netease"
    assert identify_music_service("QQMusic.exe") == "qq"
    assert identify_music_service("com.apple.Music") == "apple"
    assert identify_music_service("VLC") is None

    settings = PetSettings(artist_music_service="auto")
    monkeypatch.setattr(
        "onepic_desktop_pet.music.detect_default_music_service",
        lambda _platform=None: "qq",
    )
    assert resolve_artist_music_service(settings) == ("qq", True)
    settings.artist_music_service = "apple"
    assert resolve_artist_music_service(settings) == ("apple", False)


def test_artist_shortcut_falls_back_to_netease_for_unknown_player() -> None:
    settings = PetSettings(artist_music_service="auto")
    opened: list[str] = []
    result = open_chen_artist_page(
        settings,
        browser_opener=lambda url: opened.append(url) or True,
        platform_name="linux",
    )
    assert result.success is True
    assert result.service == "netease"
    assert result.used_auto_detection is True
    assert opened == ["https://music.163.com/#/artist?id=2124"]
