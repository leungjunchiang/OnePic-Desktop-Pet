"""投递歌曲时只最小化路径核验后的 QQ 主窗口，不激活其他应用或发送媒体键。"""
from pathlib import Path
import sys


def background_music_client(executable: Path) -> None:
    if sys.platform != 'win32' or executable.name.casefold() != 'qqmusic.exe' or not executable.is_file():
        return
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    expected = str(executable.resolve()).casefold()

    @callback_type
    def visit(hwnd, _):
        try:
            if not user.IsWindowVisible(hwnd) or user.GetWindow(hwnd, 4):
                return True
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process = kernel.OpenProcess(0x1000, False, pid.value)
            if not process:
                return True
            try:
                path = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(path))
                if (kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size))
                        and path.value.casefold() == expected):
                    user.ShowWindowAsync(hwnd, 7)  # SW_SHOWMINNOACTIVE：不抢前台焦点。
            finally:
                kernel.CloseHandle(process)
        except (OSError, ValueError):
            pass
        return True
    user.EnumWindows(visit, 0)
