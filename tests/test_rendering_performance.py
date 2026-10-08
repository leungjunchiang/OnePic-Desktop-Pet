"""透明窗拒绝系统阴影；大账本合并线性，轻量累计与完整报告保持同一口径。"""
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget
from onepic_desktop_pet.focus_analytics import FocusAnalyticsStore
from onepic_desktop_pet.focus_segments import FocusSegment
from onepic_desktop_pet.time_service import BEIJING_TIMEZONE
from onepic_desktop_pet.window import PetWindow
from test_passive_notifications import pet


def test_pet_and_clock_disable_shadow_across_flag_recreation(pet):
    app, window = pet
    for topmost in (False, True):
        window.set_always_on_top(topmost, persist=False)
        for widget in (window, window.work_duration_bubble, window.quick_panel, window.work_controls):
            assert widget.windowFlags() & Qt.WindowType.NoDropShadowWindowHint
            assert widget.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
        assert window.work_duration_bubble.isVisible()
    # Native shadow suppression must not replace the per-pixel alpha surface.
    image = window.grab().toImage()
    assert image.hasAlphaChannel()
    for x, y in ((0, 0), (image.width()-1, 0), (0, image.height()-1)):
        assert image.pixelColor(x, y).alpha() == 0
    assert not window.mask().isEmpty()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native compositor')
def test_native_shadow_flag_keeps_foreground_unchanged():
    app = QApplication.instance() or QApplication([])
    if app.platformName() != 'windows':
        pytest.skip('requires native Windows Qt backend')
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.GetForegroundWindow.restype = wintypes.HWND
    d = ctypes.windll.dwmapi
    d.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    before = u.GetForegroundWindow()
    widget = QWidget()
    try:
        widget.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                              | Qt.WindowType.NoDropShadowWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        enabled = wintypes.BOOL()
        result = d.DwmGetWindowAttribute(int(widget.winId()), 1, ctypes.byref(enabled), ctypes.sizeof(enabled))
        if result != 0:
            pytest.skip('DWM attribute unavailable')
        assert not enabled.value  # DWMWA_NCRENDERING_ENABLED
        assert u.GetForegroundWindow() == before
    finally:
        widget.close(); widget.deleteLater(); app.processEvents()


def test_large_display_merge_is_linear_and_preserves_remote_priority():
    reads = [0]
    class Remote(dict):
        def get(self, *args):
            reads[0] += 1
            return super().get(*args)
    remote = [Remote(segment_id=str(i), marker='remote') for i in range(2000)]
    local = [SimpleNamespace(segment_id=str(i), to_dict=lambda i=i: {'segment_id':str(i)}) for i in range(2500)]
    result = PetWindow._append_missing_display_segments(remote, local+local)
    assert len(result) == 2500
    assert all(result[i] is remote[i] for i in range(2000))
    assert reads[0] <= 3*len(remote)
    assert len(remote) == 2000  # No mutation of the fetched payload.


@pytest.mark.parametrize('period', ['day','week','month','year'])
def test_light_totals_match_full_report_with_midnight_overlap_and_legacy(tmp_path, period):
    now = datetime(2026, 10, 8, 12, tzinfo=BEIJING_TIMEZONE)
    store = FocusAnalyticsStore(path=tmp_path/'focus.json', persist=False, now_provider=lambda:now)
    rows = [
        {'segment_id':'cross', 'start_at': '2026-10-07T23:30:00+08:00', 'end_at':'2026-10-08T00:30:00+08:00'},
        {'segment_id':'overlap', 'start_at':'2026-10-08T00:00:00+08:00', 'end_at':'2026-10-08T01:00:00+08:00'},
        {'segment_id':'quarantine', 'start_at':'2026-10-06T09:00:00+08:00', 'end_at':'2026-10-06T10:00:00+08:00'},
    ]
    store.merge_remote_segments(rows)
    store._state['legacy_daily'] = {'2026-10-07': {'seconds':7200}}
    store._state['days']['2026-10-06']['seconds_untrusted'] = True
    store.set_live_projection_segments([FocusSegment(segment_id='live', session_id='live-session', start_at=now-timedelta(minutes=10), end_at=None)])
    full = store.period_summary(period, now)
    light = store.period_summary(period, now, include_details=False)
    assert light == {key: full[key] for key in light}
    assert light['total_seconds'] > 0
    assert not (tmp_path/'focus.json').exists()


def test_clock_projection_does_not_build_report_metrics(tmp_path, monkeypatch):
    store = FocusAnalyticsStore(path=tmp_path/'focus.json', persist=False)
    monkeypatch.setattr(store, '_best_window', lambda *_args, **_kwargs: pytest.fail('clock builds report'))
    assert store.period_summary('day', include_details=False)['total_seconds'] == 0
