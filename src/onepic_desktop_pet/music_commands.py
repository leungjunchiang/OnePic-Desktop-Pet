"""识别明确的音乐短句；换一首沿现有歌单前进，随机命令重新生成三十首。"""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class MusicRequest:
    action: str
    title: str = ''
    artist: str = '陈楚生'


def parse_music_request(message: str) -> MusicRequest | None:
    text = re.sub(r'^六毛[，,\s]*', '', message.strip()).strip(' ，,。.!！?？')
    text = re.sub(r'^(?:(?:请|帮我|给我)[，,\s]*)+', '', text)
    actions = {
        '随机听陈楚生': 'random', '随机播放陈楚生': 'random',
        '随机听一首陈楚生': 'random', '随机播放一首陈楚生': 'random',
        '放首陈楚生的歌': 'random', '来首陈楚生的歌': 'random',
        '换一首': 'next', '再来一首': 'next',
        '暂停音乐': 'pause', '暂停播放': 'pause',
        '继续音乐': 'play', '继续播放': 'play', '恢复播放': 'play',
        '下一首': 'next', '上一首': 'previous',
        '查看音乐状态': 'status', '现在在放什么歌': 'status',
    }
    if text in actions:
        return MusicRequest(actions[text])
    match = re.fullmatch(r'(?:播放|放一下|听一下)\s*(?:陈楚生的?\s*)?《([^《》\n]{1,100})》', text)
    if match and match.group(1).strip():
        return MusicRequest('song', match.group(1).strip())
    return None
