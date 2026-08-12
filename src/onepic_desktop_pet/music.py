"""处理指定歌曲搜索、客户端发现和正版网页回退，不承担基础播放控制。

支持 QQ、网易云、酷狗、Apple Music 与 Spotify。本模块不内置歌词、音频或非公开曲库接口。
用户主动点歌后，Lili 会把搜索交给已安装客户端，并在系统允许时尝试选中首条结果；客户端
不存在时打开官方搜索网页。搜索结果不等于已经建立播放控制，基础播放命令由 music_control.py
读取系统媒体 Session 或发送媒体键。键盘自动化只针对这次明确点击，不在后台控制其他应用。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import random
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from pathlib import Path


CHEN_CHUSHENG_SONGS = (
    "有没有人告诉你",
    "山楂花",
    "经过",
    "思念一个荒废的名字",
    "荒废光年",
    "原来我一直都不孤单",
    "风起时想你",
    "晓得",
    "我等待的",
    "一夜",
)

MUSIC_SERVICE_LABELS = {
    "qq": "QQ 音乐",
    "netease": "网易云音乐",
    "kugou": "酷狗音乐",
    "apple": "Apple Music",
    "spotify": "Spotify",
}


def choose_song(random_source: random.Random | None = None) -> str:
    """随机返回一个歌曲标题，不包含歌词内容。"""

    return (random_source or random).choice(CHEN_CHUSHENG_SONGS)


def music_search_url(service: str, title: str) -> str:
    """构造受支持正版音乐平台的官方搜索网址。"""

    query = urllib.parse.quote(f"陈楚生 {title}")
    if service == "qq":
        return f"https://y.qq.com/n/ryqq/search?w={query}&t=song"
    if service == "kugou":
        return f"https://www.kugou.com/yy/html/search.html#searchType=song&searchKeyWord={query}"
    if service == "apple":
        return f"https://music.apple.com/cn/search?term={query}"
    if service == "spotify":
        return f"https://open.spotify.com/search/{query}"
    return f"https://music.163.com/#/search/m/?s={query}&type=1"


def music_client_uri(service: str, title: str) -> str:
    """为支持深链的客户端生成应用内搜索地址。"""

    query = urllib.parse.quote(f"陈楚生 {title}")
    if service == "spotify":
        return f"spotify:search:{query}"
    if service == "apple":
        return f"music://music.apple.com/cn/search?term={query}"
    return music_search_url(service, title)


@dataclass(frozen=True)
class MusicLaunchResult:
    """说明客户端是否找到以及用户可见的启动结果。"""

    client_found: bool
    message: str


def music_client_candidates(service: str) -> tuple[Path, ...]:
    """返回当前平台常见的正版音乐客户端位置。"""

    if sys.platform == "darwin":
        names = {
            "qq": ("QQMusic.app", "QQ音乐.app"),
            "netease": ("NeteaseMusic.app", "网易云音乐.app"),
            "kugou": ("KugouMusic.app", "酷狗音乐.app"),
            "apple": ("Music.app",),
            "spotify": ("Spotify.app",),
        }.get(service, ())
        roots = (Path("/Applications"), Path.home() / "Applications")
        candidates = [root / name for root in roots for name in names]
        if service == "apple":
            candidates.insert(0, Path("/System/Applications/Music.app"))
        return tuple(candidates)
    if os.name == "nt":
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        program_x86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        if service == "qq":
            return (
                program_x86 / "Tencent" / "QQMusic" / "QQMusic.exe",
                program_files / "Tencent" / "QQMusic" / "QQMusic.exe",
                local / "Tencent" / "QQMusic" / "QQMusic.exe",
            )
        if service == "netease":
            return (
                program_files / "NetEase" / "CloudMusic" / "cloudmusic.exe",
                program_x86 / "NetEase" / "CloudMusic" / "cloudmusic.exe",
                local / "NetEase" / "CloudMusic" / "cloudmusic.exe",
            )
        if service == "kugou":
            return (
                program_files / "KuGou" / "KGMusic" / "KuGou.exe",
                program_x86 / "KuGou" / "KGMusic" / "KuGou.exe",
                local / "KuGou" / "KGMusic" / "KuGou.exe",
                Path(os.environ.get("APPDATA", "")) / "KuGou8" / "KuGou.exe",
            )
        if service == "apple":
            return (
                local / "Microsoft" / "WindowsApps" / "AppleMusic.exe",
                program_files / "Apple" / "Apple Music" / "AppleMusic.exe",
            )
        if service == "spotify":
            return (
                Path(os.environ.get("APPDATA", "")) / "Spotify" / "Spotify.exe",
                local / "Microsoft" / "WindowsApps" / "Spotify.exe",
                program_files / "Spotify" / "Spotify.exe",
            )
    return ()


def find_music_client(service: str, custom_path: str = "") -> Path | None:
    """优先返回用户选择的程序，否则返回首个自动检测到的客户端。"""

    selected = Path(custom_path).expanduser() if custom_path.strip() else None
    if selected is not None and selected.exists():
        if (os.name == "nt" and selected.is_file() and selected.suffix.casefold() == ".exe") or (
            sys.platform == "darwin" and selected.is_dir() and selected.suffix.casefold() == ".app"
        ):
            return selected
    return next((path for path in music_client_candidates(service) if path.exists()), None)


def search_song(service: str, title: str, custom_path: str = "") -> MusicLaunchResult:
    """在客户端发起指定歌曲搜索；绝不把“已打开”报告为“已连接”。"""

    normalized = service if service in MUSIC_SERVICE_LABELS else "netease"
    client = find_music_client(normalized, custom_path)
    query = f"陈楚生 {title}"
    if client is None:
        webbrowser.open(music_search_url(normalized, title))
        label = MUSIC_SERVICE_LABELS[normalized]
        return MusicLaunchResult(False, f"没有找到已安装的{label}，已改为打开正版搜索页。")
    try:
        if sys.platform == "darwin":
            subprocess.Popen(
                ["open", "-a", str(client), music_client_uri(normalized, title)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            threading.Thread(
                target=_macos_try_play_first_result,
                args=(client.stem, normalized),
                daemon=True,
            ).start()
        else:
            command = [str(client)]
            if normalized in {"apple", "spotify"}:
                command.append(music_client_uri(normalized, title))
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if os.name == "nt":
                threading.Thread(
                    target=_windows_search_and_play,
                    args=(client.name, query, normalized),
                    daemon=True,
                ).start()
    except OSError:
        webbrowser.open(music_search_url(normalized, title))
        return MusicLaunchResult(False, "客户端启动失败，已改为打开正版搜索页。")
    return MusicLaunchResult(
        True,
        "已检测并打开音乐客户端，正在定位搜索结果；是否开始播放取决于客户端，"
        "这不代表已建立播放控制。",
    )


def launch_music_client(service: str, title: str, custom_path: str = "") -> MusicLaunchResult:
    """兼容旧调用名称；新代码应使用 :func:`search_song`。"""

    return search_song(service, title, custom_path)


def _macos_try_play_first_result(application_name: str, service: str) -> bool:
    """在用户主动点歌后，借助已授权的辅助功能尝试播放首条结果。"""

    if sys.platform != "darwin":
        return False
    time.sleep(2.5)
    safe_name = application_name.replace("\\", "\\\\").replace('"', '\\"')
    # 不请求或绕过系统权限；未授权“辅助功能”时 osascript 会直接失败，
    # 客户端仍停留在正版搜索结果页供用户手动选择。
    down_count = 2 if service in {"apple", "spotify"} else 1
    script = (
        'tell application "System Events"\n'
        'if UI elements enabled then\n'
        f'tell process "{safe_name}"\n'
        'set frontmost to true\n'
        f'repeat {down_count} times\nkey code 125\nend repeat\n'
        'key code 36\ndelay 0.7\nkey code 36\n'
        'end tell\nend if\nend tell'
    )
    try:
        completed = subprocess.run(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _windows_search_and_play(executable_name: str, query: str, service: str = "netease") -> bool:
    """短暂聚焦刚启动的音乐客户端，通过搜索快捷键尝试播放首条结果。"""

    if os.name != "nt":
        return False
    hwnd = 0
    for _attempt in range(12):
        time.sleep(0.5)
        hwnd = _find_windows_app_window(executable_name)
        if hwnd:
            break
    if not hwnd:
        return False
    user32 = ctypes.windll.user32
    if not user32.IsWindow(hwnd):
        return False
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.4)
    if not _is_foreground_window(hwnd):
        return False
    original_cursor = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(original_cursor))
    try:
        search_points = {
            "qq": (0.50, 0.055),
            "netease": (0.43, 0.055),
            "kugou": (0.46, 0.060),
            "apple": (0.48, 0.060),
            "spotify": (0.45, 0.065),
        }
        result_points = {
            "qq": (0.45, 0.31),
            "netease": (0.43, 0.30),
            "kugou": (0.44, 0.30),
            "apple": (0.45, 0.31),
            "spotify": (0.45, 0.32),
        }
        _click_window_relative(hwnd, *search_points.get(service, search_points["netease"]))
        time.sleep(0.2)
        if not _is_foreground_window(hwnd):
            return False
        _press_virtual_key(0x11, down=True)
        _press_virtual_key(ord("A"), down=True)
        _press_virtual_key(ord("A"), down=False)
        _press_virtual_key(0x11, down=False)
        time.sleep(0.2)
        if not _is_foreground_window(hwnd):
            return False
        _send_unicode_text(query)
        _tap_virtual_key(0x0D)
        time.sleep(2.8)
        if not _is_foreground_window(hwnd):
            return False
        # Keyboard selection works across more layouts; the relative double
        # click remains a fallback for clients that keep focus in the search box.
        _tap_virtual_key(0x28)
        _tap_virtual_key(0x0D)
        time.sleep(0.7)
        if _is_foreground_window(hwnd):
            _double_click_window_relative(hwnd, *result_points.get(service, result_points["netease"]))
        return True
    finally:
        user32.SetCursorPos(original_cursor.x, original_cursor.y)


def _is_foreground_window(hwnd: int) -> bool:
    """只在目标音乐窗口确实位于前台时允许发送鼠标和键盘事件。"""

    user32 = ctypes.windll.user32
    return bool(user32.IsWindow(hwnd) and user32.GetForegroundWindow() == hwnd)


def _find_windows_app_window(executable_name: str) -> int:
    """寻找属于指定可执行文件的可见顶层窗口。"""

    process_ids = _windows_process_ids(executable_name)
    if not process_ids:
        return 0
    matches: list[tuple[int, int]] = []
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        process_id = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if process_id.value not in process_ids:
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
        matches.append((area, int(hwnd)))
        return True

    user32.EnumWindows(callback_type(visit), 0)
    return max(matches)[1] if matches else 0


def _windows_process_ids(executable_name: str) -> set[int]:
    """通过 Toolhelp 快照返回指定可执行文件的全部进程号。"""

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot in (0, ctypes.c_void_p(-1).value):
        return set()
    entry = ProcessEntry(); entry.dwSize = ctypes.sizeof(ProcessEntry)
    target = executable_name.casefold()
    result: set[int] = set()
    try:
        success = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while success:
            if str(entry.szExeFile).casefold() == target:
                result.add(int(entry.th32ProcessID))
            success = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return result


def _window_point(hwnd: int, x_ratio: float, y_ratio: float) -> tuple[int, int]:
    """把窗口比例位置转换为屏幕坐标。"""

    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (
        rect.left + round((rect.right - rect.left) * x_ratio),
        rect.top + round((rect.bottom - rect.top) * y_ratio),
    )


def _click_window_relative(hwnd: int, x_ratio: float, y_ratio: float) -> None:
    """单击窗口内的相对位置。"""

    x, y = _window_point(hwnd, x_ratio, y_ratio)
    user32 = ctypes.windll.user32
    user32.SetCursorPos(x, y)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)


def _double_click_window_relative(hwnd: int, x_ratio: float, y_ratio: float) -> None:
    """双击窗口内的相对位置，用于播放第一条搜索结果。"""

    _click_window_relative(hwnd, x_ratio, y_ratio)
    time.sleep(0.1)
    _click_window_relative(hwnd, x_ratio, y_ratio)


def _press_virtual_key(key: int, down: bool) -> None:
    """发送一个普通虚拟按键事件。"""

    ctypes.windll.user32.keybd_event(key, 0, 0 if down else 0x0002, 0)


def _tap_virtual_key(key: int) -> None:
    """按下并释放一个普通虚拟按键。"""

    _press_virtual_key(key, True)
    _press_virtual_key(key, False)


def _send_unicode_text(text: str) -> None:
    """用 Unicode 键盘事件输入中文，不读取或覆盖用户剪贴板。"""

    class KeyboardInput(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
            ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class MouseInput(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long), ("dy", ctypes.c_long),
            ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t),
        ]

    class HardwareInput(ctypes.Structure):
        _fields_ = [
            ("uMsg", ctypes.c_ulong),
            ("wParamL", ctypes.c_ushort),
            ("wParamH", ctypes.c_ushort),
        ]

    class InputUnion(ctypes.Union):
        _fields_ = [("mi", MouseInput), ("ki", KeyboardInput), ("hi", HardwareInput)]

    class Input(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("union", InputUnion)]

    for character in text:
        code = ord(character)
        down = Input(1, InputUnion(ki=KeyboardInput(0, code, 0x0004, 0, 0)))
        up = Input(1, InputUnion(ki=KeyboardInput(0, code, 0x0004 | 0x0002, 0, 0)))
        events = (Input * 2)(down, up)
        ctypes.windll.user32.SendInput(2, ctypes.byref(events), ctypes.sizeof(Input))
