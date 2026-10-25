"""验证模式进程冷启动预算、操作超时、优先取消、阶段诊断及真实 COM 隔离启动。"""
import json
from contextlib import nullcontext
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import pytest
from onepic_desktop_pet.qqmusic_mode_worker import run_mode_worker
from onepic_desktop_pet.qqmusic_queue_mode import WindowsQQModeBackend


def test_worker_import_does_not_load_gui():
    subprocess.run([sys.executable, '-c',
        'import sys; import onepic_desktop_pet.qqmusic_mode_worker; '
        'assert "onepic_desktop_pet.app" not in sys.modules; '
        'assert "PySide6.QtWidgets" not in sys.modules'], check=True, timeout=10)


def test_worker_rejects_results_outside_its_owned_temp_directory(tmp_path):
    target = tmp_path/'reply.json'
    assert run_mode_worker(['probe', '', '', str(target)]) == 2
    assert not target.exists()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native COM helper')
def test_windows_helper_runs_without_gui_or_installed_qq():
    backend = WindowsQQModeBackend(lambda: None)
    assert backend._request('probe') == 'ready'
    assert backend.last_diagnostics['status'] == 'ok'
    assert {'import-uia', 'initialize-uia', 'operation-start', 'complete'} <= {
        stage['stage'] for stage in backend.last_diagnostics['stages']}
    assert backend._request('startup') == 'loading'


def simulated_worker(monkeypatch, *, startup=8, duration=1):
    from onepic_desktop_pet import qqmusic_queue_mode as module
    now, processes = [0.0], []
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: now[0],
        sleep=lambda amount: now.__setitem__(0, now[0] + amount)))
    class Process:
        def __init__(self, command, **kwargs):
            self.result = Path(command[-1])
            self.done = self.terminated = False
            processes.append(self)
        def poll(self):
            if self.terminated:
                return -15
            ready = now[0] >= startup
            self.result.with_name('progress.json').write_text(json.dumps({
                'stage': 'read-controls' if ready else 'import-uia',
                'stages': [], 'operating': ready}), encoding='utf-8')
            if now[0] >= startup + duration:
                self.result.write_text(json.dumps({'ok': True, 'value': 'ready'}), encoding='utf-8')
                self.done = True
                return 0
            return None
        def terminate(self):
            self.terminated = True
        def wait(self, timeout):
            assert self.terminated
        def kill(self):
            pytest.fail('Cooperative helper needs no kill')
    monkeypatch.setattr(module.subprocess, 'Popen', Process)
    return now, processes


def test_slow_cold_import_has_its_own_budget_and_never_retries_command(monkeypatch):
    now, processes = simulated_worker(monkeypatch)
    backend = WindowsQQModeBackend(lambda: None)
    assert backend._request('read') == 'ready'
    assert 9 <= now[0] < 9.1 and len(processes) == 1 and processes[0].done
    assert backend.last_diagnostics['elapsed_ms'] >= 9000


@pytest.mark.parametrize('startup,duration,action,phase,maximum', [
    (100, 0, 'read', '辅助进程启动', 15.1),
    (1, 100, 'read', '控件操作', 7.1),
    (14, 100, 'set', '控件操作', 24.1),
])
def test_each_phase_is_bounded_and_only_owned_helper_is_terminated(
        monkeypatch, startup, duration, action, phase, maximum):
    now, processes = simulated_worker(monkeypatch, startup=startup, duration=duration)
    backend = WindowsQQModeBackend(lambda: None)
    with pytest.raises(OSError, match=phase + '超时'):
        backend._request(action, '顺序播放')
    assert now[0] < maximum and len(processes) == 1 and processes[0].terminated
    assert backend.last_diagnostics['status'] == 'timeout'


def test_cancel_during_import_is_immediate_and_does_not_retry(monkeypatch):
    now, processes = simulated_worker(monkeypatch)
    backend = WindowsQQModeBackend(lambda: None, cancelled=lambda: now[0] >= .2)
    with pytest.raises(OSError, match='已取消'):
        backend._request('set', '顺序播放')
    assert now[0] < .3 and len(processes) == 1 and processes[0].terminated
    assert backend.last_diagnostics['status'] == 'cancelled'


def test_pre_cancelled_request_never_starts_helper(monkeypatch):
    _, processes = simulated_worker(monkeypatch)
    backend = WindowsQQModeBackend(lambda: None, cancelled=lambda: True)
    with pytest.raises(OSError, match='已取消'):
        backend._request('set', '顺序播放')
    assert not processes


def test_progress_sharing_violation_never_becomes_a_failed_qq_operation(monkeypatch):
    import tempfile
    monkeypatch.setitem(sys.modules, 'uiautomation', SimpleNamespace(
        UIAutomationInitializerInThread=nullcontext, GetRootControl=lambda: object()))
    def locked(*_):
        raise PermissionError('Progress file currently open by the parent')
    monkeypatch.setattr(Path, 'replace', locked)
    with tempfile.TemporaryDirectory(prefix='lili-qq-mode-') as directory:
        result = Path(directory)/'reply.json'
        assert run_mode_worker(['probe', '', '', str(result)]) == 0
        reply = json.loads(result.read_text(encoding='utf-8'))
        assert reply['ok'] and reply['value'] == 'ready'
        assert reply['progress_write_failures'] >= 4
        assert reply['stages'][-1]['stage'] == 'complete'
