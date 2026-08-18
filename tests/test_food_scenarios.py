from datetime import datetime, timedelta, timezone

from onepic_desktop_pet.economy import EconomyLedger
from onepic_desktop_pet.food_scenarios import interaction_mode_action


def _ledger(now_ref: list[datetime]) -> EconomyLedger:
    ledger = EconomyLedger(
        path=None,
        now_provider=lambda: now_ref[0],
        persist=False,
    )
    ledger.record_income("测试工资", 100, source_key="test-seed")
    return ledger


def _buy(ledger: EconomyLedger, item_key: str) -> None:
    assert ledger.purchase_item(item_key, operation_key=f"buy-{item_key}") is not None


def test_coffee_starts_a_real_focus_scene_without_fake_income() -> None:
    now = [datetime(2026, 8, 18, 9, tzinfo=timezone.utc)]
    ledger = _ledger(now)
    _buy(ledger, "coffee")

    result = ledger.start_food_scene(
        "coffee",
        duration_minutes=30,
        todo_id="todo-1",
        todo_title="修改论文理论机制",
    )

    assert result is not None
    assert ledger.inventory_count("coffee") == 0
    assert ledger.balance == 88
    assert ledger.monthly_income() == 100
    scene = ledger.active_food_scene()
    assert scene is not None
    assert scene["scene_type"] == "focus"
    assert scene["duration_minutes"] == 30
    assert scene["todo_id"] == "todo-1"
    assert scene["todo_title"] == "修改论文理论机制"
    assert result["event"]["amount"] == 0


def test_milk_tea_is_a_timed_rest_and_does_not_add_focus_time() -> None:
    now = [datetime(2026, 8, 18, 14, tzinfo=timezone.utc)]
    ledger = _ledger(now)
    _buy(ledger, "milk_tea")

    result = ledger.start_food_scene("milk_tea", duration_minutes=15)
    assert result is not None
    assert ledger.active_food_scene()["scene_type"] == "rest"

    now[0] += timedelta(minutes=15)
    assert ledger.active_food_scene()["expired"] is True
    finished = ledger.finish_food_scene("timer")
    assert finished is not None
    assert ledger.active_food_scene() is None
    assert ledger.monthly_income() == 100


def test_tea_is_companion_scene_without_hunger_or_fullness_values() -> None:
    now = [datetime(2026, 8, 18, 20, tzinfo=timezone.utc)]
    ledger = _ledger(now)
    _buy(ledger, "tea")

    result = ledger.start_food_scene("tea", duration_minutes=0)
    assert result is not None
    scene = ledger.active_food_scene()
    assert scene is not None
    assert scene["scene_type"] == "companion"
    assert "hunger" not in scene
    assert "fullness" not in scene


def test_open_tea_scene_does_not_block_future_food_after_restart() -> None:
    now = [datetime(2026, 8, 18, 20, tzinfo=timezone.utc)]
    ledger = _ledger(now)
    _buy(ledger, "tea")
    _buy(ledger, "coffee")

    assert ledger.start_food_scene("tea", duration_minutes=0) is not None
    now[0] += timedelta(seconds=61)
    assert ledger.active_food_scene()["expired"] is True
    assert ledger.start_food_scene("coffee", duration_minutes=30) is not None


def test_received_food_scene_is_not_an_inventory_item_or_leaderboard_income() -> None:
    now = [datetime(2026, 8, 18, 20, tzinfo=timezone.utc)]
    ledger = _ledger(now)

    result = ledger.start_food_scene(
        "tea",
        consume_inventory=False,
        source="buddy_food_received",
    )

    assert result is not None
    assert ledger.inventory_count("tea") == 0
    assert ledger.balance == 100
    assert ledger.monthly_income() == 100
    assert result["event"]["category"] == "food_scene_received"


def test_buddy_interaction_modes_preserve_focus() -> None:
    assert interaction_mode_action("welcome", True) == "immediate"
    assert interaction_mode_action("focus_priority", True) == "queue"
    assert interaction_mode_action("focus_priority", False) == "immediate"
    assert interaction_mode_action("do_not_disturb", False) == "silent"
