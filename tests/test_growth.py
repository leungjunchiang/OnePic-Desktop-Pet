"""验证每日成长线、正向心情和动作素材映射。"""

from datetime import datetime
from pathlib import Path

from onepic_desktop_pet.growth import (
    ACTION_SPRITES,
    DAILY_GROWTH,
    growth_progress_text,
    positive_mood,
    stage_for_seconds,
    time_of_day_activity,
)


def test_daily_growth_has_visible_zero_to_eight_hour_stages() -> None:
    assert [stage.hour for stage in DAILY_GROWTH] == list(range(9))
    assert stage_for_seconds(0).title == "刚来上班"
    assert stage_for_seconds(5 * 3600).activity == "sleep"
    assert stage_for_seconds(8 * 3600).activity == "wild-king"
    assert stage_for_seconds(20 * 3600).hour == 8


def test_growth_progress_shows_next_small_goal() -> None:
    text = growth_progress_text(20 * 60)
    assert "再专注 40 分钟" in text
    assert "兔子胡萝卜" in text
    assert "今日毕业" in growth_progress_text(8 * 3600)


def test_positive_mood_never_punishes_a_quiet_day() -> None:
    assert positive_mood(0) == "😴 悠闲的一天"
    assert "饿" not in positive_mood(0)
    assert "默契" in positive_mood(3 * 3600)


def test_time_of_day_and_corrected_action_assets() -> None:
    assert time_of_day_activity(datetime(2026, 8, 10, 2, 0), True)[0] == "sleep"
    assert time_of_day_activity(datetime(2026, 8, 10, 12, 0), True)[0] == "feast"
    _, sleep_message = time_of_day_activity(datetime(2026, 8, 10, 23, 30), False)
    assert "穿好睡衣" in sleep_message
    assert "穿好睡意" not in sleep_message
    assert ACTION_SPRITES["headphones"] == "03-headphones.png"
    assert ACTION_SPRITES["wild-king"] == "27-wild-king.png"
    assert len(set(ACTION_SPRITES.values())) == 46
    root = Path(__file__).resolve().parents[1] / "assets" / "pet" / "daily-actions"
    assert all((root / filename).is_file() for filename in set(ACTION_SPRITES.values()))
    assert ACTION_SPRITES["deep-focus"] == "43-deep-focus.png"
    assert ACTION_SPRITES["work-complete"] == "44-work-complete.png"
