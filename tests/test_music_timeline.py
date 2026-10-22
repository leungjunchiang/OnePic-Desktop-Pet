"""验证 Windows 快照时间轴在播放时推进，暂停、异常时间和速率不误判曲末。"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from onepic_desktop_pet.music_control import _windows_timeline_ms


NOW = datetime(2026, 10, 23, 0, 0, tzinfo=timezone.utc)


def timeline(*, age=298):
    return SimpleNamespace(start_time=timedelta(0), end_time=timedelta(seconds=300),
                           position=timedelta(seconds=1.2),
                           last_updated_time=NOW - timedelta(seconds=age))


def test_playing_snapshot_reaches_near_end_even_without_frequent_client_updates():
    assert _windows_timeline_ms(timeline(), 'playing', now=NOW) == (299200, 300000)
    assert _windows_timeline_ms(timeline(), 'paused', now=NOW) == (1200, 300000)
    assert _windows_timeline_ms(timeline(), 'stopped', now=NOW) == (1200, 300000)


def test_invalid_timestamp_never_fakes_completion():
    assert _windows_timeline_ms(timeline(age=-100), 'playing', now=NOW) == (1200, 300000)
    assert _windows_timeline_ms(timeline(age=100000000), 'playing', now=NOW) == (1200, 300000)
    assert _windows_timeline_ms(None, 'playing', now=NOW) == (0, 0)


def test_playback_rate_is_respected_and_position_cannot_exceed_duration():
    assert _windows_timeline_ms(timeline(age=10), 'playing', rate=2, now=NOW) == (21200, 300000)
    assert _windows_timeline_ms(timeline(age=10), 'playing', rate=0, now=NOW) == (1200, 300000)
    assert _windows_timeline_ms(timeline(), 'playing', rate=2, now=NOW) == (300000, 300000)


def test_naive_windows_utc_timestamp_is_interpreted_without_local_timezone_drift():
    value = timeline(age=10)
    value.last_updated_time = value.last_updated_time.replace(tzinfo=None)
    assert _windows_timeline_ms(value, 'playing', now=NOW) == (11200, 300000)
