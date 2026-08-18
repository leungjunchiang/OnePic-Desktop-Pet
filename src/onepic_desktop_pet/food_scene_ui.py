"""六毛喂食场景选择器：食物是场景入口，不是饱食度面板。"""

from __future__ import annotations

from typing import Any, Iterable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .economy import EconomyLedger, ITEM_CATALOG


class FoodSceneDialog(QDialog):
    scene_requested = Signal(str, int, str, str)

    def __init__(
        self,
        ledger: EconomyLedger,
        todo_choices: Iterable[dict[str, Any]] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.ledger = ledger
        self.todo_choices = [dict(item) for item in todo_choices if isinstance(item, dict)]
        self.setWindowTitle("给六毛来点什么？")
        self.setModal(False)
        self.setMinimumWidth(520)
        self.setStyleSheet(
            "QDialog { background:#edf4f7; }"
            "QFrame#sceneCard { background:#ffffff; border:1px solid #cbdde4; border-radius:14px; }"
            "QLabel#title { color:#203847; font-size:18px; font-weight:700; }"
            "QLabel#desc { color:#5f7783; }"
            "QPushButton { background:#d7ece8; color:#204c4a; border:0; border-radius:9px; padding:8px 12px; }"
            "QPushButton:hover { background:#c2e2dd; }"
            "QPushButton:disabled { background:#e8eef0; color:#91a1a8; }"
            "QComboBox { background:#ffffff; border:1px solid #b9c8d0; border-radius:8px; padding:5px; }"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 18)
        root.addWidget(QLabel("食物不是喂养指标；每一张卡都会启动一段六毛生活。"))
        for key in ("coffee", "expensive_coffee", "milk_tea", "cake", "tea"):
            root.addWidget(self._card(key))

    def _todo_box(self, *, completed: bool = False) -> QComboBox:
        box = QComboBox()
        if not completed:
            box.addItem("无任务开工", "")
        choices = self.todo_choices
        if completed:
            choices = [item for item in choices if item.get("completed")]
        for item in choices:
            title = str(item.get("title") or "").strip()
            if title:
                box.addItem(title[:80], str(item.get("id") or ""))
        return box

    def _card(self, key: str) -> QWidget:
        spec = ITEM_CATALOG[key]
        card = QFrame()
        card.setObjectName("sceneCard")
        layout = QVBoxLayout(card)
        title = QLabel(f"{spec['name']}  × {self.ledger.inventory_count(key)}")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(QLabel(str(spec["description"])))
        controls = QHBoxLayout()
        combo: QComboBox | None = None
        if key in {"coffee", "expensive_coffee"}:
            combo = self._todo_box()
            controls.addWidget(combo, 1)
        elif key == "milk_tea":
            combo = QComboBox()
            combo.addItem("休息 10 分钟", 10)
            combo.addItem("休息 15 分钟", 15)
            controls.addWidget(combo, 1)
        elif key == "cake":
            combo = self._todo_box(completed=True)
            controls.addWidget(combo, 1)
        button = QPushButton({
            "coffee": "喝杯咖啡开工",
            "expensive_coffee": "今天喝贵的",
            "milk_tea": "喝杯奶茶",
            "cake": "庆祝一下",
            "tea": "喝会儿茶",
        }[key])
        button.setEnabled(self.ledger.inventory_count(key) > 0)
        button.setToolTip("库存不足时请先去六毛钱袋购买。")
        controls.addWidget(button)
        layout.addLayout(controls)
        button.clicked.connect(lambda _checked=False, item_key=key, selector=combo: self._request(item_key, selector))
        return card

    def _request(self, key: str, selector: QComboBox | None) -> None:
        if self.ledger.inventory_count(key) <= 0:
            QMessageBox.information(self, "六毛钱袋", "仓库里没有这件东西，先去六毛钱袋买一点吧。")
            return
        duration = 0
        todo_id = ""
        todo_title = ""
        if key in {"coffee", "expensive_coffee"} and selector is not None:
            todo_id = str(selector.currentData() or "")
            todo_title = str(selector.currentText() or "") if todo_id else ""
        elif key == "milk_tea" and selector is not None:
            duration = int(selector.currentData() or 10)
        elif key == "cake" and selector is not None:
            todo_id = str(selector.currentData() or "")
            todo_title = str(selector.currentText() or "") if todo_id else ""
        self.scene_requested.emit(key, duration, todo_id, todo_title)
        self.accept()
