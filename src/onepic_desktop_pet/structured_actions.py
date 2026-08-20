"""Parse explicit JSON actions returned by an AI and execute them locally.

Natural language remains AI territory.  This module only accepts a clearly
structured object, validates it, and performs the side effect in local stores.
Todo creation is authorized by the original user message immediately before
the existing manager writes; no parallel Todo store is introduced.
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
from .alarm_manager import AlarmManager
from .todo_nlp import parse_explicit_todo_request, validate_todo_creation_intent


ACTION_NAMES = {
    "create_todo", "update_todo", "complete_todo", "delete_todo", "query_today", "create_countdown",
    "update_countdown", "delete_countdown", "complete_countdown", "query_countdown", "create_anniversary",
    "update_anniversary", "delete_anniversary", "query_anniversary", "create_timeline_event", "delete_timeline_event",
    "query_timeline", "checkout_today", "rest_today", "move_pending_to_today",
}


@dataclass(frozen=True)
class ActionResult:
    action: str
    reply_hint: str
    data: dict[str, Any]
    ok: bool = True


def extract_action(text: str) -> dict[str, Any] | None:
    """Extract one object from fenced or plain AI output; reject prose."""

    source = str(text or "").strip()
    candidates: list[str] = []
    # Do not use a non-greedy ``{.*?}`` expression here: a tasks array is a
    # nested JSON object and that expression stops at the first task's brace.
    candidates.extend(
        match.group(1).strip()
        for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)\s*```", source, flags=re.IGNORECASE)
    )
    decoder = json.JSONDecoder()
    for index, char in enumerate(source):
        if char == "{":
            try:
                _, end = decoder.raw_decode(source[index:])
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
            candidates.append(source[index : index + end])
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
    def __init__(self, todos: TodoManager, countdowns: CountdownManager, anniversaries: AnniversaryManager, timeline: TimelineManager, summary: DailySummaryService, reminders: ReminderManager | None = None, alarms: AlarmManager | None = None) -> None:
        self.todos = todos
        self.countdowns = countdowns
        self.anniversaries = anniversaries
        self.timeline = timeline
        self.summary = summary
        self.reminders = reminders
        self.alarms = alarms

    def execute(self, value: dict[str, Any], *, user_text: str | None = None) -> ActionResult | None:
        action = str(value.get("action") or "")
        if action not in ACTION_NAMES:
            return None
        if action == "create_todo":
            permission = validate_todo_creation_intent(user_text, value)
            if not permission.allowed:
                return ActionResult(
                    action,
                    "我把这句按聊天理解了，没有写入待办。",
                    {"saved": False, "permission_denied": True, "reason": permission.reason},
                    False,
                )
            raw_tasks = value.get("tasks")
            if not isinstance(raw_tasks, list):
                raw_tasks = [value]
            # When a model emits the whole user sentence as the title, prefer
            # the same deterministic parser used by the offline fast path.
            # This keeps “六毛，帮我设个今天9点的待办：去图书馆自习” as a
            # structured task instead of storing the command itself.
            if user_text is not None and len(raw_tasks) == 1:
                normalized = parse_explicit_todo_request(user_text)
                if normalized and isinstance(normalized.get("tasks"), list):
                    raw_tasks = normalized["tasks"]
            created = []
            updated = []
            for raw in raw_tasks:
                if not isinstance(raw, dict) or not str(raw.get("title") or "").strip():
                    continue
                title = str(raw.get("title") or "").strip()
                date = raw.get("date")
                existing = self.todos.find_similar_pending(title, date)
                changes = {
                    key: raw[key]
                    for key in ("title", "date", "time", "important", "reminder", "reminder_mode", "due_at", "remind_at", "priority", "queue_position", "reminder_minutes_before", "alarm_sound_id", "alarm_volume", "alarm_snooze_minutes", "source")
                    if key in raw
                }
                if existing is not None and not bool(raw.get("force_new", False)):
                    if "reminder" not in changes and ("time" in changes or "remind_at" in changes):
                        changes["reminder"] = True
                    task = self.todos.update(existing.id, **changes)
                    updated.append(task)
                else:
                    task = self.todos.add(
                        title,
                        date=date,
                        time=raw.get("time"),
                        reminder=bool(raw.get("reminder", bool(raw.get("remind_at") or raw.get("time")))),
                        reminder_mode=raw.get("reminder_mode", "pet"),
                        important=bool(raw.get("important", False)),
                        due_at=raw.get("due_at"),
                        remind_at=raw.get("remind_at"),
                        priority=raw.get("priority"),
                        reminder_minutes_before=raw.get("reminder_minutes_before", 10),
                        source=str(raw.get("source") or "chat"),
                    )
                    created.append(task)
                self._sync_todo_reminder(task)
            if not created and not updated:
                return ActionResult(action, "没有明确的待办内容，我还没保存。", {"saved": False}, False)
            tasks = created + updated
            labels = [
                f"{item.time} {item.title}" if item.time else item.title
                for item in tasks
            ]
            summary = "、".join(labels[:4])
            if len(labels) > 4:
                summary += "等"
            if created and updated:
                reply = f"记上了：{summary}。"
            else:
                reply = f"好，放进待办了：{summary}。"
            return ActionResult(
                action,
                reply,
                {"saved": True, "created": [item.to_dict() for item in created], "updated": [item.to_dict() for item in updated], "tasks": [item.to_dict() for item in tasks]},
            )
        if action == "update_todo":
            item = self.todos.find(str(value.get("target") or value.get("title") or ""))
            if item is None:
                return ActionResult(action, "没找到对应的待办，我没有改动任何东西。", {"saved": False}, False)
            changes = {
                key: value[key]
                for key in ("title", "date", "time", "important", "reminder", "reminder_mode", "due_at", "remind_at", "priority", "queue_position", "reminder_minutes_before", "alarm_sound_id", "alarm_volume", "alarm_snooze_minutes", "source")
                if key in value
            }
            if not changes:
                return ActionResult(action, "你想改哪一项？我还没有改动。", {"saved": False}, False)
            if "reminder" not in changes and ("time" in changes or "remind_at" in changes):
                changes["reminder"] = True
            item = self.todos.update(item.id, **changes)
            self._sync_todo_reminder(item)
            return ActionResult(action, f"待办改好了：{item.title}。", {"saved": True, "task": item.to_dict()})
        if action in {"complete_todo", "delete_todo"}:
            item = self.todos.find(str(value.get("target") or value.get("title") or ""))
            if item is None:
                return ActionResult(action, "我没找到对应的待办，没有改动。", {"saved": False}, False)
            ok = self.todos.complete(item.id) if action == "complete_todo" else self.todos.delete(item.id)
            if self.reminders is not None:
                if action == "complete_todo":
                    self.reminders.complete_for_source(item.id)
                else:
                    self.reminders.remove_for_source(item.id)
            if self.alarms is not None:
                self.alarms.delete(f"todo:{item.id}")
            return ActionResult(action, "处理好了。" if ok else "这项没改动。", {"saved": bool(ok), "id": item.id}, bool(ok))
        if action == "query_today":
            return ActionResult(action, "", self.summary.today())
        if action == "checkout_today":
            return ActionResult(action, "今天收工，记录留好了。", self.summary.checkout(str(value.get("note") or "")))
        if action == "rest_today":
            record = self.summary.records.set_rest_day(True, note=str(value.get("note") or ""))
            return ActionResult(action, "行，那今天不算旷工。", {"date": record.date})
        if action == "create_countdown":
            item = self.countdowns.add(str(value.get("title") or "未命名倒计时"), str(value.get("target_datetime") or value.get("target_date") or ""), pinned=bool(value.get("pinned", False)), show_on_desktop=bool(value.get("show_on_desktop", False)), show_before_days=int(value.get("show_before_days", 7) or 0), category=str(value.get("category") or "other"))
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
            self.countdowns.update(item.id, **{key: val for key, val in value.items() if key in {"title", "target_datetime", "pinned", "show_on_desktop", "show_before_days", "note"}})
            return ActionResult(action, "倒计时改好了。", {"id": item.id})
        if action == "query_countdown":
            return ActionResult(action, "", {"items": [{"title": item.title, "remaining_days": self.countdowns.remaining_days(item)} for item in self.countdowns.items if not item.completed]})
        if action == "create_anniversary":
            item = self.anniversaries.add(str(value.get("title") or "未命名纪念日"), str(value.get("date") or ""), repeat=str(value.get("repeat") or "none"), show_on_desktop=bool(value.get("show_on_desktop", False)), show_before_days=int(value.get("show_before_days", 7) or 0))
            return ActionResult(action, f"这个日子我记下了：{item.title}。", {"id": item.id})
        if action in {"update_anniversary", "delete_anniversary"}:
            item = self.anniversaries.find(str(value.get("target") or value.get("title") or ""))
            if action == "update_anniversary" and item is not None:
                changes = {key: val for key, val in value.items() if key in {"title", "date", "repeat", "show_on_desktop", "show_before_days", "note"}}
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

    def _sync_todo_reminder(self, task: Any) -> None:
        """Keep one real local reminder aligned with the Todo record."""

        if self.reminders is None:
            if self.alarms is not None:
                self.alarms.sync_todo(task, reminder_mode=getattr(task, "reminder_mode", "none"))
            return
        mode = str(getattr(task, "reminder_mode", "") or ("pet" if task.reminder else "none"))
        if mode == "alarm":
            self.reminders.remove_for_source(task.id)
            if self.alarms is not None:
                self.alarms.sync_todo(task, reminder_mode=mode)
            return
        if mode == "none" or not task.reminder:
            self.reminders.remove_for_source(task.id)
            if self.alarms is not None:
                self.alarms.sync_todo(task, reminder_mode="none")
            return
        due = task.remind_at or task.due_at
        if due:
            self.reminders.upsert_for_source(task.title, due, source_id=task.id)
            if self.alarms is not None:
                self.alarms.sync_todo(task, reminder_mode="none")
        elif self.alarms is not None:
            self.alarms.sync_todo(task, reminder_mode="none")
