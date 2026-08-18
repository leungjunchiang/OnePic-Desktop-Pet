"""维护六毛的长期摘要与最近三十轮完整聊天。

本模块只接收用户与六毛的聊天文本，不读取项目、文件、窗口标题或开发上下文。
最近三十轮（最多六十条消息）保持原文；更早内容滚动压缩为有长度上限的摘要，
供所有可选 AI 提供方复用。窗口实例可以把同样的有界内容落盘到本机，
不会上传到自习室服务；API 令牌、项目文件和窗口内容不在这里保存。
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


MAX_RECENT_ROUNDS = 30
MAX_RECENT_MESSAGES = MAX_RECENT_ROUNDS * 2
MAX_SUMMARY_CHARS = 1800
MAX_CHAT_HISTORY_SESSIONS = 20
MAX_CHAT_HISTORY_MESSAGES = 120
MAX_CHAT_HISTORY_TEXT_CHARS = 4000


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


@dataclass(frozen=True)
class ChatHistorySession:
    """一段可在聊天记录中查看的本地会话。"""

    session_id: str
    created_at: str
    updated_at: str
    title: str
    messages: tuple[tuple[str, str], ...]


class ChatHistoryStore:
    """保存有限数量的本地聊天会话，不上传也不保存任何凭据。"""

    def __init__(
        self,
        persist_path: Path | None = None,
        *,
        max_sessions: int = MAX_CHAT_HISTORY_SESSIONS,
    ) -> None:
        self.persist_path = Path(persist_path) if persist_path is not None else None
        self.max_sessions = max(1, int(max_sessions))
        self._sessions: list[dict[str, object]] = []
        self._current_session_id = ""
        if self.persist_path is not None:
            self.load()

    @property
    def current_session_id(self) -> str:
        """返回当前会话 ID；尚未产生消息时可能为空。"""

        return self._current_session_id

    def current_messages(self) -> tuple[tuple[str, str], ...]:
        """返回当前会话的完整本地消息。"""

        session = self._find(self._current_session_id)
        return session.messages if session is not None else ()

    def sessions(self) -> tuple[ChatHistorySession, ...]:
        """按最近更新时间返回当前会话和历史会话。"""

        values = [self._as_session(item) for item in self._sessions]
        values.sort(key=lambda item: item.updated_at, reverse=True)
        return tuple(values)

    def get(self, session_id: str) -> ChatHistorySession | None:
        """读取指定会话，供历史记录窗口展示。"""

        item = self._find(str(session_id or ""))
        return item

    def append(self, role: str, content: str) -> None:
        """把一条用户或六毛消息写入当前会话。"""

        clean_role = str(role or "").strip().lower()
        if clean_role not in {"user", "assistant"}:
            return
        clean = str(content or "").strip()
        if not clean:
            return
        if not self._current_session_id:
            self._current_session_id = uuid.uuid4().hex
            now = self._now()
            self._sessions.append(
                {
                    "id": self._current_session_id,
                    "created_at": now,
                    "updated_at": now,
                    "title": "新对话",
                    "messages": [],
                }
            )
        item = self._find_raw(self._current_session_id)
        if item is None:
            self._current_session_id = ""
            self.append(clean_role, clean)
            return
        messages = item.setdefault("messages", [])
        if not isinstance(messages, list):
            messages = []
            item["messages"] = messages
        messages.append([clean_role, clean[:MAX_CHAT_HISTORY_TEXT_CHARS]])
        del messages[:-MAX_CHAT_HISTORY_MESSAGES]
        if item.get("title") in {None, "", "新对话"} and clean_role == "user":
            item["title"] = self._title_from(clean)
        item["updated_at"] = self._now()
        self._trim_sessions()
        self.save()

    def start_new_session(self) -> None:
        """归档当前会话，下一条消息会创建一个新的会话。"""

        if not self.current_messages():
            return
        self._current_session_id = ""
        self.save()

    def rename_session(self, session_id: str, title: str) -> bool:
        """修改一段本地会话的标题。"""

        item = self._find_raw(str(session_id or ""))
        clean_title = " ".join(str(title or "").split())[:80]
        if item is None or not clean_title:
            return False
        item["title"] = clean_title
        item["updated_at"] = self._now()
        self._trim_sessions()
        self.save()
        return True

    def delete_session(self, session_id: str) -> bool:
        """删除一段本地会话，并在删除当前会话时清空当前 ID。"""

        clean_id = str(session_id or "").strip()
        if self._find_raw(clean_id) is None:
            return False
        self._sessions = [
            item for item in self._sessions if str(item.get("id") or "") != clean_id
        ]
        if self._current_session_id == clean_id:
            self._current_session_id = ""
        self.save()
        return True

    def update_message(self, session_id: str, index: int, content: str) -> bool:
        """编辑一条本地消息；角色和会话上下文保持不变。"""

        item = self._find_raw(str(session_id or ""))
        clean = str(content or "").strip()[:MAX_CHAT_HISTORY_TEXT_CHARS]
        messages = item.get("messages") if item is not None else None
        if item is None or not clean or not isinstance(messages, list):
            return False
        if index < 0 or index >= len(messages):
            return False
        current = messages[index]
        if not isinstance(current, list) or len(current) != 2:
            return False
        role = str(current[0] or "").strip().lower()
        if role not in {"user", "assistant"}:
            return False
        messages[index] = [role, clean]
        item["updated_at"] = self._now()
        self._trim_sessions()
        self.save()
        return True

    def delete_message(self, session_id: str, index: int) -> bool:
        """删除一条本地消息；空会话会随之移除。"""

        clean_id = str(session_id or "")
        item = self._find_raw(clean_id)
        messages = item.get("messages") if item is not None else None
        if item is None or not isinstance(messages, list):
            return False
        if index < 0 or index >= len(messages):
            return False
        del messages[index]
        if not messages:
            self._sessions = [
                value
                for value in self._sessions
                if str(value.get("id") or "") != clean_id
            ]
            if self._current_session_id == clean_id:
                self._current_session_id = ""
        else:
            item["updated_at"] = self._now()
        self.save()
        return True

    def clear_all(self) -> None:
        """删除本机所有聊天记录；不触碰待办、提醒或其他数据。"""

        self._sessions.clear()
        self._current_session_id = ""
        if self.persist_path is not None:
            try:
                self.persist_path.unlink(missing_ok=True)
            except OSError:
                pass

    def bootstrap(self, messages: list[tuple[str, str]] | tuple[tuple[str, str], ...]) -> None:
        """从旧版本的有界记忆初始化一次历史记录，避免升级后旧聊天消失。"""

        if self._sessions:
            return
        for role, content in messages:
            self.append(role, content)

    def load(self) -> None:
        """读取本地会话；损坏或未知格式只会安全地忽略。"""

        if self.persist_path is None:
            return
        try:
            payload = json.loads(self.persist_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 1:
                return
            current_id = str(payload.get("current_session_id") or "").strip()
            raw_sessions = payload.get("sessions") or []
            if not isinstance(raw_sessions, list):
                return
            loaded: list[dict[str, object]] = []
            for raw in raw_sessions:
                if not isinstance(raw, dict):
                    continue
                session_id = str(raw.get("id") or "").strip()[:80]
                if not session_id:
                    continue
                messages = self._clean_messages(raw.get("messages"))
                if not messages:
                    continue
                created = str(raw.get("created_at") or self._now())[:40]
                updated = str(raw.get("updated_at") or created)[:40]
                title = str(raw.get("title") or "新对话").strip()[:80] or "新对话"
                loaded.append(
                    {
                        "id": session_id,
                        "created_at": created,
                        "updated_at": updated,
                        "title": title,
                        "messages": messages,
                    }
                )
            self._sessions = loaded
            self._current_session_id = (
                current_id if self._find_raw(current_id) is not None else ""
            )
            self._trim_sessions()
        except (OSError, ValueError, TypeError, KeyError, IndexError):
            self._sessions = []
            self._current_session_id = ""

    def save(self) -> None:
        """原子保存聊天会话，写入失败不能阻塞正常聊天。"""

        if self.persist_path is None:
            return
        payload = {
            "version": 1,
            "current_session_id": self._current_session_id,
            "sessions": self._sessions,
        }
        temporary = self.persist_path.with_suffix(".json.tmp")
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.persist_path)
        except OSError:
            return

    def _find_raw(self, session_id: str) -> dict[str, object] | None:
        for item in self._sessions:
            if str(item.get("id") or "") == session_id:
                return item
        return None

    def _find(self, session_id: str) -> ChatHistorySession | None:
        item = self._find_raw(session_id)
        return self._as_session(item) if item is not None else None

    @staticmethod
    def _as_session(item: dict[str, object]) -> ChatHistorySession:
        messages = ChatHistoryStore._clean_messages(item.get("messages"))
        return ChatHistorySession(
            session_id=str(item.get("id") or ""),
            created_at=str(item.get("created_at") or ""),
            updated_at=str(item.get("updated_at") or ""),
            title=str(item.get("title") or "新对话"),
            messages=tuple((str(role), str(text)) for role, text in messages),
        )

    @staticmethod
    def _clean_messages(value: object) -> list[list[str]]:
        if not isinstance(value, list):
            return []
        cleaned: list[list[str]] = []
        for item in value[-MAX_CHAT_HISTORY_MESSAGES:]:
            if not isinstance(item, list) or len(item) != 2:
                continue
            role = str(item[0] or "").strip().lower()
            text = str(item[1] or "").strip()
            if role in {"user", "assistant"} and text:
                cleaned.append([role, text[:MAX_CHAT_HISTORY_TEXT_CHARS]])
        return cleaned

    def _trim_sessions(self) -> None:
        self._sessions.sort(
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )
        self._sessions = self._sessions[: self.max_sessions]
        if self._current_session_id and self._find_raw(self._current_session_id) is None:
            self._current_session_id = ""

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _title_from(content: str) -> str:
        compact = " ".join(str(content).split())
        return compact[:32] + ("…" if len(compact) > 32 else "")


def conversation_history_path() -> Path:
    """返回本地聊天记录路径，与 AI 摘要分开保存。"""

    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".desktop_pet"
    return root / "Lili" / "chat-history.json"
