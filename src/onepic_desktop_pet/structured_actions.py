"""Parse explicit JSON actions returned by an AI and execute them locally.

Natural language remains AI territory.  This module only accepts a clearly
structured object, validates it, and performs the side effect in local stores.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .anniversary_manager import AnniversaryManager
from .countdown_manager import CountdownManager
from .daily_summary_service import DailySummaryService
from .timeline_manager import TimelineManager
from .todo_manager import TodoManager
from .reminder_manager import ReminderManager


ACTION_NAMES = {
    "create_todo", "complete_todo", "delete_todo", "query_today", "create_countdown",
    "update_countdown", "delete_countdown", "complete_countdown", "query_countdown", "create_anniversary",
    "update_anniversary", "delete_anniversary", "query_anniversary", "create_timeline_event", "delete_timeline_event",
    "query_timeline", "checkout_today", "rest_today", "move_pending_to_today",
}


@dataclass(frozen=True)
class ActionResult:
    action: str
    reply_hint: str
    data: dict[str, Any]


def extract_action(text: str) -> dict[str, Any] | None:
    """Extract one object from fenced or plain AI output; reject prose."""

    source = str(text or "").strip()
    candidates = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", source, flags=re.IGNORECASE | re.DOTALL)
    candidates.append(source)
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and str(value.get("action") or value.get("intent") or "") in ACTION_NAMES:
            value["action"] = str(value.get("action") or value.get("intent"))
            return value
    return None


class LocalActionExecutor:
    def __init__(self, todos: TodoManager, countdowns: CountdownManager, anniversaries: AnniversaryManager, timeline: TimelineManager, summary: DailySummaryService, reminders: ReminderManager | None = None) -> None:
        self.todos = todos
        self.countdowns = countdowns
        self.anniversaries = anniversaries
        self.timeline = timeline
        self.summary = summary
        self.reminders = reminders

    def execute(self, value: dict[str, Any]) -> ActionResult | None:
        action = str(value.get("action") or "")
        if action not in ACTION_NAMES:
            return None
        if action == "create_todo":
            created = [self.todos.add(str(item.get("title") or ""), date=item.get("date"), time=item.get("time"), reminder=bool(item.get("reminder", False)), important=bool(item.get("important", False))) for item in value.get("tasks", []) if isinstance(item, dict) and str(item.get("title") or "").strip()]
            if self.reminders is not None:
                for task in created:
                    if task.reminder and task.time:
                        self.reminders.add(task.title, f"{task.date}T{task.time}:00", source_id=task.id)
            return ActionResult(action, f"记上了，今天新增 {len(created)} 项。", {"tasks": [item.to_dict() for item in created]})
        if action in {"complete_todo", "delete_todo"}:
            item = self.todos.find(str(value.get("target") or value.get("title") or ""))
            if item is None:
                return ActionResult(action, "我没找到对应的今日事项。", {})
            ok = self.todos.complete(item.id) if action == "complete_todo" else self.todos.delete(item.id)
            return ActionResult(action, "处理好了。" if ok else "这项没改动。", {"id": item.id})
        if action == "query_today":
            return ActionResult(action, "", self.summary.today())
        if action == "checkout_today":
            return ActionResult(action, "今天收工，记录留好了。", self.summary.checkout(str(value.get("note") or "")))
        if action == "rest_today":
            record = self.summary.records.set_rest_day(True, note=str(value.get("note") or ""))
            return ActionResult(action, "行，那今天不算旷工。", {"date": record.date})
        if action == "create_countdown":
            item = self.countdowns.add(str(value.get("title") or "未命名倒计时"), str(value.get("target_datetime") or value.get("target_date") or ""), pinned=bool(value.get("pinned", False)), show_on_desktop=bool(value.get("show_on_desktop", False)), category=str(value.get("category") or "other"))
            return ActionResult(action, f"记上了。{item.title}已经放进倒计时。", {"id": item.id, "remaining_days": self.countdowns.remaining_days(item)})
        if action in {"update_countdown", "delete_countdown", "complete_countdown"}:
            item = self.countdowns.find(str(value.get("target") or value.get("title") or ""))
            if item is None:
                return ActionResult(action, "没找到这个倒计时。", {})
            if action == "delete_countdown":
                self.countdowns.delete(item.id)
                return ActionResult(action, "倒计时收起来了，历史记录不会被删。", {"id": item.id})
            if action == "complete_countdown":
                self.countdowns.complete(item.id)
                source = f"countdown:{item.id}"
                if not self.timeline.has_source(source):
                    self.timeline.add(f"完成倒计时：{item.title}", event_type="milestone", source=source, related_countdown_id=item.id, important=True)
                return ActionResult(action, "完成了，已经留进时光轴。", {"id": item.id})
            self.countdowns.update(item.id, **{key: val for key, val in value.items() if key in {"title", "target_datetime", "pinned", "show_on_desktop", "note"}})
            return ActionResult(action, "倒计时改好了。", {"id": item.id})
        if action == "query_countdown":
            return ActionResult(action, "", {"items": [{"title": item.title, "remaining_days": self.countdowns.remaining_days(item)} for item in self.countdowns.items if not item.completed]})
        if action == "create_anniversary":
            item = self.anniversaries.add(str(value.get("title") or "未命名纪念日"), str(value.get("date") or ""), repeat=str(value.get("repeat") or "none"), show_on_desktop=bool(value.get("show_on_desktop", False)))
            return ActionResult(action, f"这个日子我记下了：{item.title}。", {"id": item.id})
        if action in {"update_anniversary", "delete_anniversary"}:
            item = self.anniversaries.find(str(value.get("target") or value.get("title") or ""))
            if action == "update_anniversary" and item is not None:
                changes = {key: val for key, val in value.items() if key in {"title", "date", "repeat", "show_on_desktop", "note"}}
                self.anniversaries.update(item.id, **changes)
                return ActionResult(action, "纪念日改好了。", {"id": item.id})
            return ActionResult(action, "纪念日已删。" if item and self.anniversaries.delete(item.id) else "没找到这个纪念日。", {})
        if action == "query_anniversary":
            return ActionResult(action, "", {"items": [{"title": item.title, "remaining_days": self.anniversaries.remaining_days(item)} for item in self.anniversaries.items]})
        if action == "create_timeline_event":
            item = self.timeline.add(str(value.get("title") or "今天的记录"), date=value.get("date"), event_type=str(value.get("type") or "manual"), description=str(value.get("description") or ""), source=str(value.get("source") or "manual"), important=bool(value.get("important", False)))
            return ActionResult(action, "这个我给你记上了，之后还能翻回来。", {"id": item.id})
        if action == "delete_timeline_event":
            return ActionResult(action, "记录已删除。", {}) if self.timeline.delete(str(value.get("id") or "")) else ActionResult(action, "没找到这条记录。", {})
        if action == "query_timeline":
            return ActionResult(action, "", {"items": [item.__dict__ for item in self.timeline.query()]})
        return None
