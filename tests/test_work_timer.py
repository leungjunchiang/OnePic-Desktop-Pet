"""
本模块测试“六毛工作搭子”的本地工作计时、跨启动累计、日期切换和休息提醒。

测试只写 pytest 临时目录，不访问真实用户设置目录或网络。
"""

from datetime import datetime, timedelta

from onepic_desktop_pet.work_timer import WorkTimerModel, format_work_duration


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
    assert not reloaded.is_running


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
    assert reloaded.session_seconds() == 0


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
