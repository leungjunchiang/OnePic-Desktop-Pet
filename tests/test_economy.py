from datetime import datetime, timezone

from onepic_desktop_pet.economy import EconomyLedger


def test_focus_salary_has_daily_cap_and_early_bird_reward(tmp_path):
    ledger = EconomyLedger(tmp_path / "economy.json", now_provider=lambda: datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc))

    first = ledger.record_focus(30 * 60, started_at=datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc))
    second = ledger.record_focus(8 * 60 * 60, started_at=datetime(2026, 8, 18, 9, 45, tzinfo=timezone.utc))

    assert first["early_bird"] is True
    assert ledger.inventory["昂贵咖啡"] == 1
    assert ledger.balance == 48  # 8.5h is capped to 8h, paid at 6/hour.
    assert second["credited_seconds"] == 7 * 60 * 60 + 30 * 60


def test_windfall_and_spending_are_included_in_salary_slip(tmp_path):
    ledger = EconomyLedger(tmp_path / "economy.json", now_provider=lambda: datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc))
    event = ledger.record_income("论文录用", 300, source_key="paper:accepted:1")
    assert event is not None
    assert ledger.record_income("论文录用", 300, source_key="paper:accepted:1") is None
    assert ledger.spend("昂贵咖啡", 36, item_key="昂贵咖啡") is not None

    report = ledger.month_report("2026-08")
    assert report["windfall"] == 300
    assert report["expenses"] == 36
    assert report["net"] == 264
    assert ledger.inventory["昂贵咖啡"] == 1

