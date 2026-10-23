"""验证完整曲末及时衔接、异常进度不抢切、曲终暂停隔离及换轮免联网。"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from onepic_desktop_pet.config import PetSettings
from onepic_desktop_pet.music_control import (
    MusicController, MusicProviderManager, WindowsSessionSnapshot, _windows_timeline_age_ms,
)
from test_qqmusic_playlist import queue, track


def fresh(value, raw, age, *, position=None, state='playing'):
    return replace(track(value, state, raw), raw_position_ms=raw, timeline_age_ms=age,
                   position_ms=min(200000, raw + (age or 0)) if position is None else position)


def arm(value):
    value.queue_mode = SimpleNamespace(isolated=True)
    assert not value.ended(fresh(value, 197500, 400))
    value.test_time[0] += 1
    assert not value.ended(fresh(value, 198500, 400))


def test_fresh_advancing_tail_hands_off_at_full_deadline_without_a_wrap():
    value, _ = queue()
    arm(value)
    value.test_time[0] += 1.09
    assert not value.ended(fresh(value, 199500, 590))
    value.test_time[0] += .02
    assert value.ended(fresh(value, 199500, 610))
    assert value.completion_reason == 'fresh-timeline-end'


@pytest.mark.parametrize('bad', [
    {'raw_position_ms':70000}, {'timeline_age_ms':None}, {'timeline_age_ms':2001},
    {'timeline_age_ms':-1}, {'raw_position_ms':None},
])
def test_optimistic_or_untrusted_timeline_cannot_shorten_track(bad):
    value, _ = queue()
    arm(value)
    value.test_time[0] += 2
    snapshot = replace(fresh(value, 199500, 1000, position=200000), **bad)
    assert not value.ended(snapshot)


def test_one_tail_sample_buffering_pause_and_seek_do_not_create_completion():
    value, _ = queue()
    value.queue_mode = SimpleNamespace(isolated=True, release=lambda: True)
    assert not value.ended(fresh(value, 199500, 600))  # One snapshot is insufficient.
    assert not value.ended(fresh(value, 199500, 900))
    assert not value.ended(fresh(value, 30000, 0))  # Seek clears proof.
    value.test_time[0] += 200
    assert not value.ended(fresh(value, 199500, 600))
    assert not value.ended(fresh(value, 199500, 600, state='paused'))
    assert not value.auto_advance


def test_refreshed_frozen_tail_moves_deadline_instead_of_skipping_buffered_audio():
    value, _ = queue()
    arm(value)
    for _ in range(10):
        value.test_time[0] += .5
        assert not value.ended(fresh(value, 198500, 0, position=200000))


def test_fast_poll_is_limited_to_tail_and_reverts_for_a_stalled_timeline():
    value, _ = queue()
    assert value.poll_interval_ms == 1000
    arm(value)
    assert value.poll_interval_ms == 75
    value.test_time[0] += 5
    assert value.poll_interval_ms == 1000
    assert not value.ended(fresh(value, 30000, 0))
    assert value.poll_interval_ms == 1000


def test_timeline_freshness_rejects_stale_future_pause_and_changed_speed():
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    for age, state, rate, expected in ((.6,'playing',1,600), (2.1,'playing',1,None),
                                      (-.1,'playing',1,None), (.6,'paused',1,None),
                                      (.6,'playing',2,None)):
        timeline = SimpleNamespace(last_updated_time=now - timedelta(seconds=age))
        assert _windows_timeline_age_ms(timeline, state, rate=rate, now=now) == expected


def test_handoff_pause_retains_mode_and_confirmed_next_follows_saved_queue():
    value, player = queue()
    released, actions = [], []
    value.queue_mode = SimpleNamespace(isolated=True, release=lambda: released.append(True))
    state = ['playing']
    current = [fresh(value,197500,400)]
    def sessions():
        return (WindowsSessionSnapshot('QQMusic.exe',replace(current[0],playback_status=state[0]),frozenset({'pause'})),)
    def control(source, action):
        actions.append((source,action))
        state[0] = 'paused'
        return True
    manager = MusicProviderManager(PetSettings(), platform_name='win32', client_finder=lambda *_:None,
                                  windows_bridge=SimpleNamespace(sessions=sessions,control=control))
    manager.qq_playlist = value
    expected = value.entries[1].identifier
    assert manager._playlist_tick() is None
    value.test_time[0] += 1
    current[0] = fresh(value,198500,400)
    assert manager._playlist_tick() is None
    value.test_time[0] += 1.11
    current[0] = fresh(value,199500,610)
    result = manager._playlist_tick()
    assert result.success and result.playlist_index == 1 and player.sent[-1] == expected
    assert actions == [('QQMusic.exe','pause')] and not released
    assert value.queue_mode.isolated and value.auto_advance


def test_handoff_never_controls_a_non_qq_or_unavailable_session():
    value, _ = queue()
    calls = []
    bridge = SimpleNamespace(sessions=lambda:(WindowsSessionSnapshot('Other.exe',track(value),frozenset({'pause'})),),
                             control=lambda *args:calls.append(args))
    manager = MusicProviderManager(PetSettings(),platform_name='win32',windows_bridge=bridge,
                                   client_finder=lambda *_:None)
    manager.qq_playlist = value
    assert not manager._pause_qq_handoff() and not calls


def test_automatic_new_round_reuses_catalog_and_manual_random_refreshes_it():
    value, player = queue()
    searched = len(player.searched)
    value.index = 29
    manager = MusicProviderManager(PetSettings(),platform_name='win32',client_finder=lambda *_:None)
    manager.qq_playlist = value
    manager._qq_online_track = lambda:track(value, position=199000)
    assert manager._playlist_tick() is None
    manager._qq_online_track = lambda:track(value,'stopped',200000)
    assert manager._playlist_tick() is None
    value.test_time[0] += 1.3
    result = manager._playlist_tick()
    assert result.success and value.index == 0 and value.round_number == 2
    assert len(player.searched) == searched and len(value.entries) == 30
    assert value.create().success and len(player.searched) > searched


def test_controller_retimes_poll_after_a_tail_snapshot_without_a_result():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    value, _ = queue()
    controller = MusicController(PetSettings(), SimpleNamespace(qq_playlist=value))
    try:
        controller._completed(None)
        assert controller._playlist_timer.interval() == 1000
        arm(value)
        controller._completed(None)
        assert controller._playlist_timer.interval() == 75
        value.test_time[0] += 5
        controller._completed(None)
        assert controller._playlist_timer.interval() == 1000
    finally:
        controller.shutdown()
