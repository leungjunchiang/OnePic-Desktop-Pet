"""验证 Lili 0.7 的应用感知、音乐、提醒和成就功能。"""

from datetime import datetime

from onepic_desktop_pet.accessories import OUTFITS, unlocked_outfits
from onepic_desktop_pet.activity import classify_application
from onepic_desktop_pet.music import CHEN_CHUSHENG_SONGS, music_search_url
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


def test_wellness_channels_are_optional_and_independent() -> None:
    clock = Clock()
    model = WellnessReminderModel(clock)
    clock.value = 46 * 60
    assert model.take_due(True, False, 45, 60) == "water"
    assert model.take_due(True, False, 45, 60) is None


def test_eight_hours_unlocks_one_of_ten_outfits(tmp_path) -> None:
    clock = Clock()
    model = WorkTimerModel(
        tmp_path / "timer.json",
        now_provider=lambda: datetime(2026, 8, 10, 12, 0),
        monotonic_provider=clock,
    )
    model.start()
    clock.value = 8 * 3600
    assert model.unlocked_outfit_count() == 1
    assert model.take_new_outfit_unlock() == 1
    assert model.take_new_outfit_unlock() is None
    assert unlocked_outfits(1) == OUTFITS[:1]
    assert len(OUTFITS) == 10
