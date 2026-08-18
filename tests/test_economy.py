from datetime import datetime, timezone

from onepic_desktop_pet.economy import EconomyLedger


def _now():
    return datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)


def test_focus_salary_has_daily_cap_and_early_bird_reward(tmp_path):
    ledger = EconomyLedger(tmp_path / "economy.json", now_provider=_now)

    first = ledger.record_focus(30 * 60, started_at=_now())
    second = ledger.record_focus(
        8 * 60 * 60,
        started_at=datetime(2026, 8, 18, 9, 45, tzinfo=timezone.utc),
    )

    assert first["early_bird"] is True
    assert ledger.inventory["昂贵咖啡"] == 1
    assert ledger.balance == 48
    assert second["credited_seconds"] == 7 * 60 * 60 + 30 * 60


def test_income_spend_and_payroll_keep_income_separate_from_balance(tmp_path):
    ledger = EconomyLedger(tmp_path / "economy.json", now_provider=_now)
    event = ledger.record_income("论文录用", 100, source_key="paper:accepted:1")

    assert event is not None
    assert ledger.record_income("论文录用", 100, source_key="paper:accepted:1") is None
    ledger.record_focus(60 * 60, started_at=_now())
    purchase = ledger.purchase_item("milk_tea")
    assert purchase is not None

    report = ledger.month_report("2026-08")
    assert report["income"] == 106
    assert report["expenses"] == 4
    assert report["balance"] == 105
    assert report["net"] == 105
    assert report["identity"] == "靠作品吃饭"


def test_purchase_use_and_life_collection_are_separate_operations(tmp_path):
    ledger = EconomyLedger(tmp_path / "economy.json", now_provider=_now)
    ledger.record_income("项目结项", 20, source_key="project:1")

    purchased = ledger.purchase_item("coffee", operation_key="button-1")
    assert purchased is not None
    assert ledger.inventory_count("coffee") == 1
    assert ledger.month_report()["expenses"] == 2

    retry = ledger.purchase_item("coffee", operation_key="button-1")
    assert retry is not None
    assert retry.event_id == purchased.event_id
    assert ledger.inventory_count("coffee") == 1
    assert ledger.balance == 18

    used = ledger.use_item("coffee")
    assert used is not None
    assert used["state"] == "coffee"
    assert ledger.inventory_count("coffee") == 0
    assert ledger.life_collection()["coffee"] == 1
    assert ledger.active_states()["coffee"]


def test_households_are_permanent_and_do_not_touch_skin_system(tmp_path):
    ledger = EconomyLedger(tmp_path / "economy.json", now_provider=_now)
    ledger.record_income("论文稿费", 100, source_key="paper:2")

    assert ledger.purchase_item("desk_lamp") is not None
    assert ledger.has_household("desk_lamp")
    assert ledger.purchase_item("desk_lamp") is None
    assert "desk_lamp" not in ledger.inventory
    assert all(
        "皮肤" not in str(row) and "娃衣" not in str(row)
        for row in ledger.catalog().values()
    )


def test_gifts_are_expenses_or_inventory_events_not_leaderboard_income(tmp_path):
    ledger = EconomyLedger(tmp_path / "economy.json", now_provider=_now)
    ledger.record_income("作品稿费", 20, source_key="work:1")
    sent = ledger.record_gift_sent("buddy-1", "小梁")

    assert sent is not None
    report = ledger.month_report()
    assert report["income"] == 20
    assert report["expenses"] == 5
    assert ledger.balance == 15

    received = ledger.record_gift_received("buddy-2", "绵绵")
    assert received is not None
    assert ledger.inventory_count("coffee") == 1
    assert ledger.month_report()["income"] == 20


def test_old_expensive_coffee_inventory_is_preserved(tmp_path):
    path = tmp_path / "economy.json"
    path.write_text(
        '{"version": 1, "balance": 7, "events": [], "daily_focus": {},'
        ' "inventory": {"昂贵咖啡": 3}}',
        encoding="utf-8",
    )
    ledger = EconomyLedger(path, now_provider=_now)
    assert ledger.balance == 7
    assert ledger.inventory_count("expensive_coffee") == 3
    assert ledger.inventory["昂贵咖啡"] == 3
