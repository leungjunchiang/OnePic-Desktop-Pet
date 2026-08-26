"""
本模块测试“六毛工作搭子”的本地工作计时、跨启动累计、日期切换和休息提醒。

测试只写 pytest 临时目录，不访问真实用户设置目录或网络。
"""

from datetime import datetime, timedelta

from onepic_desktop_pet.work_timer import (
    WorkTimerModel,
    format_elapsed_clock,
    format_work_duration,
)


class FakeClock:
    """提供可控的本地日期和单调秒数。"""

    def __init__(self) -> None:
        self.now = datetime(2026, 8, 10, 9, 0, 0)
        self.monotonic = 100.0

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)
        self.monotonic += seconds


def _timer(tmp_path, clock: FakeClock) -> WorkTimerModel:
    return WorkTimerModel(
        path=tmp_path / "work_timer.json",
        now_provider=lambda: clock.now,
        monotonic_provider=lambda: clock.monotonic,
    )


def test_work_timer_accumulates_checkpoints_and_survives_restart(tmp_path) -> None:
    clock = FakeClock()
    timer = _timer(tmp_path, clock)

    assert timer.start()
    clock.advance(65)
    assert timer.checkpoint()
    clock.advance(35)
    assert timer.today_seconds() == 100
    assert timer.session_seconds() == 100
    assert timer.pause()
    assert not timer.is_running

    reloaded = _timer(tmp_path, clock)
    assert reloaded.today_seconds() == 100
    assert reloaded.lifetime_seconds() == 100
    assert reloaded.session_seconds() == 100
    assert reloaded.has_active_session
    assert not reloaded.is_running


def test_current_elapsed_excludes_previous_checkpoint_segments(tmp_path) -> None:
    clock = FakeClock()
    timer = _timer(tmp_path, clock)

    assert timer.start()
    clock.advance(3 * 60 * 60)
    assert timer.checkpoint()
    assert timer.session_seconds() == 3 * 60 * 60
    assert timer.current_elapsed_seconds() == 0
    clock.advance(90)
    assert timer.current_elapsed_seconds() == 90
    assert timer.session_seconds() == 3 * 60 * 60 + 90


def test_checkpoint_keeps_the_real_current_segment_start_for_reports(tmp_path) -> None:
    clock = FakeClock()
    timer = _timer(tmp_path, clock)

    assert timer.start()
    started_at = timer.current_segment_started_at()
    clock.advance(65)
    assert timer.checkpoint()

    # Checkpoints reset only the monotonic display slice.  The report must
    # continue to draw the segment from its wall-clock start instead of
    # producing ``now–now``.
    assert timer.current_segment_started_at() == started_at
    clock.advance(35)
    assert timer.current_segment_started_at() == started_at


def test_running_work_timer_recovers_last_checkpoint_after_restart(tmp_path) -> None:
    """A crash/restart keeps the saved running session without counting downtime."""
    clock = FakeClock()
    timer = _timer(tmp_path, clock)
    assert timer.start()
    clock.advance(65)
    assert timer.checkpoint()
    clock.advance(3600)

    reloaded = _timer(tmp_path, clock)
    assert reloaded.is_running
    assert reloaded.recovered_active_session
    assert reloaded.session_seconds() == 65
    clock.advance(30)
    assert reloaded.session_seconds() == 95
    reloaded.pause()


def test_analytics_cursor_survives_restart_without_replaying_session_total(tmp_path) -> None:
    clock = FakeClock()
    timer = _timer(tmp_path, clock)
    assert timer.start()
    clock.advance(120)
    timer.mark_analytics_recorded(timer.session_seconds())
    assert timer.analytics_recorded_session_seconds() == 120
    timer.pause()

    reloaded = _timer(tmp_path, clock)
    assert reloaded.has_active_session
    assert reloaded.analytics_recorded_session_seconds() == 120
    assert reloaded.focus_session_id


def test_legacy_active_timer_without_cursor_is_treated_as_already_recorded(tmp_path) -> None:
    path = tmp_path / "work_timer.json"
    path.write_text(
        '{"date":"2026-08-10","accumulated_seconds":120,"lifetime_seconds":120,'
        '"running":false,"session_active":true,"session_accumulated_seconds":120}',
        encoding="utf-8",
    )
    clock = FakeClock()
    timer = _timer(tmp_path, clock)
    assert timer.analytics_recorded_session_seconds() == 120


def test_work_timer_does_not_double_start_or_count_offline_time(tmp_path) -> None:
    clock = FakeClock()
    timer = _timer(tmp_path, clock)

    assert timer.start()
    assert not timer.start()
    clock.advance(90)
    timer.pause()
    clock.advance(3600)

    reloaded = _timer(tmp_path, clock)
    assert reloaded.today_seconds() == 90
    assert reloaded.session_seconds() == 90
    assert reloaded.has_active_session
    assert reloaded.start()
    clock.advance(30)
    assert reloaded.session_seconds() == 120
    reloaded.finish()
    assert reloaded.session_seconds() == 0
    assert not reloaded.has_active_session


def test_pause_reason_and_uninterrupted_episode_are_persisted(tmp_path) -> None:
    clock = FakeClock()
    timer = _timer(tmp_path, clock)

    assert timer.start()
    clock.advance(120)
    assert timer.episode_seconds() == 120
    assert timer.pause("idle_10m")
    assert timer.state == "paused_idle"
    assert timer.pause_reason == "idle_10m"
    assert timer.episode_seconds() == 120

    reloaded = _timer(tmp_path, clock)
    assert reloaded.has_active_session
    assert not reloaded.is_running
    assert reloaded.state == "paused_idle"
    assert reloaded.pause_reason == "idle_10m"
    assert reloaded.start()  # explicit user resume only
    assert reloaded.episode_seconds() == 0
    clock.advance(30)
    assert reloaded.episode_seconds() == 30


def test_lock_sleep_and_video_have_distinct_pause_states(tmp_path) -> None:
    clock = FakeClock()
    timer = _timer(tmp_path, clock)
    for reason, expected in (
        ("lock", "paused_lock"),
        ("sleep", "paused_sleep"),
        ("fullscreen_video", "paused_video"),
    ):
        assert timer.start()
        assert timer.pause(reason)
        assert timer.state == expected
        assert timer.pause_reason == reason
        assert timer.finish() >= 0


def test_new_date_resets_today_total(tmp_path) -> None:
    clock = FakeClock()
    timer = _timer(tmp_path, clock)
    timer.start()
    clock.advance(600)
    timer.pause()
    assert timer.today_seconds() == 600

    clock.now += timedelta(days=1)
    assert timer.today_seconds() == 0
    assert "0分钟" in timer.status_text()


def test_remote_focus_totals_merge_without_double_counting_live_seconds(tmp_path) -> None:
    clock = FakeClock()
    timer = _timer(tmp_path, clock)
    assert timer.start()
    clock.advance(50)

    assert timer.merge_remote_state(
        today_seconds=600,
        lifetime_seconds=3600,
        date_key="2026-08-10",
    )
    assert timer.today_seconds() == 600
    assert timer.lifetime_seconds() == 3600

    clock.advance(20)
    assert timer.today_seconds() == 620
    assert timer.lifetime_seconds() == 3620
    assert not timer.merge_remote_state(
        today_seconds=600,
        lifetime_seconds=3600,
        date_key="2026-08-10",
    )


def test_remote_lifetime_unlocks_survive_a_server_date_boundary(tmp_path) -> None:
    """A UTC/Beijing date mismatch must not hide cross-device outfit unlocks."""

    clock = FakeClock()
    timer = _timer(tmp_path, clock)
    assert timer.merge_remote_state(
        today_seconds=900,
        lifetime_seconds=12 * 3600,
        date_key="2026-08-09",
    )
    # The stale daily bucket is intentionally ignored, while lifetime remains
    # available for the outfit unlock calculation.
    assert timer.today_seconds() == 0
    assert timer.lifetime_seconds() == 12 * 3600


def test_work_timer_switches_to_an_isolated_account_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    clock = FakeClock()
    timer = WorkTimerModel(
        now_provider=lambda: clock.now,
        monotonic_provider=lambda: clock.monotonic,
    )

    assert timer.switch_account("account-a")
    assert timer.start()
    clock.advance(90)
    assert timer.pause()
    assert timer.today_seconds() == 90

    assert timer.switch_account("account-b")
    assert timer.today_seconds() == 0
    assert timer.lifetime_seconds() == 0

    assert timer.switch_account("account-a")
    assert timer.today_seconds() == 90
    assert timer.lifetime_seconds() == 90


def test_reminders_fire_once_at_focus_break_and_long_work_thresholds(tmp_path) -> None:
    clock = FakeClock()
    timer = _timer(tmp_path, clock)
    timer.start()

    clock.advance(25 * 60)
    assert timer.take_due_reminder() == "focus"
    assert timer.take_due_reminder() is None

    clock.advance(25 * 60)
    assert timer.take_due_reminder() == "break"
    assert timer.take_due_reminder() is None

    clock.advance(40 * 60)
    assert timer.take_due_reminder() == "long_break"
    assert timer.take_due_reminder() is None

    clock.advance(45 * 60)
    assert timer.take_due_reminder() == "long_break"


def test_duration_formatting_is_compact_and_readable() -> None:
    assert format_work_duration(0) == "0分钟"
    assert format_work_duration(30) == "不足1分钟"
    assert format_work_duration(25 * 60) == "25分钟"
    assert format_work_duration(65 * 60) == "1小时5分钟"
    assert format_elapsed_clock(0) == "00:00"
    assert format_elapsed_clock(25 * 60 + 7) == "25:07"
    assert format_elapsed_clock(65 * 60 + 2) == "1:05:02"
