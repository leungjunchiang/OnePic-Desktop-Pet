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
    ledger.record_income("论文稿费", 144, source_key="paper:2")
    assert ledger.catalog()["coffee_pot"]["price"] == 144

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


def test_important_todo_no_longer_grants_cake(tmp_path):
    ledger = EconomyLedger(tmp_path / "economy.json", now_provider=_now)
    first = ledger.record_important_todo_completion("todo-1", "论文机制")
    second = ledger.record_important_todo_completion("todo-1", "论文机制")
    assert first is None
    assert second is None
    assert ledger.inventory_count("cake") == 0
    assert ledger.monthly_income() == 0

def test_coffee_pot_grants_one_coffee_per_day(tmp_path):
    ledger = EconomyLedger(tmp_path / "economy.json", now_provider=_now)
    ledger.record_income("咖啡壶测试资金", 144, source_key="coffee-pot-test")
    assert ledger.purchase_item("coffee_pot") is not None

    assert ledger.ensure_daily_household_supply() is False
    assert ledger.inventory_count("coffee") == 1
    assert ledger.ensure_daily_household_supply() is False
    assert ledger.inventory_count("coffee") == 1
    assert ledger.daily_supply_status()["coffee"]["coffee_pot_claimed"] is True

    # The existing first-work allowance is the same daily coffee allowance.
    ledger.record_focus(60, started_at=_now())
    assert ledger.inventory_count("coffee") == 1

    next_day = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)
    ledger._now = lambda: next_day
    assert ledger.ensure_daily_household_supply() is True
    assert ledger.inventory_count("coffee") == 2


def test_expensive_coffee_two_hour_reward_is_one_time_and_not_income(tmp_path):
    ledger = EconomyLedger(tmp_path / "economy.json", now_provider=_now)
    ledger.record_income("测试工资", 100, source_key="expensive-two-hour-seed")
    ledger.purchase_item("expensive_coffee")
    scene_result = ledger.start_food_scene("expensive_coffee")
    assert scene_result is not None
    assert scene_result["scene"]["duration_minutes"] == 150
    assert ledger.update_active_food_scene_metadata({"work_episode_seconds_at_start": 12}) is not None
    assert ledger.active_food_scene()["metadata"]["work_episode_seconds_at_start"] == 12

    event = ledger.grant_expensive_coffee_focus_reward("scene-1")
    assert event is not None
    assert event.amount == 0
    assert ledger.inventory_count("coffee") == 1
    assert ledger.balance == 40
    assert ledger.monthly_income() == 100
    assert ledger.grant_expensive_coffee_focus_reward("scene-1") is None
    assert ledger.inventory_count("coffee") == 1


def test_achievement_income_requires_two_distinct_witnesses_and_monthly_cap(tmp_path):
    ledger = EconomyLedger(tmp_path / "economy.json", now_provider=_now)
    submitted = ledger.register_achievement_income("论文 / 稿费", "论文录用", 80)
    assert submitted is not None
    assert submitted["status"] == "pending"
    assert ledger.balance == 0
    assert ledger.monthly_income() == 0

    first = ledger.confirm_achievement(submitted["id"], "buddy-1", "小梁")
    assert first is not None and first["status"] == "pending"
    assert ledger.balance == 0
    duplicate = ledger.confirm_achievement(submitted["id"], "buddy-1", "小梁")
    assert duplicate is not None and duplicate["status"] == "duplicate_witness"

    settled = ledger.confirm_achievement(submitted["id"], "buddy-2", "小苗")
    assert settled is not None and settled["status"] == "settled"
    assert settled["event"]["amount"] == 200
    assert ledger.balance == 200
    assert ledger.monthly_income() == 200

    assert ledger.register_achievement_income("项目", "成果2", 1) is not None
    assert ledger.register_achievement_income("项目", "成果3", 1) is not None
    for item in ledger.pending_achievements():
        ledger.confirm_achievement(item["id"], "buddy-a")
        ledger.confirm_achievement(item["id"], "buddy-b")
    assert ledger.monthly_achievement_count() == 3
    blocked = ledger.register_achievement_income("项目", "成果5", 1)
    assert blocked is not None
    limited = ledger.confirm_achievement(blocked["id"], "buddy-c")
    assert limited is not None and limited["status"] == "monthly_limit"


def test_manual_witness_slots_reject_uninvited_and_allow_one_replacement(tmp_path):
    ledger = EconomyLedger(tmp_path / "economy.json", now_provider=_now)
    submitted = ledger.register_achievement_income(
        "作品", "手动见证成果", note="说明",
        witness_ids=["buddy-a", "buddy-b"], witness_names=["A", "B"],
    )
    assert submitted is not None
    assert len(submitted["witness_slots"]) == 2
    assert ledger.confirm_achievement(submitted["id"], "buddy-x") ["status"] == "not_invited"
    assert ledger.reject_achievement(submitted["id"], "buddy-b")["status"] == "rejected"
    assert ledger.confirm_achievement(submitted["id"], "buddy-a")["status"] == "pending"
    replaced = ledger.replace_achievement_witnesses(submitted["id"], ["buddy-c"])
    assert replaced is not None and replaced["replacement_round"] == 1
    settled = ledger.confirm_achievement(submitted["id"], "buddy-c")
    assert settled is not None and settled["status"] == "settled"
    assert settled["event"]["amount"] == 200
    assert ledger.replace_achievement_witnesses(submitted["id"], ["buddy-d"]) is None


def test_legacy_food_inventory_aliases_can_be_used(tmp_path):
    path = tmp_path / "economy.json"
    path.write_text(
        '{"version": 3, "balance": 0, "events": [], "daily_focus": {}, '
        '"inventory": {"普通咖啡": 1, "奶茶": 1, "小蛋糕": 1, "茶": 1}}',
        encoding="utf-8",
    )
    ledger = EconomyLedger(path, now_provider=_now)

    assert ledger.inventory_count("coffee") == 1
    assert ledger.start_food_scene("coffee", duration_minutes=30) is not None
    assert ledger.inventory_count("coffee") == 0

    # The old Chinese keys are normalized after a mutation, so the next use
    # cannot disagree with what the warehouse displayed.
    assert ledger.inventory_count("milk_tea") == 1
    assert ledger.active_food_scene()["item_key"] == "coffee"


def test_food_scene_start_error_distinguishes_inventory_and_active_scene(tmp_path):
    ledger = EconomyLedger(tmp_path / "economy.json", now_provider=_now)
    assert ledger.food_scene_start_error("coffee") == "inventory"

    ledger.record_income("补给测试资金", 12, source_key="food-error-test")
    assert ledger.purchase_item("coffee") is not None
    assert ledger.food_scene_start_error("coffee") is None
    assert ledger.start_food_scene("coffee", duration_minutes=30) is not None
    assert ledger.food_scene_start_error("milk_tea", consume_inventory=False) == "active_scene"
