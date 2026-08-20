"""验证应用级单实例锁，避免重复启动第二个桌面宠物进程。"""

from PySide6.QtCore import QLockFile


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
