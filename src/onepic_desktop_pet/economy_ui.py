"""六毛钱袋：钱袋总览、工资条、账本、小卖部、仓库和生活图鉴。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .economy import CATEGORY_LABELS, ITEM_CATALOG, EconomyLedger


class EconomyDialog(QDialog):
    """把经济系统呈现为六毛的生活记录，而不是积分商城。"""

    changed = Signal()

    def __init__(self, ledger: EconomyLedger, parent=None) -> None:
        super().__init__(parent)
        self.ledger = ledger
        self.setWindowTitle("六毛钱袋")
        self.resize(720, 760)
        self.setStyleSheet(
            "QDialog{background:#edf4f7;} QLabel{color:#263746;}"
            "QLabel#muted{color:#607b8a;}"
            "QGroupBox{background:#fff;border:1px solid #d3e0e5;border-radius:14px;"
            "margin-top:10px;padding:12px;font-weight:650;}"
            "QGroupBox::title{subcontrol-origin:margin;left:12px;padding:0 5px;color:#29485a;}"
            "QPushButton{padding:8px 14px;border:0;border-radius:9px;background:#d7ece8;"
            "color:#204c4a;font-weight:600;}"
            "QPushButton:hover{background:#c2e5df;}"
            "QListWidget{background:#fff;border:1px solid #c5d4da;border-radius:10px;padding:6px;}"
            "QTabWidget::pane{border:0;}"
        )

        root = QVBoxLayout(self)
        title = QLabel("六毛钱袋")
        title.setStyleSheet("font-size:28px;font-weight:750;color:#203847;")
        root.addWidget(title)
        subtitle = QLabel("荒野打工生活 · 认真挣工钱，也给六毛过日子")
        subtitle.setObjectName("muted")
        root.addWidget(subtitle)

        self.tabs = QTabWidget()
        self.overview_page = self._build_overview()
        self.payroll_page = self._build_payroll()
        self.ledger_page = self._build_ledger()
        self.shop_page = self._build_shop()
        self.inventory_page = self._build_inventory()
        self.collection_page = self._build_collection()
        for page, label in (
            (self.overview_page, "钱袋"),
            (self.payroll_page, "工资条"),
            (self.ledger_page, "荒野账本"),
            (self.shop_page, "荒野小卖部"),
            (self.inventory_page, "小仓库"),
            (self.collection_page, "生活图鉴"),
        ):
            self.tabs.addTab(page, label)
        root.addWidget(self.tabs, 1)

        close = QPushButton("关闭")
        close.clicked.connect(self.close)
        root.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        self.refresh()

    @staticmethod
    def _card(title: str, subtitle: str = "") -> tuple[QGroupBox, QVBoxLayout]:
        card = QGroupBox(title)
        layout = QVBoxLayout(card)
        if subtitle:
            hint = QLabel(subtitle)
            hint.setObjectName("muted")
            hint.setWordWrap(True)
            layout.addWidget(hint)
        return card, layout

    def _build_overview(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        overview, grid = self._card("钱袋总览", "余额是现在能花的钱；本月创收是本月合法收入累计，消费不会降低创收。")
        self.balance = QLabel()
        self.income = QLabel()
        self.identity = QLabel()
        self.inventory_summary = QLabel()
        self.leaderboard = QLabel("按本月创收参与荒野国王富豪榜；参评状态在搭子自习室设置。")
        for label in (self.balance, self.income, self.identity, self.inventory_summary, self.leaderboard):
            label.setWordWrap(True)
            label.setStyleSheet("font-size:16px;padding:4px;")
        grid.addWidget(self.balance, 0, 0)
        grid.addWidget(self.income, 0, 1)
        grid.addWidget(self.identity, 1, 0)
        grid.addWidget(self.inventory_summary, 1, 1)
        grid.addWidget(self.leaderboard, 2, 0, 1, 2)
        root.addWidget(overview)

        today, today_layout = self._card("今天的六毛生活", "只显示今天发生的收入、消费和道具事件。")
        self.today = QLabel()
        self.today.setWordWrap(True)
        self.today.setStyleSheet("padding:5px;")
        today_layout.addWidget(self.today)
        root.addWidget(today)

        actions, actions_layout = self._card("去做点什么")
        grid = QGridLayout()
        buttons = (
            ("去消费", "shop"),
            ("本月工资条", "payroll"),
            ("荒野账本", "ledger"),
            ("生活图鉴", "collection"),
            ("登记成果 / 外快", "income"),
            ("我的小仓库", "inventory"),
        )
        for index, (label, action) in enumerate(buttons):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, action=action: self._overview_action(action))
            grid.addWidget(button, index // 3, index % 3)
        actions_layout.addLayout(grid)
        root.addWidget(actions)
        root.addStretch()
        return page

    def _build_payroll(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        card, layout = self._card("本月工资条", "实时入账；月末自动留下总结，不会把钱拖到月底才发。")
        self.salary = QLabel()
        self.salary.setWordWrap(True)
        self.salary.setStyleSheet("background:#fffdf4;border:1px solid #eadfb4;border-radius:12px;padding:16px;font-size:16px;")
        layout.addWidget(self.salary)
        self.salary_comment = QLabel()
        self.salary_comment.setWordWrap(True)
        self.salary_comment.setStyleSheet("font-size:17px;font-weight:650;color:#087f74;padding:10px;")
        layout.addWidget(self.salary_comment)
        root.addWidget(card)
        root.addStretch()
        return page

    def _build_ledger(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        card, layout = self._card("荒野账本", "每笔收入、消费和生活事件都来自同一个本地账本。")
        row = QHBoxLayout()
        self.ledger_filter = QComboBox()
        self.ledger_filter.addItems(("全部", "收入", "支出", "工资", "成果", "消费", "搭子互动", "特殊奖励"))
        self.ledger_filter.currentTextChanged.connect(self.refresh_ledger)
        row.addWidget(QLabel("筛选："))
        row.addWidget(self.ledger_filter)
        row.addStretch()
        layout.addLayout(row)
        self.events = QListWidget()
        layout.addWidget(self.events, 1)
        root.addWidget(card, 1)
        return page

    def _build_shop(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        card, layout = self._card("荒野小卖部", "不卖皮肤、不卖娃衣；这里只卖让六毛今天过得更像生活的东西。")
        self.shop_group = QComboBox()
        self.shop_group.addItems(("吃点喝点", "添置家当"))
        self.shop_group.currentTextChanged.connect(self.refresh_shop)
        layout.addWidget(self.shop_group)
        self.shop_list = QListWidget()
        layout.addWidget(self.shop_list, 1)
        root.addWidget(card, 1)
        return page

    def _build_inventory(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        card, layout = self._card("我的小仓库", "先买下来，以后想用再用；家当会一直留在六毛的生活里。")
        self.inventory_list = QListWidget()
        layout.addWidget(self.inventory_list, 1)
        root.addWidget(card, 1)
        return page

    def _build_collection(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        card, layout = self._card("六毛生活图鉴", "记录六毛经历过什么，不会解锁新皮肤，也不修改真实工作数据。")
        self.collection_list = QListWidget()
        layout.addWidget(self.collection_list, 1)
        root.addWidget(card, 1)
        return page

    def _overview_action(self, action: str) -> None:
        if action == "income":
            self._record_income()
            return
        index = {"shop": 3, "payroll": 1, "ledger": 2, "inventory": 4, "collection": 5}.get(action)
        if index is not None:
            self.tabs.setCurrentIndex(index)

    def refresh(self) -> None:
        report = self.ledger.month_report()
        self.balance.setText(f"钱袋余额\n{self.ledger.balance} 毛币")
        self.income.setText(f"本月创收\n{report['income']} 毛币")
        self.identity.setText(f"本月身份\n{report['identity']}")
        inventory = [
            f"{row['name']} × {row['quantity']}"
            for row in self.ledger.inventory_rows()
            if row["kind"] == "consumable" and int(row["quantity"]) > 0
        ]
        self.inventory_summary.setText("库存摘要\n" + ("　".join(inventory[:3]) if inventory else "暂时没有库存"))
        today = self.ledger.today_summary()
        today_events = list(today.get("events") or [])
        if today_events:
            lines = [
                f"今日工资/收入：{today['income']} 毛币　·　今日消费：{today['expenses']} 毛币",
            ]
            for event in today_events[:5]:
                amount = int(event.get("amount") or 0)
                sign = "+" if amount > 0 else ""
                lines.append(f"· {event.get('label') or '生活事件'}　{sign}{amount} 毛币")
            self.today.setText("\n".join(lines))
        else:
            self.today.setText("今天还没有新的六毛生活记录。先认真开一小会儿工吧。")
        self.refresh_payroll()
        self.refresh_ledger()
        self.refresh_shop()
        self.refresh_inventory()
        self.refresh_collection()

    def refresh_payroll(self) -> None:
        report = self.ledger.month_report()
        self.salary.setText(
            f"{report['month']} 年 {int(report['month'].split('-')[1])} 月工资条\n\n"
            f"基础工资　　　　　　　　 +{report['salary']} 毛币\n"
            f"早鸟补贴（免费咖啡）　　 {report['early_bird_count']} 次\n"
            f"成果 / 外快　　　　　　　+{report['windfall']} 毛币\n"
            f"任务绩效　　　　　　　　 +{report['performance']} 毛币\n"
            f"搭子互动 / 特殊奖励　　　+{report['social_reward'] + report['special_reward']} 毛币\n"
            "────────────────────\n"
            f"本月创收　　　　　　　　 {report['income']} 毛币\n"
            f"本月花费　　　　　　　　-{report['expenses']} 毛币\n"
            f"当前钱袋　　　　　　　　 {report['balance']} 毛币"
        )
        self.salary_comment.setText(f"六毛说：{self.ledger.salary_comment()}")

    def refresh_ledger(self) -> None:
        if not hasattr(self, "events"):
            return
        self.events.clear()
        for event in self.ledger.ledger_events(self.ledger_filter.currentText()):
            amount = int(event.amount)
            sign = "+" if amount > 0 else ""
            stamp = event.created_at[11:16] if len(event.created_at) >= 16 else ""
            category = CATEGORY_LABELS.get(event.category, event.category)
            self.events.addItem(
                f"{event.occurred_on} {stamp}　{category}\n"
                f"{event.label}　{sign}{amount} 毛币"
            )
        if not self.events.count():
            self.events.addItem("这类账目还没有记录。")

    @staticmethod
    def _row_widget(text: str, button: QPushButton | None = None) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(6, 4, 6, 4)
        label = QLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label, 1)
        if button is not None:
            layout.addWidget(button)
        return widget

    def refresh_shop(self) -> None:
        if not hasattr(self, "shop_list"):
            return
        self.shop_list.clear()
        group = self.shop_group.currentText()
        for key, spec in ITEM_CATALOG.items():
            if spec["group"] != group:
                continue
            owned = self.ledger.has_household(key)
            button = QPushButton("已添置" if owned else f"购买 {spec['price']} 毛币")
            button.setEnabled(not owned)
            button.clicked.connect(lambda _checked=False, key=key: self._purchase(key))
            text = f"{spec['name']}\n{spec['description']}"
            item = QListWidgetItem(self.shop_list)
            item.setSizeHint(self._row_widget(text, button).sizeHint())
            self.shop_list.addItem(item)
            self.shop_list.setItemWidget(item, self._row_widget(text, button))

    def refresh_inventory(self) -> None:
        if not hasattr(self, "inventory_list"):
            return
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
                button.clicked.connect(lambda _checked=False, key=row["item_key"]: self._use(key))
            widget = self._row_widget(text, button)
            item = QListWidgetItem(self.inventory_list)
            item.setSizeHint(widget.sizeHint())
            self.inventory_list.addItem(item)
            self.inventory_list.setItemWidget(item, widget)
        if not self.inventory_list.count():
            self.inventory_list.addItem("仓库还是空的，去荒野小卖部添一点东西吧。")

    def refresh_collection(self) -> None:
        if not hasattr(self, "collection_list"):
            return
        self.collection_list.clear()
        names = {
            "coffee": "普通咖啡",
            "expensive_coffee": "昂贵咖啡",
            "milk_tea": "奶茶休息",
            "cake": "小蛋糕庆祝",
            "early_bird": "早鸟开工",
            "gift_sent": "请搭子喝咖啡",
            "gift_received": "收到搭子咖啡",
        }
        collection = self.ledger.life_collection()
        for key, label in names.items():
            self.collection_list.addItem(f"{label}　{int(collection.get(key, 0))} 次")
        self.collection_list.addItem("")
        self.collection_list.addItem("长期称号")
        for title in self.ledger.titles:
            self.collection_list.addItem(f"「{title}」")
        if not self.ledger.titles:
            self.collection_list.addItem("完成真实生活事件后，称号会慢慢留下来。")

    def _record_income(self) -> None:
        kinds = ("论文 / 稿费", "项目", "比赛", "作品", "其他成果")
        kind, ok = QInputDialog.getItem(self, "登记成果 / 外快", "类型：", kinds, 0, False)
        if not ok:
            return
        name, ok = QInputDialog.getText(self, "登记成果 / 外快", "名称：")
        if not ok or not name.strip():
            return
        amount, ok = QInputDialog.getInt(self, "登记成果 / 外快", "折算毛币：", 20, 1, 100000)
        if not ok:
            return
        note, ok = QInputDialog.getText(self, "登记成果 / 外快", "备注（可选）：")
        if not ok:
            return
        if QMessageBox.question(
            self, "确认登记",
            f"确认登记“{name.strip()}”并增加 {amount} 毛币吗？\n这会进入本月创收和荒野账本。",
        ) != QMessageBox.StandardButton.Yes:
            return
        event = self.ledger.register_achievement_income(kind, name, amount, note)
        if event is None:
            QMessageBox.warning(self, "登记失败", "这笔成果没有成功记入账本。")
            return
        self.refresh()
        self.changed.emit()

    def _purchase(self, item_key: str) -> None:
        spec = ITEM_CATALOG.get(item_key) or {}
        if self.ledger.balance < int(spec.get("price") or 0):
            QMessageBox.information(self, "钱袋有点瘪", "哥们，毛币不够，先去认真开一会儿工吧。")
            return
        event = self.ledger.purchase_item(item_key)
        if event is None:
            QMessageBox.information(self, "暂时不能购买", "这件家当已经添置过，或购买没有成功。")
            return
        self.refresh()
        self.changed.emit()

    def _use(self, item_key: str) -> None:
        result = self.ledger.use_item(item_key)
        if result is None:
            QMessageBox.information(self, "仓库里没有", "先去荒野小卖部买一个，再回来使用吧。")
            return
        self.refresh()
        self.changed.emit()
        QMessageBox.information(self, "六毛生活记录", str(result.get("feedback") or "六毛把这件事记下来了。"))
