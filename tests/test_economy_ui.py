from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from onepic_desktop_pet.economy import EconomyLedger
from onepic_desktop_pet.economy_ui import EconomyDialog


def test_shop_separates_inventory_price_and_purchase_action(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    dialog = EconomyDialog(
        EconomyLedger(tmp_path / "economy.json", persist=False),
    )
    dialog.shop_group.setCurrentText("吃点喝点")
    dialog.refresh_shop()
    app.processEvents()

    assert any(
        label.text() == "商品价格使用吉他拨片结算；右侧显示单价，商品名旁显示当前库存。"
        for label in dialog.findChildren(QLabel)
    )

    coffee_row = dialog.shop_list.itemWidget(dialog.shop_list.item(0))
    assert coffee_row is not None
    labels = [label.text() for label in coffee_row.findChildren(QLabel)]
    assert "普通咖啡" in labels
    assert "库存 ×0" in labels
    assert "单价：12 吉他拨片" in labels

    buttons = coffee_row.findChildren(QPushButton)
    assert [button.text() for button in buttons] == ["购买"]

    dialog.close()
    dialog.deleteLater()
    app.processEvents()
