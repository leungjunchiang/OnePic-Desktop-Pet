"""
本模块提供“六毛工作搭子”的本地工作计时与温和休息提醒，不创建窗口或访问网络。

职责范围：
- 记录当天累计工作秒数，并在日期变化时自动开始新一天；
- 支持开始、暂停、完成、状态格式化和运行中定期落盘；
- 只在本机应用数据目录保存日期与累计秒数，不保存任务名称或聊天内容；
- 按单次连续工作时长产生 25 分钟鼓励、50 分钟休息和更长时段劝慰提醒。

计时使用单调时钟避免系统时间微调造成跳变；自然退出时会保存当前进度，异常退出最多
损失一个自动保存间隔内的秒数，并且下次启动不会把离线时间误算为工作时间。
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path


def work_timer_path() -> Path:
    """返回当前用户的本地工作计时文件路径。"""

    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".desktop_pet"
    return root / "SixHairWorkmate" / "work_timer.json"


def format_work_duration(seconds: int) -> str:
    """把秒数格式化为适合菜单和气泡显示的中文时长。"""

    safe_seconds = max(0, int(seconds))
    if safe_seconds < 60:
        return "不足1分钟" if safe_seconds else "0分钟"
    total_minutes = safe_seconds // 60
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}小时{minutes}分钟"
    if hours:
        return f"{hours}小时"
    return f"{minutes}分钟"


class WorkTimerModel:
    """维护单个用户的今日累计时长和当前连续工作时段。"""

    def __init__(
        self,
        path: Path | None = None,
        now_provider: Callable[[], datetime] | None = None,
        monotonic_provider: Callable[[], float] | None = None,
    ) -> None:
        self.path = path or work_timer_path()
        self._now = now_provider or datetime.now
        self._monotonic = monotonic_provider or time.monotonic
        self._date_key = self._today_key()
        self._accumulated_seconds = 0
        self._session_accumulated_seconds = 0
        self._running_since: float | None = None
        self._last_checkpoint = self._monotonic()
        self._last_reminder_key: str | None = None
        self._load()

    @property
    def is_running(self) -> bool:
        """返回当前是否正在计时。"""

        return self._running_since is not None

    def _today_key(self) -> str:
        """返回本地日期键。"""

        return self._now().date().isoformat()

    def _load(self) -> None:
        """读取当天累计秒数；损坏或过期文件安全回退为零。"""

        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("date") != self._date_key:
                return
            seconds = int(data.get("accumulated_seconds", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return
        self._accumulated_seconds = max(0, seconds)

    def _rollover_if_needed(self) -> None:
        """日期变化时清空昨日累计，并保持运行状态从当前时刻重新计时。"""

        today = self._today_key()
        if today == self._date_key:
            return
        was_running = self.is_running
        self._date_key = today
        self._accumulated_seconds = 0
        self._session_accumulated_seconds = 0
        self._running_since = self._monotonic() if was_running else None
        self._last_checkpoint = self._monotonic()
        self._last_reminder_key = None
        self._save()

    def _current_elapsed(self) -> int:
        """返回当前未落盘工作段的完整秒数。"""

        if self._running_since is None:
            return 0
        return max(0, int(self._monotonic() - self._running_since))

    def today_seconds(self) -> int:
        """返回当天累计工作秒数，包括当前运行段。"""

        self._rollover_if_needed()
        return self._accumulated_seconds + self._current_elapsed()

    def session_seconds(self) -> int:
        """返回本次连续工作段秒数，暂停后归零。"""

        self._rollover_if_needed()
        return self._session_accumulated_seconds + self._current_elapsed()

    def start(self) -> bool:
        """开始或继续新的工作段；已运行时返回 False。"""

        self._rollover_if_needed()
        if self.is_running:
            return False
        now = self._monotonic()
        self._running_since = now
        self._session_accumulated_seconds = 0
        self._last_checkpoint = now
        self._last_reminder_key = None
        return True

    def pause(self) -> bool:
        """暂停计时并保存；未运行时返回 False。"""

        self._rollover_if_needed()
        if not self.is_running:
            return False
        elapsed = self._current_elapsed()
        self._accumulated_seconds += elapsed
        self._session_accumulated_seconds = 0
        self._running_since = None
        self._last_reminder_key = None
        self._save()
        return True

    def finish(self) -> int:
        """完成当前工作段并返回今天累计秒数。"""

        self.pause()
        return self.today_seconds()

    def checkpoint(self, minimum_interval_seconds: int = 60) -> bool:
        """运行中按最小间隔保存进度，避免频繁写盘。"""

        self._rollover_if_needed()
        if not self.is_running:
            return False
        now = self._monotonic()
        if now - self._last_checkpoint < max(1, minimum_interval_seconds):
            return False
        elapsed = self._current_elapsed()
        self._accumulated_seconds += elapsed
        self._session_accumulated_seconds += elapsed
        self._running_since = now
        self._last_checkpoint = now
        self._save()
        return True

    def status_text(self) -> str:
        """返回带运行状态的今日工作时长。"""

        suffix = " · 正在计时" if self.is_running else " · 已暂停"
        return f"今日工作 {format_work_duration(self.today_seconds())}{suffix}"

    def take_due_reminder(self) -> str | None:
        """在连续工作达到提醒阈值时返回一次提醒类型。"""

        if not self.is_running:
            return None
        minutes = self.session_seconds() // 60
        if minutes >= 90:
            reminder_key = f"long-{(minutes - 90) // 45}"
            reminder_kind = "long_break"
        elif minutes >= 50:
            reminder_key = "break-50"
            reminder_kind = "break"
        elif minutes >= 25:
            reminder_key = "focus-25"
            reminder_kind = "focus"
        else:
            return None
        if reminder_key == self._last_reminder_key:
            return None
        self._last_reminder_key = reminder_key
        return reminder_kind

    def _save(self) -> None:
        """原子保存日期和累计秒数，不写入任何工作内容。"""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        data = {
            "date": self._date_key,
            "accumulated_seconds": max(0, int(self._accumulated_seconds)),
        }
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
