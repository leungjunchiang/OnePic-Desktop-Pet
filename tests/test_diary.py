"""验证六毛日报统计只保存正向、非内容型的本地计数。"""

import json
from datetime import datetime

from onepic_desktop_pet.diary import DailyCompanionStats


def test_daily_stats_persist_counts_without_task_or_chat_content(tmp_path) -> None:
    path = tmp_path / "daily.json"
    stats = DailyCompanionStats(path, now_provider=lambda: datetime(2026, 8, 10, 18, 0))
    stats.record_focus(4980, completed=True)
    stats.record_touch(); stats.record_touch()
    stats.record_sleep(); stats.record_event("guitar")

    restored = DailyCompanionStats(path, now_provider=lambda: datetime(2026, 8, 10, 20, 0))
    snapshot = restored.snapshot()
    assert snapshot["completed_tasks"] == 1
    assert snapshot["longest_focus_seconds"] == 4980
    assert snapshot["touches"] == 2
    assert snapshot["sleeps"] == 1
    assert snapshot["random_events"] == 1
    data = json.loads(path.read_text(encoding="utf-8"))
    assert not any(key in data for key in ("task", "chat", "title", "message"))


def test_daily_stats_reset_without_negative_penalty(tmp_path) -> None:
    current = [datetime(2026, 8, 10, 20, 0)]
    stats = DailyCompanionStats(tmp_path / "daily.json", now_provider=lambda: current[0])
    stats.record_touch()
    current[0] = datetime(2026, 8, 11, 8, 0)

    assert stats.snapshot()["touches"] == 0
    assert stats.snapshot()["completed_tasks"] == 0
