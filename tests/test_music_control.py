"""验证 Windows/macOS 本机音乐 adapter、真实状态和媒体键回退。"""

from __future__ import annotations

import subprocess
import logging
from pathlib import Path

import pytest

from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.music_control import (
    MusicControlState,
    MusicProviderManager,
    TrackInfo,
    WindowsSessionSnapshot,
)
from onepic_desktop_pet.music_playback import (
    MusicPlaybackOutcome,
    SongCandidate,
)


class FakeWindowsBridge:
    """返回固定 GSMTC 快照并记录实际发送的 Session 命令。"""

    def __init__(self, sessions=(), success: bool = True) -> None:
        self.snapshots = tuple(sessions)
        self.success = success
        self.commands: list[tuple[str, str]] = []

    def sessions(self):
        return self.snapshots

    def control(self, source_id: str, action: str) -> bool:
        self.commands.append((source_id, action))
        return self.success


class FakeRandomAdapter:
    """模拟独立 Provider 的歌手搜索与播放按钮。"""

    def __init__(self, provider: str, *, play_success: bool) -> None:
        self.provider = provider
        self.play_success = play_success
        self.played: list[SongCandidate] = []

    def search(self, _title: str, artist: str):
        return (SongCandidate(self.provider, f"{self.provider}歌曲", artist),)

    def play(self, candidate: SongCandidate) -> bool:
        self.played.append(candidate)
        return self.play_success


def _client_finder(_provider: str, _custom: str) -> Path:
    return Path("detected-player.exe")


def test_windows_uses_matching_system_media_session_before_fallback() -> None:
    session = WindowsSessionSnapshot(
        "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify",
        TrackInfo("若梦", "陈楚生", "专辑", "playing"),
        frozenset({"toggle", "next", "previous"}),
    )
    bridge = FakeWindowsBridge((session,))
    fallback: list[str] = []
    manager = MusicProviderManager(
        PetSettings(music_service="spotify"),
        platform_name="win32",
        windows_bridge=bridge,
        client_finder=_client_finder,
        process_checker=lambda _name: True,
        media_key_sender=lambda action: fallback.append(action) or True,
    )

    result = manager.control("spotify", "toggle")

    assert result.success is True
    assert result.used_fallback is False
    assert result.status.state is MusicControlState.PLAYING
    assert result.status.track == session.track
    assert bridge.commands == [(session.source_id, "toggle")]
    assert fallback == []


def test_managed_provider_exposes_unified_detection_and_track_api() -> None:
    session = WindowsSessionSnapshot(
        "Spotify.exe",
        TrackInfo("经过", "陈楚生", playback_status="playing"),
        frozenset({"toggle"}),
    )
    manager = MusicProviderManager(
        PetSettings(),
        platform_name="win32",
        windows_bridge=FakeWindowsBridge((session,)),
        client_finder=_client_finder,
        process_checker=lambda name: name == "Spotify.exe",
        window_checker=lambda provider: provider == "spotify",
    )

    provider = manager.provider("spotify")

    assert provider.detect().application_detected is True
    assert provider.is_running() is True
    assert provider.can_control() is True
    assert provider.read_current_track() == session.track


@pytest.mark.parametrize(
    ("provider", "source_id"),
    (
        ("qq", "Tencent.QQMusic"),
        ("netease", "cloudmusic.exe"),
        ("kugou", "KuGou.KGMusic"),
        ("apple", "AppleInc.AppleMusicWin"),
        ("spotify", "Spotify.exe"),
    ),
)
def test_windows_identifies_supported_player_sessions(provider: str, source_id: str) -> None:
    session = WindowsSessionSnapshot(source_id, TrackInfo("歌名"), frozenset({"toggle"}))
    manager = MusicProviderManager(
        PetSettings(),
        platform_name="win32",
        windows_bridge=FakeWindowsBridge((session,)),
        client_finder=lambda _provider, _custom: None,
        process_checker=lambda _name: False,
    )

    status = manager.inspect(provider)

    assert status.state is MusicControlState.CONTROL_READY
    assert status.application_running is True
    assert status.session_id == source_id


def test_windows_running_player_without_session_uses_media_key_fallback() -> None:
    sent: list[str] = []
    manager = MusicProviderManager(
        PetSettings(),
        platform_name="win32",
        windows_bridge=FakeWindowsBridge(),
        client_finder=_client_finder,
        process_checker=lambda name: name.casefold().startswith("cloudmusic"),
        media_key_sender=lambda action: sent.append(action) or True,
    )

    result = manager.control("netease", "next")

    assert result.success is True
    assert result.used_fallback is True
    assert result.status.state is MusicControlState.BASIC_ONLY
    assert "仅支持基础" in result.status.message
    assert sent == ["next"]


def test_windows_installed_but_not_running_is_not_connected() -> None:
    sent: list[str] = []
    manager = MusicProviderManager(
        PetSettings(),
        platform_name="win32",
        windows_bridge=FakeWindowsBridge(),
        client_finder=_client_finder,
        process_checker=lambda _name: False,
        media_key_sender=lambda action: sent.append(action) or True,
    )

    result = manager.control("qq", "toggle")

    assert result.success is False
    assert result.status.state is MusicControlState.APP_DETECTED
    assert "请先运行" in result.message
    assert "已连接" not in result.message
    assert sent == []


def test_transport_launches_waits_for_target_session_then_controls_without_media_key() -> None:
    """基础控制只查找目标 Session；启动和等待成功后不应触发全局媒体键。"""

    session = WindowsSessionSnapshot(
        "Tencent.QQMusic",
        TrackInfo("有没有人告诉你", "陈楚生", playback_status="paused"),
        frozenset({"play"}),
    )
    bridge = FakeWindowsBridge()
    running = False
    launches: list[tuple[str, Path]] = []
    media_keys: list[str] = []

    def launch(provider: str, client: Path) -> bool:
        nonlocal running
        running = True
        bridge.snapshots = (session,)
        launches.append((provider, client))
        return True

    manager = MusicProviderManager(
        PetSettings(),
        platform_name="win32",
        windows_bridge=bridge,
        client_finder=_client_finder,
        process_checker=lambda name: running and name == "QQMusic.exe",
        client_launcher=launch,
        media_key_sender=lambda action: media_keys.append(action) or True,
        transport_wait_timeout=0,
    )

    result = manager.control("qq", "play")

    assert result.success is True
    assert launches == [("qq", Path("detected-player.exe"))]
    assert bridge.commands == [(session.source_id, "play")]
    assert media_keys == []


def test_transport_control_never_invokes_song_resolver_adapter() -> None:
    """播放/暂停/切歌只能走 Transport，不得顺带搜索或点击播放器 UI。"""

    session = WindowsSessionSnapshot(
        "cloudmusic.exe",
        TrackInfo("当前队列歌曲", "陈楚生", playback_status="playing"),
        frozenset({"pause"}),
    )

    class ExplodingSearchAdapter(FakeRandomAdapter):
        def search(self, _title: str, _artist: str):
            raise AssertionError("基础控制不应进入 Song Resolver")

    manager = MusicProviderManager(
        PetSettings(),
        platform_name="win32",
        windows_bridge=FakeWindowsBridge((session,)),
        client_finder=_client_finder,
        process_checker=lambda name: name.casefold().startswith("cloudmusic"),
        playback_adapters={"netease": ExplodingSearchAdapter("netease", play_success=True)},
    )

    result = manager.control("netease", "pause")

    assert result.success is True
    assert result.status.state is MusicControlState.PLAYING


def test_launched_process_still_initializing_has_distinct_state() -> None:
    manager = MusicProviderManager(
        PetSettings(),
        platform_name="win32",
        windows_bridge=FakeWindowsBridge(),
        client_finder=_client_finder,
        process_checker=lambda _name: False,
        client_launcher=lambda _provider, _client: True,
        media_key_sender=lambda _action: False,
        transport_wait_timeout=0,
    )

    result = manager.control("qq", "play")

    assert result.success is False
    assert result.status.state is MusicControlState.APP_STARTED
    assert "正在等待应用初始化" in result.message


def test_auto_transport_respects_explicit_preferred_player_before_first_song() -> None:
    session = WindowsSessionSnapshot(
        "Tencent.QQMusic",
        TrackInfo("队列歌曲", "陈楚生", playback_status="paused"),
        frozenset({"play"}),
    )
    bridge = FakeWindowsBridge()
    running = False

    def launch(_provider: str, _client: Path) -> bool:
        nonlocal running
        running = True
        bridge.snapshots = (session,)
        return True

    manager = MusicProviderManager(
        PetSettings(music_service="qq"),
        platform_name="win32",
        windows_bridge=bridge,
        client_finder=lambda provider, _custom: Path("qq.exe") if provider == "qq" else None,
        process_checker=lambda name: running and name == "QQMusic.exe",
        client_launcher=launch,
        transport_wait_timeout=0,
    )

    result = manager.control("auto", "play")

    assert result.success is True
    assert result.provider == "qq"
    assert bridge.commands == [(session.source_id, "play")]


def test_provider_status_separates_app_transport_and_song_selection() -> None:
    manager = MusicProviderManager(
        PetSettings(),
        platform_name="win32",
        windows_bridge=FakeWindowsBridge(),
        client_finder=_client_finder,
        process_checker=lambda name: name.casefold().startswith("cloudmusic"),
        transport_wait_timeout=0,
    )
    manager.inspect("netease")

    text = manager.provider_status_text("netease")

    assert "应用状态：正在运行" in text
    assert "基础播放控制：已启动 · 正在等待播放控制" in text
    assert "自动选歌：实验性" in text


def test_macos_apple_music_uses_apple_events_and_reads_track() -> None:
    scripts: list[str] = []

    def runner(command, **_kwargs):
        script = command[-1]
        scripts.append(script)
        output = "playing\t有没有人告诉你\t陈楚生\t原来我一直都不孤单\n" if "set t" in script else ""
        return subprocess.CompletedProcess(command, 0, output, "")

    manager = MusicProviderManager(
        PetSettings(),
        platform_name="darwin",
        client_finder=_client_finder,
        process_checker=lambda name: name == "Music",
        command_runner=runner,
        media_key_sender=lambda _action: False,
    )

    result = manager.control("apple", "pause")

    assert result.success is True
    assert result.used_fallback is False
    assert result.status.state is MusicControlState.CONTROL_READY
    assert result.status.track is not None
    assert result.status.track.title == "有没有人告诉你"
    assert any('tell application "Music"' in script and "pause" in script for script in scripts)


def test_macos_explicit_player_launches_before_apple_events_control() -> None:
    running = False
    launches: list[str] = []

    def launch(provider: str, _client: Path) -> bool:
        nonlocal running
        running = True
        launches.append(provider)
        return True

    def runner(command, **_kwargs):
        output = "playing\t瘾\t陈楚生\t专辑\n" if "set t" in command[-1] else ""
        return subprocess.CompletedProcess(command, 0, output, "")

    manager = MusicProviderManager(
        PetSettings(music_service="apple"),
        platform_name="darwin",
        client_finder=_client_finder,
        process_checker=lambda name: running and name == "Music",
        client_launcher=launch,
        command_runner=runner,
        transport_wait_timeout=0,
    )

    result = manager.control("auto", "play")

    assert result.success is True
    assert result.provider == "apple"
    assert launches == ["apple"]
    assert result.status.track is not None
    assert result.status.track.artist == "陈楚生"


def test_macos_automation_permission_error_is_explicit_and_has_no_fallback() -> None:
    fallback: list[str] = []

    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(command, 1, "", "Not authorized to send Apple events. (-1743)")

    manager = MusicProviderManager(
        PetSettings(),
        platform_name="darwin",
        client_finder=_client_finder,
        process_checker=lambda name: name == "Music",
        command_runner=runner,
        media_key_sender=lambda action: fallback.append(action) or True,
    )

    result = manager.control("apple", "toggle")

    assert result.success is False
    assert result.status.state is MusicControlState.PERMISSION_REQUIRED
    assert "隐私与安全性" in result.message
    assert fallback == []


def test_macos_player_without_native_adapter_is_basic_control_only() -> None:
    sent: list[str] = []
    manager = MusicProviderManager(
        PetSettings(),
        platform_name="darwin",
        client_finder=_client_finder,
        process_checker=lambda name: name == "NeteaseMusic",
        media_key_sender=lambda action: sent.append(action) or True,
    )

    result = manager.control("netease", "previous")

    assert result.success is True
    assert result.used_fallback is True
    assert result.status.state is MusicControlState.BASIC_ONLY
    assert sent == ["previous"]


def test_auto_provider_falls_back_records_history_and_locks_controls(caplog) -> None:
    """首选失败后自动尝试下一个，后续基础控制留在真正成功的平台。"""

    qq_session = WindowsSessionSnapshot(
        "Tencent.QQMusic",
        TrackInfo("旧歌曲", "其他歌手", playback_status="playing"),
        frozenset({"next"}),
    )
    netease_session = WindowsSessionSnapshot(
        "cloudmusic.exe",
        TrackInfo("有没有人告诉你", "陈楚生", playback_status="playing"),
        frozenset({"next"}),
    )
    bridge = FakeWindowsBridge((qq_session, netease_session))
    qq = FakeRandomAdapter("qq", play_success=False)
    netease = FakeRandomAdapter("netease", play_success=True)
    settings = PetSettings(music_service="auto")
    manager = MusicProviderManager(
        settings,
        platform_name="win32",
        windows_bridge=bridge,
        client_finder=lambda provider, _custom: Path(f"{provider}.exe") if provider in {"qq", "netease"} else None,
        process_checker=lambda name: name in {"QQMusic.exe", "cloudmusic.exe"},
        window_checker=lambda provider: provider == "qq",
        playback_adapters={"qq": qq, "netease": netease},
        playback_sleep=lambda _seconds: None,
    )

    with caplog.at_level(logging.DEBUG, logger="onepic_desktop_pet.music_control"):
        result = manager.play_random_artist_auto("陈楚生")

    assert result.success is True
    assert result.provider == "netease"
    assert result.outcome is MusicPlaybackOutcome.PLAYBACK_CONFIRMED
    assert result.attempted_providers == ("qq", "netease")
    assert manager.active_provider == "netease"
    assert settings.music_provider_history["qq"]["consecutive_failures"] == 1
    assert settings.music_provider_history["netease"]["success_count"] == 1
    assert "provider=qq" in caplog.text and "result=failed" in caplog.text
    assert "provider=netease" in caplog.text and "result=success" in caplog.text

    control = manager.control("auto", "next")

    assert control.success is True
    assert control.provider == "netease"
    assert bridge.commands[-1] == (netease_session.source_id, "next")
    assert manager.ranked_providers()[0].provider == "netease"


def test_auto_provider_reports_failure_only_after_every_installed_provider() -> None:
    adapters = {
        provider: FakeRandomAdapter(provider, play_success=False)
        for provider in ("qq", "netease", "kugou")
    }
    manager = MusicProviderManager(
        PetSettings(),
        platform_name="win32",
        windows_bridge=FakeWindowsBridge(),
        client_finder=lambda provider, _custom: Path(f"{provider}.exe") if provider in adapters else None,
        process_checker=lambda _name: False,
        window_checker=lambda _provider: False,
        playback_adapters=adapters,
        playback_sleep=lambda _seconds: None,
    )

    result = manager.play_random_artist_auto("陈楚生")

    assert result.success is False
    assert result.attempted_providers == ("qq", "netease", "kugou")
    assert result.message == "暂时没能找到可以播放的音乐软件，点击重试。"
    assert all(adapter.played for adapter in adapters.values())


def test_song_selection_failure_does_not_misreport_transport_as_disconnected() -> None:
    adapter = FakeRandomAdapter("netease", play_success=False)
    manager = MusicProviderManager(
        PetSettings(),
        platform_name="win32",
        windows_bridge=FakeWindowsBridge(),
        client_finder=lambda provider, _custom: Path("cloudmusic.exe") if provider == "netease" else None,
        process_checker=lambda name: name.casefold().startswith("cloudmusic"),
        playback_adapters={"netease": adapter},
        playback_sleep=lambda _seconds: None,
        transport_wait_timeout=0,
    )

    result = manager.play_random_artist_auto("陈楚生")

    assert result.success is False
    assert "自动选歌失败" in result.message
    assert "播放/暂停" in result.message
    assert "连接失败" not in result.message
