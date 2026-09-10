"""Join the local time records into compact, AI-safe context and summaries."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from .daily_record_manager import DailyRecordManager
from .time_service import format_duration, today_key
from .todo_manager import TodoManager
from .work_session_manager import WorkSessionManager


class DailySummaryService:
    def __init__(self, todos: TodoManager, records: DailyRecordManager, sessions: WorkSessionManager) -> None:
        self.todos = todos
        self.records = records
        self.sessions = sessions

    def today(self, date_key: str | None = None) -> dict:
        key = date_key or today_key(self.records._now)
        tasks = self.todos.for_date(key)
        record = self.records.get(key)
        return {
            "date": key,
            "focus_seconds": record.focus_seconds,
            "focus": format_duration(record.focus_seconds),
            "tasks": [{"id": item.id, "title": item.title, "completed": item.completed, "important": item.important, "work_seconds": item.work_seconds} for item in tasks],
            "pending_tasks": [item.title for item in tasks if not item.completed],
            "completed_tasks": sum(1 for item in tasks if item.completed),
            "total_tasks": len(tasks),
            "sessions": record.sessions,
            "checked_in": record.checked_in,
            "rest_day": record.rest_day,
            "diary": record.diary,
        }

    def refresh_tasks(self, date_key: str | None = None) -> dict:
        summary = self.today(date_key)
        self.records.sync_tasks(
            completed_tasks=summary["completed_tasks"],
            total_tasks=summary["total_tasks"],
            main_task_completed=any(item["important"] and item["completed"] for item in summary["tasks"]),
            date_key=summary["date"],
        )
        return self.today(summary["date"])

    def checkout(self, note: str = "") -> dict:
        summary = self.refresh_tasks()
        record = self.records.checkout(
            completed_tasks=summary["completed_tasks"],
            total_tasks=summary["total_tasks"],
            main_task_completed=any(item["important"] and item["completed"] for item in summary["tasks"]),
            note=note,
        )
        return self.today(record.date)

    def context(self) -> str:
        summary = self.refresh_tasks()
        pending = "、".join(summary["pending_tasks"][:8]) or "无"
        return (
            "真实本地工作状态（只可据此回答，不要猜测）：\n"
            f"今天已专注：{summary['focus']}；有效打卡：{'是' if summary['checked_in'] else '否'}；\n"
            f"今日任务：{summary['completed_tasks']}/{summary['total_tasks']} 完成；未完成：{pending}；\n"
            f"当前记录的工作段：{summary['sessions']} 次；休息日：{'是' if summary['rest_day'] else '否'}。"
        )

    def stats_text(self, days: int = 7) -> str:
        end = date.fromisoformat(today_key(self.records._now))
        stats = self.records.stats(start=end - timedelta(days=max(0, days - 1)), end=end)
        return f"工作{stats['work_days']}天，累计专注{format_duration(stats['focus_seconds'])}，完成任务{stats['completed_tasks']}项。"

