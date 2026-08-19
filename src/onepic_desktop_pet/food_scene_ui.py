"""六毛补给站：食物是场景入口，仓库、商店和账本共用一个经济核心。"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .economy import ACTIVE_HOUSEHOLD_KEYS, CATEGORY_LABELS, EconomyLedger, ITEM_CATALOG


class FoodSceneDialog(QDialog):
    """Reusable 六毛补给站 window; no fullness, hunger or parallel timer."""

    scene_requested = Signal(str, int, str, str)
    _SCENE_ORDER = ("coffee", "expensive_coffee", "milk_tea", "cake", "tea")
    _SCENE_BUTTONS = {
        "coffee": "喝杯咖啡开工",
        "expensive_coffee": "今天喝贵的",
        "milk_tea": "喝杯奶茶",
        "cake": "庆祝一下",
        "tea": "喝会儿茶",
    }

    def __init__(
        self,
        ledger: EconomyLedger,
        todo_choices: Iterable[dict[str, Any]] = (),
        parent: QWidget | None = None,
        *,
        witness_choices: Callable[[], Iterable[dict[str, Any]]] | None = None,
        achievement_submitter: Callable[[str, str, list[str]], Any] | None = None,
    ) -> None:
        super().__init__(None)
        del parent
        self.ledger = ledger
        self.todo_choices = [dict(item) for item in todo_choices if isinstance(item, dict)]
        self.witness_choices = witness_choices
        self.achievement_submitter = achievement_submitter
        self._remote_achievement_claims: list[dict[str, Any]] = []
        self.setObjectName("foodSupplyStation")
        self.setWindowTitle("给六毛来点什么？ · 六毛补给站")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.resize(760, 720)
        self.setMinimumSize(620, 560)
        self.setStyleSheet(
            "QDialog { background:#edf4f7; }"
            "QLabel#heading { color:#203847; font-size:24px; font-weight:700; }"
            "QLabel#subtitle { color:#5f7783; font-size:14px; }"
            "QLabel#balance { color:#0b756f; font-size:18px; font-weight:700; }"
            "QFrame#sceneCard { background:#ffffff; border:1px solid #cbdde4; border-radius:14px; }"
            "QLabel#cardTitle { color:#203847; font-size:18px; font-weight:700; }"
            "QLabel#rules { color:#557681; font-size:12px; background:#f3f8f8; border-radius:7px; padding:6px 8px; }"
            "QLabel#source { color:#557681; font-size:12px; background:#f3f8f8; border-radius:7px; padding:4px 7px; }"
            "QPushButton { background:#d7ece8; color:#204c4a; border:0; border-radius:9px; padding:8px 12px; }"
            "QPushButton:hover { background:#c2e2dd; }"
            "QPushButton:disabled { background:#e8eef0; color:#91a1a8; }"
            "QComboBox { background:#ffffff; border:1px solid #b9c8d0; border-radius:8px; padding:5px; }"
            "QTabWidget::pane { background:#ffffff; border:1px solid #cbdde4; border-radius:12px; }"
            "QTabBar::tab { background:#dcecef; color:#426471; padding:9px 16px; margin-right:3px; border-radius:8px; }"
            "QTabBar::tab:selected { background:#8ed1c5; color:#124d4a; font-weight:700; }"
            "QListWidget { background:#ffffff; border:0; border-radius:10px; }"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 18)
        root.setSpacing(10)
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        heading = QLabel("六毛补给站")
        heading.setObjectName("heading")
        title_box.addWidget(heading)
        subtitle = QLabel("食物不是喂养指标；每一张卡都会启动一段六毛生活。")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(subtitle)
        currency_hint = QLabel(
            "吉他拨片规则（当前标准）：\n"
            "• 有效专注工资：每小时 6 个；每天最多计薪 8 小时，即每天最多 48 个。\n"
            "• 早鸟补贴：首次有效工作在 10:00 前开始并达到 20 分钟，送昂贵咖啡 ×1，不额外发拨片。\n"
            "• 完成 Todo：任务绩效 +2 个；小蛋糕想庆祝就使用，不绑定重要 Todo。\n"
            "• 成果见证：手动邀请两名不同搭子确认后固定奖励 200 个，每月最多成功 3 次、发起 4 次。\n"
            "• 咖啡壶：144 个拨片；买下后每天补给普通咖啡 ×1，每天最多一杯。"
        )
        currency_hint.setObjectName("rules")
        currency_hint.setWordWrap(True)
        title_box.addWidget(currency_hint)
        header.addLayout(title_box, 1)
        money_box = QVBoxLayout()
        self.balance_label = QLabel()
        self.balance_label.setObjectName("balance")
        self.today_label = QLabel()
        self.today_label.setObjectName("subtitle")
        self.today_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        money_box.addWidget(self.balance_label, 0, Qt.AlignmentFlag.AlignRight)
        money_box.addWidget(self.today_label, 0, Qt.AlignmentFlag.AlignRight)
        header.addLayout(money_box)
        root.addLayout(header)
        self.tabs = QTabWidget(self)
        self._build_today_tab()
        self._build_inventory_tab()
        self._build_shop_tab()
        self._build_achievement_tab()
        self._build_ledger_tab()
        root.addWidget(self.tabs, 1)
        self.refresh()

    @staticmethod
    def _clear_layout(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _build_today_tab(self) -> None:
        page = QWidget()
        outer = QVBoxLayout(page)
        scroll = QScrollArea(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.today_container = QWidget(scroll)
        self.today_layout = QVBoxLayout(self.today_container)
        self.today_layout.setContentsMargins(4, 4, 4, 4)
        self.today_layout.setSpacing(10)
        scroll.setWidget(self.today_container)
        outer.addWidget(scroll)
        self.tabs.addTab(page, "今日补给")

    def _build_inventory_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("先买下来，以后想用再用；食物不会过期，家当会一直留在六毛的生活里。"))
        self.inventory_list = QListWidget(page)
        layout.addWidget(self.inventory_list, 1)
        self.tabs.addTab(page, "我的仓库")

    def _build_shop_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        row.addWidget(QLabel("吉他拨片商店"))
        self.shop_group = QComboBox(page)
        self.shop_group.addItems(("吃点喝点", "添置家当"))
        self.shop_group.currentTextChanged.connect(self.refresh_shop)
        row.addWidget(self.shop_group)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(QLabel("价格统一从六毛钱袋读取；这里不展示皮肤或娃衣。"))
        self.shop_list = QListWidget(page)
        layout.addWidget(self.shop_list, 1)
        self.tabs.addTab(page, "吉他拨片商店")

    def _build_ledger_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        row.addWidget(QLabel("收支记录"))
        self.ledger_filter = QComboBox(page)
        self.ledger_filter.addItems(("全部", "收入", "支出", "工资", "成果", "消费", "搭子互动", "特殊奖励"))
        self.ledger_filter.currentTextChanged.connect(self.refresh_ledger)
        row.addWidget(self.ledger_filter)
        row.addStretch(1)
        layout.addLayout(row)
        self.ledger_list = QListWidget(page)
        layout.addWidget(self.ledger_list, 1)
        self.tabs.addTab(page, "收支记录")

    def _build_achievement_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("把现实里做成的一件事记下来，手动邀请两位搭子帮你见证。两人同意后固定获得 200 吉他拨片。"))
        button = QPushButton("＋ 登记成果")
        button.clicked.connect(self._register_achievement)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        self.achievement_list = QListWidget(page)
        layout.addWidget(self.achievement_list, 1)
        self.tabs.addTab(page, "成果见证")

    def _todo_box(self, *, completed: bool = False) -> QComboBox:
        box = QComboBox()
        if not completed:
            box.addItem("无任务开工", "")
        for item in self.todo_choices:
            if completed and not item.get("completed"):
                continue
            title = str(item.get("title") or "").strip()
            if title:
                box.addItem(title[:80], str(item.get("id") or ""))
        return box

    def _source_text(self, key: str) -> str:
        status = self.ledger.daily_supply_status().get(key) or {}
        if key == "coffee" and status.get("coffee_pot_enabled"):
            text = str(status.get("coffee_pot_rule") or "咖啡壶买下后每天补给普通咖啡 ×1")
            marks = []
            if status.get("claimed"):
                marks.append("开工补给已领取")
            if status.get("coffee_pot_claimed"):
                marks.append("咖啡壶今日已补给")
            return text + (f" · {' · '.join(marks)} ✓" if marks else "")
        text = str(status.get("rule") or "可在吉他拨片商店购买")
        return f"{text} · 今日已获得 ✓" if status.get("claimed") else text

    def _card(self, key: str) -> QWidget:
        spec = ITEM_CATALOG[key]
        card = QFrame()
        card.setObjectName("sceneCard")
        layout = QVBoxLayout(card)
        title = QLabel(f"{spec['name']}  × {self.ledger.inventory_count(key)}")
        title.setObjectName("cardTitle")
        layout.addWidget(title)
        layout.addWidget(QLabel(str(spec["description"])))
        source = QLabel(f"获取方式：{self._source_text(key)}")
        source.setObjectName("source")
        source.setWordWrap(True)
        layout.addWidget(source)
        controls = QHBoxLayout()
        selector: QComboBox | None = None
        if key == "milk_tea":
            selector = QComboBox()
            selector.addItem("休息 10 分钟", 10)
            selector.addItem("休息 15 分钟", 15)
            controls.addWidget(selector, 1)
        else:
            controls.addStretch(1)
        button = QPushButton(self._SCENE_BUTTONS[key])
        button.setEnabled(self.ledger.inventory_count(key) > 0)
        button.setToolTip("库存不足时请切换到“吉他拨片商店”购买。")
        button.clicked.connect(lambda _checked=False, item_key=key, box=selector: self._request(item_key, box))
        controls.addWidget(button)
        layout.addLayout(controls)
        return card

    def refresh_today(self) -> None:
        self._clear_layout(self.today_layout)
        self.today_layout.addWidget(QLabel("今天怎么生活，六毛就怎么获得补给。免费补给和购买库存可以同时存在。"))
        for key in self._SCENE_ORDER:
            self.today_layout.addWidget(self._card(key))
        self.today_layout.addStretch(1)

    @staticmethod
    def _row_widget(text: str, button: QPushButton | None = None) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(8, 6, 8, 6)
        label = QLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label, 1)
        if button is not None:
            layout.addWidget(button)
        return widget

    def refresh_inventory(self) -> None:
        self.inventory_list.clear()
        for row in self.ledger.inventory_rows():
            quantity = int(row["quantity"])
            if row["kind"] == "household":
                text = f"{row['name']} · 已添置\n{row['description']}"
                button = None
            else:
                text = f"{row['name']} × {quantity}\n{row['description']}"
                button = QPushButton("使用")
                button.setEnabled(quantity > 0)
                button.clicked.connect(lambda _checked=False, key=row["item_key"]: self._use_from_inventory(key))
            widget = self._row_widget(text, button)
            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())
            self.inventory_list.addItem(item)
            self.inventory_list.setItemWidget(item, widget)
        if not self.inventory_list.count():
            self.inventory_list.addItem("仓库还是空的，先认真开一会儿工吧。")

    def refresh_shop(self) -> None:
        self.shop_list.clear()
        group = self.shop_group.currentText()
        for key, spec in ITEM_CATALOG.items():
            if spec["group"] != group:
                continue
            if spec["kind"] == "household" and key not in ACTIVE_HOUSEHOLD_KEYS:
                continue
            owned = self.ledger.has_household(key)
            quantity = self.ledger.inventory_count(key) if spec["kind"] == "consumable" else int(owned)
            label = "已添置" if owned else f"购买 {spec['price']} 吉他拨片"
            button = QPushButton(label)
            button.setEnabled(not owned)
            button.clicked.connect(lambda _checked=False, item_key=key: self._purchase(item_key))
            text = f"{spec['name']}" + (f" × {quantity}" if spec["kind"] == "consumable" and quantity else "") + f"\n{spec['description']}"
            widget = self._row_widget(text, button)
            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())
            self.shop_list.addItem(item)
            self.shop_list.setItemWidget(item, widget)

    def refresh_ledger(self) -> None:
        self.ledger_list.clear()
        for event in self.ledger.ledger_events(self.ledger_filter.currentText()):
            amount = int(event.amount)
            sign = "+" if amount > 0 else ""
            stamp = event.created_at[11:16] if len(event.created_at) >= 16 else ""
            category = CATEGORY_LABELS.get(event.category, event.category)
            self.ledger_list.addItem(f"{event.occurred_on} {stamp} · {category}\n{event.label}  {sign}{amount} 吉他拨片")
        if not self.ledger_list.count():
            self.ledger_list.addItem("还没有收支记录。先认真开工，六毛会把每一笔记下来。")

    def refresh_achievements(self) -> None:
        if not hasattr(self, "achievement_list"):
            return
        self.achievement_list.clear()
        pending = list(self.ledger.pending_achievements()) + list(self._remote_achievement_claims)
        if pending:
            for item in pending:
                witnesses = len(item.get("witnesses") or []) if isinstance(item, dict) else 0
                self.achievement_list.addItem(
                    f"{item.get('name') or '未命名成果'}\n"
                    f"等待搭子见证 · {witnesses}/2 · 通过后固定 +200 吉他拨片"
                )
        else:
            self.achievement_list.addItem("还没有待见证成果。完成一件现实里的事情，就来这里记一笔。")

    def refresh(self) -> None:
        self.ledger.ensure_daily_household_supply()
        report = self.ledger.month_report()
        today = self.ledger.today_summary()
        self.balance_label.setText(f"🎸 {self.ledger.balance} 吉他拨片")
        self.today_label.setText(f"今日收入 +{today['income']} · 本月创收 {report['income']}")
        self.refresh_today()
        self.refresh_inventory()
        self.refresh_shop()
        self.refresh_achievements()
        self.refresh_ledger()

    def _purchase(self, item_key: str) -> None:
        spec = ITEM_CATALOG.get(item_key) or {}
        if self.ledger.balance < int(spec.get("price") or 0):
            QMessageBox.information(self, "钱袋有点瘪", "哥们，吉他拨片不够，先去认真开一会儿工吧。")
            return
        event = self.ledger.purchase_item(item_key)
        if event is None:
            QMessageBox.information(self, "暂时不能购买", "这件家当已经添置过，或购买没有成功。")
            return
        self.refresh()

    def _use_from_inventory(self, item_key: str) -> None:
        if item_key not in ITEM_CATALOG or ITEM_CATALOG[item_key]["kind"] != "consumable":
            return
        # Do not reject based on a stale button snapshot. The economy core
        # resolves legacy/canonical inventory aliases atomically and the main
        # window returns a precise reason if another window changed the state.
        self._request(item_key, None)

    def _request(self, key: str, selector: QComboBox | None) -> None:
        duration = 0
        todo_id = ""
        todo_title = ""
        if key == "milk_tea":
            duration = int(selector.currentData() if selector is not None else 10)
        self.scene_requested.emit(key, duration, todo_id, todo_title)
        self.hide()

    def _register_achievement(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "登记成果", "成果名称：")
        if not ok or not name.strip():
            return
        note, ok = QInputDialog.getText(self, "登记成果", "备注（可选）：")
        if not ok:
            return
        selected_ids: list[str] = []
        selected_names: list[str] = []
        if self.witness_choices is not None:
            choices = [dict(item) for item in (self.witness_choices() or ()) if isinstance(item, dict)]
            choices = [item for item in choices if str(item.get("user_id") or item.get("id") or "").strip()]
            if len(choices) < 2:
                QMessageBox.information(self, "暂时不能登记", "成果奖励需要 2 名已经建立搭子关系的好友共同见证。")
                return
            for slot in range(2):
                labels = [str(item.get("nickname") or item.get("owner_nickname") or item.get("user_id"))[:60] for item in choices if str(item.get("user_id") or item.get("id")) not in selected_ids]
                picked, ok = QInputDialog.getItem(
                    self, "选择成果见证人", f"选择第 {slot + 1} 位搭子：", labels, 0, False
                )
                if not ok:
                    return
                chosen = next((item for item in choices if str(item.get("nickname") or item.get("owner_nickname") or item.get("user_id"))[:60] == picked and str(item.get("user_id") or item.get("id")) not in selected_ids), None)
                if chosen is None:
                    return
                selected_ids.append(str(chosen.get("user_id") or chosen.get("id")))
                selected_names.append(str(chosen.get("nickname") or chosen.get("owner_nickname") or "搭子")[:60])
        try:
            if self.achievement_submitter is not None:
                pending = self.achievement_submitter(str(name).strip(), str(note).strip(), selected_ids)
            else:
                pending = self.ledger.register_achievement_income(
                    "其他成果", name, note=note, witness_ids=selected_ids, witness_names=selected_names
                )
        except Exception as exc:
            QMessageBox.warning(self, "暂时不能登记", str(exc))
            return
        if pending is None:
            QMessageBox.information(self, "暂时不能登记", "同月同名成果不能重复提交。")
            return
        if self.achievement_submitter is not None and isinstance(pending, dict):
            pending = dict(pending)
            pending.update({
                "name": str(name).strip(),
                "kind": "其他成果",
                "note": str(note).strip(),
                "amount": 200,
                "witness_slots": [
                    {"id": value, "name": selected_names[index], "status": "pending"}
                    for index, value in enumerate(selected_ids)
                ],
            })
            self._remote_achievement_claims.append(pending)
        self.refresh()
        if isinstance(pending, dict) and pending.get("status") == "submission_limit":
            QMessageBox.information(self, "本月申请已达上限", "本月最多发起 4 次成果见证申请，下个月再来。")
            return
        QMessageBox.information(self, "等待搭子见证", "已向两名指定搭子发送见证申请；两人同意后固定获得 200 吉他拨片，确认前不会入账。")

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()
