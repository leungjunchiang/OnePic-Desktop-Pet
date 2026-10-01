"""搭子互动收件箱：内容驱动的小卡片，未读与待回应分开，回应复用原业务入口。"""
from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
from .time_service import now_beijing, parse_server_datetime, format_clock
from .ui_feedback import decorate_buttons

LABELS = {
    "visit":"🏠 来串了个门", "cheer":"💪 给你加了个油", "praise":"✨ 夸了夸你",
    "tease":"😈 嘲讽了你一下", "flower":"🌸 送你一朵小红花", "knock":"👊 敲了敲你的桌子",
    "poke":"👊 拍了拍你", "start":"⏰ 提醒你开工", "return":"⏰ 喊你回来专注",
    "finish":"🌙 提醒你下班", "approve_finish":"🌙 批准你下班",
    "food_coffee":"☕ 投喂了咖啡，邀请一起开工", "food_milk_tea":"🥤 投喂了奶茶，邀请一起休息",
    "food_tea":"🍵 敬了一杯茶", "food_cake":"🍰 投喂了蛋糕", "food_cake_share":"🍰 邀请一起吃蛋糕",
    "rest":"☕ 提醒你休息有点久了", "take_break":"☕ 提醒你休息一下",
    "buddy_request":"👥 申请成为你的搭子", "ask":"📝 想问问你怎么回事",
    "explain":"📝 提醒你处理说明", "progress":"📋 提醒你看看进度", "rest_more":"☕ 让你再歇会儿",
    "buddy_outgoing":"👥 等待对方回应你的搭子申请",
}

def response_actions(row):
    kind = str(row.get("event_type") or "")
    if kind == "buddy_outgoing": return [("撤回申请","cancel_request")] if (row.get("payload") or {}).get("status")=="pending" else []
    if row.get("requires_action"):
        return [("去处理", "handle")]
    if kind == "coaching_action":
        return [("去看看", "handle")]
    if kind == "cheer": return [("回个加油", "cheer")]
    if kind.startswith("food_"): return [("回请咖啡", "food")]
    if kind == "tease": return [("嘲讽回去", "taunt")]
    if kind == "visit": return [("串回去", "visit")]
    if kind == "flower": return [("谢谢", "ack"), ("回一朵", "flower")]
    if kind in {"start", "return", "knock", "poke"}: return [("知道了", "ack"), ("去开工", "focus")]
    return [("知道了", "ack")]

def grouped_rows(rows, at=None):
    today = (at or now_beijing()).date()
    groups = {"待我回应":[], "今天":[], "更早":[]}
    for row in rows[:30]:
        if not isinstance(row, dict): continue
        stamp = parse_server_datetime(row.get("created_at"))
        group = "待我回应" if row.get("requires_action") else "今天" if stamp and stamp.astimezone(now_beijing().tzinfo).date()==today else "更早"
        groups[group].append(row)
    return groups

class InteractionFeed(QWidget):
    def __init__(self, respond, parent=None):
        super().__init__(parent)
        self.respond = respond
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0,0,0,0)
        self.layout.setSpacing(8)
        self.render([])

    def render(self, rows):
        import json
        signature = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str) + now_beijing().date().isoformat()
        if signature == getattr(self,"_signature",None):
            return
        self._signature = signature
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        # 搭子/投喂邀请已由上方原有接受/拒绝卡呈现，避免重复处理入口。
        groups = grouped_rows([r for r in rows if not (r.get("requires_action") and r.get("source") in {"buddy","visit"})])
        if not rows:
            quiet = QLabel("今天还挺安静。暂时没有人来闹你。")
            quiet.setStyleSheet("color:#52675f;padding:10px;")
            self.layout.addWidget(quiet)
            button = QPushButton("去看看谁在学习 →")
            button.clicked.connect(lambda:self.respond({},"home"))
            self.layout.addWidget(button)
        for title, items in groups.items():
            if not items: continue
            heading = QLabel(f"{title} · {len(items)}")
            heading.setStyleSheet("font-weight:650;color:#087f74;padding-top:6px;")
            self.layout.addWidget(heading)
            for row in items:
                card = QFrame()
                card.setObjectName("interactionEntry")
                card.setStyleSheet("QFrame#interactionEntry{background:#f5faf8;border:1px solid #d3e5df;border-radius:9px;}")
                layout = QVBoxLayout(card)
                header = QHBoxLayout()
                name = str(row.get("display_name") or row.get("nickname") or "搭子")
                who = QLabel(("● " if row.get("unread") else "") + name)
                who.setTextFormat(Qt.TextFormat.PlainText)
                header.addWidget(who,1)
                header.addWidget(QLabel(format_clock(row.get("created_at"))))
                layout.addLayout(header)
                kind = str(row.get("event_type") or "")
                payload = row.get("payload") or {}
                state = payload.get("state")
                text = LABELS.get(kind,"给你一个轻提醒")
                if kind == "coaching_action":
                    text = {"pending":"📝 有一条新的训导要求", "rejected":"📝 请重新说明", "explained":"📝 提交了说明，等待你审阅",
                            "active":"📋 已接受补时要求", "completed":"✓ 补时完成，此事项已结案", "forgiven":"✓ 此事项已解除"}.get(state,"📋 训导事项已更新")
                body = QLabel(text + ("\n" + str(payload["message"])[:240] if payload.get("message") else ""))
                body.setTextFormat(Qt.TextFormat.PlainText)
                body.setWordWrap(True)
                layout.addWidget(body)
                actions = QHBoxLayout()
                actions.addStretch()
                for label,action in response_actions(row):
                    button = QPushButton(label)
                    button.clicked.connect(lambda checked=False,r=row,a=action:self.respond(r,a))
                    actions.addWidget(button)
                layout.addLayout(actions)
                self.layout.addWidget(card)
        decorate_buttons(self)
