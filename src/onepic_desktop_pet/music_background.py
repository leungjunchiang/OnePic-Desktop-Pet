"""按完整路径识别 QQ；仅本次冷启动且无后续用户输入时收起一次，已有窗口和后续换曲不处理。"""
from pathlib import Path
import subprocess
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
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
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


def qq_background_startup_options(executable: Path) -> dict:
    """只设置新 QQ 进程的启动提示，不在投递后、确认循环或曲终移动已有窗口。"""
    if sys.platform != 'win32' or executable.name.casefold() != 'qqmusic.exe' or not executable.is_file():
        return {}
    try:
        if music_client_windows(executable):
            return {}
        from .music import _windows_process_ids
        if _windows_process_ids('QQMusic.exe'):
            return {}  # A tray-only or still-initializing client also belongs to the user.
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 7  # SW_SHOWMINNOACTIVE: only a launch-time request.
        return {'startupinfo': startup}
    except (OSError, AttributeError, ValueError):
        return {}  # Unknown ownership must never trigger a later forced minimize.


def music_client_mode_windows(executable: Path) -> tuple[int, ...]:
    """模式读取只查询可见界面和主 GUI，排除后台计时窗、阴影及大量隐藏服务窗。"""
    handles = music_client_windows(executable)
    if not handles:
        return ()
    import ctypes
    from ctypes import wintypes
    class Placement(ctypes.Structure):
        _fields_ = [('length', wintypes.UINT), ('flags', wintypes.UINT), ('show', wintypes.UINT),
                    ('min', wintypes.POINT), ('max', wintypes.POINT), ('rect', wintypes.RECT)]
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetWindow.restype = wintypes.HWND
    user.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(Placement)]
    visible, hidden_gui = [], []
    for hwnd in handles:
        name = ctypes.create_unicode_buffer(256)
        if not user.GetClassNameW(hwnd, name, len(name)):
            continue
        if name.value in {'QMShadowWndClass', 'AnimationTimerClass'}:
            continue
        if user.IsWindowVisible(hwnd):
            visible.append(hwnd)
        elif name.value == 'TXGuiFoundation' and not user.GetWindow(hwnd, 4):
            placement = Placement()
            placement.length = ctypes.sizeof(placement)
            if user.GetWindowPlacement(hwnd, ctypes.byref(placement)):
                area = max(0, placement.rect.right - placement.rect.left) * max(0, placement.rect.bottom - placement.rect.top)
                hidden_gui.append((area, hwnd))
    if hidden_gui:
        visible.append(max(hidden_gui)[1])
    return tuple(dict.fromkeys(visible))


def _qq_running(executable):
    from .music import _windows_process_ids
    return bool(music_client_windows(executable) or _windows_process_ids('QQMusic.exe'))


def _last_input_stamp():
    import ctypes
    from ctypes import wintypes
    class Input(ctypes.Structure):
        _fields_ = [('size', wintypes.UINT), ('tick', wintypes.DWORD)]
    info = Input()
    info.size = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        raise OSError('Cannot establish user input ownership')
    return info.tick


def _minimize_own_cold_start(executable):
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetWindow.restype = wintypes.HWND
    user.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    for hwnd in music_client_mode_windows(executable):
        if user.IsWindowVisible(hwnd) and not user.GetWindow(hwnd, 4):
            user.ShowWindowAsync(hwnd, 7)


class QQColdStartPolicy:
    """一次点歌请求的一次性冷启动票据；用户已有进程或新增输入即放弃收起。"""
    def __init__(self, *, running=None, input_stamp=None, minimize=None):
        self.running = running or _qq_running
        self.input_stamp = input_stamp or _last_input_stamp
        self.minimize = minimize or _minimize_own_cold_start

    def begin(self, executable):
        if sys.platform != 'win32' or not executable.is_file() or executable.name.casefold() != 'qqmusic.exe':
            return None
        try:
            if self.running(executable):
                return None
            return {'executable': executable, 'input': self.input_stamp(), 'handled': False}
        except (OSError, AttributeError, ValueError):
            return None

    def finish(self, ticket):
        if ticket is None or ticket['handled']:
            return
        ticket['handled'] = True
        try:
            if self.input_stamp() == ticket['input']:
                self.minimize(ticket['executable'])
        except (OSError, AttributeError, ValueError):
            pass
