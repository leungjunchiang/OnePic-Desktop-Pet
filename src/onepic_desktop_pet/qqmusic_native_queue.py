"""把公开元数据交给 QQ 临时播放队列，让客户端负责曲终衔接。

省略 listId/listName，避免请求变成创建云歌单；不下载音频、不读取凭据。
元数据按 ID 缓存，优先复用经相同比对验证的公开歌手目录，换轮无需重新联网。
UTF-16 临时文件延迟清理，防止异步命令读到已删除的文件。
未运行的 QQ 仅在进程启动时请求后台显示；已有窗口、后续换曲和用户打开窗口均不处理。
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
from types import SimpleNamespace
from urllib.parse import urlencode
import uuid

from .qqmusic_online import QQCatalogError, _request_json, candidate_matches
from .music_background import qq_background_startup_options


class QQNativeQueue:
    def __init__(self, *, request_json=None, cancelled=lambda: False, popen=None):
        self.request_json = request_json or _request_json
        self.cancelled = cancelled
        self.popen = popen or subprocess.Popen
        self._rows = {}
        self._directory = None

    @staticmethod
    def _validated(song, row):
        if (not re.fullmatch(r'[1-9][0-9]{0,9}', song.identifier) or int(song.identifier) > 4294967295
                or row.get('type') != 0 or str(row.get('id')) != song.identifier):
            raise ValueError('unexpected song')
        observed = SimpleNamespace(title=row.get('title') or row['name'],
            artist=' / '.join(s['name'] for s in row['singer']), album=row['album']['name'])
        if (not candidate_matches(song, observed) or not isinstance(row.get('file'), dict)
                or not row['file'] or not row.get('mid') or type(row.get('interval')) is not int
                or row['interval'] <= 0):
            raise ValueError('incomplete song metadata: ' + song.identifier)
        return row

    def prepare(self, entries):
        """分批查询真实歌曲资料，完整校验后才允许发送播放命令。"""
        try:
            for song in entries:
                row = (song.native or {}).get('track_info')
                if row is None:
                    row = self._rows.get(song.identifier)
                if row is not None:
                    self._rows[song.identifier] = self._validated(song, row)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise OSError('QQ 歌手目录的完整歌曲资料不匹配，未发出播放命令。') from exc
        missing = [s for s in entries if s.identifier not in self._rows]
        for start in range(0, len(missing), 10):
            if self.cancelled():
                raise OSError('音乐操作已取消。')
            batch = missing[start:start + 10]
            payload = {'comm': {'ct': 24, 'cv': 0}}
            for i, song in enumerate(batch):
                if (not re.fullmatch(r'[1-9][0-9]{0,9}', song.identifier)
                        or int(song.identifier) > 4294967295):
                    raise OSError('QQ 临时歌单包含无效歌曲 ID。')
                payload[f'req{i}'] = {'module': 'music.pf_song_detail_svr',
                    'method': 'get_song_detail_yqq', 'param': {'song_id': int(song.identifier)}}
            try:
                response = self.request_json('https://u.y.qq.com/cgi-bin/musicu.fcg?' +
                    urlencode({'data': json.dumps(payload, separators=(',', ':'))}))
                validated = {}
                for i, song in enumerate(batch):
                    result = response[f'req{i}']
                    row = result['data']['track_info']
                    if result.get('code') != 0:
                        raise ValueError('unexpected song')
                    validated[song.identifier] = self._validated(song, row)
                self._rows.update(validated)
            except (QQCatalogError, KeyError, TypeError, ValueError, AttributeError) as exc:
                raise OSError('QQ 临时歌单资料未完整取得，已保留六毛歌单，请稍后重试。') from exc

    def dispatch(self, executable, entries, index):
        """替换当前临时队列，明确省略会导向云歌单的两个字段。"""
        executable = Path(executable)
        if executable.name.casefold() != 'qqmusic.exe' or not executable.is_file():
            raise OSError('请在设置中选择真实的 QQMusic.exe。')
        if not 1 <= len(entries) <= 200 or type(index) is not int or not 0 <= index < len(entries):
            raise ValueError('QQ 临时歌单位置无效。')
        self.prepare(entries)
        if self.cancelled():
            raise OSError('音乐操作已取消。')
        query = {'cmd': 'playSong', 'params': {
            'songList': [self._rows[s.identifier] for s in entries], 'playIndex': index,
            'clearLast': 1, 'bSingle': '0', 'depot': 0, 'justPlay': 0,
            'recode': '0', 'errstring': '', 'isShowTips': 0}}
        raw = json.dumps(query, ensure_ascii=False, separators=(',', ':')).encode('utf-16-le')
        if len(raw) > 2_000_000:
            raise OSError('QQ 临时歌单资料过大。')
        if self._directory is None:
            self._directory = tempfile.TemporaryDirectory(prefix='lili-qq-native-')
        folder = Path(self._directory.name).resolve()
        path = folder / (uuid.uuid4().hex + '.xml')
        if any(c in str(path) for c in "'\r\n\x00"):
            raise OSError('当前临时目录不兼容 QQ 命令，请更换不含单引号的临时目录。')
        path.write_bytes(raw)
        # QQ has its own single-quote parser; no shell interprets this string.
        options = qq_background_startup_options(executable)
        command = subprocess.list2cmdline([str(executable)]) + " /background /ParseXMLFile '" + str(path) + "'"
        self.popen(command, executable=str(executable), shell=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            **options)
        for old in folder.glob('*.xml'):
            try:
                if old != path and time.time() - old.stat().st_mtime > 60:
                    old.unlink()
            except OSError:
                pass
