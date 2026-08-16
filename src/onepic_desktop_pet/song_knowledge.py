"""本地歌曲作品理解层。

公开资源只保存歌名、主题、情绪和六毛使用建议，不保存歌词正文。用户可以在
设置中选择自己有权使用的歌词 TXT；运行时只在本机建立短语索引，识别出歌曲后
把作品卡交给 AI，不把原文上传、写入提示词或自动续写。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from .resources import resource_path


_SEPARATOR = re.compile(r"\n\s*[-—─]{10,}\s*\n")
_MAX_LOCAL_BYTES = 2 * 1024 * 1024
_LYRIC_MATCH_MIN_CHARS = 8
_SONG_CONTEXT_MARKERS = (
    "《",
    "》",
    "歌曲",
    "歌名",
    "谁唱",
    "播放",
    "放一下",
    "听听",
    "这首",
    "那首",
    "专辑",
    "代表作",
    "陈楚生",
)


@dataclass(frozen=True)
class SongCard:
    title: str
    artist: str
    aliases: tuple[str, ...]
    themes: tuple[str, ...]
    emotions: tuple[str, ...]
    imagery: tuple[str, ...]
    liumao_usage: tuple[str, ...]
    action_hints: tuple[str, ...]
    trigger_tags: tuple[str, ...]
    summary: str

    @classmethod
    def from_mapping(cls, value: object) -> "SongCard | None":
        if not isinstance(value, dict):
            return None
        title = str(value.get("title") or value.get("song") or "").strip()
        if not title:
            return None
        return cls(
            title=title,
            artist=str(value.get("artist") or "陈楚生").strip() or "陈楚生",
            aliases=_strings(value.get("aliases")) or (title,),
            themes=_strings(value.get("themes")),
            emotions=_strings(value.get("emotions", value.get("emotion"))),
            imagery=_strings(value.get("imagery")),
            liumao_usage=_strings(value.get("liumao_usage")),
            action_hints=_strings(value.get("action_hints")),
            trigger_tags=_strings(value.get("trigger_tags")),
            summary=str(value.get("summary") or "").strip(),
        )

    def to_mapping(self) -> dict[str, object]:
        """返回不含歌词正文的可公开作品卡。"""

        return {
            "title": self.title,
            "artist": self.artist,
            "aliases": list(self.aliases),
            "themes": list(self.themes),
            "emotions": list(self.emotions),
            "imagery": list(self.imagery),
            "liumao_usage": list(self.liumao_usage),
            "action_hints": list(self.action_hints),
            "trigger_tags": list(self.trigger_tags),
            "summary": self.summary,
        }


@dataclass(frozen=True)
class SongMatch:
    card: SongCard
    match_type: str
    confidence: float


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _normalize(value: str) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").casefold(), flags=re.UNICODE)


def _read_text(path: Path) -> str:
    if not path.is_file() or path.stat().st_size > _MAX_LOCAL_BYTES:
        return ""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def parse_local_catalog(path: str | Path) -> tuple[tuple[str, str, str], ...]:
    """Parse a private catalog into ``(title, artist, body)`` in memory only."""

    source = Path(path).expanduser()
    text = _read_text(source)
    if not text:
        return ()
    entries: list[tuple[str, str, str]] = []
    for block in _SEPARATOR.split(text):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        title = lines[0]
        artist = lines[1] if len(lines) > 1 and lines[1] else "陈楚生"
        body_start = 2 if len(lines) > 1 and lines[1] == artist else 1
        entries.append((title, artist, "\n".join(lines[body_start:])))
    return tuple(entries)


_TAG_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("远行与出发", ("离开", "远方", "行走", "旅馆", "旅", "出发", "城市", "路")),
    ("思念与等待", ("想你", "思念", "想念", "等", "回头", "不远", "好久不见", "再见")),
    ("关系与爱", ("爱", "爱情", "拥抱", "温暖", "女孩", "姑娘", "朋友")),
    ("孤独与独处", ("孤单", "一个人", "离群", "无话", "荒岛", "荒废", "暗夜")),
    ("成长与生活", ("人生", "明天", "生活", "青春", "时代", "以后", "岁", "原来")),
    ("自然与海风", ("海", "风", "雨", "花", "山", "森林", "鱼", "鹿", "月亮")),
    ("自由与旷野", ("荒野", "自由", "天空", "天外", "行走")),
)

_EMOTION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("克制", ("不说", "无话", "沉默", "藏", "不再", "懂")),
    ("怀念", ("想", "思念", "回头", "回忆", "再见", "好久")),
    ("孤独", ("一个人", "孤单", "离群", "荒岛", "暗夜")),
    ("温柔", ("温暖", "拥抱", "亲爱的", "花", "月亮")),
    ("明亮", ("光", "天亮", "青春", "快乐", "美好")),
    ("旷达", ("荒野", "远行", "天空", "海", "行走")),
)


_CURATED: dict[str, dict[str, object]] = {
    "有没有人告诉你": {
        "themes": ["离乡与城市", "联系与思念"],
        "emotions": ["克制", "孤独", "怀念"],
        "imagery": ["陌生城市", "夜晚", "远方", "失联与联系"],
        "liumao_usage": ["用户独自在外地时", "深夜工作或想家时"],
        "action_hints": ["戴耳机", "安静听歌"],
        "summary": "围绕离开熟悉环境后的孤独、思念，以及人与人之间想要保持联系的感觉展开。",
    },
    "山楂花": {
        "themes": ["青春", "回忆与故乡"],
        "emotions": ["温柔", "怀念"],
        "imagery": ["花", "季节", "旧日时光"],
        "liumao_usage": ["用户聊到青春或旧地方时"],
        "action_hints": ["抱吉他", "安静听歌"],
    },
    "荒废光年": {
        "themes": ["时间", "错过与远方"],
        "emotions": ["克制", "孤独", "怀念"],
        "imagery": ["光年", "时间", "远方"],
        "liumao_usage": ["用户谈到错过或很久没有结果时"],
        "action_hints": ["坐在桌边发呆"],
    },
    "荒野国王": {
        "themes": ["自由", "旷野"],
        "emotions": ["旷达", "明亮"],
        "imagery": ["荒野", "风", "远方"],
        "liumao_usage": ["用户想出门走走或切换荒野造型时"],
        "action_hints": ["切换荒野国王造型", "跟着节奏晃"],
    },
    "获奖之作": {
        "themes": ["完成与庆祝", "舞台感"],
        "emotions": ["明亮", "旷达"],
        "imagery": ["舞台", "奖杯", "收尾"],
        "liumao_usage": ["用户完成重要任务时低频玩梗"],
        "action_hints": ["小型庆祝动作"],
    },
    "鹿回头": {
        "themes": ["海边", "回望"],
        "emotions": ["温柔", "怀念"],
        "imagery": ["海风", "三亚", "回头"],
        "liumao_usage": ["用户提到海南、三亚或海边时"],
        "action_hints": ["看海", "坐着发呆"],
    },
    "向海而生": {
        "themes": ["海边", "继续生活"],
        "emotions": ["旷达", "明亮"],
        "imagery": ["海", "风", "出发"],
        "liumao_usage": ["用户需要放松或想换个环境时"],
        "action_hints": ["看海", "吹风"],
    },
    "西涌客栈": {
        "themes": ["海边", "夜晚与停留"],
        "emotions": ["松弛", "孤独"],
        "imagery": ["海边客栈", "夜晚", "远方"],
        "liumao_usage": ["深夜工作太久需要收尾时"],
        "action_hints": ["坐下休息", "戴耳机"],
    },
}


def make_public_card(title: str, artist: str, body: str = "") -> SongCard:
    """从本地歌词材料抽取标签；绝不把正文写入结果。"""

    curated = _CURATED.get(title, {})
    corpus = f"{title}\n{body}"
    themes = list(_strings(curated.get("themes")))
    emotions = list(_strings(curated.get("emotions")))
    for label, terms in _TAG_RULES:
        if any(term in corpus for term in terms) and label not in themes:
            themes.append(label)
    for label, terms in _EMOTION_RULES:
        if any(term in corpus for term in terms) and label not in emotions:
            emotions.append(label)
    themes = themes[:4] or ["歌曲与情绪"]
    emotions = emotions[:4] or ["克制"]
    imagery = list(_strings(curated.get("imagery")))
    if not imagery:
        imagery = [label.replace("与", "、") for label, _terms in _TAG_RULES if label in themes][:3]
    usage = list(_strings(curated.get("liumao_usage"))) or ["用户明确聊到这首歌或相近情绪时"]
    actions = list(_strings(curated.get("action_hints"))) or ["安静听歌", "陪用户工作"]
    summary = str(curated.get("summary") or "").strip()
    if not summary:
        summary = f"这首作品可从{themes[0]}和{emotions[0]}的方向理解；这里只保留作品标签与情绪理解。"
    trigger_tags = [title, *themes, *emotions, *imagery]
    return SongCard(
        title=title,
        artist=artist or "陈楚生",
        aliases=(title,),
        themes=tuple(dict.fromkeys(themes)),
        emotions=tuple(dict.fromkeys(emotions)),
        imagery=tuple(dict.fromkeys(imagery)),
        liumao_usage=tuple(dict.fromkeys(usage)),
        action_hints=tuple(dict.fromkeys(actions)),
        trigger_tags=tuple(dict.fromkeys(trigger_tags)),
        summary=summary,
    )


def discover_lyrics_path(configured: str = "") -> Path | None:
    """Return an explicitly selected file or the user's conventional Desktop file."""

    candidates = []
    if str(configured or "").strip():
        candidates.append(Path(str(configured).strip()).expanduser())
    candidates.append(Path.home() / "Desktop" / "陈楚生歌词.txt")
    for candidate in candidates:
        try:
            if candidate.is_file() and candidate.stat().st_size <= _MAX_LOCAL_BYTES:
                return candidate
        except OSError:
            continue
    return None


@lru_cache(maxsize=2)
def _load_public_cards(path: str) -> tuple[SongCard, ...]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return ()
    values = payload.get("songs", []) if isinstance(payload, dict) else payload
    cards = [SongCard.from_mapping(item) for item in values] if isinstance(values, list) else []
    return tuple(card for card in cards if card is not None)


def load_public_cards(resource_dir: Path | None = None) -> tuple[SongCard, ...]:
    directory = resource_dir or resource_path("resources")
    return _load_public_cards(str(directory / "chen_chusheng_song_cards.json"))


@lru_cache(maxsize=4)
def _local_entries(path: str, modified_ns: int) -> tuple[tuple[str, str, str], ...]:
    del modified_ns
    return parse_local_catalog(path)


def _entries_for(path: Path | None) -> tuple[tuple[str, str, str], ...]:
    if path is None:
        return ()
    try:
        return _local_entries(str(path), path.stat().st_mtime_ns)
    except OSError:
        return ()


def _quoted_candidates(message: str) -> tuple[str, ...]:
    return tuple(match.strip() for match in re.findall(r"[《「“\"']([^》」”\"']+)[》」”\"']", message) if match.strip())


def _explicit_song_context(message: str) -> bool:
    return any(marker in message for marker in _SONG_CONTEXT_MARKERS)


def _match_local_line(query: str, body: str) -> bool:
    query_normalized = _normalize(query)
    if len(query_normalized) < _LYRIC_MATCH_MIN_CHARS:
        return False
    for line in body.splitlines():
        line_normalized = _normalize(line)
        if len(line_normalized) < _LYRIC_MATCH_MIN_CHARS:
            continue
        if query_normalized in line_normalized or line_normalized in query_normalized:
            return True
    return False


def find_song_matches(
    message: str,
    history: Iterable[tuple[str, str]] = (),
    configured_path: str = "",
    *,
    limit: int = 3,
) -> tuple[SongMatch, ...]:
    """Find song titles or local lyric-fragment matches without returning lyric text."""

    text = str(message or "").strip()
    if not text:
        return ()
    cards = load_public_cards()
    by_title = {_normalize(card.title): card for card in cards}
    local_path = discover_lyrics_path(configured_path)
    local_entries = _entries_for(local_path)
    local_by_title = {
        _normalize(title): (title, artist, body)
        for title, artist, body in local_entries
    }
    explicit = _explicit_song_context(text)
    matches: list[SongMatch] = []
    candidates = (text, *_quoted_candidates(text))
    for card in cards:
        title_normalized = _normalize(card.title)
        title_hit = any(title_normalized and title_normalized in _normalize(candidate) for candidate in candidates)
        if title_hit and (explicit or "陈楚生" in text) and _normalize(text) != title_normalized:
            matches.append(SongMatch(card, "song_title", 0.98))
            continue
        local_entry = local_by_title.get(title_normalized)
        if local_entry and any(_match_local_line(candidate, local_entry[2]) for candidate in candidates):
            matches.append(SongMatch(card, "lyric_fragment", 0.91))
    if not matches:
        # Follow-up questions such as “那首歌是什么感觉” can use the last
        # explicit song mention, while a bare ambiguous title remains casual.
        for role, content in reversed(tuple(history)):
            if role not in {"user", "assistant"}:
                continue
            prior = find_song_matches(str(content), (), configured_path, limit=1)
            if prior and any(marker in text for marker in ("这首", "那首", "它", "后面")):
                matches.append(SongMatch(prior[0].card, "conversation_context", 0.86))
                break
    unique: dict[str, SongMatch] = {}
    for match in matches:
        unique.setdefault(match.card.title, match)
    return tuple(unique.values())[: max(1, limit)]


def song_prompt_context(
    message: str,
    history: Iterable[tuple[str, str]] = (),
    configured_path: str = "",
) -> str:
    matches = find_song_matches(message, history, configured_path)
    if not matches:
        return ""
    lines = [
        "本地歌曲作品卡（只用于理解，不要引用、展示或续写歌词正文）：",
        "本地歌词索引只在用户设备上匹配；模型不要复述匹配到的原句。",
    ]
    for match in matches:
        card = match.card
        lines.append(
            f"《{card.title}》/ {card.artist}；识别方式：{match.match_type}；"
            f"主题：{'、'.join(card.themes)}；情绪：{'、'.join(card.emotions)}；"
            f"意象：{'、'.join(card.imagery)}；作品理解：{card.summary}；"
            f"六毛使用：{'；'.join(card.liumao_usage)}。"
        )
    lines.append("事实优先；如果用户只是说半句普通话，不要因为歌名相似就强行认歌。")
    return "\n".join(lines)[:4200]


def offline_song_reply(
    message: str,
    history: Iterable[tuple[str, str]] = (),
    configured_path: str = "",
) -> str | None:
    """Offline fallback that identifies a song without exposing lyric text."""

    matches = find_song_matches(message, history, configured_path, limit=1)
    if not matches:
        return None
    match = matches[0]
    card = match.card
    if match.match_type == "lyric_fragment":
        return f"你说的这句像是《{card.title}》里的片段。我不把歌词整段搬出来，但知道你在说哪首；这首大概是{card.themes[0]}和{card.emotions[0]}的气质。"
    return f"这是《{card.title}》，陈楚生唱的。六毛记得它的气质是{card.themes[0]}、{card.emotions[0]}；歌词原文我不直接展开。"
