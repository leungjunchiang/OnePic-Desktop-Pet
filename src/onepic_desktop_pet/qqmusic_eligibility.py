"""识别随机歌单的现场版本与 QQ 公开播放授权；会员/付费授权不等于无版权。

对应官方网页歌曲归一化逻辑：switch 第 1、2、3、17 位为完整播放权限，
pay_play/payplay 或 pay_down/paydownload 为另行付费授权。缺少字段返回未知，
不把未知冒充已授权；不获取账户、凭据、音频地址或绕过客户端授权。
"""
from __future__ import annotations

import re
import unicodedata


def is_live_song(song):
    """识别现场、晚会、首唱及节目分期元数据，避免 Alive 等普通单词误判。"""
    native = song.native or {}
    fields = [song.title, native.get('album', '')]
    track = native.get('track_info')
    if isinstance(track, dict):
        fields.extend(track.get(key, '') for key in ('title', 'subtitle'))
        album = track.get('album')
        if isinstance(album, dict):
            fields.extend(album.get(key, '') for key in ('name', 'title', 'subtitle'))
    return any(re.search(r'(?<![a-z])(?:live(?:house)?|concert)(?![a-z])|'
                         r'现场|現場|演唱会|演唱會|音乐会|音樂會|巡演|首唱|晚会|晚會|义演|義演|'
                         r'第[0-9一二三四五六七八九十百]+期', unicodedata.normalize('NFKC', text), re.I)
               for text in fields if isinstance(text, str))


def playback_rights(row):
    """True 为公开字段有播放或付费授权，False 为明确无授权，None 为无法确认。"""
    if not isinstance(row, dict):
        return None
    if row.get('disabled') is True:
        return False
    action = row.get('action')
    action = action if isinstance(action, dict) else {}
    pay = row.get('pay')
    pay = pay if isinstance(pay, dict) else {}
    # Subscription / purchase rights remain eligible even with no free-play bit.
    def positive(value):
        return (isinstance(value, int) and value > 0
                or isinstance(value, str) and bool(re.fullmatch(r'[0-9]{1,10}', value)) and int(value) > 0)
    if any(positive(pay.get(key)) for key in ('pay_play', 'payplay', 'pay_down', 'paydownload')):
        return True
    if action.get('play') in (1, '1'):
        return True
    bits = action.get('switch', action.get('switches', row.get('switch')))
    if isinstance(bits, str) and re.fullmatch(r'[0-9]{1,10}', bits):
        bits = int(bits)
    if type(bits) is not int or not 0 <= bits <= 4294967295:
        return None
    return bool(bits & ((1 << 1) | (1 << 2) | (1 << 3) | (1 << 17)))


def random_song_eligible(song):
    """随机仅收有专辑且明确获授权的非现场版本；会员曲不另设权重或排除。"""
    if is_live_song(song):
        return False
    native = song.native or {}
    row = native.get('track_info')
    album = native.get('album')
    if not album and isinstance(row, dict) and isinstance(row.get('album'), dict):
        album = row['album'].get('name')
    if not isinstance(album, str) or not album.strip():
        return False
    return (playback_rights(row) is True if isinstance(row, dict)
            else native.get('playback_rights') is True)
