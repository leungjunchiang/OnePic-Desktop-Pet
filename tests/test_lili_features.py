"""验证 Lili 的应用感知、本地音乐、提醒和每小时娃衣功能。"""

from datetime import datetime
import os
import sys

import pytest

from onepic_desktop_pet.accessories import OUTFITS, unlocked_outfits
from onepic_desktop_pet.activity import classify_application
from onepic_desktop_pet.music import CHEN_CHUSHENG_SONGS, find_music_client, launch_music_client, music_search_url
from onepic_desktop_pet.wellness import WellnessReminderModel
from onepic_desktop_pet.work_timer import WorkTimerModel


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def test_application_categories_do_not_need_window_titles() -> None:
    assert classify_application("cloudmusic.exe") == "music"
    assert classify_application("WINWORD.EXE") == "office"
    assert classify_application("Codex.exe") == "coding"
    assert classify_application("unknown.exe") == "other"


def test_music_links_use_official_platforms_and_only_titles() -> None:
    assert len(CHEN_CHUSHENG_SONGS) >= 10
    assert music_search_url("netease", "荒废光年").startswith("https://music.163.com/")
    assert music_search_url("qq", "山楂花").startswith("https://y.qq.com/")
    assert music_search_url("kugou", "有没有人告诉你").startswith("https://www.kugou.com/")
    assert music_search_url("apple", "经过").startswith("https://music.apple.com/")
    assert music_search_url("spotify", "晓得").startswith("https://open.spotify.com/")


def test_missing_music_client_falls_back_to_official_search(monkeypatch) -> None:
    opened = []
    monkeypatch.setattr("onepic_desktop_pet.music.find_music_client", lambda _service, _path="": None)
    monkeypatch.setattr("onepic_desktop_pet.music.webbrowser.open", opened.append)
    result = launch_music_client("qq", "山楂花")
    assert result.client_found is False
    assert opened and opened[0].startswith("https://y.qq.com/")


def test_music_search_never_claims_connection_or_guaranteed_playback(monkeypatch, tmp_path) -> None:
    executable = tmp_path / "Spotify.exe"
    executable.write_bytes(b"MZ")
    monkeypatch.setattr("onepic_desktop_pet.music.find_music_client", lambda _service, _path="": executable)
    monkeypatch.setattr("onepic_desktop_pet.music.subprocess.Popen", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("onepic_desktop_pet.music.threading.Thread.start", lambda _thread: None)

    result = launch_music_client("spotify", "经过", str(executable))

    assert result.client_found is True
    assert "已连接" not in result.message
    assert "正在播放" not in result.message
    assert "不代表已建立播放控制" in result.message


def test_user_selected_music_program_has_priority(tmp_path) -> None:
    if os.name == "nt":
        executable = tmp_path / "QQMusic.exe"
        executable.write_bytes(b"MZ")
    elif sys.platform == "darwin":
        executable = tmp_path / "QQMusic.app"
        executable.mkdir()
    else:
        pytest.skip("Lili currently supports local music clients on Windows and macOS")

    assert find_music_client("qq", str(executable)) == executable


def test_wellness_channels_are_optional_and_independent() -> None:
    clock = Clock()
    model = WellnessReminderModel(clock)
    clock.value = 46 * 60
    assert model.take_due(True, False, 45, 60) == "water"
    assert model.take_due(True, False, 45, 60) is None


def test_each_hour_unlocks_one_of_twelve_outfits_and_final_is_wild_king(tmp_path) -> None:
    clock = Clock()
    model = WorkTimerModel(
        tmp_path / "timer.json",
        now_provider=lambda: datetime(2026, 8, 10, 12, 0),
        monotonic_provider=clock,
    )
    model.start()
    clock.value = 3600
    assert model.unlocked_outfit_count() == 1
    assert model.take_new_outfit_unlock() == 1
    assert model.take_new_outfit_unlock() is None
    assert unlocked_outfits(1) == OUTFITS[:1]
    clock.value = 14 * 3600
    assert model.unlocked_outfit_count() == 12
    assert len(OUTFITS) == 12
    assert OUTFITS[-1].key == "hour-12"
    assert OUTFITS[-1].name == "荒野国王"
