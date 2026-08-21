"""处理六毛的选歌、音乐 Deep Link 和正版网页回退，不承担基础播放控制。

支持 QQ、网易云、酷狗、Apple Music 与 Spotify。本模块不内置歌词、音频或非公开曲库接口。
快捷“随机听一首”只负责从本地曲库选歌，然后直接尝试平台 Deep Link；Deep Link 失效、客户端
未安装或系统拒绝唤起时，降级到正版 HTTPS 页面。打开客户端不等于已确认播放，基础播放命令
仍由 music_control.py 读取系统媒体 Session 或发送媒体键。
“随机电台”则只按用户设置的默认音乐软件打开陈楚生歌手入口；不跨平台抢占其它播放器，后续
随机播放交给用户选择的音乐客户端或其官方网页。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import random
import re
import subprocess
import sys
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .resources import resource_path


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


@dataclass(frozen=True)
class SongEntry:
    """曲库中的一首歌；平台 ID 只用于生成可失效的唤起地址。"""

    id: str
    title: str
    artist: str = "陈楚生"
    album: str = ""
    tags: tuple[str, ...] = ()
    netease_song_id: str = ""
    qq_song_mid: str = ""
    apple_music_url: str = ""
    spotify_url: str = ""
    web_urls: Mapping[str, str] = field(default_factory=dict)
    enabled: bool = True
    weight: float = 1.0
    last_verified: str = ""

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object], *, default_index: int = 0) -> "SongEntry":
        """从公开 JSON 曲目卡读取一首歌，并忽略未知字段。"""

        netease = raw.get("netease") if isinstance(raw.get("netease"), Mapping) else {}
        qq = raw.get("qqmusic") if isinstance(raw.get("qqmusic"), Mapping) else {}
        apple = raw.get("apple_music") if isinstance(raw.get("apple_music"), Mapping) else {}
        spotify = raw.get("spotify") if isinstance(raw.get("spotify"), Mapping) else {}
        tags = raw.get("tags")
        web_urls = raw.get("web_urls")
        return cls(
            id=str(raw.get("id") or f"chen_chusheng_{default_index:03d}"),
            title=str(raw.get("title") or "").strip(),
            artist=str(raw.get("artist") or "陈楚生").strip(),
            album=str(raw.get("album") or "").strip(),
            tags=tuple(str(item).strip() for item in tags if str(item).strip())
            if isinstance(tags, (list, tuple))
            else (),
            netease_song_id=str(netease.get("song_id") or raw.get("netease_song_id") or "").strip(),
            qq_song_mid=str(qq.get("song_mid") or raw.get("qq_song_mid") or "").strip(),
            apple_music_url=str(apple.get("url") or raw.get("apple_music_url") or "").strip(),
            spotify_url=str(spotify.get("url") or raw.get("spotify_url") or "").strip(),
            web_urls={str(key): str(value) for key, value in web_urls.items()}
            if isinstance(web_urls, Mapping)
            else {},
            enabled=bool(raw.get("enabled", True)),
            weight=max(0.1, float(raw.get("weight", 1.0) or 1.0)),
            last_verified=str(raw.get("last_verified") or "").strip(),
        )


class ShuffleBag:
    """一轮内不重复的随机袋，避免随机选择连续撞到同一首歌。"""

    def __init__(
        self,
        *,
        bag_ids: Sequence[str] = (),
        recent_ids: Sequence[str] = (),
        random_source: random.Random | None = None,
        recent_limit: int = 3,
    ) -> None:
        self.random = random_source or random.Random()
        self.bag_ids = list(dict.fromkeys(str(item) for item in bag_ids if str(item)))
        self.recent_limit = max(1, int(recent_limit))
        self.recent_ids = list(dict.fromkeys(str(item) for item in recent_ids if str(item)))[-self.recent_limit:]

    def next(self, entries: Sequence[SongEntry]) -> SongEntry:
        """从启用曲目中取下一首；曲目不足时才允许跨轮重复。"""

        available = [entry for entry in entries if entry.enabled and entry.title]
        if not available:
            raise ValueError("曲库没有可用歌曲")
        by_id = {entry.id: entry for entry in available}
        self.bag_ids = [item for item in self.bag_ids if item in by_id]
        if not self.bag_ids:
            ordered = list(available)
            # 权重只影响一轮内的顺序，不会改变“不重复”的保证。
            ordered.sort(
                key=lambda entry: self.random.random() ** (1.0 / max(0.1, entry.weight)),
                reverse=True,
            )
            self.bag_ids = [entry.id for entry in ordered]
        if len(by_id) > 1:
            for index, song_id in enumerate(self.bag_ids):
                if song_id not in self.recent_ids:
                    if index:
                        self.bag_ids.insert(0, self.bag_ids.pop(index))
                    break
        selected_id = self.bag_ids.pop(0)
        self.recent_ids.append(selected_id)
        self.recent_ids = self.recent_ids[-self.recent_limit:]
        return by_id[selected_id]

    def state(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """返回可安全持久化到 PetSettings 的纯字符串状态。"""

        return tuple(self.bag_ids), tuple(self.recent_ids)


def load_song_catalog(path: Path | None = None) -> tuple[SongEntry, ...]:
    """加载打包曲库；失败时返回内置标题卡，保证旧版本仍可点歌。"""

    catalog_path = path or resource_path("resources/chen_chusheng_music_catalog.json")
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
        raw_songs = payload.get("songs", []) if isinstance(payload, Mapping) else []
        entries = tuple(
            entry
            for index, raw in enumerate(raw_songs)
            if isinstance(raw, Mapping)
            for entry in (SongEntry.from_mapping(raw, default_index=index),)
            if entry.title
        )
        if entries:
            return entries
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return tuple(
        SongEntry(id=f"legacy_{index:03d}", title=title)
        for index, title in enumerate(CHEN_CHUSHENG_SONGS)
    )

MUSIC_SERVICE_LABELS = {
    "qq": "QQ 音乐",
    "netease": "网易云音乐",
    "kugou": "酷狗音乐",
    "apple": "Apple Music",
    "spotify": "Spotify",
}


_DEFAULT_SHUFFLE_BAG = ShuffleBag()


def choose_song(random_source: random.Random | None = None) -> str:
    """按洗牌袋返回一个歌曲标题，不包含歌词内容。"""

    entries = tuple(
        SongEntry(id=str(index), title=title)
        for index, title in enumerate(CHEN_CHUSHENG_SONGS)
    )
    if random_source is not None:
        return ShuffleBag(random_source=random_source).next(entries).title
    return _DEFAULT_SHUFFLE_BAG.next(entries).title


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


def artist_collection_url(service: str, artist: str = "陈楚生") -> str:
    """返回连续播放的官方歌手/曲库入口，不假设第三方 Scheme 永久有效。"""

    normalized = str(service or "").casefold()
    if normalized == "netease" and artist == "陈楚生":
        return "https://music.163.com/#/artist?id=2124"
    if normalized == "apple" and artist == "陈楚生":
        return "https://music.apple.com/cn/artist/%E9%99%88%E6%A5%9A%E7%94%9F/930912184"
    return music_search_url(normalized, artist)


def artist_collection_deep_link(service: str, artist: str = "陈楚生") -> str:
    """返回客户端歌手入口；私有 Scheme 失败时由调用方回退到 HTTPS。"""

    normalized = str(service or "").casefold()
    if normalized == "netease" and artist == "陈楚生":
        # 网易云 Windows 客户端支持通过 orpheus 打开歌手页；autoplay
        # 让客户端有机会从歌手曲库开始播放，连续播放仍由客户端负责。
        return "orpheus://artist/2124/?autoplay=1"
    return ""


def music_client_uri(service: str, title: str) -> str:
    """为支持深链的客户端生成应用内搜索地址。"""

    query = urllib.parse.quote(f"陈楚生 {title}")
    if service == "spotify":
        return f"spotify:search:{query}"
    if service == "apple":
        return f"music://music.apple.com/cn/search?term={query}"
    return music_search_url(service, title)


def song_deep_link(service: str, song: SongEntry) -> str:
    """生成平台私有但常用的单曲唤起地址；没有真实 ID 时返回空串。"""

    normalized = str(service or "").casefold()
    if normalized == "netease" and song.netease_song_id:
        return f"orpheus://song/{urllib.parse.quote(song.netease_song_id, safe='')}/?autoplay=1"
    if normalized == "qq" and song.qq_song_mid:
        encoded = urllib.parse.quote(song.qq_song_mid, safe="")
        return f"qqmusic://qq.com/media/playSonglist?p={encoded}"
    if normalized == "apple" and song.apple_music_url:
        return song.apple_music_url
    if normalized == "spotify" and song.spotify_url:
        return song.spotify_url
    return ""


def song_web_url(service: str, song: SongEntry) -> str:
    """返回优先级最高的正版 HTTPS 歌曲链接，缺失时再生成官方搜索页。"""

    normalized = str(service or "").casefold()
    explicit = song.web_urls.get(normalized, "") if isinstance(song.web_urls, Mapping) else ""
    if explicit.startswith("https://"):
        return explicit
    if normalized == "apple" and song.apple_music_url.startswith("https://"):
        return song.apple_music_url
    if normalized == "spotify" and song.spotify_url.startswith("https://"):
        return song.spotify_url
    return music_search_url(normalized, song.title)


@dataclass(frozen=True)
class MusicLaunchResult:
    """说明客户端是否找到以及用户可见的启动结果。"""

    client_found: bool
    message: str
    attempted_url: str = ""
    fallback_used: bool = False
    confirmed: bool = False


@dataclass(frozen=True)
class CatalogSongLaunch:
    """曲库选歌结果；success 只表示系统接受了唤起，不伪装成播放已确认。"""

    success: bool
    provider: str
    song: SongEntry
    message: str
    attempted_url: str = ""
    fallback_used: bool = False
    confirmed: bool = False


def open_music_url(
    url: str,
    *,
    platform_name: str | None = None,
    startfile: Callable[[str], object] | None = None,
    popen: Callable[..., object] | None = None,
    browser_open: Callable[[str], object] | None = None,
    service: str = "",
    executable: Path | str | None = None,
) -> bool:
    """用系统默认方式唤起 URL；Windows 可用真实 exe 绕过坏的 Scheme 命令。"""

    if not str(url or "").strip():
        return False
    platform = platform_name or sys.platform
    try:
        if platform == "win32":
            if executable:
                client = Path(executable).expanduser()
                if not client.is_file():
                    return False
                runner = popen or subprocess.Popen
                args = [str(client)]
                if str(service or "").casefold() == "netease":
                    # 网易云的 orpheus 注册器使用的就是这个参数；直接传参时
                    # 同时设置 cwd，避免便携/非 Program Files 安装缺少 DLL 搜索目录。
                    args.append(f"--webcmd={url}")
                else:
                    args.append(url)
                kwargs: dict[str, object] = {
                    "cwd": str(client.parent),
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                }
                creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
                if creationflags:
                    kwargs["creationflags"] = creationflags
                existing_client = False
                if client.name:
                    try:
                        # 网易云采用单实例：新的 webcmd 启动器可能把请求交给
                        # 已有进程后立即退出。已有同名进程时不能把这个退出码
                        # 当成“客户端不存在”，否则会错误打开官网回退页。
                        existing_client = bool(_windows_process_ids(client.name))
                    except (AttributeError, OSError, TypeError, ValueError):
                        existing_client = False
                process = runner(args, **kwargs)
                # Loader/initialization error 可能表现为“进程已创建后立刻退出”，
                # 且没有已有实例时才走 HTTPS 回退；单实例客户端的正常转交
                # 不应被误判为启动失败。
                poll = getattr(process, "poll", None)
                if callable(poll):
                    exit_code = poll()
                    if exit_code not in (None, 0) and not existing_client:
                        return False
                return True
            launcher = startfile or getattr(os, "startfile")
            launcher(url)
            return True
        if platform == "darwin":
            runner = popen or subprocess.Popen
            runner(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        opener = browser_open or webbrowser.open
        return bool(opener(url))
    except (AttributeError, OSError, TypeError, ValueError):
        return False


class CatalogMusicService:
    """六毛“帮你挑一首”的轻量服务，和旧 Transport 控制层解耦。"""

    def __init__(
        self,
        settings: object | None = None,
        *,
        songs: Sequence[SongEntry] | None = None,
        random_source: random.Random | None = None,
        opener: Callable[[str], bool] | None = None,
        browser_opener: Callable[[str], bool] | None = None,
        platform_name: str | None = None,
    ) -> None:
        self.settings = settings
        self.songs = tuple(songs or load_song_catalog())
        bag_ids = getattr(settings, "music_shuffle_bag", ()) if settings is not None else ()
        recent_ids = getattr(settings, "music_recent_history", ()) if settings is not None else ()
        self.shuffle_bag = ShuffleBag(
            bag_ids=bag_ids,
            recent_ids=recent_ids,
            random_source=random_source,
        )
        self.platform_name = platform_name or sys.platform
        self._custom_opener = opener is not None
        self.opener = opener or (
            lambda url: open_music_url(url, platform_name=self.platform_name)
        )
        self.browser_opener = browser_opener or (
            lambda url: open_music_url(url, platform_name="other")
        )
        self.last_provider = ""
        self.last_used_deep_link = False

    def _providers(self) -> tuple[str, ...]:
        preferred = str(getattr(self.settings, "music_service", "auto") or "auto").casefold()
        if preferred in {"qq", "netease", "apple", "spotify", "kugou"}:
            return (preferred,) + tuple(
                item for item in ("netease", "qq", "apple") if item != preferred
            )
        return ("netease", "qq", "apple")

    def _artist_collection_providers(self) -> tuple[str, ...]:
        """返回“随机电台”唯一允许使用的播放器顺序。

        电台是用户主动选择的播放器入口，不应像单曲容错那样跨平台尝试；否则用户明明
        选择了 QQ 音乐，网易云失败后却会被悄悄打开，甚至抢占正在播放的其它客户端。
        auto 仍保留兼容顺序，但显式选择时只使用该平台及其网页回退。
        """

        preferred = str(getattr(self.settings, "music_service", "auto") or "auto").casefold()
        if preferred in {"qq", "netease", "kugou", "apple", "spotify"}:
            return (preferred,)
        return ("netease", "qq", "kugou", "apple", "spotify")

    @staticmethod
    def _try_open(opener: Callable[[str], bool], url: str) -> bool:
        """把第三方客户端的异常限制在本次唤起，确保还能走网页降级。"""

        try:
            return bool(opener(url))
        except (AttributeError, OSError, TypeError, ValueError):
            return False

    def _open_deep_link(self, provider: str, url: str) -> bool:
        """在 Windows 优先直接启动真实客户端，其它平台仍走原生 URL 打开器。"""

        if self._custom_opener:
            return self._try_open(self.opener, url)
        custom_path = str(getattr(self.settings, f"{provider}_music_path", "") or "")
        client = find_music_client(provider, custom_path)
        return open_music_url(
            url,
            platform_name=self.platform_name,
            service=provider,
            executable=client if self.platform_name == "win32" else None,
        )

    def play_random_song(self) -> CatalogSongLaunch:
        """选一首歌并直接唤起；私有 Scheme 失败后打开同平台官方网页。"""

        song = self.shuffle_bag.next(self.songs)
        if self.settings is not None:
            bag, recent = self.shuffle_bag.state()
            setattr(self.settings, "music_shuffle_bag", list(bag))
            setattr(self.settings, "music_recent_history", list(recent))
        for provider in self._providers():
            deep_link = song_deep_link(provider, song)
            if deep_link and self._open_deep_link(provider, deep_link):
                return CatalogSongLaunch(True, provider, song, f"给你挑了《{song.title}》♪", deep_link)
        return self.open_song_web_fallback(song)

    def open_song_web_fallback(self, song: SongEntry) -> CatalogSongLaunch:
        """在客户端没有真正播放能力时打开正版歌曲网页作为最终兜底。"""

        for provider in self._providers():
            web_url = song_web_url(provider, song)
            if self._try_open(self.browser_opener, web_url):
                return CatalogSongLaunch(
                    True,
                    provider,
                    song,
                    f"给你挑了《{song.title}》♪ 已打开正版网页。",
                    web_url,
                    fallback_used=True,
                )
        return CatalogSongLaunch(
            False,
            self._providers()[0],
            song,
            f"这次没能打开《{song.title}》，请确认音乐客户端或浏览器可用。",
        )

    def open_artist_collection(self, artist: str = "陈楚生") -> bool:
        """按默认播放器打开歌手入口，失败后只回退到同一平台网页。"""

        self.last_provider = ""
        self.last_used_deep_link = False
        providers = self._artist_collection_providers()
        for provider in providers:
            deep_link = artist_collection_deep_link(provider, artist)
            if deep_link and self._open_deep_link(provider, deep_link):
                self.last_provider = provider
                self.last_used_deep_link = True
                return True
        for provider in providers:
            if self._try_open(self.browser_opener, artist_collection_url(provider, artist)):
                self.last_provider = provider
                return True
        return False


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
    """优先返回用户选择的程序，否则返回首个自动检测到的客户端。

    Windows 的网易云安装目录不一定在 Program Files；有些安装器只注册了
    ``orpheus`` URL Scheme。因此最后再读取注册表中的真实可执行文件路径，
    但不执行注册表里的整条命令。这样可以规避类似
    ``"cloudmusic.exe"--webcmd="%1"`` 这种缺少空格的坏注册命令。
    """

    selected = Path(custom_path).expanduser() if custom_path.strip() else None
    if selected is not None and selected.exists():
        if (os.name == "nt" and selected.is_file() and selected.suffix.casefold() == ".exe") or (
            sys.platform == "darwin" and selected.is_dir() and selected.suffix.casefold() == ".app"
        ):
            return selected
    # URL Scheme 是 Windows 当前真正选择的客户端；优先于机器上可能遗留的
    # 旧 Program Files 副本（本机就同时存在一个旧版 C: 副本和 D: 的活动副本）。
    registered = _registered_music_client(service)
    detected = next((path for path in music_client_candidates(service) if path.exists()), None)
    return registered or detected


def _extract_executable_from_shell_command(command: str) -> Path | None:
    """从 Windows URL Scheme 命令中只提取 exe，不信任其余参数。"""

    value = str(command or "").strip()
    if not value:
        return None
    if value.startswith('"'):
        end = value.find('"', 1)
        if end <= 1:
            return None
        candidate = value[1:end]
    else:
        match = re.match(r"([^\s]+)", value)
        if match is None:
            return None
        candidate = match.group(1)
    path = Path(candidate).expanduser()
    if path.is_file() and path.suffix.casefold() == ".exe":
        return path
    return None


def _registered_music_client(service: str) -> Path | None:
    """读取 Windows URL Scheme 的 exe 路径；macOS/Linux 不触碰注册表。"""

    if os.name != "nt":
        return None
    scheme = {"netease": "orpheus", "qq": "qqmusic"}.get(str(service or "").casefold())
    if not scheme:
        return None
    try:
        import winreg
    except ImportError:
        return None
    subkey = rf"Software\Classes\{scheme}\shell\open\command"
    roots = (
        getattr(winreg, "HKEY_CURRENT_USER", None),
        getattr(winreg, "HKEY_LOCAL_MACHINE", None),
    )
    for root in roots:
        if root is None:
            continue
        try:
            with winreg.OpenKey(root, subkey) as key:
                raw_command, _ = winreg.QueryValueEx(key, None)
            executable = _extract_executable_from_shell_command(str(raw_command))
        except (OSError, TypeError, ValueError):
            continue
        if executable is not None:
            return executable
    return None


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

