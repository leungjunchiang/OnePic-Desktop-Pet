"""搭子互动收件箱：北京日期 Feed、静默消息首次处理、回应按钮生命周期与四秒即时反馈。"""
from __future__ import annotations
from datetime import timedelta
from time import monotonic
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
from .time_service import now_beijing, parse_server_datetime, format_clock, to_beijing
from .ui_feedback import decorate_buttons, begin_button_work, end_button_work

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

INTERACTION_HINT_DURATION_MS = 4000
HINT_LABELS = {"return":"👀 该回来了", "cheer":"💪 加油", "praise":"✨ 夸一下",
               "tease":"😈 嘲讽了一下", "taunt":"😈 嘲讽了一下", "knock":"👊 敲桌子",
               "poke":"👊 拍了拍你", "visit":"🏠 来串门了", "flower":"🌸 小红花",
               "start":"⏰ 提醒开工", "finish":"🌙 提醒下班", "approve_finish":"🌙 批准下班",
               "rest":"☕ 休息有点久啦", "take_break":"☕ 歇一会儿", "rest_more":"☕ 再歇会儿",
               "progress":"📋 看看进度", "ask":"📝 搭子问问你", "drink":"🥤 奶茶",
               "food_coffee":"☕ 投喂咖啡", "food_milk_tea":"🥤 投喂奶茶", "food_tea":"🍵 敬茶",
               "food_cake":"🍰 投喂蛋糕", "food_cake_share":"🍰 一起吃蛋糕"}


class InteractionHintState:
    """只存本运行周期的提示计数；沿用六毛现有单次 speech_timer 隐藏。"""
    def __init__(self):
        self.kind = None
        self.count = 0
        self.until = 0.0

    def text(self, kind, label=None, *, at=None):
        now = monotonic() if at is None else at
        self.count = self.count + 1 if kind == self.kind and now < self.until else 1
        self.kind = kind
        self.until = now + INTERACTION_HINT_DURATION_MS / 1000
        label = label or HINT_LABELS.get(kind) or ("🎁 投喂" if kind.startswith("food_") else "收到新互动")
        return f"{label} · +{self.count}"


def grouped_rows(rows, at=None):
    """时间与分组共用服务端 UTC 解析；今天及之前六个北京自然日，待回应独立。"""
    today = to_beijing(at or now_beijing()).date()
    earliest = today - timedelta(days=6)
    pending, dated, unknown = [], {}, []
    parsed = [(row, parse_server_datetime(row.get("created_at"))) for row in rows if isinstance(row, dict)]
    parsed.sort(key=lambda item: item[1].timestamp() if item[1] else float('-inf'), reverse=True)
    for row, stamp in parsed[:30]:
        if row.get("requires_action"):
            pending.append(row)
            continue
        if stamp is None:
            unknown.append(row)
            continue
        day = stamp.date()
        if earliest <= day <= today:
            dated.setdefault(day, []).append(row)
    groups = {"待我回应":pending}
    for day in sorted(dated, reverse=True):
        title = "今天" if day == today else "昨天" if day == today-timedelta(days=1) else (
            f"{day.year}年{day.month}月{day.day}日" if day.year != today.year else f"{day.month}月{day.day}日")
        groups[title] = dated[day]
    if unknown:
        groups["日期未知"] = unknown
    return groups

class InteractionFeed(QWidget):
    def __init__(self, respond, parent=None):
        super().__init__(parent)
        self.respond = respond
        self._responses = {}
        self._response_buttons = {}
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
        self._response_buttons = {}
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        # 搭子/投喂邀请已由上方原有接受/拒绝卡呈现，避免重复处理入口。
        groups = grouped_rows([r for r in rows if isinstance(r, dict) and not (r.get("requires_action") and r.get("source") in {"buddy","visit"})])
        if not any(groups.values()):
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
                stamp = parse_server_datetime(row.get("created_at"))
                header.addWidget(QLabel(format_clock(stamp) or "时间未知"))
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
                if row.get("received_silent") and not row.get("handled_at"):
                    hint = QLabel("免打扰收到 · 首次回应后启用互动效果")
                    hint.setWordWrap(True)
                    hint.setStyleSheet("color: #52645d;")
                    layout.addWidget(hint)
                actions = QHBoxLayout()
                actions.addStretch()
                for label,action in response_actions(row):
                    button = QPushButton(label)
                    key = (str(row.get("event_id")), action)
                    self._response_buttons[key] = button
                    if key in self._responses:
                        begin_button_work(button, self._responses[key][1])
                    button.clicked.connect(lambda checked=False,r=row,a=action,b=button:self._respond(r,a,b))
                    actions.addWidget(button)
                layout.addLayout(actions)
                self.layout.addWidget(card)
        decorate_buttons(self)

    def _respond(self, row, action, button):
        deferred = row.get("received_silent") is True and not row.get("handled_at")
        if action not in {"cheer", "taunt", "food", "flower", "visit"} and not deferred:
            return self.respond(row, action)
        key = (str(row.get("event_id")), action)
        if key in self._responses:
            return
        token = object()
        self._responses[key] = (token, "正在发送…")
        begin_button_work(button, "正在发送…")
        def complete(success, message=""):
            from shiboken6 import isValid
            if not isValid(self) or self._responses.get(key, (None,))[0] is not token:
                return
            if success:
                text = {"taunt":"✓ 已回击", "food":"✓ 已回请", "cheer":"✓ 已加油",
                        "flower":"✓ 已回一朵", "visit":"✓ 已串门"}.get(action, "✓ 已处理")
                self._responses[key] = (token, text)
                current = self._response_buttons.get(key)
                if current is not None and isValid(current): current.setText(text)
                QTimer.singleShot(2000, self, restore)
            else:
                restore()
        def restore():
            from shiboken6 import isValid
            if not isValid(self) or self._responses.get(key, (None,))[0] is not token: return
            self._responses.pop(key, None)
            current = self._response_buttons.get(key)
            if current is not None and isValid(current): end_button_work(current)
        try:
            # 回调只在本地流转，不进入 RPC payload 或互动缓存。
            self.respond({**row, "_response_done": complete}, action)
        except Exception:
            restore()
            raise
