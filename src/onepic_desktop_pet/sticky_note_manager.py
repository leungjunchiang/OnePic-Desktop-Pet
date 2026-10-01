"""北京时间业务时间与显示统一；UTC 事实和持续时长不作手工偏移。

A tiny durable text note, intentionally separate from the Todo list."""

from __future__ import annotations

from .time_service import now_beijing

from datetime import datetime
from typing import Any, Callable

from .local_data import local_data_path, read_json, write_json_atomic
from .time_service import now_local


class StickyNoteManager:
    """Persist one lightweight free-form note for the standalone 便利贴."""

    def __init__(
        self,
        path=None,
        *,
        now_provider: Callable[[], datetime] | None = None,
        persist: bool = True,
    ) -> None:
        self.path = path or local_data_path("sticky_note.json")
        self._now = (lambda: now_beijing(now_provider))
        self.persist = bool(persist)
        raw = read_json(self.path, {})
        self.text = str(raw.get("text") or "")[:4000] if isinstance(raw, dict) else ""
        self.updated_at = str(raw.get("updated_at") or "") if isinstance(raw, dict) else ""

    def update(self, text: Any) -> str:
        self.text = str(text or "")[:4000]
        self.updated_at = now_local(self._now).isoformat()
        if self.persist:
            write_json_atomic(
                self.path,
                {"text": self.text, "updated_at": self.updated_at},
            )
        return self.text
