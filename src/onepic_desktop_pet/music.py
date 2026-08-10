"""提供陈楚生歌曲标题随机选择和正版音乐平台搜索入口。

本模块不内置歌词、音频或非公开接口，也不尝试绕过平台播放规则。用户主动点击后，程序只会
打开网易云音乐或 QQ 音乐的官方搜索页，由用户在已安装客户端或浏览器中确认播放。
"""

from __future__ import annotations

import random
import urllib.parse


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


def choose_song(random_source: random.Random | None = None) -> str:
    """随机返回一个歌曲标题，不包含歌词内容。"""

    return (random_source or random).choice(CHEN_CHUSHENG_SONGS)


def music_search_url(service: str, title: str) -> str:
    """构造网易云音乐或 QQ 音乐的官方搜索网址。"""

    query = urllib.parse.quote(f"陈楚生 {title}")
    if service == "qq":
        return f"https://y.qq.com/n/ryqq/search?w={query}&t=song"
    return f"https://music.163.com/#/search/m/?s={query}&type=1"
