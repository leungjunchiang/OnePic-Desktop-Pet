"""在进程内维护六毛的长期摘要与最近三十轮完整聊天。

本模块只接收用户与六毛的聊天文本，不读取项目、文件、窗口标题或开发上下文。
最近三十轮（最多六十条消息）保持原文；更早内容滚动压缩为有长度上限的摘要，
供所有可选 AI 提供方复用。记忆仅存在于当前进程内，不写入磁盘或服务器。
"""

from __future__ import annotations

from dataclasses import dataclass


MAX_RECENT_ROUNDS = 30
MAX_RECENT_MESSAGES = MAX_RECENT_ROUNDS * 2
MAX_SUMMARY_CHARS = 1800


@dataclass(frozen=True)
class ConversationSnapshot:
    """一次 AI 请求可见的长期摘要和最近原始消息。"""

    summary: str
    recent: tuple[tuple[str, str], ...]

    def as_history(self) -> list[tuple[str, str]]:
        """转换为现有 AI 服务兼容的角色/文本列表。"""

        history: list[tuple[str, str]] = []
        if self.summary:
            history.append(("summary", self.summary))
        history.extend(self.recent)
        return history


class ConversationMemory:
    """滚动保留三十轮原文，并以简短片段更新更早对话摘要。"""

    def __init__(self, max_recent_rounds: int = MAX_RECENT_ROUNDS) -> None:
        self.max_recent_messages = max(2, int(max_recent_rounds) * 2)
        self._recent: list[tuple[str, str]] = []
        self._summary_items: list[str] = []

    @property
    def summary(self) -> str:
        """返回带稳定标题的有界长期摘要。"""

        if not self._summary_items:
            return ""
        return "；".join(self._summary_items)[:MAX_SUMMARY_CHARS]

    @property
    def recent(self) -> tuple[tuple[str, str], ...]:
        """返回最近三十轮以内的完整原始消息。"""

        return tuple(self._recent)

    def snapshot(self) -> ConversationSnapshot:
        """生成不会随之后写入变化的请求快照。"""

        return ConversationSnapshot(self.summary, self.recent)

    def add(self, role: str, content: str) -> None:
        """加入一条聊天；超过上限时只压缩最早的完整对话轮。"""

        if role not in {"user", "assistant"}:
            return
        clean = str(content).strip()
        if not clean:
            return
        self._recent.append((role, clean))
        while len(self._recent) > self.max_recent_messages:
            self._compress_oldest_round()

    def _compress_oldest_round(self) -> None:
        """移出最早的一问一答，并提炼人物、状态、偏好、话题与约定。"""

        if not self._recent:
            return
        batch = [self._recent.pop(0)]
        if batch[0][0] == "user" and self._recent and self._recent[0][0] == "assistant":
            batch.append(self._recent.pop(0))
        for role, content in batch:
            item = self._summary_item(role, content)
            if item and item not in self._summary_items:
                self._summary_items.append(item)
        while len("；".join(self._summary_items)) > MAX_SUMMARY_CHARS and len(self._summary_items) > 1:
            self._summary_items.pop(0)

    @staticmethod
    def _summary_item(role: str, content: str) -> str:
        """保留可支持连续陪伴的事实，避免复制冗长原话。"""

        text = content[:180]
        important_markers = (
            "我叫", "叫我", "我的", "朋友", "家人", "同事", "喜欢", "不喜欢",
            "希望", "以后", "记得", "约定", "习惯", "最近", "今天", "正在",
            "难过", "焦虑", "开心", "累", "压力", "目标", "计划", "决定",
        )
        if role == "user":
            prefix = "用户曾说"
            if any(marker in text for marker in important_markers):
                return f"{prefix}：{text}"
            return f"较早话题：{text[:100]}"
        agreement_markers = ("我会", "我们", "下次", "记住", "陪你", "可以先", "约定")
        if any(marker in text for marker in agreement_markers):
            return f"六毛曾回应：{text[:120]}"
        return ""
