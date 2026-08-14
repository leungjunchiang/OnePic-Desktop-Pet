�r�^�f��ئ{��y�'vî���"""维护六毛的长期摘要与最近三十轮完整聊天。

本模块只接收用户与六毛的聊天文本，不读取项目、文件、窗口标题或开发上下文。
最近三十轮（最多六十条消息）保持原文；更早内容滚动压缩为有长度上限的摘要，
供所有可选 AI 提供方复用。窗口实例可以把同样的有界内容落盘到本机，
不会上传到自习室服务；API 令牌、项目文件和窗口内容不在这里保存。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


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

    def __init__(
        self,
        max_recent_rounds: int = MAX_RECENT_ROUNDS,
        persist_path: Path | None = None,
    ) -> None:
        self.max_recent_messages = max(2, int(max_recent_rounds) * 2)
        self._recent: list[tuple[str, str]] = []
        self._summary_items: list[str] = []
        self.persist_path = Path(persist_path) if persist_path is not None else None
        if self.persist_path is not None:
            self.load()

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
        self.save()

    def load(self) -> None:
        """从本机恢复有界记忆；损坏或旧格式只会被忽略。"""

        if self.persist_path is None:
            return
        try:
            payload = json.loads(self.persist_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 1:
                return
            recent = payload.get("recent") or []
            summary = payload.get("summary") or []
            if isinstance(recent, list):
                self._recent = [
                    (str(item[0]), str(item[1]))
                    for item in recent
                    if isinstance(item, list)
                    and len(item) == 2
                    and item[0] in {"user", "assistant"}
                    and str(item[1]).strip()
                ][-self.max_recent_messages :]
            if isinstance(summary, list):
                self._summary_items = [str(item)[:180] for item in summary if str(item).strip()]
                while len(self.summary) > MAX_SUMMARY_CHARS and len(self._summary_items) > 1:
                    self._summary_items.pop(0)
        except (OSError, ValueError, TypeError, KeyError, IndexError):
            self._recent = []
            self._summary_items = []

    def save(self) -> None:
        """原子保存摘要和最近消息，不向任何网络服务发送。"""

        if self.persist_path is None:
            return
        payload = {
            "version": 1,
            "summary": self._summary_items,
            "recent": [[role, content] for role, content in self._recent],
        }
        temporary = self.persist_path.with_suffix(".json.tmp")
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.persist_path)
        except OSError:
            # Memory persistence is best effort and must never block chat.
            return

    def clear(self) -> None:
        """清除本机聊天记忆。"""

        self._recent.clear()
        self._summary_items.clear()
        if self.persist_path is not None:
            try:
                self.persist_path.unlink(missing_ok=True)
            except OSError:
                pass

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


def conversation_memory_path() -> Path:
    """返回六毛本机聊天记忆路径；不使用项目目录或云端存储。"""

    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".desktop_pet"
    return root / "Lili" / "conversation-memory.json"
