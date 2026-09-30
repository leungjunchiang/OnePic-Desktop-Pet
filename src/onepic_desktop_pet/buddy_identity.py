"""搭子的本地显示身份：私有备注优先，公开昵称与账号辅助识别。"""

from __future__ import annotations


def public_name(record: dict | None) -> str:
    row = record or {}
    return next((str(row.get(key) or "").strip() for key in
                 ("owner_nickname", "nickname", "display_name", "username", "account_name", "user_id", "id")
                 if str(row.get(key) or "").strip()), "搭子")[:80]


def buddy_name(record: dict | None) -> str:
    return str((record or {}).get("private_note_name") or "").strip()[:40] or public_name(record)


def buddy_choice(record: dict) -> str:
    primary, public = buddy_name(record), public_name(record)
    secondary = f"（{public}）" if primary != public else ""
    identifier = str(record.get("user_id") or record.get("id") or "")
    identifier = str(record.get("username") or record.get("account_name") or identifier)
    if len(identifier) > 18:
        identifier = identifier[:8] + "…" + identifier[-4:]
    state = str(record.get("status") or "")
    status = "🟢 专注中" if state in {"focus", "focused"} else "🟡 休息" if state in {"rest", "resting"} else "⚫ 离线" if state == "offline" else "状态同步中"
    return f"{primary}{secondary} · @{identifier} · {status}"
