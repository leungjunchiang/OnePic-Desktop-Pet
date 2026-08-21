"""维护只保存在本机的六毛每日陪伴统计与相册路径。

统计只包含日期、计时秒数衍生指标、完成次数和互动次数，不保存任务名、聊天内容、窗口标题
或用户输入。日期变化时自动开始新记录，历史工作卡以 PNG 保存到本机相册目录。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path


def diary_state_path() -> Path:
    """返回本机陪伴统计文件。"""

    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".desktop_pet"
    return root / "Lili" / "daily_companion.json"


def album_directory() -> Path:
    """返回只在本机保存每日工作卡的六毛相册目录。"""

    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".desktop_pet"
    return root / "Lili" / "album"


class DailyCompanionStats:
    """记录当天完成段、最长专注、摸六毛、睡觉和随机事件次数。"""

    def __init__(
        self,
        path: Path | None = None,
        now_provider: Callable[[], datetime] | None = None,
        persist: bool = True,
    ) -> None:
        self.path = path or diary_state_path()
        self._now = now_provider or datetime.now
        self._persist = bool(persist)
        self.date = self._now().date().isoformat()
        self.completed_tasks = 0
        self.longest_focus_seconds = 0
        self.touches = 0
        self.sleeps = 0
        self.random_events = 0
        self.last_activity = "stand"
        self.last_report_date = ""
        if self._persist:
            self._load()

    def _rollover(self) -> None:
        today = self._now().date().isoformat()
        if today == self.date:
            return
        self.date = today
        self.completed_tasks = 0
        self.longest_focus_seconds = 0
        self.touches = 0
        self.sleeps = 0
        self.random_events = 0
        self.last_activity = "stand"
        self.last_report_date = ""
        self._save()

    def report_generated_for(self, day: str | None = None) -> bool:
        """Return whether the scheduled report was already written for a day."""

        self._rollover()
        target = str(day or self.date)[:10]
        return self.last_report_date == target

    def mark_report_generated(self, day: str | None = None) -> None:
        """Persist the once-per-day scheduled-report marker."""

        self._rollover()
        self.last_report_date = str(day or self.date)[:10]
        self._save()

    def record_focus(self, seconds: int, *, completed: bool = False) -> None:
        """更新最长连续专注；明确结束一次计时时增加完成任务数。"""

        self._rollover()
        self.longest_focus_seconds = max(self.longest_focus_seconds, max(0, int(seconds)))
        if completed:
            self.completed_tasks += 1
        self._save()

    def record_touch(self) -> None:
        self._rollover(); self.touches += 1; self._save()

    def record_sleep(self) -> None:
        self._rollover(); self.sleeps += 1; self.last_activity = "sleep"; self._save()

    def record_event(self, activity: str) -> None:
        self._rollover(); self.random_events += 1; self.last_activity = str(activity)[:60]; self._save()

    def snapshot(self) -> dict[str, int | str]:
        """返回生成日报所需的只读副本。"""

        self._rollover()
        return {
            "date": self.date,
            "completed_tasks": self.completed_tasks,
            "longest_focus_seconds": self.longest_focus_seconds,
            "touches": self.touches,
            "sleeps": self.sleeps,
            "random_events": self.random_events,
            "last_activity": self.last_activity,
        }

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("date") != self.date:
                return
            self.completed_tasks = max(0, int(data.get("completed_tasks", 0)))
            self.longest_focus_seconds = max(0, int(data.get("longest_focus_seconds", 0)))
            self.touches = max(0, int(data.get("touches", 0)))
            self.sleeps = max(0, int(data.get("sleeps", 0)))
            self.random_events = max(0, int(data.get("random_events", 0)))
            self.last_activity = str(data.get("last_activity", "stand"))[:60]
            self.last_report_date = str(data.get("last_report_date", ""))[:10]
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return

    def _save(self) -> None:
        if not self._persist:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        data = {
            "date": self.date,
            "completed_tasks": self.completed_tasks,
            "longest_focus_seconds": self.longest_focus_seconds,
            "touches": self.touches,
            "sleeps": self.sleeps,
            "random_events": self.random_events,
            "last_activity": self.last_activity,
            "last_report_date": self.last_report_date,
        }
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
