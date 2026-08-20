"""Small, deterministic intent routing for Lili chat.

The classifier is deliberately conservative.  A title that can also be a
normal sentence stays casual unless the user supplies clear song context.
It does not answer questions or rewrite facts; it only decides which local
knowledge and response budget are appropriate.
Offline message-type classification is kept separate from the existing
knowledge intent and never performs local writes.
Family-topic context is carried across turns only when the current message
contains an explicit anaphoric reference; unrelated questions start fresh.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


CASUAL_CHAT = "casual_chat"
FACTUAL_QA = "factual_qa"
CHEN_PROFILE = "chen_chusheng_profile"
SONG_QUERY = "song_query"
EMOTIONAL_SUPPORT = "emotional_support"
WORK_COMPANION = "work_companion"
RELATION_QUERY = "relation_query"

CASUAL_CHAT_MESSAGE = "CASUAL_CHAT"
FACTUAL_QUESTION = "FACTUAL_QUESTION"
MEMORY_RECALL = "MEMORY_RECALL"
TASK_COMMAND = "TASK_COMMAND"
TODO_COMMAND = "TODO_COMMAND"
TIMER_COMMAND = "TIMER_COMMAND"
EMOTIONAL_CHAT = "EMOTIONAL_CHAT"
UNKNOWN_MESSAGE = "UNKNOWN"


@dataclass(frozen=True)
class ChatIntent:
    primary_intent: str
    secondary_intent: str | None
    need_knowledge: bool
    knowledge_domains: tuple[str, ...]
    confidence: float
    retrieval_limit: int
    answer_style: str
    story_allowed: bool


_SONG_TITLES = (
    "有没有人告诉你", "姑娘", "趋光", "侦探C", "涂鸦森林", "获奖之作",
    "荒岛", "荒野国王", "鹿回头",
)
_SONG_CONTEXT = (
    "谁唱", "是谁唱", "播放", "放一下", "这首歌", "这首", "歌曲", "歌名",
    "代表作", "专辑", "听听", "听歌", "正在听", "唱的", "歌单",
)
_PROFILE_MARKERS = (
    "经历", "人生经历", "成长", "出道", "以前", "后来", "一路", "职业生涯",
    "讲讲", "介绍一下", "从哪里", "怎么走过来", "怎么出道",
)
_FACT_MARKERS = (
    "哪年", "什么时候", "出生", "冠军", "第几", "是什么", "什么意思",
    "哪位", "是谁", "多少人", "有哪些", "获得", "称号", "日期",
)
_RELATION_MARKERS = (
    "0713", "再就业", "快乐再出发", "蘑菇屋", "什么关系", "都有谁", "哪六个",
    "成员", "兄弟",
)
_EMOTION_MARKERS = (
    "累", "困", "压力", "烦", "崩溃", "没人看", "没结果", "没成果", "太晚",
    "年龄大", "想放弃", "学不下去", "来不及", "孤独", "想家", "难过",
    "想换城市", "重新开始", "离开这里", "去别的城市", "错过机会", "没机会了",
    "终于做完", "交稿了", "任务完成", "完成了", "做完了", "回家", "想三亚", "去三亚",
)
_WORK_MARKERS = (
    "工作", "论文", "任务", "代码", "学习", "专注", "交稿", "开工", "收工",
    "写不动", "开始干活", "工作了多久",
)
_FAMILY_MARKERS = ("陈楚生", "我爹", "你爹", "他的歌", "陈楚生家的")
_FAMILY_ANAPHORA = (
    "他的", "他是", "他在", "他有", "他会", "他怎么", "他为什么", "他是谁", "那他",
    "这个人", "那个人", "这位", "那位", "这首", "那首", "这张", "那张",
    "这件事", "那件事", "它是", "它的", "它在", "刚才", "上面", "前面",
)
_KNOWLEDGE_INTENTS = frozenset({SONG_QUERY, CHEN_PROFILE, FACTUAL_QA, RELATION_QUERY})


def _clean(value: str) -> str:
    return " ".join(str(value or "").split())[:600]


def _history_text(history: Iterable[tuple[str, str]]) -> str:
    return " ".join(
        str(content or "")
        for role, content in history
        if role in {"user", "assistant"}
    )[-1200:]


def _contains(text: str, markers: Iterable[str]) -> bool:
    return any(marker in text for marker in markers)


def _has_family_context(
    text: str,
    history: Iterable[tuple[str, str]] = (),
) -> bool:
    """Only carry the Chen/family topic across an explicit anaphoric follow-up.

    A previous mention of a song is not permission to reinterpret an unrelated
    question such as ``人生的意义是什么`` as a question about Chen Chusheng's
    biography.  Context is retained for phrases that actually refer back to a
    person, song, album, or event.
    """

    if _contains(text, _FAMILY_MARKERS):
        return True
    recent = _history_text(history)
    return bool(
        recent
        and _contains(recent, _FAMILY_MARKERS)
        and _contains(text, _FAMILY_ANAPHORA)
    )


def is_topic_shift(
    message: str,
    history: Iterable[tuple[str, str]] = (),
) -> bool:
    """Detect a clear switch away from a recent knowledge-heavy topic.

    This is only a prompt-boundary hint.  It does not discard local chat
    memory, execute actions, or create a second conversation router.
    """

    entries = tuple(history)
    if not entries:
        return False
    text = _clean(message)
    if _contains(text, _FAMILY_ANAPHORA) or _contains(text, ("还记得", "记得吗", "继续")):
        return False
    if classify_intent(text, entries).primary_intent != CASUAL_CHAT:
        return False
    for role, content in reversed(entries[-8:]):
        if role != "user":
            continue
        if classify_intent(str(content or ""), ()).primary_intent in _KNOWLEDGE_INTENTS:
            return True
    return False


def classify_offline_message(message: str) -> str:
    """Classify fallback traffic without creating a second Todo router."""

    text = _clean(message)
    if not text:
        return UNKNOWN_MESSAGE
    if re.search(r"^(?:开始工作|开工|开始计时|暂停(?:工作|计时)?|继续工作|恢复计时|结束工作|收工)", text):
        return TIMER_COMMAND
    if re.search(r"加到待办|加入待办|放到待办|放进待办|记到待办|创建待办|新增待办|添加待办|提醒我|设置提醒|帮我记", text):
        return TODO_COMMAND
    if re.search(r"还记得|记不记得|你.*记得|知不知道|你知道.*吗|记得什么|记得谁", text):
        return MEMORY_RECALL
    if _contains(text, ("好累", "很累", "不想干", "好烦", "崩溃", "难过", "想休息")):
        return EMOTIONAL_CHAT
    if _contains(text, ("你的状态", "你怎么样", "精力", "饱食度", "心情怎么样")):
        return CASUAL_CHAT_MESSAGE
    if re.search(r"(哪里|哪儿|是什么|什么意思|为什么|怎么|多少|哪位|哪一年|\?|？)", text):
        return FACTUAL_QUESTION
    if _contains(text, ("待办", "提醒")):
        return TODO_COMMAND
    if _contains(text, ("工作", "任务", "论文", "学习", "专注")):
        return CASUAL_CHAT_MESSAGE
    return CASUAL_CHAT_MESSAGE


def _has_explicit_song_context(text: str) -> bool:
    """Resolve title/sentence ambiguity before knowledge retrieval.

    The bare phrase “有没有人告诉你” is intentionally not a song query.
    """

    has_title = any(title in text for title in _SONG_TITLES)
    if not has_title:
        return _contains(text, ("陈楚生有哪些歌", "陈楚生的代表作", "歌单"))
    return _contains(text, _SONG_CONTEXT) or "陈楚生的" in text or "陈楚生《" in text


def classify_intent(
    message: str,
    history: Iterable[tuple[str, str]] = (),
) -> ChatIntent:
    text = _clean(message)
    entries = tuple(history)
    family_context = _has_family_context(text, entries)

    if _has_explicit_song_context(text):
        return ChatIntent(SONG_QUERY, None, True, ("songs", "music"), 0.96, 3, "short", False)

    if _contains(text, _RELATION_MARKERS) and (
        _contains(text, ("0713", "再就业", "蘑菇屋", "快乐再出发"))
        or _contains(text, ("关系", "成员", "兄弟", "都有谁", "哪六个"))
    ):
        return ChatIntent(RELATION_QUERY, None, True, ("relations", "timeline"), 0.94, 4, "medium", False)

    if family_context and _contains(text, _PROFILE_MARKERS):
        return ChatIntent(CHEN_PROFILE, None, True, ("profile", "timeline", "history"), 0.93, 8, "detailed", False)

    if family_context and _contains(text, _FACT_MARKERS):
        return ChatIntent(FACTUAL_QA, None, True, ("facts", "history", "songs", "relations"), 0.88, 3, "short", False)

    if _contains(text, _WORK_MARKERS):
        secondary = EMOTIONAL_SUPPORT if _contains(text, _EMOTION_MARKERS) else None
        return ChatIntent(WORK_COMPANION, secondary, False, (), 0.86, 0, "short", True)

    if _contains(text, _EMOTION_MARKERS):
        return ChatIntent(EMOTIONAL_SUPPORT, None, False, (), 0.84, 0, "short", True)

    return ChatIntent(CASUAL_CHAT, None, False, (), 0.78, 0, "short", False)


def intent_prompt_context(intent: ChatIntent) -> str:
    """Turn routing metadata into a short instruction for the model."""

    if intent.primary_intent == CHEN_PROFILE:
        style = "这是宽泛人物经历问题：按时间线组织 6-10 句，优先讲阶段转折，不要逐段复述资料。"
    elif intent.primary_intent == RELATION_QUERY:
        style = "这是人物关系问题：先纠正概念，再给名单或关系，控制在 2-4 句。"
    elif intent.primary_intent == SONG_QUERY:
        style = "这是明确歌曲问题：先回答歌名、歌手或播放意图，再轻轻角色化；不要只回答‘我爹’。"
    elif intent.primary_intent == FACTUAL_QA:
        style = "这是事实问题：先给事实答案，再最多加一句六毛口吻，不要用角色关系替代事实。"
    elif intent.primary_intent in {EMOTIONAL_SUPPORT, WORK_COMPANION}:
        style = "六毛首先是工作搭子；先处理用户眼前的一小步，除非故事引擎明确命中，不要提陈楚生。"
    else:
        style = "这是普通聊天；只有当前句明确出现‘他/这首/那张’等指代时才沿用上一话题，否则视为换题，直接回答当前问题。关键词只能作为候选线索，不要把它直接当成歌曲、事实或固定回复。"
    return (
        f"本轮意图：{intent.primary_intent}；置信度 {intent.confidence:.2f}。{style}"
        "事实优先，‘我爹’最多自然出现一次。"
    )

