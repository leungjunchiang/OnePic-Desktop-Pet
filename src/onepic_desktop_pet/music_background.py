"""按完整路径枚举 QQ 窗口；后台点歌只最小化目标，不激活其他应用或发送媒体键。"""
from pathlib import Path
import sys


def music_client_windows(executable: Path) -> tuple[int, ...]:
    if sys.platform != 'win32' or executable.name.casefold() != 'qqmusic.exe' or not executable.is_file():
        return ()
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
    handles = []

    @callback_type
    def visit(hwnd, _):
        try:
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
                    handles.append(int(hwnd))
            finally:
                kernel.CloseHandle(process)
        except (OSError, ValueError):
            pass
        return True
    user.EnumWindows(visit, 0)
    return tuple(handles)


def background_music_client(executable: Path) -> None:
    handles = music_client_windows(executable)
    if not handles:
        return
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
    user.GetWindow.restype = wintypes.HWND
    user.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    for hwnd in handles:
        if user.IsWindowVisible(hwnd) and not user.GetWindow(hwnd, 4):
            user.ShowWindowAsync(hwnd, 7)  # SW_SHOWMINNOACTIVE：不抢前台焦点。
