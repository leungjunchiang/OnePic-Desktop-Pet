"""后台投递 QQ 在线歌曲并核对平台公开的真实元数据；暂停取消优先于后续确认。

仅传递歌曲元数据与客户端命令，不下载音频。目录属于可变化的客户端接口；错误、
登录和会员限制交给客户端处理。命令只投递一次；元数据匹配不等于精确 ID 或完整
播放权限已获确认。所有阻塞操作由既有 MusicCommandThread 调用；分页收集歌单，切歌复用候选元数据。
已有 QQ 窗口不处理；仅本次成功冷启动且无用户新输入时收起一次，不在轮询或曲终反复隐藏。
陈楚生随机歌单优先分页读取公开歌手目录，缓存完整版本元数据，不依赖本机诊断脚本。
保留官方热门排序的原始名次，用于前五十热门与完整曲库各抽十五首。
可信平台快照缺专辑不再误判为播放失败，非空专辑、完整版本标题和歌手仍须匹配。
冷启动先普通打开并等待登录界面就绪，歌曲命令只交给已运行客户端，不使用后台自启动参数。
"""

from __future__ import annotations

import json
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
import unicodedata
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .music import ShuffleBag, SongEntry, find_music_client
from .music_background import QQColdStartPolicy
from .qqmusic_startup import QQClientNotReady, require_running_qq
from .music_playback import (
    MusicPlaybackError, MusicPlaybackOutcome, SongCandidate, SongPlaybackResult,
)


class QQCatalogError(RuntimeError):
    """在线目录不可用、返回格式变化或没有符合条件的歌曲。"""


def canonical(value: str) -> str:
    """保留版本标记，忽略大小写、全半角和标点差异。"""

    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())


def artist_names(value: str) -> frozenset[str]:
    return frozenset(canonical(s) for s in re.split(r'[/、;&，,]', value) if canonical(s))


def candidate_matches(selected: SongCandidate, track) -> bool:
    """完整标题和歌手必须一致；仅可信平台未提供专辑时容许缺值，非空仍严格比对。"""
    if track is None:
        return False
    native = selected.native or {}
    expected, actual = artist_names(selected.artist), artist_names(track.artist)
    artists_match = expected <= actual if native.get('primary_artist_only') else expected == actual
    return (canonical(track.title) == canonical(selected.title) and artists_match
            and (not native.get('album') or canonical(track.album) == canonical(native['album'])
                 or (getattr(track, 'metadata_scope', '') in {'mac-accessibility', 'windows-gsmtc'} and not track.album)))


def _fetch_json(url: str) -> dict:
    request = Request(url, headers={'Referer': 'https://y.qq.com/', 'User-Agent': 'OnePic-Desktop-Pet/0.23.331'})
    try:
        with urlopen(request, timeout=3) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise QQCatalogError('QQ 音乐目录响应过大。')
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('code') != 0:
            raise QQCatalogError('QQ 音乐目录暂时拒绝查询，请稍后再试。')
        return data
    except (OSError, ValueError) as exc:
        raise QQCatalogError('QQ 音乐在线目录查询失败，请检查网络后重试。') from exc


_HTTP_LOCK = threading.Lock()


def _request_json(url: str, *, wall_timeout: float = 3.5) -> dict:
    """连 DNS 解析也设置墙钟上限，退出时不遗留一个无法结束的 Qt 线程。"""

    if not _HTTP_LOCK.acquire(blocking=False):
        raise QQCatalogError('上一项 QQ 目录请求尚未结束，请稍后重试。')
    response = queue.Queue(maxsize=1)

    def fetch():
        try:
            response.put((True, _fetch_json(url)))
        except Exception as exc:
            response.put((False, exc))
        finally:
            _HTTP_LOCK.release()

    # 该守护线程仅作 HTTPS 只读请求，不访问 Qt、媒体控制或用户凭据。
    worker = threading.Thread(target=fetch, name='qq-catalog-http', daemon=True)
    try:
        worker.start()
    except Exception:
        _HTTP_LOCK.release()
        raise
    try:
        ok, value = response.get(timeout=wall_timeout)
    except queue.Empty as exc:
        raise QQCatalogError('QQ 音乐目录查询超时，未发出播放命令，请稍后重试。') from exc
    if not ok:
        raise QQCatalogError(str(value)) from value
    return value


class QQMusicCatalog:
    """只查询公开歌曲元数据，严格筛选歌手、类型和数字 ID。"""

    def __init__(self, request_json=None) -> None:
        self.request_json = request_json or _request_json
        self._artist_cache = None

    def artist_candidates(self, artist: str, *, cancelled=lambda: False) -> tuple[SongCandidate, ...]:
        """读取歌手目录的各页，不把搜索热度前四十首当成整个随机曲库。"""
        if canonical(artist) != canonical('陈楚生'):
            raise QQCatalogError('当前随机歌手目录只支持陈楚生。')
        if self._artist_cache and time.monotonic() - self._artist_cache[0] < 600:
            if cancelled():
                raise QQCatalogError('音乐操作已取消。')
            return self._artist_cache[1]
        songs = {}
        for begin in range(0, 1040, 80):
            if cancelled():
                raise QQCatalogError('音乐操作已取消。')
            query = {'comm': {'ct': 24, 'cv': 0}, 'req': {
                'module': 'musichall.song_list_server', 'method': 'GetSingerSongList',
                'param': {'singerMid': '002PZBgg1S9xPX', 'begin': begin, 'num': 80, 'order': 1}}}
            response = self.request_json('https://u.y.qq.com/cgi-bin/musicu.fcg?' +
                urlencode({'data': json.dumps(query, separators=(',', ':'))}))
            try:
                result = response['req']
                data = result['data']
                rows, total = data['songList'], data['totalNum']
                if (result.get('code') != 0 or data.get('singerMid') != '002PZBgg1S9xPX'
                        or not isinstance(rows, list) or type(total) is not int or total < 0
                        or len(rows) > 80):
                    raise ValueError('unexpected artist catalog')
                for offset, wrapped in enumerate(rows):
                    row = wrapped['songInfo']
                    song_id = str(row.get('id', ''))
                    title = row.get('title') or row.get('name')
                    singers = row.get('singer')
                    if (not re.fullmatch(r'[1-9][0-9]{0,9}', song_id) or int(song_id) > 4294967295
                            or row.get('type') != 0 or not isinstance(title, str) or not title.strip() or len(title) > 200
                            or not isinstance(singers, list) or not singers
                            or re.search(r'伴奏|纯音乐|卡拉\s*OK|试听|片段|预告|\b(?:instrumental|karaoke|preview|snippet|teaser|trailer)\b', title, re.I)):
                        continue
                    names = [s['name'] for s in singers if isinstance(s.get('name'), str)]
                    if canonical(artist) not in artist_names(' / '.join(names)):
                        continue
                    if (not isinstance(row.get('file'), dict) or not row['file'] or not row.get('mid')
                            or type(row.get('interval')) is not int or row['interval'] < 90):
                        continue
                    album = row['album']['name']
                    if not isinstance(album, str) or len(album) > 500:
                        continue
                    songs.setdefault(song_id, SongCandidate('qq', title.strip(), ' / '.join(names), identifier=song_id,
                        native={'album': album, 'requires_subscription': bool(row.get('pay', {}).get('pay_play', 0)),
                                'popularity_rank': begin + offset + 1, 'track_info': row}))
                if not rows or begin + len(rows) >= total:
                    break
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise QQCatalogError('QQ 音乐歌手目录格式已变化，已保留备用目录。') from exc
        if cancelled():
            raise QQCatalogError('音乐操作已取消。')
        if not songs:
            raise QQCatalogError('QQ 音乐歌手目录暂未返回可用歌曲。')
        self._artist_cache = (time.monotonic(), tuple(songs.values()))
        return self._artist_cache[1]

    def search(self, title: str, artist: str, *, page: int = 1) -> tuple[SongCandidate, ...]:
        query = f'{artist} {title}'.strip()
        if not query or len(query) > 200:
            raise QQCatalogError('点歌内容应为 1–200 个字符。')
        url = 'https://c.y.qq.com/soso/fcgi-bin/client_search_cp?' + urlencode(
            {'format': 'json', 'p': max(1, min(5, page)), 'n': 40, 'w': query})
        payload = self.request_json(url)
        try:
            if payload.get('code') != 0:
                raise ValueError('catalog error')
            rows = payload['data']['song']['list']
            if not isinstance(rows, list):
                raise ValueError('song list missing')
            candidates = {}
            for row in rows[:40]:
                song_id = str(row.get('songid', ''))
                if not re.fullmatch(r'[1-9][0-9]{0,9}', song_id) or int(song_id) > 4294967295:
                    continue
                if row.get('type') != 0:
                    continue
                singers = tuple(str(s['name']).strip() for s in row['singer'])
                song_title = str(row['songname']).strip()
                if canonical(artist) not in artist_names(' / '.join(singers)) or not song_title:
                    continue
                if title and canonical(title) != canonical(song_title):
                    continue
                candidates[song_id] = SongCandidate(
                    'qq', song_title, ' / '.join(singers), identifier=song_id,
                    native={'album': str(row.get('albumname') or ''),
                            'requires_subscription': bool(row.get('pay', {}).get('payplay', 0))})
            return tuple(candidates.values())
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise QQCatalogError('QQ 音乐歌曲目录格式已变化，未发出播放命令。') from exc


def dispatch_song(executable: Path, song_id: str, *, popen=None, running_check=None) -> None:
    """只把经过校验的十进制 ID 作为参数交给 QQMusic.exe，不执行 shell。"""

    if executable.name.casefold() != 'qqmusic.exe' or not executable.is_file():
        raise OSError('请在设置中选择真实的 QQMusic.exe。')
    if not re.fullmatch(r'[1-9][0-9]{0,9}', song_id) or int(song_id) > 4294967295:
        raise ValueError('无效的 QQ 音乐数字歌曲 ID。')
    # 不 wait/kill：冷启动时该进程可能成为客户端主进程。
    (running_check or require_running_qq)(executable)
    args = [str(executable)]
    args += ['/playbysongid', f'cmd_count==1&&id_0=={song_id}&&songtype_0==0']
    (popen or subprocess.Popen)(args,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        shell=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        cwd=str(executable.parent))


class QQMusicOnlinePlayer:
    """在线随机袋与精确点歌共用单次投递和有上限的状态确认。"""

    def __init__(self, settings, track_reader, *, catalog=None, client_finder=find_music_client,
                 dispatcher=dispatch_song, verify_timeout=10.0, poll_interval=0.35,
                 sleep=time.sleep, clock=time.monotonic, cancelled=lambda: False,
                 candidate_dispatcher=None, backgrounder=None, verification_detail=None, window_policy=None,
                 client_preparer=None) -> None:
        self.settings = settings
        self.track_reader = track_reader
        self.catalog = catalog or QQMusicCatalog()
        self.client_finder = client_finder
        self.dispatcher = dispatcher
        self.candidate_dispatcher = candidate_dispatcher
        self.verification_detail = verification_detail or (lambda: '')
        self.window_policy = window_policy or QQColdStartPolicy()
        self.client_preparer = client_preparer
        self.current_client = None
        # Retain the old keyword for integrations; playback never calls a window-hiding callback.
        self.verify_timeout = max(0.0, verify_timeout)
        self.poll_interval = max(0.01, poll_interval)
        self.sleep, self.clock = sleep, clock
        self.cancelled = cancelled
        self.bag = ShuffleBag(
            bag_ids=getattr(settings, 'music_shuffle_bag', ()),
            recent_ids=getattr(settings, 'music_recent_history', ()))

    def play(self, title: str = '', artist: str = '陈楚生') -> SongPlaybackResult:
        def failed(message, code):
            return SongPlaybackResult(False, 'qq', title, artist, message, code)

        if self.cancelled():
            return failed('音乐操作已取消。', MusicPlaybackError.PLAY_ACTION_FAILED)
        client = self.client_finder('qq', self.settings.qq_music_path)
        if client is None:
            return failed('未找到 QQ 音乐。请先安装并登录，在设置中选择 QQ 音乐客户端。',
                          MusicPlaybackError.PLAY_ACTION_FAILED)
        try:
            candidates = self.catalog.search(title, artist)
        except QQCatalogError as exc:
            return failed(str(exc), MusicPlaybackError.SEARCH_FAILED)
        if self.cancelled():
            return failed('音乐操作已取消。', MusicPlaybackError.PLAY_ACTION_FAILED)
        if not candidates:
            return failed(f'QQ 音乐目录没有找到{artist}的这首歌，请换一个歌名。',
                          MusicPlaybackError.RESULT_NOT_FOUND)
        if title:
            selected = candidates[0]
        else:
            # 优先选择目录标记无需订阅的歌曲；授权仍由 QQ 客户端判断。
            unrestricted = tuple(c for c in candidates if not (c.native or {}).get('requires_subscription'))
            candidates = unrestricted or candidates
            songs = tuple(SongEntry('qq:' + c.identifier, c.title, c.artist) for c in candidates)
            chosen = self.bag.next(songs)
            selected = next(c for c in candidates if 'qq:' + c.identifier == chosen.id)
            self.settings.music_shuffle_bag, self.settings.music_recent_history = map(list, self.bag.state())
        return self.play_candidate(selected, title=title, artist=artist)

    def play_candidate(self, selected: SongCandidate, *, title: str = '',
                       artist: str = '陈楚生', prepare_queue=None, dispatch_candidate=None,
                       queue_failure_message=None) -> SongPlaybackResult:
        """投递、核对确定歌曲及播放模式，保持用户现有窗口状态。"""

        def failed(message, code):
            return SongPlaybackResult(False, 'qq', title, artist, message, code)

        if self.cancelled():
            return failed('音乐操作已取消。', MusicPlaybackError.PLAY_ACTION_FAILED)
        client = self.client_finder('qq', self.settings.qq_music_path)
        if client is None:
            return failed('未找到 QQ 音乐，请在设置中选择 QQ 音乐客户端。',
                          MusicPlaybackError.PLAY_ACTION_FAILED)
        window_ticket = self.window_policy.begin(client)
        try:
            if self.client_preparer is not None:
                client = self.client_preparer(client)
            self.current_client = client
            if self.cancelled():
                raise QQClientNotReady('音乐操作已取消，未请求播放。')
            candidate_dispatcher = dispatch_candidate or self.candidate_dispatcher
            if candidate_dispatcher is not None:
                candidate_dispatcher(client, selected)
            else:
                self.dispatcher(client, selected.identifier)
        except QQClientNotReady as exc:
            return failed(str(exc), MusicPlaybackError.CLIENT_NOT_READY)
        except (OSError, ValueError, ImportError, AttributeError) as exc:
            return failed(f'QQ 音乐歌曲投递失败：{exc}', MusicPlaybackError.PLAY_ACTION_FAILED)
        deadline = self.clock() + self.verify_timeout
        latest = None
        while True:
            if self.cancelled():
                break
            try:
                latest = self.track_reader()
            except Exception:
                latest = None
            if (not self.cancelled() and latest and str(latest.playback_status).casefold() == 'playing'
                    and candidate_matches(selected, latest)):
                if prepare_queue is not None:
                    ready = prepare_queue()
                    if self.cancelled():
                        return failed('音乐操作已取消，正在执行暂停。', MusicPlaybackError.PLAY_ACTION_FAILED)
                    if not ready:
                        from .qqmusic_queue_mode import MODE_SETUP_MESSAGE
                        message = queue_failure_message() if callable(queue_failure_message) else queue_failure_message
                        return failed(message or MODE_SETUP_MESSAGE, MusicPlaybackError.UI_AUTOMATION_UNAVAILABLE)
                if not self.cancelled():
                    self.window_policy.finish(window_ticket)
                return SongPlaybackResult(
                    True, 'qq', title, artist,
                    f'QQ 音乐正在播放：{latest.artist}《{latest.title}》，六毛陪你一起听♪',
                    selected=selected, current_title=latest.title, current_artist=latest.artist,
                    play_attempts=1, outcome=MusicPlaybackOutcome.PLAYBACK_CONFIRMED)
            remaining = deadline - self.clock()
            if remaining <= 0:
                break
            self.sleep(min(self.poll_interval, remaining))
        if self.cancelled():
            return failed('音乐操作已取消，正在执行暂停。', MusicPlaybackError.PLAY_ACTION_FAILED)
        try:
            detail = str(self.verification_detail() or '')[:600]
        except Exception:
            detail = ''
        return SongPlaybackResult(
            True, 'qq', title, artist,
            f'已向 QQ 音乐请求《{selected.title}》，尚未读到匹配的播放状态。'
            '请检查客户端登录、会员权限或弹窗；可说“查看音乐状态”。' + (f' 原因：{detail}' if detail else ''),
            selected=selected, play_attempts=1,
            outcome=MusicPlaybackOutcome.PLAYBACK_STARTED_UNVERIFIED)
