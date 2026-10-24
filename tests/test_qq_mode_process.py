"""验证 QQ 模式及启动状态工作进程不导入 Qt、不越界回传，并在 Windows 实际隔离启动。"""
import subprocess
import sys
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
    assert backend._request('startup') == 'loading'
