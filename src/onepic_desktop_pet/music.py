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
        else:
            command = [str(client)]
            if normalized in {"apple", "spotify"}:
                command.append(music_client_uri(normalized, title))
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except OSError:
        webbrowser.open(music_search_url(normalized, title))
        return MusicLaunchResult(False, "客户端启动失败，已改为打开正版搜索页。")
    return MusicLaunchResult(
        True,
        "已检测并打开音乐客户端搜索页；尚未执行歌曲精确匹配、播放动作或当前歌曲校验。",
    )


def launch_music_client(service: str, title: str, custom_path: str = "") -> MusicLaunchResult:
    """兼容旧调用名称；新代码应使用 :func:`search_song`。"""

    return search_song(service, title, custom_path)


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
