"""验证六毛曲库、洗牌袋和跨平台音乐唤起降级。"""

import random
import subprocess

from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.music import (
    CatalogMusicService,
    SongEntry,
    ShuffleBag,
    artist_collection_deep_link,
    artist_collection_url,
    load_song_catalog,
    music_search_url,
    open_music_url,
    song_deep_link,
    _extract_executable_from_shell_command,
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


def test_windows_shell_command_parser_handles_missing_space_after_exe(tmp_path) -> None:
    executable = tmp_path / "cloudmusic.exe"
    executable.write_bytes(b"MZ")

    command = f'"{executable}"--webcmd="%1"'

    assert _extract_executable_from_shell_command(command) == executable


def test_windows_netease_launch_uses_exe_cwd_and_webcmd(tmp_path, monkeypatch) -> None:
    executable = tmp_path / "cloudmusic.exe"
    executable.write_bytes(b"MZ")
    calls = []

    class Process:
        def poll(self):
            return None

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return Process()

    monkeypatch.setattr("onepic_desktop_pet.music.subprocess.Popen", fake_popen)

    assert open_music_url(
        "orpheus://song/66525/?autoplay=1",
        platform_name="win32",
        service="netease",
        executable=executable,
    )
    expected_kwargs = {
        "cwd": str(tmp_path),
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if getattr(subprocess, "CREATE_NO_WINDOW", 0):
        expected_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    assert calls == [(
        [str(executable), "--webcmd=orpheus://song/66525/?autoplay=1"],
        expected_kwargs,
    )]


def test_windows_single_instance_exit_is_not_treated_as_launch_failure(tmp_path, monkeypatch) -> None:
    executable = tmp_path / "cloudmusic.exe"
    executable.write_bytes(b"MZ")

    class Process:
        def poll(self):
            return 1

    monkeypatch.setattr("onepic_desktop_pet.music._windows_process_ids", lambda _name: {1234})
    monkeypatch.setattr("onepic_desktop_pet.music.subprocess.Popen", lambda *_args, **_kwargs: Process())

    assert open_music_url(
        "orpheus://song/66525/?autoplay=1",
        platform_name="win32",
        service="netease",
        executable=executable,
    )


def test_catalog_default_windows_launcher_uses_configured_netease_exe(tmp_path, monkeypatch) -> None:
    executable = tmp_path / "cloudmusic.exe"
    executable.write_bytes(b"MZ")
    calls = []

    class Process:
        def poll(self):
            return None

    monkeypatch.setattr(
        "onepic_desktop_pet.music.subprocess.Popen",
        lambda args, **kwargs: calls.append((args, kwargs)) or Process(),
    )
    settings = PetSettings(
        music_service="netease",
        netease_music_path=str(executable),
    )
    service = CatalogMusicService(
        settings,
        songs=(SongEntry("song-1", "白石洲", netease_song_id="2112804681"),),
        platform_name="win32",
    )

    result = service.play_random_song()

    assert result.success is True
    assert calls[0][0] == [str(executable), "--webcmd=orpheus://song/2112804681/?autoplay=1"]
    assert calls[0][1]["cwd"] == str(tmp_path)


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


def test_collection_prefers_netease_client_deep_link() -> None:
    opened: list[str] = []
    browser: list[str] = []
    service = CatalogMusicService(
        PetSettings(music_service="netease"),
        opener=lambda url: opened.append(url) or True,
        browser_opener=lambda url: browser.append(url) or True,
    )

    assert service.open_artist_collection() is True
    assert opened == ["orpheus://artist/2124/?autoplay=1"]
    assert browser == []
    assert service.last_provider == "netease"
    assert service.last_used_deep_link is True


def test_collection_falls_back_to_web_when_netease_deep_link_fails() -> None:
    opened: list[str] = []
    browser: list[str] = []
    service = CatalogMusicService(
        PetSettings(music_service="netease"),
        opener=lambda url: opened.append(url) or False,
        browser_opener=lambda url: browser.append(url) or True,
    )

    assert service.open_artist_collection() is True
    assert opened == ["orpheus://artist/2124/?autoplay=1"]
    assert browser == ["https://music.163.com/#/artist?id=2124"]
    assert service.last_provider == "netease"
    assert service.last_used_deep_link is False


def test_artist_collection_deep_link_is_only_defined_for_supported_client() -> None:
    assert artist_collection_deep_link("netease") == "orpheus://artist/2124/?autoplay=1"
    assert artist_collection_deep_link("qq") == ""

