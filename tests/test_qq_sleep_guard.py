"""暂停守卫必须认可已停止；未明确选择时不得关闭 QQ 及其账号会话。"""
import json
import sys
from types import SimpleNamespace

import pytest

from tools import qqmusic_sleep_guard as guard


@pytest.mark.parametrize('state,expected,status', [
    ('paused', 0, 'PAUSED_CONFIRMED'),
    ('stopped', 0, 'STOPPED_CONFIRMED'),
    ('closed', 0, 'STOPPED_CONFIRMED'),
    ('playing', 1, 'PAUSE_NOT_CONFIRMED'),
    ('unknown', 1, 'PAUSE_NOT_CONFIRMED'),
])
def test_default_guard_never_closes_client_and_accepts_stopped(tmp_path, monkeypatch, state, expected, status):
    report = tmp_path / 'guard.json'
    track = SimpleNamespace(playback_status=state, artist='陈楚生', title='歌曲', album='专辑')
    session = SimpleNamespace(source_id='QQMusic.exe', track=track)
    controls = []
    monkeypatch.setattr(guard, 'WindowsGSMTCBridge', lambda **_: SimpleNamespace(
        sessions=lambda: (session,), control=lambda *args: controls.append(args)))
    monkeypatch.setattr(guard.time, 'sleep', lambda _: None)
    monkeypatch.setattr(guard, 'qq_pids', lambda _: pytest.fail('Default guard must not enumerate targets to close'))
    monkeypatch.setattr(guard.subprocess, 'run', lambda *_a, **_k: pytest.fail('Default guard must not close QQ'))
    monkeypatch.setattr(sys, 'argv', ['guard', '--qq-exe', 'QQMusic.exe', '--report', str(report), '--timeout', '0'])
    assert guard.main() == expected
    evidence = json.loads(report.read_text(encoding='utf-8'))
    assert evidence['status'] == status
    assert controls and all(action == 'pause' for _, action in controls)
    assert 'closed_pids' not in evidence
