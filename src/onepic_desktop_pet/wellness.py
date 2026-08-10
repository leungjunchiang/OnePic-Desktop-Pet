"""管理可关闭的喝水和站立休息提醒，不访问网络或读取工作内容。"""

from __future__ import annotations

import time
from collections.abc import Callable


class WellnessReminderModel:
    """用单调时钟为两个独立提醒通道提供一次性到期事件。"""

    def __init__(self, monotonic_provider: Callable[[], float] | None = None) -> None:
        self._now = monotonic_provider or time.monotonic
        current = self._now()
        self._last_water = current
        self._last_stand = current

    def take_due(
        self,
        water_enabled: bool,
        stand_enabled: bool,
        water_minutes: int,
        stand_minutes: int,
    ) -> str | None:
        """按优先级返回 ``water`` 或 ``stand``，同一时刻最多提醒一次。"""

        now = self._now()
        if water_enabled and now - self._last_water >= max(5, water_minutes) * 60:
            self._last_water = now
            return "water"
        if stand_enabled and now - self._last_stand >= max(5, stand_minutes) * 60:
            self._last_stand = now
            return "stand"
        return None

    def acknowledge(self, kind: str) -> None:
        """用户喝水或站立后重置对应计时。"""

        if kind == "water":
            self._last_water = self._now()
        elif kind == "stand":
            self._last_stand = self._now()
