from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QCheckBox, QFrame, QListWidget

from onepic_desktop_pet.economy import EconomyLedger
from onepic_desktop_pet.food_scene_ui import FoodSceneDialog


def test_cake_buddy_checkbox_keeps_a_visible_selected_row(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    ledger = EconomyLedger(path=tmp_path / "economy.json", persist=False)
    dialog = FoodSceneDialog(
        ledger,
        buddy_choices=lambda: [
            {"user_id": "buddy-1", "nickname": "yiliang"},
            {"user_id": "buddy-2", "nickname": "毛毛冲"},
        ],
    )
    app.processEvents()

    people = dialog.findChild(QListWidget, "cakeBuddyChooser")
    assert people is not None
    assert people.count() == 2
    row = people.itemWidget(people.item(0))
    assert isinstance(row, QFrame)
    check = row.findChild(QCheckBox, "cakeBuddyCheck")
    assert check is not None

    check.setChecked(True)
    app.processEvents()
    assert check.isChecked()
    assert row.property("selected") is True

    check.setChecked(False)
    app.processEvents()
    assert not check.isChecked()
    assert row.property("selected") is False

    dialog.close()
    dialog.deleteLater()
    app.processEvents()
