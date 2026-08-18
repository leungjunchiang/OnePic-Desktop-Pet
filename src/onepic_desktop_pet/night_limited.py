"""提供六毛每天 00:30–06:30 的短时夜间限定造型选择。

这套规则只描述本地时间窗口与当天的稳定随机选择，不写入永久娃衣装备，
也不把熬夜视为积分或成长奖励。窗口结束后，调用方应恢复普通陪伴状态。
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import datetime


NIGHT_LIMIT_START_MINUTE = 30
NIGHT_LIMIT_END_MINUTE = 6 * 60 + 30
NIGHT_LIMITED_ACTIVITY_POOL: tuple[str, ...] = ("night-study-limited",)


def is_night_limited_window(now: datetime) -> bool:
    """返回本地时间是否处于 00:30（含）至 06:30（不含）窗口。"""

    minute = now.hour * 60 + now.minute
    return NIGHT_LIMIT_START_MINUTE <= minute < NIGHT_LIMIT_END_MINUTE


def night_limited_activity(
    now: datetime,
    pool: Sequence[str] = NIGHT_LIMITED_ACTIVITY_POOL,
) -> str | None:
    """为当天夜间窗口稳定随机选择一项限定造型，窗口外返回 None。

    使用日期作为种子，因此同一天重启应用或每次定时检查都会得到同一项，
    不会因为 15 秒轮询而在桌面上不断跳换造型。未来增加素材到 pool 后，
    每天仍会从完整池子中随机选一项。
    """

    choices = tuple(item for item in pool if item)
    if not choices or not is_night_limited_window(now):
        return None
    seed = f"lili-night-limited:{now.date().isoformat()}"
    return random.Random(seed).choice(choices)
