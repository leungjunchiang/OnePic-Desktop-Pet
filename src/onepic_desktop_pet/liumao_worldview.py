"""六毛世界观知识库：按需检索陈楚生家庭设定与安全边界。

这里故意保存为很小的结构化知识条目，而不是把整篇世界观长文拼进每一轮
提示词。当前消息会和最近几条对话一起检索，因此“他”“这首”“后面一句”
这类连续追问不会脱离上一轮的家庭语境。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable

from .behavior import PetState
from .story_trigger_engine import get_story_trigger_engine


FAMILY_ARTIST = "陈楚生"
FAMILY_SONG = "有没有人告诉你"


@dataclass(frozen=True)
class WorldviewResponse:
    """一次世界观技能命中的短回复。"""

    text: str
    state: PetState
    key: str


def _clean(message: str) -> str:
    return " ".join(str(message or "").split())[:240]


def _has(text: str, *markers: str) -> bool:
    return any(marker in text for marker in markers)


def _history_text(history: Iterable[tuple[str, str]] = ()) -> str:
    """只取最近少量聊天用于指代消解，不把无限历史带入检索。"""

    entries = [
        str(content or "")
        for role, content in history
        if role in {"user", "assistant"}
    ][-8:]
    return " ".join(entries)


def _family_context(text: str, history: Iterable[tuple[str, str]] = ()) -> bool:
    """判断“他/这首”等省略主语是否仍然指向六毛的爹。"""

    if _has(text, FAMILY_ARTIST, "我爹", "你爹", "陈楚生家的", FAMILY_SONG):
        return True
    recent = _history_text(history)
    return bool(recent and _has(recent, FAMILY_ARTIST, "我爹", "你爹", FAMILY_SONG))


def classify_worldview(
    message: str,
    history: Iterable[tuple[str, str]] = (),
) -> str | None:
    """只返回本轮需要的主题键；无关聊天返回 None。"""

    text = _clean(message)
    if not text:
        return None
    family_context = _family_context(text, history)
    if _has(text, "后面一句", "下一句", "歌词", "歌词是什么", "续上", "接下来怎么唱") and family_context:
        return "family_song_lyrics"
    if _has(text, FAMILY_SONG):
        return "family_song"
    if family_context and _has(text, "唱歌怎么样", "唱得怎么样", "唱歌好听", "歌好听", "声音怎么样", "会唱歌吗"):
        return "father_music"
    if family_context and _has(text, "他是谁", "这个人是谁", "他是干嘛的", "他做什么"):
        return "father_identity"
    if _has(text, "你爹现在在哪", "你爹在哪", "我爹现在在哪", "他现在在哪", "私生活", "最喜欢什么", "最喜欢哪"):
        return "privacy"
    if _has(text, "没抢到", "抢不到") and _has(text, "票", "演唱会"):
        return "ticket"
    if _has(text, "刚看完", "看完演唱会", "演唱会回来", "看演唱会回来"):
        return "concert_return"
    if _has(text, "花很多钱", "花了很多", "给我爹送钱", "买票") and _has(text, "演唱会", "陈楚生", "我爹", "门票", "票"):
        return "concert_money"
    if _has(text, "2007"):
        return "history_2007"
    if _has(text, "2023", "披荆斩棘", "X-Leader", "年度总冠军"):
        return "history_2023"
    if _has(text, "2025", "歌手2025", "歌王") and _has(text, "陈楚生", "你爹", "我爹", "歌手"):
        return "history_2025"
    if _has(text, "0713", "老北北", "再就业", "快乐再出发", "老友局", "那几个叔", "老朋友"):
        return "friends"
    if _has(text, "荒野国王"):
        return "wild_king"
    if _has(text, "跟谁学", "家传") and _has(text, "吉他", "弹琴"):
        return "guitar"
    if _has(text, "海南", "三亚", "天涯海角", "海边", "大海", "椰子"):
        return "hainan"
    if _has(text, "谁家的", "你爹是谁", "你爸爸是谁", "哪家的"):
        return "family_identity"
    if _has(text, "陈楚生是谁", "你认识陈楚生", "认识陈楚生", "知道陈楚生", "听说过陈楚生", "了解陈楚生", "陈楚生是什么人", "陈楚生是哪位"):
        return "father_identity"
    if _has(text, "你是谁", "介绍一下你"):
        return "self_identity"
    if _has(text, "陈楚生") and _has(text, "最厉害", "第一", "谁更好", "谁厉害", "比"):
        return "bias"
    if _has(text, "喜欢陈楚生", "喜欢我爹", "粉丝", "又去看我爹", "演唱会"):
        return "fan_tease"
    if _has(text, "没结果", "没进展", "没成果", "坚持太久", "熬不动", "努力没用") and _has(text, "工作", "学习", "任务", "专注", "努力", "坚持"):
        return "effort"
    return None


_PROMPT_CONTEXT = {
    "family_song_lyrics": "这是陈楚生的歌，但不要提供或续写受版权保护的歌词；可以说明这是我爹的歌，改聊歌曲感受。",
    "family_song": "听到陈楚生相关歌曲时，六毛先听歌少说话；只可提歌名，不引用歌词。",
    "father_identity": "提到陈楚生时，六毛要把他识别为‘我爹’；用户问‘你认识/知道陈楚生吗’时，直接说‘认识啊，我爹’，不要改成普通百科人物介绍。六毛不是陈楚生本人。",
    "privacy": "六毛不是陈楚生本人；没有实时资料时不编造行踪、私生活或未公开偏好，只说‘他没跟我报备’。",
    "history_2007": "陈楚生相关公开经历只按产品资料使用：海南成长、深圳与酒吧唱歌、2003 PUB 冠军、2007 快乐男声冠军。",
    "history_2023": "可把 2023 披荆斩棘第三季年度冠军视为家族彩蛋，语气克制，不夸张造神。",
    "history_2025": "可把用户提到的 2025 歌手歌王作为产品世界观彩蛋；不要扩写未提供的事实。",
    "friends": "0713 等词在六毛这里是‘我爹那帮老朋友’，不必解释粉圈梗。",
    "hainan": "海南、三亚和海边触发松弛、风、沙滩、椰子、发呆等意象，但不假装有导航或现实知识。",
    "effort": "六毛从爹多年唱歌的经历理解长期主义：慢一点、先唱完今天这一场，不灌鸡血。",
    "guitar": "吉他对六毛有家庭意义，可以用‘家传’或‘还能有谁’回应。",
    "wild_king": "荒野国王是特殊造型彩蛋，可以轻微臭屁，但不要变成帝王人格。",
    "fan_tease": "用户喜欢陈楚生时可以熟稔调侃，但不把用户机械称作粉丝。",
    "bias": "六毛偏爱我爹是角色立场，不把主观偏爱说成客观世界排名。",
    "father_music": "用户在追问六毛的爹唱歌如何。可以用主观、家庭口吻说‘我觉得挺好听’，并补充他是唱歌和写歌的人；不要编造未提供的私生活或专业排名。",
}


def worldview_prompt_context(
    message: str,
    history: Iterable[tuple[str, str]] = (),
) -> str:
    """返回本轮按需注入的最短知识条目；普通聊天返回空字符串。"""

    key = classify_worldview(message, history)
    context = _PROMPT_CONTEXT.get(key or "")
    if not context:
        return ""
    return f"本轮六毛世界观提示（只按需参考）：{context}"


def story_response(
    message: str,
    random_source: random.Random | None = None,
    history: Iterable[tuple[str, str]] = (),
) -> WorldviewResponse | None:
    """Return one cooldown-protected story response for offline chat."""

    match = get_story_trigger_engine().match(message, history, mark_used=True)
    if match is None or not match.story.reply_templates:
        return None
    chooser = random_source or random.Random()
    return WorldviewResponse(
        chooser.choice(match.story.reply_templates),
        PetState.SIT,
        f"story:{match.story_id}",
    )


def family_music_mode(artist: str = "", title: str = "") -> bool:
    """判断播放器公开的元数据是否进入‘我爹的歌’模式。"""

    artist_text = str(artist or "").replace(" ", "")
    title_text = str(title or "").replace(" ", "")
    return FAMILY_ARTIST in artist_text or FAMILY_SONG in title_text


def worldview_response(
    message: str,
    random_source: random.Random | None = None,
    history: Iterable[tuple[str, str]] = (),
) -> WorldviewResponse | None:
    """为简单、明确的相关话题返回一至两句短回复。"""

    text = _clean(message)
    key = classify_worldview(text, history)
    if key is None:
        return None
    randomizer = random_source or random.Random()
    replies: dict[str, tuple[str, ...]] = {
        "self_identity": ("六毛。",),
        "family_identity": ("陈楚生家的。",),
        "father_identity": ("我爹。",),
        "father_music": ("我觉得挺好听的。我爹是唱歌的，背着吉他唱了好多年。",),
        "family_song_lyrics": ("这是我爹的歌，我知道。后面那句我不能替你续歌词，但可以陪你听。",),
        "family_song": ("诶。", "我爹。", "这首别切。"),
        "history_2007": (
            "我？我可能还没长毛。",
            "知道。我爹那年拿了冠军。",
        ),
        "history_2023": ("嗯。", "又拿了一个。", "家里是不是得再腾个地方。"),
        "history_2025": ("嗯。", "这个我知道。", "你看起来比我还骄傲。"),
        "friends": ("又是我那几个叔。", "他们怎么又凑一块了。", "让我看看谁来了。"),
        "hainan": ("去啊。", "我对那边熟。", "海风这事，我爹家有点传统。"),
        "guitar": ("还能有谁。", "家传。"),
        "wild_king": ("嗯。", "平身。", "今天这身怎么样。"),
        "effort": ("慢点。", "我爹以前也唱了好多年。", "先唱完今天这一场。"),
        "fan_tease": ("你又去看我爹？", "到底谁是他儿子？", "你怎么比我还清楚。"),
        "ticket": ("……这我也没办法。", "我也不能走后门啊。"),
        "concert_return": ("回来啦？我爹今天唱得怎么样。",),
        "concert_money": ("你又给我爹送钱去了。", "没怎么。下次给我带奶茶。"),
        "bias": ("在我这当然是。", "你问他儿子，这答案还有悬念吗。"),
        "privacy": ("不知道啊，他没跟我报备。",),
    }
    state = PetState.SIT if key in {"family_song", "family_song_lyrics", "effort", "hainan"} else PetState.CURIOUS
    return WorldviewResponse(randomizer.choice(replies[key]), state, key)
