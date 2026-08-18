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
    assert report["expenses"] == 20
    assert report["balance"] == 86
    assert report["net"] == 86
    assert report["identity"] == "靠作品吃饭"


def test_purchase_use_and_life_collection_are_separate_operations(tmp_path):
    ledger = EconomyLedger(tmp_path / "economy.json", now_provider=_now)
    ledger.record_income("项目结项", 20, source_key="project:1")

    purchased = ledger.purchase_item("coffee", operation_key="button-1")
    assert purchased is not None
    assert ledger.inventory_count("coffee") == 1
    assert ledger.month_report()["expenses"] == 12

    retry = ledger.purchase_item("coffee", operation_key="button-1")
    assert retry is not None
    assert retry.event_id == purchased.event_id
    assert ledger.inventory_count("coffee") == 1
    assert ledger.balance == 8

    used = ledger.use_item("coffee")
    assert used is not None
    assert used["state"] == "coffee"
    assert ledger.inventory_count("coffee") == 0
    assert ledger.life_collection()["coffee"] == 1
    assert ledger.active_states()["coffee"]


def test_households_are_permanent_and_do_not_touch_skin_system(tmp_path):
    ledger = EconomyLedger(tmp_path / "economy.json", now_provider=_now)
    ledger.record_income("论文稿费", 100, source_key="paper:2")

    assert ledger.purchase_item("coffee_pot") is not None
    assert ledger.has_household("coffee_pot")
    assert ledger.purchase_item("coffee_pot") is None
    assert "coffee_pot" not in ledger.inventory
    assert ledger.purchase_item("desk_lamp") is None
    assert not ledger.has_household("desk_lamp")
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
    assert report["expenses"] == 12
    assert ledger.balance == 8

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

def test_focus_grants_daily_supply_without_changing_income(tmp_path):
    ledger = EconomyLedger(tmp_path / "economy.json", now_provider=_now)
    result = ledger.record_focus(40 * 60, started_at=_now())
    assert result["coins"] == 4
    assert ledger.inventory_count("coffee") == 1
    assert ledger.inventory_count("milk_tea") == 1
    assert ledger.inventory_count("tea") == 0
    assert ledger.monthly_income() == 4
    assert ledger.daily_supply_status()["coffee"]["claimed"] is True


def test_important_todo_grants_one_daily_cake(tmp_path):
    ledger = EconomyLedger(tmp_path / "economy.json", now_provider=_now)
    first = ledger.record_important_todo_completion("todo-1", "论文机制")
    second = ledger.record_important_todo_completion("todo-1", "论文机制")
    assert first is not None
    assert second is not None
    assert ledger.inventory_count("cake") == 1
    assert ledger.monthly_income() == 0

def test_coffee_pot_grants_one_limited_supply_per_day(tmp_path):
    current = [_now()]
    ledger = EconomyLedger(
        tmp_path / "economy.json",
        now_provider=lambda: current[0],
    )
    ledger.record_income("咖啡壶测试资金", 100, source_key="coffee-pot-test")
    assert ledger.purchase_item("coffee_pot") is not None

    assert ledger.ensure_daily_household_supply() is True
    assert ledger.inventory_count("coffee") == 1
    assert ledger.ensure_daily_household_supply() is False
    assert ledger.inventory_count("coffee") == 1
    assert ledger.daily_supply_status()["coffee"]["coffee_pot_claimed"] is True

    # The existing first-work supply remains separate from the coffee-pot supply.
    ledger.record_focus(60, started_at=current[0])
    assert ledger.inventory_count("coffee") == 2

    current[0] = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)
    assert ledger.ensure_daily_household_supply() is True
    assert ledger.inventory_count("coffee") == 3
