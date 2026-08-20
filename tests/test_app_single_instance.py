"""验证应用级单实例锁，避免重复启动第二个桌面宠物进程。"""

from PySide6.QtCore import QLockFile
import pytest


def test_app_lock_allows_one_process_only(tmp_path):
    path = str(tmp_path / "lili.lock")
    first = QLockFile(path)
    second = QLockFile(path)
    first.setStaleLockTime(0)
    second.setStaleLockTime(0)
    assert first.tryLock(100)
    try:
        assert not second.tryLock(100)
    finally:
        first.unlock()


def test_application_controller_has_one_process_local_owner(monkeypatch):
    """A second bootstrap cannot construct another PetWindow in one process."""

    from onepic_desktop_pet.app import DesktopPetApplication

    sentinel = object()
    monkeypatch.setattr(DesktopPetApplication, "_active_instance", sentinel)
    with pytest.raises(RuntimeError, match="ownership"):
        DesktopPetApplication.__new__(DesktopPetApplication).__init__()
