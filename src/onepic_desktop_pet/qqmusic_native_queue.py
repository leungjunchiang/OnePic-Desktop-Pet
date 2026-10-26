"""以 QQ 官方网页的歌曲 ID 列表投递临时队列，由客户端按当前账号取得权益。

公开元数据只用于本地曲目和版权校验，不把游客试听／付费状态发送给 QQ。
官方多首格式为一个命令内使用竖线分隔 ID 和类型，不是多个独立播放命令。
从所选位置发送剩余队列，保留六毛完整歌单及位置，曲终由 QQ 自然顺序衔接。
不创建云歌单、不下载音频、不读取凭据；资料缓存供后续换轮及点选校验。
未运行的 QQ 仅在进程启动时请求后台显示；已有窗口、后续换曲和用户打开窗口均不处理。
队列命令仅投递已运行客户端；不以带后台或队列参数的进程恢复账号登录。
资料校验同时要求明确播放或付费授权；会员曲保留，无版权或授权未知不发送。
补发前独立核对唯一 QQ 主窗口标题仍是旧暂停曲；媒体状态陈旧时不能重启已开始的目标歌。
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace
from urllib.parse import urlencode

from .qqmusic_online import QQCatalogError, _request_json, candidate_matches, canonical
from .qqmusic_startup import require_running_qq
from .qqmusic_eligibility import playback_rights


class QQNativeQueue:
    def __init__(self, *, request_json=None, cancelled=lambda: False, popen=None, running_check=None):
        self.request_json = request_json or _request_json
        self.cancelled = cancelled
        self.popen = popen or subprocess.Popen
        self.running_check = running_check or require_running_qq
        self._rows = {}

    def retry_is_safe(self, executable, previous):
        """只读 QQ 自身标题作独立否决证据；缺失、默认标题、弹窗或多主窗均不重发。"""
        import ctypes
        from ctypes import wintypes
        from .music_background import music_client_mode_windows
        try:
            user = ctypes.WinDLL('user32', use_last_error=True)
            user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
            user.GetWindow.restype = wintypes.HWND
            user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            user.IsWindowVisible.argtypes = [wintypes.HWND]
            captions, visible_captions = [], []
            self.retry_diagnostics = {'windows': [], 'exact_caption_count': 0}
            for hwnd in music_client_mode_windows(Path(executable)):
                name = ctypes.create_unicode_buffer(256)
                if not user.GetClassNameW(hwnd, name, len(name)):
                    continue
                title = ctypes.create_unicode_buffer(512)
                has_caption = bool(user.GetWindowTextW(hwnd, title, len(title)))
                visible = bool(user.IsWindowVisible(hwnd))
                exact = has_caption and unchanged_song_caption([title.value.strip()], previous)
                self.retry_diagnostics['windows'].append({'class': name.value, 'owned': bool(user.GetWindow(hwnd, 4)), 'visible': visible,
                                                          'has_caption': has_caption, 'exact_caption': exact})
                self.retry_diagnostics['exact_caption_count'] += bool(exact)
                if has_caption:
                    captions.append(title.value.strip())
                    if visible:
                        visible_captions.append(title.value.strip())
            # Mode discovery also includes a hidden main GUI as a tray fallback.
            # Its generic caption is not a second visible playback window.
            return unchanged_song_caption(visible_captions or captions, previous)
        except (OSError, AttributeError, TypeError, ValueError):
            return False

    @staticmethod
    def _validated(song, row):
        if (not re.fullmatch(r'[1-9][0-9]{0,9}', song.identifier) or int(song.identifier) > 4294967295
                or row.get('type') != 0 or str(row.get('id')) != song.identifier):
            raise ValueError('unexpected song')
        observed = SimpleNamespace(title=row.get('title') or row['name'],
            artist=' / '.join(s['name'] for s in row['singer']), album=row['album']['name'])
        if (not candidate_matches(song, observed) or not isinstance(row.get('file'), dict)
                or not row['file'] or not row.get('mid') or type(row.get('interval')) is not int
                or row['interval'] <= 0 or playback_rights(row) is not True):
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
        """校验后仅发送剩余歌曲 ID，让已登录客户端解析完整曲目及会员权益。"""
        executable = Path(executable)
        if executable.name.casefold() != 'qqmusic.exe' or not executable.is_file():
            raise OSError('请在设置中选择真实的 QQMusic.exe。')
        if not 1 <= len(entries) <= 200 or type(index) is not int or not 0 <= index < len(entries):
            raise ValueError('QQ 临时歌单位置无效。')
        self.prepare(entries)
        if self.cancelled():
            raise OSError('音乐操作已取消。')
        remaining = entries[index:]
        ids = '|'.join(song.identifier for song in remaining)
        types = '|'.join('0' for _ in remaining)
        query = ('cmd_count==1&&cmd_0==playsong&&id_0==' + ids +
                 '&&songtype_0==' + types + '&&info_0==&&quality_0==quality')
        self.running_check(executable)
        self.popen([str(executable), '/playbysongid', query], shell=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            cwd=str(executable.parent))


def unchanged_song_caption(captions, previous):
    """只有唯一明确的同歌同歌手标题支持补发，不用通用“QQ音乐”标题证明未起播。"""
    return (len(captions) == 1 and bool(previous.title) and bool(previous.artist)
            and canonical(captions[0]) == canonical(previous.title + ' - ' + previous.artist))
