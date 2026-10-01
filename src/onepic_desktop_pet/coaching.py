"""双向训导事项的只读投影；状态由服务端流转，补时复用原始专注区间。"""

from __future__ import annotations

from datetime import datetime, timedelta
from time import monotonic
from uuid import NAMESPACE_URL, uuid5

from .discipline import local_work_time
from .focus_segments import aggregate_focus_time, segment_from_record
from .work_timer import format_work_duration

TERMINAL = {"completed", "forgiven"}
STATE_LABELS = {"pending": "等待回应", "acknowledged": "已回应", "explained": "说明待审核",
                "active": "补时中", "completed": "已结案", "forgiven": "已放过", "rejected": "说明被退回"}
KIND_LABELS = {"late_start": "迟到", "long_break": "长休超时", "focus_shortfall": "目标缺口",
               "weekly_shortfall": "周内补账", "early_finish": "下班异常"}


def action_id(case_id, revision, action):
    """同一次完成的重试使用相同 ID；新版本不能重放旧动作。"""
    return str(uuid5(NAMESPACE_URL, f"lili-coaching:{case_id}:{revision}:{action}"))


def effective_seconds_since(engine, accepted_at, now=None):
    """复用 FocusSession 的区间并集；跨设备重叠、暂停、午夜均不额外计时。"""
    try:
        start = datetime.fromisoformat(str(accepted_at).replace("Z", "+00:00"))
        if start.tzinfo is None:
            return 0
        end = now or (engine.now_provider() if callable(engine.now_provider) else local_work_time())
        if end <= start:
            return 0
        rows = engine.focus_sessions() or []
        segments = [segment_from_record(row, index) if isinstance(row, dict) else row
                    for index, row in enumerate(rows)]
        segments = [row for row in segments if row is not None]
        return aggregate_focus_time(segments, start, end, now=end).total_seconds
    except (TypeError, ValueError, OverflowError):
        return 0


def remaining_seconds(engine, case, now=None):
    return max(0, int(case.get("required_seconds") or 0)
               - effective_seconds_since(engine, case.get("accepted_at"), now))


def case_detail(case, name="搭子"):
    text = str(case.get("title") or KIND_LABELS.get(case.get("kind"), "训导事项"))
    detail = str(case.get("detail") or "")
    parts = [text, detail, f"{name} · {STATE_LABELS.get(case.get('state'), '待处理')}"]
    if case.get("explanation"):
        parts.append("我的说明：" + str(case["explanation"]))
    if case.get("review_note"):
        parts.append("监督者：" + str(case["review_note"]))
    if case.get("required_seconds"):
        parts.append("要求补时：" + format_work_duration(int(case["required_seconds"])))
    return "\n".join(part for part in parts if part)


def completion_feedback(case, name="搭子"):
    """区分接受说明、真正完成补时和放过；更正事实不能算处罚完成。"""
    if case.get("state") == "forgiven":
        return "✓ " + name + " 放过了这件事"
    if int(case.get("required_seconds") or 0) > 0:
        return "✓ 补时完成"
    return "✓ " + name + " 接受了你的说明"


def projection(engine, today_seconds=0, now=None):
    """只选一张待回应卡和一个执行牌；观察期不生成云端事件。"""
    moment = local_work_time(now or (engine.now_provider() if callable(engine.now_provider) else None))
    if engine.store.is_exempt(moment.date()):
        return {"card": None, "card_count": 0, "badge": None, "badge_count": 0}
    cases = [dict(row) for row in engine.store.coaching_cases
             if row.get("state") not in TERMINAL and not row.get("paused")]
    # A fresh runtime permission snapshot is required before using remote cases.
    if engine._remote_mode != "officer" or monotonic() >= engine._remote_until or not engine.enabled:
        cases = []
    rank = {"long_break": 0, "late_start": 1, "focus_shortfall": 2, "early_finish": 3, "weekly_shortfall": 4}
    pending = sorted((row for row in cases if row.get("state") in {"pending", "rejected"}),
                     key=lambda row: (0 if row.get("state") == "pending" else 1,
                                      rank.get(row.get("kind"), 5), str(row.get("created_at", ""))))
    badges = []
    for row in cases:
        if row.get("state") == "active":
            remaining = remaining_seconds(engine, row, moment)
            row.update(badge_text="⏱ 补时中 · 还差 " + format_work_duration(remaining)
                       if remaining else "⏱ 补时已达标 · 等待数据确认", remaining_seconds=remaining, priority=0)
            badges.append(row)
            try:
                accepted = datetime.fromisoformat(str(row.get("accepted_at")).replace("Z", "+00:00"))
                if accepted > moment and row.get("schedule") == "tomorrow":
                    row["badge_text"] = "⏱ 明天优先补 " + format_work_duration(remaining)
            except (ValueError, TypeError):
                pass
        elif row.get("state") == "explained":
            row.update(badge_text="📝 说明待处理", priority=1)
            badges.append(row)
    # Mild issues stay observations unless a formal case already owns the source.
    owned = {str(row.get(key)) for row in engine.store.coaching_cases for key in ("source_event_id", "local_source_event_id")}
    if engine.enabled:
        latest_break = next((row for row in reversed(engine.store.events_for_day(moment.date()))
                             if row.get("event_type") in {"start_break", "end_break", "finish_work"}), None)
        if latest_break and latest_break.get("event_type") == "start_break":
            try:
                start = datetime.fromisoformat(latest_break["occurred_at"])
                overtime = max(0, int((moment - start).total_seconds()) - engine.store.settings.break_limit_minutes * 60)
                if overtime:
                    badges.append({"id": "observe-break:" + str(latest_break["id"]), "priority": 2,
                                   "badge_text": "👀 该回来了", "detail": "休息已超出计划 " + format_work_duration(overtime)})
            except (ValueError, TypeError):
                pass
        for row in reversed(engine.store.events_for_day(moment.date())):
            if str(row.get("id")) in owned:
                continue
            if row.get("event_type") in {"long_break", "late_start"}:
                value_key = "overtime_seconds" if row["event_type"] == "long_break" else "minutes_late"
                if int(row.get("metadata", {}).get(value_key) or 0) <= 0:
                    continue
                badges.append({"id": "observe:" + str(row.get("id")), "priority": 2 if row["event_type"] == "long_break" else 3,
                               "badge_text": "👀 今天有长休 · 再认真一会" if row["event_type"] == "long_break" else "⚠ 今天迟到了 · 再认真一会",
                               "detail": str(row.get("metadata", {}).get("detail") or "普通提醒，无需提交说明。")})
                break
        yesterday = moment.date() - timedelta(days=1)
        debt = next((row for row in reversed(engine.store.events_for_day(yesterday))
                     if row.get("event_type") == "focus_shortfall" and int(row.get("metadata", {}).get("gap_seconds") or 0) > 0), None)
        if debt and str(debt.get("id")) not in owned and today_seconds < 3600:
            badges.append({"id": "observe-debt:" + yesterday.isoformat(), "priority": 4,
                           "badge_text": "😼 昨天欠账 · 再干 " + format_work_duration(3600 - today_seconds),
                           "detail": "这是今日 60 分钟观察期；昨日实际缺口仍按本周剩余目标分配。"})
    badges.sort(key=lambda row: row["priority"])
    return {"card": pending[0] if pending else None, "card_count": len(pending),
            "badge": badges[0] if badges else None, "badge_count": len(badges)}


def closed_case_lines(store, day):
    """历史只展示已结案的生命周期摘要，不记录每分钟剩余值。"""
    lines = []
    for row in store.coaching_cases:
        if row.get("event_date") != day.isoformat() or row.get("state") not in TERMINAL:
            continue
        text = str(row.get("title") or "训导事项") + " · " + STATE_LABELS[row["state"]]
        if row.get("explanation"):
            text += " · 已说明"
        if row.get("required_seconds") and row["state"] == "completed":
            text += " · 补时 " + format_work_duration(int(row["required_seconds"])) + " 完成"
        if row.get("closed_at"):
            try:
                closed = datetime.fromisoformat(str(row["closed_at"]).replace("Z", "+00:00"))
                text += " · " + local_work_time(closed).strftime("%H:%M")
            except (ValueError, TypeError):
                pass
        lines.append(text)
    return lines
