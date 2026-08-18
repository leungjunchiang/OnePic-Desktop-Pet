"""Small local wallet and month-end salary slip window."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QLabel, QInputDialog, QListWidget, QMessageBox, QPushButton, QVBoxLayout

from .economy import EconomyLedger


class EconomyDialog(QDialog):
    """Expose the wallet without turning the desktop pet into a game store."""

    changed = Signal()

    def __init__(self, ledger: EconomyLedger, parent=None) -> None:
        super().__init__(parent)
        self.ledger = ledger
        self.setWindowTitle("六毛钱包与工资条")
        self.resize(500, 520)
        self.setStyleSheet(
            "QDialog{background:#edf4f7;} QLabel{color:#263746;} "
            "QPushButton{padding:8px 14px;border:0;border-radius:9px;background:#d7ece8;color:#204c4a;font-weight:600;} "
            "QListWidget{background:#fff;border:1px solid #c5d4da;border-radius:10px;padding:6px;}"
        )
        root = QVBoxLayout(self)
        title = QLabel("六毛钱包")
        title.setStyleSheet("font-size:24px;font-weight:700;color:#203847;")
        root.addWidget(title)
        self.balance = QLabel()
        self.balance.setStyleSheet("font-size:22px;font-weight:700;color:#087f74;")
        root.addWidget(self.balance)
        self.salary = QLabel()
        self.salary.setWordWrap(True)
        self.salary.setStyleSheet("background:#fff;border:1px solid #d6e1e6;border-radius:12px;padding:12px;")
        root.addWidget(self.salary)
        actions = QHBoxLayout()
        income = QPushButton("登记稿费 / 成果")
        income.clicked.connect(self._record_income)
        spend = QPushButton("消费")
        spend.clicked.connect(self._spend)
        actions.addWidget(income); actions.addWidget(spend)
        root.addLayout(actions)
        self.events = QListWidget()
        root.addWidget(QLabel("最近账目"))
        root.addWidget(self.events, 1)
        close = QPushButton("关闭")
        close.clicked.connect(self.close)
        root.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        self.refresh()

    def refresh(self) -> None:
        report = self.ledger.month_report()
        self.balance.setText(f"余额：{self.ledger.balance} 毛币　·　昂贵咖啡 × {self.ledger.inventory.get('昂贵咖啡', 0)}")
        self.salary.setText(
            f"{report['month']} 月工资条\n"
            f"基本工资　{report['salary']}\n"
            f"早鸟补贴　{report['early_bird']}（奖励咖啡不计现金）\n"
            f"稿费 / 偶然所得　{report['windfall']}\n"
            f"本月收入　{report['income']}　·　消费　{report['expenses']}\n"
            f"实发净额　{report['net']} 毛币"
        )
        self.events.clear()
        for event in self.ledger.recent_events():
            sign = "+" if event.amount > 0 else ""
            self.events.addItem(f"{event.occurred_on}　{event.label}　{sign}{event.amount} 毛币")
        if not self.events.count():
            self.events.addItem("还没有账目。先完成一轮真实专注吧。")

    def _record_income(self) -> None:
        label, ok = QInputDialog.getText(self, "登记稿费 / 成果", "成果名称：", text="论文录用")
        if not ok or not label.strip():
            return
        amount, ok = QInputDialog.getInt(self, "登记收入", "入账毛币：", 300, 1, 1_000_000)
        if not ok:
            return
        if self.ledger.record_income(label, amount) is None:
            QMessageBox.warning(self, "没有记账", "这笔收入没有成功登记。")
            return
        self.refresh(); self.changed.emit()

    def _spend(self) -> None:
        choices = (("昂贵咖啡", 36, "昂贵咖啡"), ("奶茶", 18, "奶茶"), ("桌面小物", 80, "桌面小物"))
        labels = [f"{label}（{amount} 毛币）" for label, amount, _ in choices]
        choice, ok = QInputDialog.getItem(self, "六毛商店", "选择消费：", labels, 0, False)
        if not ok:
            return
        index = labels.index(choice)
        label, amount, item_key = choices[index]
        if self.ledger.spend(label, amount, item_key=item_key) is None:
            QMessageBox.information(self, "余额不足", "先完成专注或登记一笔稿费，再来买东西吧。")
            return
        self.refresh(); self.changed.emit()

