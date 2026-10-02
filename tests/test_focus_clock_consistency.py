"""验证长时间计时的保存精度、缓存推进及北京时间跨午夜口径，不访问网络。"""

from datetime import datetime, timedelta
from types import SimpleNamespace

from onepic_desktop_pet.work_timer import BEIJING_TIMEZONE, WorkTimerModel
from onepic_desktop_pet.window import PetWindow


def test_fractional_checkpoints_do_not_lose_minutes_and_recover(tmp_path):
    clock = [0.0]
    start = datetime(2026, 10, 2, 9, tzinfo=BEIJING_TIMEZONE)
    options = dict(path=tmp_path / "timer.json", monotonic_provider=lambda: clock[0],
                   now_provider=lambda: start + timedelta(seconds=clock[0]))
    timer = WorkTimerModel(**options, persist=False)
    timer.start()
    for index in range(600):
        # Advance simulated hours in memory; only the last checkpoint needs
        # disk I/O for recovery. Hundreds of instant renames can race Windows
        # file scanners, unlike production's one write per minute.
        if index == 599:
            timer.path = options["path"]
        clock[0] += 60.75
        assert timer.checkpoint()
        assert timer.session_seconds() == int(clock[0])
    assert timer.today_seconds() == 36450
    # Recovery seals the durable prefix, without counting shutdown downtime.
    recovered = WorkTimerModel(**options)
    assert recovered.session_seconds() == 36450
    clock[0] += 0.5
    cutoff = start + timedelta(seconds=clock[0] - 0.25)
    assert timer.session_seconds_at(cutoff) == 36450
    timer.pause()
    assert timer.session_seconds() == 36450


def test_account_projection_keeps_advancing_across_disk_checkpoints_and_midnight(tmp_path):
    clock = [0.0]
    start = datetime(2026, 10, 2, 23, 52, tzinfo=BEIJING_TIMEZONE)
    now = lambda: start + timedelta(seconds=clock[0])
    timer = WorkTimerModel(path=tmp_path / "timer.json", now_provider=now,
                           monotonic_provider=lambda: clock[0])
    timer.start()
    reads = []

    def summary(period, moment):
        reads.append(period)
        midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        elapsed = int((moment - max(start, midnight)).total_seconds())
        return {"total_seconds": elapsed if period == "day" else int(clock[0]),
                "raw_period_evidence": True}

    owner = SimpleNamespace(work_timer=timer, _focus_projection_cache=None,
        _recorded_focus_session_seconds=0,
        focus_analytics=SimpleNamespace(period_summary=summary),
        _set_local_live_focus_projection=lambda: None,
        _cross_device_today_display_value=lambda: None)
    read = lambda: PetWindow._shared_focus_period_seconds(owner, now())
    assert read() == {"today_seconds": 0, "week_seconds": 0}
    for minute in range(1, 8):
        clock[0] = minute * 60.75
        timer.checkpoint()
        assert read() == {"today_seconds": int(clock[0]), "week_seconds": int(clock[0])}
    assert reads == ["day", "week"]  # Each tick reuses the cache; no network/read scan.
    clock[0] = 600.0  # 00:02: yesterday's eight minutes stay in this week.
    timer.checkpoint()
    assert read() == {"today_seconds": 120, "week_seconds": 600}
    clock[0] += 60.75
    timer.checkpoint()
    assert read() == {"today_seconds": 180, "week_seconds": 660}
