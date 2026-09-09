Warning: truncated output (original token count: 72539)
Total output lines: 5875

"""搭子自习室界面、后台同步线程和双六毛本地串门窗口。

账号注册会明确显示“等待邮箱确认”状态，并允许用户重新发送确认邮件；
邮箱确认页打开项目页面后，用户回到这里即可登录，不会把“没有即时 session”误报成注册失败。
专注后台同步只传本设备待确认的 sealed facts；上传成功后把本地确认信息交给账号账本持久化。
"""

from __future__ import annotations

import sys
import time
import logging
import json
import threading
from copy import deepcopy
from functools import cmp_to_key
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QEvent, QLocale, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtCore import QCollator
from PySide6.QtGui import QCloseEvent, QFont, QFontDatabase, QHideEvent, QPixmap, QShowEvent
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QStackedWidget, QTabBar, QTabWidget, QMenu,
    QVBoxLayout, QWidget, QSizePolicy,
)

from .resources import resource_path
from .accessories import SPECIAL_OUTFIT_SPRITES
from .social import (
    SignupResult,
    SocialClient,
    SocialError,
    _heartbeat_payload,
    _next_presence_sequence,
    _session_user_id,
    _apply_buddy_private_notes,
    _private_note_deletions_from_dashboard,
    _private_notes_from_dashboard,
    _dashboard_payload_has_core_shape,
    _merge_dashboard_overlay,
    presence_device_id,
    social_user_message,
)
from .config import PET_NAME, clean_owner_nickname, clean_social_pet_name, social_pet_label
from .focus_analytics import MAX_ANALYTICS_DAY_SECONDS
from .focus_sync_protocol import (
    canonicalize_focus_upload_segments,
    validate_focus_delta_response,
)
from .login_rewards import login_reward_granted, login_streak_days
from .work_timer import format_work_duration
from .lifecycle_log import lifecycle_log

LOGGER = logging.getLogger(__name__)
_NO_PENDING_OWNER_NICKNAME = object()


def _json_payload_bytes(value: object) -> int:
    """Return a bounded UTF-8 size estimate for one RPC JSON payload."""

    try:
        return len(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        )
    except (TypeError, ValueError, OverflowError):
        return 0


_DASHBOARD_IDENTITY_FIELDS = (
    "user_id",
    "id",
    "invite_code",
    "owner_nickname",
    "nickname",
)


def _focus_upload_ack_status(
    uploaded_segments: object,
    response: object,
) -> tuple[bool, set[str]]:
    """Validate the v2 acknowledgement without changing local state."""

    valid, _reason, accepted_ids = validate_focus_delta_response(
        response,
        uploaded_segments,
    )
    return valid, accepted_ids


def _merge_dashboard_snapshot(
    previous: dict[str, Any], incoming: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Keep a partial response from erasing identity or relationships.

    ``lili_dashboard`` owns the account identity and accepted-buddy lists.
    Heartbeat/context responses and mixed-version relays can still return a
    JSON object without all three fields.  The UI must not interpret that as a
    legitimate empty account.  A complete response remains authoritative,
    including explicit empty buddy/room lists.
    """

    previous = previous if isinstance(previous, dict) else {}
    incoming = incoming if isinstance(incoming, dict) else {}
    complete = _dashboard_payload_has_core_shape(incoming)
    merged = deepcopy(incoming) if complete else deepcopy(previous)

    if not complete:
        # Preserve the last known core snapshot when it exists.  On the first
        # run, retain any valid core fields from a cache/older relay so a
        # missing optional field does not discard useful peer cards.
        for field in ("me", "buddies", "room_people"):
            old_value = previous.get(field)
            if field in previous and (
                (field == "me" and isinstance(old_value, dict) and bool(old_value))
                or (field != "me" and isinstance(old_value, list))
            ):
                merged[field] = deepcopy(old_value)
            elif field not in merged and field in incoming:
                value = incoming.get(field)
                if (field == "me" and isinstance(value, dict)) or (
                    field != "me" and isinstance(value, list)
                ):
                    merged[field] = deepcopy(value)
        # Do not let an incomplete response replace the cached optional
        # collections either, but accept new optional fields such as a
        # connection diagnostic or server timestamp.
        merged.update(deepcopy(incoming))
        for field in ("me", "buddies", "room_people"):
            old_value = previous.get(field)
            if field in previous and (
                (field == "me" and isinstance(old_value, dict) and bool(old_value))
                or (field != "me" and isinstance(old_value, list))
            ):
                merged[field] = deepcopy(old_value)
    else:
        merged.update(deepcopy(incoming))

    # The room projection may be a newer complete snapshot while omitting
    # optional profile labels that the account projection already supplied.
    # Preserve those labels only for omission; explicit null remains a clear.
    merged = _merge_dashboard_overlay(previous, merged)

    # A response with a sparse ``me`` mapping must not blank a durable invite
    # code or nickname.  Empty fields are treated as “not supplied” here; an
    # explicit rename is sent through the profile update path instead.
    old_me = previous.get("me") if isinstance(previous.get("me"), dict) else {}
    new_me = merged.get("me") if isinstance(merged.get("me"), dict) else {}
    if old_me and new_me:
        for field in _DASHBOARD_IDENTITY_FIELDS:
            if not str(new_me.get(field) or "").strip() and str(old_me.get(field) or "").strip():
                new_me[field] = old_me[field]
        # Unlike the durable identity fields above, null is a meaningful
        # value for pet_name because it explicitly clears the optional name.
        if "pet_name" not in new_me and "pet_name" in old_me:
            new_me["pet_name"] = old_me["pet_name"]
        merged["me"] = new_me

    if not complete:
        merged["_dashboard_partial"] = True
        merged["is_stale"] = True
        merged["data_source"] = "server_partial"
        merged["_data_source"] = "server_partial"
        merged["_sync_error"] = "服务器返回了不完整的社交快照，已保留上次正常数据。"
    else:
        merged.pop("_dashboard_partial", None)
        # ``_merge_dashboard_overlay`` deliberately starts from the prior
        # snapshot to keep omitted profile labels. Transport flags are not
        # durable labels, however: carrying a cached DEGRADED flag into a
        # complete healthy dashboard makes a recovered connection look
        # offline forever. Only clear them when the incoming payload itself
        # is not an offline/cache response.
        state = str(incoming.get("_connection_state") or "").upper()
        incoming_is_stale = bool(incoming.get("_sync_offline")) or bool(incoming.get("is_stale"))
        if not incoming_is_stale and state not in {"DEGRADED", "OFFLINE", "AUTH_ERROR"}:
            for field in (
                "_connection_state",
                "_sync_offline",
                "_presence_grace_active",
                "_presence_uncertainty_seconds",
                "_sync_age_minutes",
                "_sync_error",
            ):
                merged.pop(field, None)

    # Viewer-only labels have a different merge contract from public profile
    # fields. Missing labels are expected from older relays and must not erase
    # a known private remark. A successful notes RPC is authoritative; an
    # explicit null/deletion marker is authoritative for one peer.
    previous_notes = _private_notes_from_dashboard(previous)
    incoming_notes = _private_notes_from_dashboard(incoming)
    if incoming.get("_private_notes_loaded"):
        note_by_user = dict(incoming_notes)
    else:
        note_by_user = dict(previous_notes)
        note_by_user.update(incoming_notes)
        for user_id in _private_note_deletions_from_dashboard(incoming):
            note_by_user.pop(user_id, None)
    _apply_buddy_private_notes(merged, note_by_user)
    if not incoming.get("_private_notes_loaded"):
        deleted = sorted(_private_note_deletions_from_dashboard(incoming))
        if deleted:
            merged["_private_notes_deleted"] = deleted
    return merged, not complete


def _unwrap_reaction_payload(payload: object) -> dict[str, Any] | None:
    """Normalize direct PostgREST and relay responses for reaction state.

    JSONB RPCs normally arrive as a dictionary, but older relay builds and a
    few HTTP clients wrap the same value in ``data``/``result`` or encode it
    as a JSON string/one-item list.  Treat those representations identically
    so a valid server taunt cannot be silently dropped by the UI.
    """

    value: object = payload
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if isinstance(value, list):
        if len(value) != 1:
            return None
        value = value[0]
    if not isinstance(value, dict):
        return None
    # Edge relays may wrap the RPC result while direct PostgREST returns it
    # directly. Only unwrap when the nested value is itself a state object.
    for key in ("data", "result", "payload"):
        nested = value.get(key)
        if isinstance(nested, (dict, list, str)):
            unwrapped = _unwrap_reaction_payload(nested)
            if unwrapped is not None and (
                "taunt" in unwrapped or "encouragement" in unwrapped
            ):
                return unwrapped
    return value


def _unwrap_single_reaction_state(payload: object) -> dict[str, Any] | None:
    """Normalize a taunt/encouragement state JSONB RPC response."""

    value: object = payload
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if isinstance(value, list):
        if len(value) != 1:
            return None
        value = value[0]
    if not isinstance(value, dict):
        return None
    for key in ("data", "result", "payload"):
        nested = value.get(key)
        if isinstance(nested, (dict, list, str)):
            unwrapped = _unwrap_single_reaction_state(nested)
            if unwrapped is not None and "active" in unwrapped:
                return unwrapped
    return value if "active" in value else None

# Supabase returns timestamptz values with their UTC offset.  The room UI is
# intentionally fixed to China Standard Time instead of inheriting the
# machine's local timezone, so users in different regions see the same room
# timeline.  A fixed UTC+8 offset is sufficient for Beijing (no DST).
BEIJING_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")
PRESENCE_RECOVERY_STATUS = "自习室连接暂时不稳定，搭子最近状态仍保留显示；正在自动恢复实时同步。"

def _beijing_now() -> datetime:
    return datetime.now(BEIJING_TIMEZONE)


def _format_beijing_time(value: str) -> str:
    """Convert an ISO-8601 timestamp to the room's Beijing time (HH:MM)."""

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # Server timestamps are timestamptz values.  Treat a legacy naive
        # value as UTC rather than silently using the user's machine timezone.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(BEIJING_TIMEZONE).strftime("%H:%M")
    except (TypeError, ValueError, OverflowError):
        return ""


def _room_focus_summary_text(summary: dict[str, Any], member_count: int = 0, focus_count: int = 0) -> str:
    """Render room focus as today's time plus the room's historical total.

    ``shared_focus_seconds`` remains a compatibility fallback for an older
    deployed function. New dashboards provide the two explicit room-scoped
    values so a member's personal daily total is never shown as the room
    total.
    """

    today_seconds = int(summary.get("today_shared_focus_seconds") or 0)
    cumulative_seconds = int(
        summary.get("cumulative_shared_focus_seconds")
        or summary.get("shared_focus_seconds")
        or 0
    )
    focus_text = (
        "当前专注人数待确认"
        if summary.get("presence_uncertain")
        else f"{int(summary.get('focus_count') or focus_count)} 人正在专注"
    )
    return (
        f"本房间 {int(summary.get('member_count') or member_count)} 人 · "
        f"{focus_text} · "
        f"今日共同专注 {format_work_duration(today_seconds)} · "
        f"累计共同专注 {format_work_duration(cumulative_seconds)}"
    )


def _study_focus_summary_text(
    working_count: int,
    me_seconds: int,
    *,
    presence_uncertain: bool = False,
) -> str:
    """Render the homepage count without turning an unknown state into zero."""

    if presence_uncertain:
        focus_text = (
            f"最近确认 {int(working_count)} 位搭子正在专注（实时状态待确认）"
            if working_count > 0
            else "搭子实时专注人数待确认"
        )
    else:
        focus_text = f"现在 {int(working_count)} 位搭子正在专注"
    return f"{focus_text}　·　我的今日专注 {format_work_duration(me_seconds)}"


def _presence_working(presence: dict[str, Any]) -> bool:
    """Read both the legacy boolean and the new explicit presence status.

    Older dashboard functions only returned ``working`` while the repaired
    function also returns ``status``.  Keeping this normalization in the UI
    prevents a mixed-version pair of clients from showing a false rest state.
    """

    status = str(presence.get("status") or "").strip().casefold()
    if status in {"focus", "working", "专注", "工作", "专注中", "正在工作"}:
        return True
    if status in {"rest", "idle", "offline", "休息", "休息中", "离线"}:
        return False
    value = presence.get("working")
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "focus", "working", "专注", "工作", "专注中", "正在工作"}
    return bool(value)


def _presence_status(presence: dict[str, Any]) -> str:
    """Return a stable user-facing status for old and new API payloads."""

    # A short dashboard outage is a transport problem, not a peer leave.  Keep
    # that state distinct so an old snapshot cannot be rendered as a false
    # “offline” result.
    if bool(presence.get("presence_uncertain")):
        return "unknown"
    if bool(presence.get("stale_presence")):
        return "offline"
    # Some older dashboard payloads can retain ``working`` or ``status``
    # after the server has already marked the user offline.  The explicit
    # online flag is authoritative in that case, otherwise the UI shows a
    # grey dot together with the contradictory “正在工作” label.
    if presence.get("online") is False:
        return "offline"
    status = str(presence.get("status") or "").strip().casefold()
    if status in {"offline", "离线"}:
        return "offline"
    if _presence_working(presence):
        return "focus"
    return "rest"


def _taunt_available(presence: dict[str, Any]) -> bool:
    """Whether the buddy card should expose the persistent taunt action.

    ``working`` is retained in a few legacy/cached dashboard payloads after a
    buddy goes offline.  The normalized presence status already resolves that
    contradiction (an explicit offline flag and stale presence win), so using
    the raw boolean here could hide the action indefinitely.  Unknown state is
    intentionally excluded: when the connection is uncertain, the UI should
    not encourage an action that the server may reject.
    """

    return _presence_status(presence) in {"rest", "offline"}


def _taunt_window_open(now: datetime | None = None) -> bool:
    """Return whether Beijing local time currently permits playful taunts."""

    current = now or _beijing_now()
    minutes = current.hour * 60 + current.minute
    return 8 * 60 <= minutes <= 22 * 60 + 30


def _reaction_label(presence: dict[str, Any], now: datetime | None = None) -> str:
    """Show the action that matches the buddy's confirmed presence state.

    The button stays labelled ``嘲讽`` while a buddy is resting/offline so the
    user can understand what the action normally does.  The click handler
    performs the Beijing-time check and explains after-hours privacy time
    without sending an RPC or consuming a quota.
    """

    return "嘲讽" if _taunt_available(presence) else "加油"


def _wealth_leaderboard_enabled(profile: dict[str, Any] | None) -> bool:
    """Keep the leaderboard opt-in default for legacy profiles.

    ``wealth_leaderboard_preference_set`` distinguishes an old row that has
    never made an explicit choice from a deliberate opt-out. This lets the
    UI remain enabled by default without undoing a user's saved opt-out.
    """

    if not isinstance(profile, dict):
        return True
    if not bool(profile.get("wealth_leaderboard_preference_set", False)):
        return True
    return bool(profile.get("wealth_leaderboard_enabled", True))


def _owner_nickname(record: dict[str, Any] | None) -> str:
    """Return the viewer label, with a private remark taking precedence.

    A private remark is intentionally scoped to this viewer, so it may be used
    in that viewer's buddy card. When it is absent, fall back to the buddy's
    public self-chosen nickname. It must never be replaced by the neutral
    default merely because another identity field is missing.
    """

    if not isinstance(record, dict):
        return "搭子"
    return str(
        record.get("private_note_name")
        or _public_owner_nickname(record)
    ).strip() or "搭子"


def _public_owner_nickname(record: dict[str, Any] | None) -> str:
    if not isinstance(record, dict):
        return "搭子"
    return str(
        record.get("owner_nickname")
        or record.get("nickname")
        or record.get("display_name")
        or "搭子"
    ).strip() or "搭子"


def _owner_label(record: dict[str, Any] | None) -> str:
    if isinstance(record, dict):
        private_note = clean_social_pet_name(record.get("private_note_name"))
        if private_note:
            return social_pet_label(private_note)
        # ``pet_name`` is a legacy compatibility field for the fixed pet
        # identity.  It must never override the owner's public name: the only
        # editable part of “XX家的六毛” is XX (owner_nickname).
    return social_pet_label(_public_owner_nickname(record))


def _leaderboard_focus_seconds(record: dict[str, Any] | None) -> int:
    """Read the server aggregate while keeping old payloads usable."""

    if not isinstance(record, dict):
        return 0
    for key in ("week_seconds", "period_seconds", "focus_seconds", "period_income"):
        try:
            value = record.get(key)
            if value is not None:
                return max(0, int(value))
        except (TypeError, ValueError, OverflowError):
            continue
    return 0


def _notification_sender_id(record: dict[str, Any] | None) -> str:
    if not isinstance(record, dict):
        return ""
    return str(
        record.get("sender_id")
        or record.get("requester_id")
        or record.get("peer_id")
        or record.get("actor_id")
        or record.get("user_id")
        or ""
    )


def _compare_buddies(left: dict[str, Any], right: dict[str, Any]) -> int:
    """在线优先、今日专注降序，最后按备注/姓名的中文拼音排序。"""

    def online(record: dict[str, Any]) -> int:
        return 0 if _presence_status(record) in {"focus", "rest"} else 1

    left_online, right_online = online(left), online(right)
    if left_online != right_online:
        return -1 if left_online < right_online else 1
    try:
        left_today = max(0, int(left.get("today_seconds") or 0))
    except (TypeError, ValueError):
        left_today = 0
    try:
        right_today = max(0, int(right.get("today_seconds") or 0))
    except (TypeError, ValueError):
        right_today = 0
    if left_today != right_today:
        return -1 if left_today > right_today else 1
    collator = QCollator(QLocale(QLocale.Language.Chinese, QLocale.Country.China))
    return collator.compare(_owner_nickname(left), _owner_nickname(right))


def _focus_timestamp(value: object) -> datetime | None:
    """Parse a server focus timestamp and normalize it to Beijing time."""

    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        # Keep the same compatibility rule as _format_beijing_time: a legacy
        # server timestamp without an offset is interpreted as UTC.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(BEIJING_TIMEZONE)


def _live_session_seconds(record: dict[str, Any]) -> int | None:
    """Calculate a peer's current round from the server start timestamp."""

    if _presence_status(record) != "focus":
        return 0
    started = str(record.get("session_started_at") or "")
    if not started:
        value = record.get("session_seconds")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError, OverflowError):
            return None
    started_at = _focus_timestamp(started)
    if started_at is None:
        value = record.get("session_seconds")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError, OverflowError):
            return None
    now = _focus_timestamp(record.get("_server_timestamp"))
    if now is None:
        now = _beijing_now()
    return max(0, int((now - started_at).total_seconds()))


def _live_focus_window_seconds(
    record: dict[str, Any],
    window_start: datetime,
) -> int:
    """Return only the active interval that belongs to one calendar window."""

    if _presence_status(record) != "focus":
        return 0
    started_at = _focus_timestamp(record.get("session_started_at"))
    if started_at is None:
        return 0
    now = _focus_timestamp(record.get("_server_timestamp"))
    if now is None:
        now = _beijing_now()
    begin = max(started_at, window_start)
    return max(0, int((now - begin).total_seconds()))


def _safe_nonnegative_seconds(value: object) -> int | None:
    """Read an optional duration without converting hidden values to zero."""

    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _project_legacy_live_focus_totals(
    record: dict[str, Any],
    *,
    server_timestamp: str = "",
    totals_source: str = "",
) -> dict[str, Any]:
    """Patch old dashboard rows with the active interval until the SQL fix lands.

    The canonical dashboard marks its totals as an interval union.  Those rows
    already include fresh device presence and must not receive a second live
    supplement.  Older dashboard functions expose only a stale closed counter;
    for those rows, adding the current session interval keeps the visible card
    aligned while the backend migration is being rolled out.
    """

    projected = dict(record)
    if server_timestamp and not projected.get("_server_timestamp"):
        projected["_server_timestamp"] = server_timestamp
    source = str(
        projected.get("focus_totals_source")
        or projected.get("_focus_totals_source")
        or totals_source
        or ""
    ).strip()
    if source in {
        "canonical_interval_union",
        "canonical_interval_union_legacy_daily_compat",
        "canonical_interval_union_legacy_floor",
        "legacy_daily_compat",
        "legacy_floor",
        "client_live_compat",
    }:
        return projected
    if _presence_status(projected) != "focus" or not projected.get("session_started_at"):
        return projected

    now = _focus_timestamp(projected.get("_server_timestamp"))
    if now is None:
        now = _beijing_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())
    live_today = _live_focus_window_seconds(projected, today_start)
    live_week = _live_focus_window_seconds(projected, week_start)
    changed = False
    for key, live in (("today_seconds", live_today), ("week_seconds", live_week)):
        base = _safe_nonnegative_seconds(projected.get(key))
        if base is not None:
            projected[key] = base + live
            changed = True
    current_round = _live_session_seconds(projected)
    base_round = _safe_nonnegative_seconds(projected.get("session_seconds"))
    if current_round is not None and base_round is not None:
        projected["session_seconds"] = max(base_round, current_round)
        changed = True
    if changed:
        projected["_focus_totals_source"] = "client_live_compat"
    return projected


_SOCIAL_FONT_CACHE: QFont | None = None


def _social_font() -> QFont:
    global _SOCIAL_FONT_CACHE
    if _SOCIAL_FONT_CACHE is not None:
        return QFont(_SOCIAL_FONT_CACHE)
    candidates = (
        (Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/simhei.ttf"))
        if sys.platform == "win32"
        else (Path("/System/Library/Fonts/PingFang.ttc"), Path("/System/Library/Fonts/Hiragino Sans GB.ttc"))
    )
    family = ""
    for path in candidates:
        if path.is_file():
            font_id = QFontDatabase.addApplicationFont(str(path))
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families: family = families[0]; break
    _SOCIAL_FONT_CACHE = QFont(family or "sans-serif", 10)
    return QFont(_SOCIAL_FONT_CACHE)


class EqualWidthTabBar(QTabBar):
    """Keep the four primary navigation tabs equal during every layout pass."""

    def sizeHint(self) -> QSize:
        size = super().sizeHint()
        host = self.parentWidget()
        if host is not None:
            size.setWidth(max(size.width(), host.width() - 2))
        return size

    def tabSizeHint(self, index: int) -> QSize:
        size = super().tabSizeHint(index)
        count = max(1, self.count())
        host = self.parentWidget()
        # Use the tab bar's actual width.  The navigation controller may
        # be a few pixels wider than the bar; using the host width here would
        # make Qt distribute a remainder and alternate tab widths (e.g. 288/289).
        available = max(1, self.width())
        size.setWidth(max(1, available // count))
        return size


class SocialHeartbeatWorker:
    """Send only the lightweight presence heartbeat on an independent loop.

    The dashboard poll deliberately remains a short-lived worker, but a
    heartbeat must not wait behind dashboard/statistics/reaction requests.
    This is a plain Python worker instead of a custom ``QThread`` because it
    does not need Qt signals or an event loop; avoiding a Qt-owned condition
    thread also makes native window teardown deterministic on Windows.
    """

    def __init__(self, client: SocialClient, parent=None, *, interval_seconds: float = 15.0) -> None:
        self.client = client
        self.interval_seconds = max(5.0, float(interval_seconds))
        self._condition = threading.Condition()
        self._stopped = False
        self._pending: dict[str, Any] | None = None
        self._shutdown_payload: dict[str, Any] | None = None
        self._send_now = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopped = False
            self._thread = threading.Thread(
                target=self.run,
                name="lili-social-heartbeat",
                daemon=True,
            )
            self._thread.start()

    def isRunning(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def wait(self, timeout_ms: int = 0) -> bool:
        thread = self._thread
        if thread is None:
            return True
        thread.join(max(0.0, float(timeout_ms)) / 1000.0)
        return not thread.is_alive()

    def update_presence(self, presence: dict[str, Any], *, immediate: bool = False) -> None:
        raw = dict(presence)
        defer_inactive = bool(raw.pop("_defer_inactive_until_focus_ack", False))
        payload = _heartbeat_payload(raw)
        with self._condition:
            # A local pause has already sealed a WAL/store fact, but its
            # delta ACK may still be in flight. Do not replace a queued live
            # heartbeat with inactive presence before that ACK exists.
            if defer_inactive and not bool(payload.get("session_active")):
                return
            self._pending = dict(payload)
            self._send_now = self._send_now or bool(immediate)
            self._condition.notify()

    def stop(self, final_presence: dict[str, Any] | None = None) -> None:
        with self._condition:
            if isinstance(final_presence, dict):
                # Queue one best-effort inactive state before the daemon
                # worker exits.  This is intentionally asynchronous: closing
                # the desktop must never wait on a network socket, while an
                # explicit finalize helps peers stop showing a ghost session
                # before the normal server freshness timeout.
                raw = dict(final_presence)
                if not bool(raw.pop("_defer_inactive_until_focus_ack", False)):
                    self._shutdown_payload = _heartbeat_payload(raw)
            self._stopped = True
            self._condition.notify_all()

    def run(self) -> None:
        next_due = 0.0
        while True:
            with self._condition:
                while not self._stopped and self._pending is None:
                    self._condition.wait()
                if self._shutdown_payload is not None:
                    payload = self._shutdown_payload
                    self._shutdown_payload = None
                    shutdown_send = True
                elif self._stopped:
                    return
                else:
                    shutdown_send = False
                    now = time.monotonic()
                    wait_for = 0.0 if self._send_now or now >= next_due else next_due - now
                    if wait_for > 0:
                        self._condition.wait(timeout=wait_for)
                        if self._stopped and self._shutdown_payload is None:
                            return
                        if self._shutdown_payload is not None:
                            payload = self._shutdown_payload
                            self._shutdown_payload = None
                            shutdown_send = True
                        elif self._send_now or time.monotonic() >= next_due:
                            pass
                        else:
                            continue
                    if not shutdown_send:
                        # Consume both values under the same lock. Otherwise a
                        # concurrent immediate final-state update can be
                        # overwritten by this worker clearing the flag after
                        # it has been set.
                        payload = dict(self._pending or {})
                        self._send_now = False
            user_id = str(payload.get("user_id") or "").strip()
            if not user_id:
                # Some compatibility clients expose the authenticated session
                # through an auth manager or their active HTTP backend instead
                # of ``client.session``.  Do not send an anonymous heartbeat:
                # that would make every peer see this account as offline.
                user_id = _session_user_id(self.client)
                if user_id:
                    payload["user_id"] = user_id
            if user_id:
                # Sequence assignment happens on this independent transport
                # thread, immediately before send.  A slow dashboard queue
                # can therefore never reuse or reorder a presence version.
                payload["sequence"] = _next_presence_sequence(user_id)
            heartbeat_started = time.monotonic()
            try:
                self.client.heartbeat(**payload)
                LOGGER.debug("social heartbeat sent independently")
                lifecycle_log(
                    "social.heartbeat.sent",
                    user_id=user_id,
                    working=bool(payload.get("working")),
                    session_active=bool(payload.get("session_active")),
                    sequence=int(payload.get("sequence") or 0),
                    latency_ms=round((time.monotonic() - heartbeat_started) * 1000, 1),
                )
            except (SocialError, TypeError) as exc:
                # Do not terminate the worker for a transient outage.  The
                # next payload retries naturally, while dashboard polling can
                # continue to use its own fallback route.
                LOGGER.warning("independent social heartbeat failed: %s", exc)
                lifecycle_log(
                    "social.heartbeat.failed",
                    user_id=user_id,
                    working=bool(payload.get("working")),
                    session_active=bool(payload.get("session_active")),
                    sequence=int(payload.get("sequence") or 0),
                    latency_ms=round((time.monotonic() - heartbeat_started) * 1000, 1),
                    error_kind=getattr(exc, "kind", "transport"),
                )
            except Exception:
                # A transport adapter must not be able to kill the dedicated
                # liveness loop. Keep the next latest payload eligible for a
                # retry and leave the diagnostic traceback in the log.
                LOGGER.exception("independent social heartbeat crashed")
                lifecycle_log(
                    "social.heartbeat.crashed",
                    user_id=user_id,
                    working=bool(payload.get("working")),
                    session_active=bool(payload.get("session_active")),
                    sequence=int(payload.get("sequence") or 0),
                    latency_ms=round((time.monotonic() - heartbeat_started) * 1000, 1),
                )
            if shutdown_send:
                return
            next_due = time.monotonic() + self.interval_seconds


class SocialSyncThread(QThread):
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, client: SocialClient, presence: dict[str, Any], parent=None, *, send_heartbeat: bool = False, request_generation: int = 0) -> None:
        super().__init__(parent)
        self.client = client
        self.presence = presence
        self.send_heartbeat = send_heartbeat
        self.request_generation = max(0, int(request_generation or 0))

    def run(self) -> None:
        try:
            heartbeat_error = ""
            focus_history_result = None
            focus_segments_result = None
            focus_segment_integrity_result = None
            focus_live_projection_result = None
            personal_state_result = None
            presence_context_updated: bool | None = None
            taunt_state_result = None
            encouragement_state_result = None
            focus_segments_cursor_before = ""
            focus_segments_upload_count = 0
            focus_segments_device_id = ""
            focus_segments_sync_mode = "delta"
            focus_segments_sync_started = 0.0
            focus_upload_gate_blocked = False
            focus_upload_payload_diagnostics: dict[str, Any] = {}
            focus_sync_metrics = {
                "delta_rpc_calls": 0,
                "integrity_rpc_calls": 0,
                "reconciliation_rpc_calls": 0,
                "upload_rows": 0,
                "returned_segment_rows": 0,
                "manifest_rows": 0,
                "request_bytes": 0,
                "response_bytes": 0,
                "manifest_bytes": 0,
                "full_bootstrap_count": 0,
            }
            personal_state = self.presence.get("personal_state")
            personal_state_factory = self.presence.get("_personal_state_factory")
            if personal_state is None and callable(personal_state_factory):
                try:
                    personal_state = personal_state_factory()
                except Exception:
                    LOGGER.exception("background personal-state preparation failed")
                    personal_state = None
            heartbeat_presence = _heartbeat_payload(self.presence)
            if self.send_heartbeat:
                try:
                    self.client.heartbeat(**heartbeat_presence)
                except (SocialError, TypeError) as exc:
                    # A transient write failure must not prevent the same
                    # cycle's dashboard read. Otherwise one platform can
                    # disappear from the other simply because its heartbeat
                    # proxy is briefly unavailable.
                    heartbeat_error = str(exc)
                    LOGGER.warning("social presence heartbeat failed: %s", exc)
            # Room/outfit/quick-status are presentation context, not liveness
            # and not duration.  Send them through a separate, background RPC
            # so the heartbeat payload remains deliberately liveness-only.
            presence_context = self.presence.get("_presence_context")
            context_rpc = getattr(self.client, "rpc", None)
            if isinstance(presence_context, dict) and callable(context_rpc):
                try:
                    context_result = context_rpc(
                        "lili_update_presence_context",
                        {
                            "p_room_id": presence_context.get("room_id"),
                            "p_outfit_key": str(presence_context.get("outfit_key") or "")[:60],
                            "p_quick_status": str(presence_context.get("quick_status") or "")[:40],
                            "p_quick_status_expires_at": presence_context.get("quick_status_expires_at"),
                        },
                    )
                    # The context RPC intentionally does not create a row.
                    # If it races the first heartbeat, retry on the next
                    # background cycle instead of losing the room association.
                    presence_context_updated = bool(
                        context_result.get("updated", False)
                        if isinstance(context_result, dict)
                        else False
                    )
                except (SocialError, AttributeError, TypeError) as exc:
                    LOGGER.info("presence context sync deferred: %s", exc)
                    presence_context_updated = False
                except Exception:
                    LOGGER.exception("presence context sync crashed")
                    presence_context_updated = False
            if isinstance(personal_state, dict):
                sync_rpc = getattr(self.client, "rpc", None)
                if callable(sync_rpc):
                    try:
                        personal_state_result = sync_rpc(
                            "lili_sync_personal_state",
                            {
                                "p_focus_date": str(personal_state.get("focus_date") or ""),
                                "p_today_seconds": int(personal_state.get("today_seconds") or 0),
                                "p_lifetime_seconds": int(personal_state.get("lifetime_seconds") or 0),
                                "p_week_start": str(personal_state.get("week_start") or ""),
                                "p_week_seconds": int(personal_state.get("week_seconds") or 0),
                                "p_outfit_key": str(personal_state.get("outfit_key") or ""),
                                "p_outfit_set": bool(personal_state.get("outfit_set")),
                            },
                        )
                    except (SocialError, AttributeError, TypeError) as exc:
                        # Older relays can serve the room dashboard before the
                        # personal-state migration is deployed.  Keep the
                        # social room usable and retry on the next heartbeat.
                        LOGGER.info("personal state sync deferred: %s", exc)
                    try:
                        focus_history_result = sync_rpc(
                            "lili_sync_focus_history",
                            {"p_history": personal_state.get("focus_history") or []},
                        )
                    except (SocialError, AttributeError, TypeError) as exc:
                        # Daily history is additive.  If an older relay has not
                        # received this migration yet, the existing profile
                        # sync and local cache continue to work.
                        LOGGER.info("daily focus history sync deferred: %s", exc)
                    try:
                        focus_segments = personal_state.get("focus_segments") or []
                        focus_segments_sources = personal_state.get(
                            "focus_segments_sources"
                        )
                        focus_segments_cursor_before = str(
                            personal_state.get("focus_segments_sync_cursor") or ""
                        )
                        focus_segments_sync_mode = str(
                            personal_state.get("focus_segments_sync_mode") or "delta"
                        )[:40]
                        focus_segments_sync_started = time.monotonic()
                        focus_segments_device_id = presence_device_id(
                            _session_user_id(self.client)
                            or str(self.presence.get("user_id") or "")
                        )
                        canonical_segments, focus_upload_payload_diagnostics = (
                            canonicalize_focus_upload_segments(
                                focus_segments,
                                source_entries=focus_segments_sources,
                            )
                        )
                        focus_upload_gate_blocked = not bool(
                            focus_upload_payload_diagnostics.get("ok")
                        )
                        focus_segments_upload_count = len(canonical_segments)
                        focus_segments_request_body = {
                            "p_segments": canonical_segments,
                            "p_since": focus_segments_cursor_before or None,
                        }
                        if focus_upload_gate_blocked:
                            # A duplicate/conflicting local batch is a local
                            # protocol failure.  Do not call Supabase and do
                            # not manufacture an ACK, fingerprint, or cursor.
                            LOGGER.error(
                                "focus segment upload blocked before RPC: %s",
                                focus_upload_payload_diagnostics,
                            )
                            lifecycle_log(
                                "focus.segment_sync.blocked",
                                cursor_before=focus_segments_cursor_before,
                                input_count=focus_upload_payload_diagnostics.get(
                                    "input_count", 0
                                ),
                                duplicate_segment_ids=focus_upload_payload_diagnostics.get(
                                    "duplicate_segment_ids", []
                                ),
                                conflict_segment_ids=focus_upload_payload_diagnostics.get(
                                    "conflict_segment_ids", []
                                ),
                                error=focus_upload_payload_diagnostics.get(
                                    "error", "upload_payload_invalid"
                                ),
                                device_id=focus_segments_device_id,
                            )
                            focus_segments_result = {
                                "segments": [],
                                "full_sync": False,
                                "next_cursor": focus_segments_cursor_before or None,
                                "has_more": False,
                                "requested_count": 0,
                                "accepted_count": 0,
                                "accepted_segment_ids": [],
                            }
                        else:
                            focus_sync_metrics["delta_rpc_calls"] += 1
                            focus_sync_metrics["upload_rows"] += focus_segments_upload_count
                            focus_sync_metrics["request_bytes"] += _json_payload_bytes(
                                focus_segments_request_body
                            )
                            focus_segments_result = sync_rpc(
                                "lili_sync_focus_segments_delta_v2",
                                focus_segments_request_body,
                            )
                        focus_sync_metrics["response_bytes"] += _json_payload_bytes(
                            focus_segments_result
                        )
                    except (SocialError, AttributeError, TypeError) as exc:
                        focus_segments_sync_error = str(exc)[:240]
                        focus_segments_sync_duration_ms = round(
                            (time.monotonic() - focus_segments_sync_started)
                            * 1000,
                            1,
                        )
                        LOGGER.info("incremental focus segment sync deferred: %s", exc)
                        lifecycle_log(
                            "focus.segment_sync.transport",
                            cursor_before=focus_segments_cursor_before,
                            upload_count=focus_segments_upload_count,
                            returned_count=0,
                            cursor_after=focus_segments_cursor_before,
                            sync_mode=focus_segments_sync_mode,
                            device_id=focus_segments_device_id,
                            duration_ms=focus_segments_sync_duration_ms,
                            error=focus_segments_sync_error,
                        )
                    except Exception as exc:
                        focus_segments_sync_error = str(exc)[:240]
                        focus_segments_sync_duration_ms = round(
                            (time.monotonic() - focus_segments_sync_started)
                            * 1000,
                            1,
                        )
                        LOGGER.exception("incremental focus segment sync crashed")
                        lifecycle_log(
                            "focus.segment_sync.transport",
                            cursor_before=focus_segments_cursor_before,
                            upload_count=focus_segments_upload_count,
                            returned_count=0,
                            cursor_after=focus_segments_cursor_before,
                            sync_mode=focus_segments_sync_mode,
                            device_id=focus_segments_device_id,
                            duration_ms=focus_segments_sync_duration_ms,
                            error=focus_segments_sync_error,
                        )
                    if isinstance(focus_segments_result, dict):
                        focus_segments_result = dict(focus_segments_result)
                        uploaded_segments = list(
                            []
                            if focus_upload_gate_blocked
                            else (
                                focus_segments_request_body.get("p_segments")
                                if isinstance(focus_segments_request_body, dict)
                                else []
                            )
                        )
                        # A successful HTTP/RPC response is not sufficient:
                        # the server must explicitly acknowledge every sealed
                        # fact and return a complete ordered-stream page.
                        # Missing/partial ACKs, malformed rows, and an empty
                        # response that advances the cursor keep the whole
                        # transaction retryable.
                        if focus_upload_gate_blocked:
                            protocol_ok = False
                            protocol_error = str(
                                focus_upload_payload_diagnostics.get("error")
                                or "focus_upload_payload_invalid"
                            )
                            accepted_segment_ids: set[str] = set()
                        else:
                            protocol_ok, protocol_error, accepted_segment_ids = (
                                validate_focus_delta_response(
                                    focus_segments_result,
                                    uploaded_segments,
                                    cursor_before=focus_segments_cursor_before,
                                )
                            )
                        upload_ack_ok = protocol_ok
                        if not protocol_ok:
                            LOGGER.warning(
                                "focus segment delta response rejected: %s",
                                protocol_error,
                            )
                        focus_segments_result["_uploaded_segments"] = uploaded_segments
                        focus_segments_result["_upload_ack_ok"] = upload_ack_ok
                        focus_segments_result["_protocol_valid"] = protocol_ok
                        focus_segments_result["_protocol_error"] = protocol_error
                        focus_segments_result["_accepted_count"] = len(accepted_segment_ids)
                        focus_segments_result["_upload_payload_diagnostics"] = (
                            focus_upload_payload_diagnostics
                        )
                        focus_segments_sync_duration_ms = round(
                            (time.monotonic() - focus_segments_sync_started)
                            * 1000,
                            1,
                        )
                        focus_segments_result["_sync_mode"] = focus_segments_sync_mode
                        returned_segments = focus_segments_result.get("segments")
                        returned_count = (
                            len(returned_segments)
                            if isinstance(returned_segments, list)
                            else 0
                        )
                        focus_sync_metrics["returned_segment_rows"] += returned_count
                        if bool(focus_segments_result.get("full_sync")):
                            focus_sync_metrics["full_bootstrap_count"] += 1
                        focus_segments_result["_sync_diagnostics"] = {
                            "cursor_before": focus_segments_cursor_before,
                            "upload_count": focus_segments_upload_count,
                            "payload_input_count": focus_upload_payload_diagnostics.get(
                                "input_count", focus_segments_upload_count
                            ),
                            "payload_duplicate_segment_ids": focus_upload_payload_diagnostics.get(
                                "duplicate_segment_ids", []
                            ),
                            "payload_conflict_segment_ids": focus_upload_payload_diagnostics.get(
                                "conflict_segment_ids", []
                            ),
                            "requested_count": focus_segments_result.get("requested_count"),
                            "server_accepted_count": focus_segments_result.get("accepted_count"),
                            "accepted_count": len(accepted_segment_ids),
                            "returned_count": returned_count,
                            "cursor_after": str(
                                focus_segments_result.get("next_cursor") or ""
                            ),
                            "full_sync": bool(focus_segments_result.get("full_sync")),
                            "sync_mode": focus_segments_sync_mode,
                            "device_id": focus_segments_device_id,
                            "duration_ms": focus_segments_sync_duration_ms,
                            "error": "" if upload_ack_ok else (
                                protocol_error or "upload_ack_missing_or_mismatch"
                            ),
                            "request_bytes": _json_payload_bytes(
                                focus_segments_request_body
                            ),
                            "response_bytes": _json_payload_bytes(
                                focus_segments_result
                            ),
                        }
                        lifecycle_log(
                            "focus.segment_sync.transport",
                            cursor_before=focus_segments_cursor_before,
                            upload_count=focus_segments_upload_count,
                            requested_count=focus_segments_result.get("requested_count"),
                            server_accepted_count=focus_segments_result.get("accepted_count"),
                            accepted_count=len(accepted_segment_ids),
                            returned_count=returned_count,
                            cursor_after=str(
                                focus_segments_result.get("next_cursor") or ""
                            ),
                            full_sync=bool(focus_segments_result.get("full_sync")),
                            sync_mode=focus_segments_sync_mode,
                            device_id=focus_segments_device_id,
                            duration_ms=focus_segments_sync_duration_ms,
                            error="" if upload_ack_ok else (
                                protocol_error or "upload_ack_missing_or_mismatch"
                            ),
                        )
                    integrity_manifest = personal_state.get(
                        "focus_segment_integrity_manifest"
                    )
                    if isinstance(integrity_manifest, list) and integrity_manifest:
                        integrity_manifest_kind = str(
                            personal_state.get("focus_segment_integrity_manifest_kind")
                            or "integrity"
                        ).strip().lower()[:32]
                        integrity_request_body = {
                            "p_segment_ids": integrity_manifest,
                        }
                        focus_sync_metrics["integrity_rpc_calls"] += 1
                        if integrity_manifest_kind == "reconciliation":
                            focus_sync_metrics["reconciliation_rpc_calls"] += 1
                        focus_sync_metrics["manifest_rows"] += len(integrity_manifest)
                        focus_sync_metrics["manifest_bytes"] += _json_payload_bytes(
                            integrity_request_body
                        )
                        focus_sync_metrics["request_bytes"] += _json_payload_bytes(
                            integrity_request_body
                        )
                        integrity_started = time.monotonic()
                        try:
                            audit_result = sync_rpc(
                                "lili_focus_segment_integrity_v1",
                                integrity_request_body,
                            )
                            focus_sync_metrics["response_bytes"] += _json_payload_bytes(
                                audit_result
                            )
                            if isinstance(audit_result, dict):
                                focus_segment_integrity_result = dict(audit_result)
                                focus_segment_integrity_result[
                                    "_requested_segment_ids"
                                ] = list(integrity_manifest)
                        except (SocialError, AttributeError, TypeError) as exc:
                            LOGGER.info("focus segment integrity audit deferred: %s", exc)
                            focus_segment_integrity_result = {
                                "_error": str(exc)[:240],
                                "_requested_segment_ids": list(integrity_manifest),
                            }
                        except Exception as exc:
                            LOGGER.exception("focus segment integrity audit crashed")
                            focus_segment_integrity_result = {
                                "_error": str(exc)[:240],
                                "_requested_segment_ids": list(integrity_manifest),
                            }
                        if isinstance(focus_segment_integrity_result, dict):
                            focus_segment_integrity_result["_duration_ms"] = round(
                                (time.monotonic() - integrity_started) * 1000,
                                1,
                            )
                            focus_segment_integrity_result["_device_id"] = (
                                focus_segments_device_id
                            )
            # Active FocusSession intervals remain local/canonical until they
            # close.  Read the separate per-device liveness projection so the
            # display can union all currently active devices without mutating
            # the fact ledger.  The production client negative-caches a
            # missing RPC during mixed-version rollout; older test/backends
            # can still use the generic RPC path.
            live_projection_reader = getattr(self.client, "focus_live_projection", None)
            live_rpc = getattr(self.client, "rpc", None)
            if callable(live_projection_reader) or callable(live_rpc):
                try:
                    focus_live_projection_result = (
                        live_projection_reader()
                        if callable(live_projection_reader)
                        else live_rpc("lili_focus_live_projection", {})
                    )
                except (SocialError, AttributeError, TypeError) as exc:
                    LOGGER.info("focus live projection deferred: %s", exc)
            # Taunts are separate from room events because the receiver must
            # keep the state across devices until the first work heartbeat
            # plus twenty minutes.  Older relays may not know this optional
            # RPC yet; in that case the rest of the dashboard remains usable.
            taunt_rpc = getattr(self.client, "rpc", None)
            if self.presence.get("_include_reaction_state") and callable(taunt_rpc):
                try:
                    reaction_state = _unwrap_reaction_payload(
                        taunt_rpc("lili_reaction_state", {})
                    )
                    if reaction_state is not None:
                        taunt_state_result = _unwrap_single_reaction_state(
                            reaction_state.get("taunt")
                        )
                        encouragement_state_result = _unwrap_single_reaction_state(
                            reaction_state.get("encouragement")
                        )
                    # A mixed-version relay can return HTTP 200 with a
                    # partial/empty combined snapshot.  Do one compatibility
                    # read instead of silently dropping an active punishment.
                    if taunt_state_result is None:
                        taunt_state_result = _unwrap_single_reaction_state(
                            taunt_rpc("lili_taunt_state", {})
                        )
                except (SocialError, AttributeError, TypeError) as exc:
                    # Older relays know the original taunt RPC but not the
                    # combined reaction snapshot. Keep those clients usable
                    # without adding another request on the current backend.
                    LOGGER.info("combined reaction state deferred: %s", exc)
                    try:
                        taunt_state_result = _unwrap_single_reaction_state(
                            taunt_rpc("lili_taunt_state", {})
                        )
                    except (SocialError, AttributeError, TypeError) as fallback_exc:
                        LOGGER.info("taunt state sync deferred: %s", fallback_exc)
            room_id = self.presence.get("room_id")
            try:
                data = self.client.dashboard(room_id=room_id)
            except TypeError:
                # Keep third-party/test backends compatible while they adopt
                # the room-scoped dashboard argument.
                data = self.client.dashboard()
            # The leaderboard is a low-frequency view, not presence data.
            # Fetching it on every passive dashboard cycle caused a slow
            # ranking RPC to hold up the completed signal and repaint path.
            leaderboard = getattr(self.client, "focus_leaderboard", None)
            if (
                self.presence.get("_include_leaderboard")
                and callable(leaderboard)
                and getattr(self.client, "signed_in", True)
            ):
                try:
                    data = dict(data or {})
                    data["leaderboard"] = leaderboard(period="week")
                except (SocialError, TypeError):
                    # A missing/temporarily unavailable economy RPC must not
                    # make the room heartbeat fail or clear the cached rows.
                    pass
            if heartbeat_error:
                data = dict(data or {})
                data["_presence_heartbeat_error"] = heartbeat_error
            if presence_context_updated is not None:
                data = dict(data or {})
                data["_presence_context_updated"] = presence_context_updated
            if isinstance(focus_history_result, dict):
                data = dict(data or {})
                data["_focus_history"] = focus_history_result
            if isinstance(focus_segments_result, dict):
                data = dict(data or {})
                data["_focus_segments"] = focus_segments_result
            if isinstance(focus_segment_integrity_result, dict):
                data = dict(data or {})
                data["_focus_segment_integrity"] = focus_segment_integrity_result
            if isinstance(focus_live_projection_result, dict):
                data = dict(data or {})
                data["_focus_live_projection"] = focus_live_projection_result
            if isinstance(personal_state_result, dict):
                data = dict(data or {})
                # The dashboard function on older deployments does not yet
                # expose the account lifetime/outfit columns.  Keep the
                # merged RPC response alongside it so a second computer can
                # still recover the unlock count and selected wardrobe.
                data["_personal_state"] = personal_state_result
            if isinstance(taunt_state_result, dict):
                data = dict(data or {})
                data["_taunt_state"] = taunt_state_result
            if isinstance(encouragement_state_result, dict):
                data = dict(data or {})
                data["_encouragement_state"] = encouragement_state_result
            data = dict(data or {})
            data["_focus_sync_metrics"] = dict(focus_sync_metrics)
            if isinstance(data, dict) and self.request_generation:
                data = dict(data)
                data["_request_generation"] = self.request_generation
            self.completed.emit(data)
        except SocialError as exc:
            cached_loader = getattr(self.client, "cached_dashboard", None)
            cached = cached_loader(self.presence.get("room_id")) if callable(cached_loader) else None
            if cached is not None:
                cached = dict(cached)
                cached["_focus_sync_metrics"] = dict(focus_sync_metrics)
                if presence_context_updated is not None:
                    cached["_presence_context_updated"] = presence_context_updated
                if self.request_generation:
                    cached["_request_generation"] = self.request_generation
                self.completed.emit(cached)
            else:
                self.failed.emit(str(exc))


class SocialDashboardThread(QThread):
    """Fetch one dashboard without blocking the Qt GUI thread."""

    completed = Signal(dict)
    failed = Signal(object)

    def __init__(
        self,
        client: SocialClient,
        room_id: str | None,
        parent=None,
        *,
        force_auxiliary_refresh: bool = False,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.room_id = room_id
        self.force_auxiliary_refresh = bool(force_auxiliary_refresh)

    def run(self) -> None:
        try:
            try:
                data = self.client.dashboard(
                    room_id=self.room_id,
                    force_auxiliary_refresh=self.force_auxiliary_refresh,
                )
            except TypeError:
                # Keep small offline/test backends compatible with the room
                # scoped dashboard while the real request stays off the GUI.
                try:
                    data = self.client.dashboard(room_id=self.room_id)
                except TypeError:
                    data = self.client.dashboard()
            self.completed.emit(dict(data or {}))
        except SocialError as exc:
            cached_loader = getattr(self.client, "cached_dashboard", None)
            cached = cached_loader(self.room_id) if callable(cached_loader) else None
            if cached is not None:
                self.completed.emit(cached)
            else:
                self.failed.emit(exc)


class SocialLeaderboardThread(QThread):
    """Load the optional leaderboard after the main room snapshot renders."""

    completed = Signal(list)
    failed = Signal(object)

    def __init__(self, client: SocialClient, parent=None) -> None:
        super().__init__(parent)
        self.client = client

    def run(self) -> None:
        try:
            leaderboard = getattr(self.client, "focus_leaderboard", None)
            rows = leaderboard(period="week") if callable(leaderboard) else []
            self.completed.emit(list(rows or []) if isinstance(rows, list) else [])
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception as exc:
            self.failed.emit(SocialError(str(exc), kind="network", retryable=True))


class SocialHealthThread(QThread):
    """Probe the configured social endpoint without blocking the UI."""

    completed = Signal(dict)
    failed = Signal(object)

    def __init__(self, client: SocialClient, room_id: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.room_id = room_id

    def run(self) -> None:
        try:
            checker = getattr(self.client, "diagnose_connection", None)
            if callable(checker):
                self.completed.emit(dict(checker(room_id=self.room_id) or {}))
                return
            checker = getattr(self.client, "health", None)
            if not callable(checker):
                raise SocialError("当前自习室后端未提供健康检查。", kind="config")
            self.completed.emit(dict(checker() or {}))
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception as exc:
            self.failed.emit(SocialError(f"健康检查失败：{exc}", kind="network"))


class SocialLoginThread(QThread):
    """Authenticate without blocking the Qt GUI thread."""

    completed = Signal(object)
    failed = Signal(object)

    def __init__(self, client: SocialClient, email: str, password: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.email = email
        self._password = password

    def run(self) -> None:
        try:
            self.client.sign_in(self.email, self._password)
            streak = {}
            recorder = getattr(self.client, "record_login_streak", None)
            if callable(recorder):
                try:
                    streak = dict(recorder() or {})
                except SocialError as exc:
                    # Login is already valid. A rollout race or a temporary
                    # RPC outage must not turn a successful login into a
                    # failed one; the next app launch can record the day.
                    LOGGER.info("login streak record deferred: %s", exc)
                except Exception as exc:
                    LOGGER.info("login streak record deferred: %s", exc)
            self.completed.emit(streak)
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception:
            # Never surface an unexpected transport exception or credentials
            # from the worker thread.  The UI can still offer a retry.
            self.failed.emit(
                SocialError("登录请求失败，请稍后重试。", kind="network", retryable=True)
            )
        finally:
            self._password = ""


class SocialLoginStreakThread(QThread):
    """Record a restored session's once-per-Beijing-day login."""

    completed = Signal(dict)
    failed = Signal(object)

    def __init__(self, client: SocialClient, parent=None) -> None:
        super().__init__(parent)
        self.client = client

    def run(self) -> None:
        try:
            recorder = getattr(self.client, "record_login_streak", None)
            self.completed.emit(dict(recorder() or {}) if callable(recorder) else {})
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception as exc:
            self.failed.emit(SocialError(str(exc), kind="network", retryable=True))


class SocialSignupThread(QThread):
    """Create an account without blocking the Qt GUI thread on SMTP."""

    completed = Signal(object)
    failed = Signal(object)

    def __init__(
        self,
        client: SocialClient,
        email: str,
        password: str,
        nickname: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.email = email
        self._password = password
        self.nickname = nickname

    def run(self) -> None:
        try:
            self.completed.emit(
                self.client.sign_up(self.email, self._password, self.nickname)
            )
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception:
            # Keep unexpected transport/client failures user-safe and off the
            # GUI thread. Do not include credentials in the error text.
            self.failed.emit(
                SocialError("注册请求失败，请稍后重试。", kind="network", retryable=True)
            )
        finally:
            self._password = ""


class SocialResendConfirmationThread(QThread):
    """Resend a confirmation email without freezing the Qt GUI thread."""

    completed = Signal()
    failed = Signal(object)

    def __init__(self, client: SocialClient, email: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.email = email

    def run(self) -> None:
        try:
            self.client.resend_confirmation(self.email)
            self.completed.emit()
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception:
            self.failed.emit(
                SocialError("确认邮件重发失败，请稍后重试。", kind="network", retryable=True)
            )


class SocialChangePasswordThread(QThread):
    """Change a password away from the Qt GUI thread."""

    completed = Signal()
    failed = Signal(object)

    def __init__(self, client: SocialClient, current_password: str, new_password: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self._current_password = current_password
        self._new_password = new_password

    def run(self) -> None:
        try:
            self.client.change_password(self._current_password, self._new_password)
            self.completed.emit()
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception:
            self.failed.emit(SocialError("密码修改失败，请稍后重试。", kind="network", retryable=True))
        finally:
            self._current_password = ""
            self._new_password = ""


class SocialPasswordResetThread(QThread):
    """Request a recovery email without blocking the GUI thread."""

    completed = Signal()
    failed = Signal(object)

    def __init__(self, client: SocialClient, email: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.email = email

    def run(self) -> None:
        try:
            self.client.request_password_reset(self.email)
            self.completed.emit()
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception:
            self.failed.emit(SocialError("密码重置邮件发送失败，请稍后重试。", kind="network", retryable=True))


class SocialPasswordOtpResetThread(QThread):
    """Verify the in-memory recovery OTP and set the new password."""

    completed = Signal()
    failed = Signal(object)

    def __init__(self, client: SocialClient, email: str, otp: str, new_password: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.email = email
        self._otp = otp
        self._new_password = new_password

    def run(self) -> None:
        try:
            self.client.verify_password_reset_otp(self.email, self._otp)
            # Clear the code before the password update. The app never writes
            # the OTP to settings, logs, files, or the credential store.
            self._otp = ""
            self.client.set_password_after_reset(self._new_password)
            self.completed.emit()
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception:
            self.failed.emit(SocialError("验证码验证或密码修改失败，请检查验证码后重试。", kind="network", retryable=True))
        finally:
            self._otp = ""
            self._new_password = ""


class PasswordResetDialog(QDialog):
    """Email OTP recovery flow; the server enforces the ten-minute expiry."""

    password_reset_completed = Signal(str)

    def __init__(self, client: SocialClient, email: str = "", parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.email = str(email or "").strip()
        self._request_thread: SocialPasswordResetThread | None = None
        self._verify_thread: SocialPasswordOtpResetThread | None = None
        self._remaining_seconds = 0
        self.setWindowTitle("邮箱验证码重置密码 - Lili")
        self.setModal(True)
        self.resize(470, 430)
        self.setStyleSheet("""
            QDialog { background:#edf4f7; }
            QLabel { color:#263746; }
            QLabel#title { font-size:22px; font-weight:700; }
            QLabel#muted { color:#667984; }
            QLabel#status { background:#e1efec; color:#087f74; border-radius:8px; padding:7px 10px; }
            QLineEdit { background:white; border:1px solid #b8ccd6; border-radius:8px; padding:7px; }
            QPushButton { background:#d8efeb; color:#075d57; border:0; border-radius:8px; padding:8px 12px; }
            QPushButton:hover { background:#c7e5e1; }
            QPushButton#link { background:transparent; color:#087f74; text-align:left; padding:2px; }
        """)
        layout = QVBoxLayout(self); layout.setSpacing(10)
        title = QLabel("邮箱验证码重置密码"); title.setObjectName("title"); layout.addWidget(title)
        hint = QLabel("输入注册邮箱，发送 6 位验证码；验证码 10 分钟内有效，过期后必须重新发送。Lili 不保存验证码。")
        hint.setObjectName("muted"); hint.setWordWrap(True); layout.addWidget(hint)
        form = QFormLayout()
        self.email_edit = QLineEdit(self.email); self.email_edit.setPlaceholderText("注册邮箱")
        self.otp_edit = QLineEdit(); self.otp_edit.setPlaceholderText("邮件中的 6 位数字验证码"); self.otp_edit.setMaxLength(10)
        self.new_password = QLineEdit(); self.new_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_password = QLineEdit(); self.confirm_password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("邮箱", self.email_edit); layout.addLayout(form)
        otp_row = QHBoxLayout(); otp_row.addWidget(self.otp_edit, 1)
        self.send_button = QPushButton("发送验证码"); self.send_button.clicked.connect(self._send_code); otp_row.addWidget(self.send_button)
        layout.addLayout(otp_row)
        self.countdown = QLabel("尚未发送验证码"); self.countdown.setObjectName("muted"); layout.addWidget(self.countdown)
        password_form = QFormLayout(); password_form.addRow("新密码", self.new_password); password_form.addRow("确认新密码", self.confirm_password); layout.addLayout(password_form)
        self.status_label = QLabel(); self.status_label.setObjectName("status"); self.status_label.setWordWrap(True); self.status_label.hide(); layout.addWidget(self.status_label)
        self.verify_button = QPushButton("确认修改密码"); self.verify_button.setEnabled(False); self.verify_button.clicked.connect(self._verify_and_reset); layout.addWidget(self.verify_button)
        cancel = QPushButton("取消"); cancel.clicked.connect(self.reject); layout.addWidget(cancel)
        layout.addStretch(1)
        self._countdown_timer = QTimer(self); self._countdown_timer.setInterval(1000); self._countdown_timer.timeout.connect(self._tick)

    def _show_status(self, message: str, *, error: bool = False) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            f"background:{'#f7e5e5' if error else '#e1efec'};color:{'#a33a3a' if error else '#087f74'};border-radius:8px;padding:7px 10px;"
        )
        self.status_label.show()

    def _send_code(self) -> None:
        email = self.email_edit.text().strip()
        if not email or "@" not in email:
            self._show_status("请输入有效的注册邮箱。", error=True); return
        if self._request_thread is not None and self._request_thread.isRunning():
            return
        self.email = email
        self.send_button.setEnabled(False); self.verify_button.setEnabled(False)
        self._show_status("正在发送验证码…")
        thread = SocialPasswordResetThread(self.client, email, self)
        self._request_thread = thread
        thread.completed.connect(self._code_sent)
        thread.failed.connect(self._code_failed)
        thread.finished.connect(lambda: self._request_finished(thread), Qt.ConnectionType.QueuedConnection)
        thread.start()

    def _code_sent(self) -> None:
        self._remaining_seconds = 600
        self._countdown_timer.start()
        self.verify_button.setEnabled(True)
        self._show_status("如果该邮箱已注册，验证码已发送；请检查收件箱和垃圾邮件。")

    def _code_failed(self, error: object) -> None:
        exc = error if isinstance(error, Exception) else SocialError(str(error), kind="network")
        self._show_status(social_user_message(exc), error=True)

    def _request_finished(self, thread: SocialPasswordResetThread) -> None:
        if self._request_thread is thread:
            self._request_thread = None
        self.send_button.setEnabled(True); thread.deleteLater()

    def _tick(self) -> None:
        self._remaining_seconds = max(0, self._remaining_seconds - 1)
        minutes, seconds = divmod(self._remaining_seconds, 60)
        if self._remaining_seconds:
            self.countdown.setText(f"验证码剩余有效时间：{minutes:02d}:{seconds:02d}")
        else:
            self._countdown_timer.stop(); self.verify_button.setEnabled(False); self.countdown.setText("验证码已过期，请重新发送。")

    def _verify_and_reset(self) -> None:
        email = self.email_edit.text().strip(); otp = self.otp_edit.text().strip()
        new = self.new_password.text(); confirm = self.confirm_password.text()
        if self._remaining_seconds <= 0:
            self._show_status("验证码已过期，请重新发送。", error=True); return
        if not otp.isdigit() or len(otp) != 6:
            self._show_status("请输入邮件中的 6 位数字验证码。", error=True); return
        if len(new) < 8:
            self._show_status("新密码至少需要 8 位。", error=True); return
        if new != confirm:
            self._show_status("两次输入的新密码不一致。", error=True); return
        if self._verify_thread is not None and self._verify_thread.isRunning():
            return
        self.send_button.setEnabled(False); self.verify_button.setEnabled(False); self._show_status("正在验证验证码并修改密码…")
        thread = SocialPasswordOtpResetThread(self.client, email, otp, new, self)
        self._verify_thread = thread
        thread.completed.connect(self._reset_succeeded)
        thread.failed.connect(self._reset_failed)
        thread.finished.connect(lambda: self._verify_finished(thread), Qt.ConnectionType.QueuedConnection)
        thread.start()

    def _reset_succeeded(self) -> None:
        self._countdown_timer.stop(); self.otp_edit.clear(); self.new_password.clear(); self.confirm_password.clear()
        self._show_status("密码已修改成功，现在可以使用新密码登录自习室。")
        self.password_reset_completed.emit(self.email)
        self.accept()

    def _reset_failed(self, error: object) -> None:
        exc = error if isinstance(error, Exception) else SocialError(str(error), kind="network")
        self.verify_button.setEnabled(self._remaining_seconds > 0)
        self._show_status(social_user_message(exc), error=True)

    def _verify_finished(self, thread: SocialPasswordOtpResetThread) -> None:
        if self._verify_thread is thread:
            self._verify_thread = None
        self.send_button.setEnabled(True); thread.deleteLater()


class SocialDeleteAccountThread(QThread):
    """Re-authenticate and delete the current account away from the GUI."""

    completed = Signal()
    failed = Signal(object)

    def __init__(self, client: SocialClient, email: str, current_password: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.email = email
        self._current_password = current_password

    def run(self) -> None:
        try:
            # Require a fresh password check before the destructive RPC. The
            # RPC itself derives the user from auth.uid(), never from client
            # supplied IDs or email addresses.
            self.client.sign_in(self.email, self._current_password)
            self.client.delete_account()
            self.completed.emit()
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception:
            self.failed.emit(SocialError("注销账号失败，请稍后重试。", kind="network", retryable=True))
        finally:
            self._current_password = ""


class SocialEventThread(QThread):
    """Send a room event without freezing pet animation or the study window."""

    completed = Signal()
    failed = Signal(str)

    def __init__(self, client: SocialClient, event: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.event = event

    def run(self) -> None:
        try:
            kind = str(self.event.get("kind") or "")
            sender = getattr(self.client, "send_interaction", None)
            if kind in {"poke", "cheer", "drink"} and callable(sender):
                sender(
                    target=str(self.event.get("target_id") or ""),
                    kind=kind,
                    room_id=str(self.event.get("room_id") or "") or None,
                )
            else:
                self.client.record_room_event(**self.event)
            self.completed.emit()
        except (SocialError, AttributeError) as exc:
            self.failed.emit(str(exc))


class SocialVisitResponseThread(QThread):
    """Accept or reject one incoming visit/food request off the UI thread."""

    completed = Signal(dict, bool)
    failed = Signal(dict, str)

    def __init__(self, client: SocialClient, event: dict[str, Any], accept: bool, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.event = dict(event)
        self.accept = bool(accept)

    def run(self) -> None:
        try:
            self.client.rpc(
                "lili_respond_visit",
                {"event_id": str(self.event.get("id") or ""), "accept": self.accept},
            )
            self.completed.emit(self.event, self.accept)
        except (SocialError, AttributeError, TypeError) as exc:
            self.failed.emit(self.event, str(exc))


class SocialProfileThread(QThread):
    """Persist an owner nickname away from the Qt GUI thread."""

    completed = Signal()
    failed = Signal(str)

    def __init__(self, client: SocialClient, nickname: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.nickname = nickname

    def run(self) -> None:
        try:
            self.client.update_owner_nickname(self.nickname)
            self.completed.emit()
        except (SocialError, AttributeError) as exc:
            self.failed.emit(str(exc))


class SocialBuddyRpcThread(QThread):
    """Run buddy lookup/request actions away from the Qt GUI thread."""

    completed = Signal(object)
    failed = Signal(object)

    def __init__(self, client: SocialClient, name: str, body: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.name = name
        self.body = dict(body)

    def run(self) -> None:
        try:
            self.completed.emit(self.client.rpc(self.name, self.body))
        except SocialError as exc:
            self.failed.emit(exc)
        except Exception:
            self.failed.emit(
                SocialError("搭子服务暂时没有响应，请稍后重试。", kind="network", retryable=True)
            )


class BuddyCodeDialog(QDialog):
    """仅负责输入搭子码；网络查找和发送申请由后台线程完成。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("查找搭子")
        self.setModal(True)
        self.setMinimumWidth(330)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("输入对方的 8 位搭子码"))
        hint = QLabel("先查找并确认资料，不会直接建立搭子关系。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.code_edit = QLineEdit()
        self.code_edit.setMaxLength(8)
        self.code_edit.setPlaceholderText("例如 AB12CD34")
        self.code_edit.setInputMethodHints(Qt.InputMethodHint.ImhUppercaseOnly)
        layout.addWidget(self.code_edit)
        buttons = QHBoxLayout()
        find = QPushButton("查找")
        cancel = QPushButton("取消")
        find.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        self.code_edit.returnPressed.connect(self.accept)
        buttons.addStretch()
        buttons.addWidget(find)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        self.code_edit.setFocus()

    @property
    def code(self) -> str:
        return self.code_edit.text().strip().upper()


class BuddyProfileDialog(QDialog):
    """展示查找到的搭子资料，并把申请动作交给用户明确确认。"""

    def __init__(self, profile_text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("搭子资料确认")
        self.setModal(True)
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        title = QLabel("已找到搭子资料")
        title.setObjectName("title")
        layout.addWidget(title)

        profile = QLabel(profile_text)
        profile.setWordWrap(True)
        layout.addWidget(profile)

        hint = QLabel("确认资料后，点击“提交搭子申请”才会发送申请；点击“返回”不会发送。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.submit_button = QPushButton("提交搭子申请")
        self.return_button = QPushButton("返回")
        self.submit_button.clicked.connect(self.accept)
        self.return_button.clicked.connect(self.reject)
        buttons.addWidget(self.submit_button)
        buttons.addWidget(self.return_button)
        layout.addLayout(buttons)
        self.return_button.setFocus()


class BuddyCardWidget(QWidget):
    """把搭子的在线、工作和今日时长显示成一眼能看清的卡片。"""

    interaction_requested = Signal(dict, str)
    food_interaction_requested = Signal(dict, str)
    interaction_blocked = Signal(str)
    subscription_requested = Signal(dict, bool)

    def __init__(self, buddy: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.buddy = buddy
        self._cooldown_seconds = 15
        self._cooldown_until: dict[str, float] = {}
        self._buttons: dict[str, QPushButton] = {}
        self._food_buttons: dict[str, QPushButton] = {}
        self.setObjectName("buddyCard")
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 5, 8, 5)
        root.setSpacing(2)
        uncertain = bool(buddy.get("presence_uncertain"))
        status = _presence_status(buddy)
        online_flag = buddy.get("online")
        online = (
            status != "offline"
            and (online_flag is None or bool(online_flag))
            and not bool(buddy.get("stale_presence"))
        )
        nickname = _owner_nickname(buddy)
        is_self = bool(buddy.get("is_self"))
        if status == "unknown":
            if bool(buddy.get("online")) and bool(buddy.get("working")):
                status_text = "正在工作（同步恢复中）"
            elif bool(buddy.get("online")):
                status_text = "在线待确认"
            else:
                status_text = "状态待确认"
        else:
            status_text = {"focus": "正在工作", "rest": "正在休息", "offline": "已离线"}[status]
        headline = QLabel(
            f"{'🟡' if uncertain else '🟢' if online else '⚪'}  {_owner_label(buddy)}"
            f"{status_text}{'（我）' if is_self else ''}"
        )
        self._headline_label = headline
        headline.setWordWrap(False)
        headline.setStyleSheet("font-size:14px;font-weight:600;color:#203847;")
        root.addWidget(headline)
        duration = buddy.get("today_seconds")
        week_duration = buddy.get("week_seconds")
        if uncertain:
            age = int(buddy.get("presence_age_seconds") or 0)
            age_text = f"约 {max(1, age // 60)} 分钟前" if age else "刚才"
            time_text = f"实时状态暂无法确认（最后确认{age_text}），正在自动恢复"
        elif buddy.get("stale_presence"):
            time_text = "离线缓存；上次状态不计入当前专注"
        else:
            today_text = "今日专注时长已隐藏" if duration is None else f"今日已专注 {format_work_duration(duration)}"
            week_text = "本周专注时长已隐藏" if week_duration is None else f"本周已专注 {format_work_duration(week_duration)}"
            time_text = f"{today_text}　·　{week_text}"
        focus = QLabel(time_text)
        self._focus_label = focus
        focus.setStyleSheet("font-size:14px;font-weight:700;color:#087f74;")
        root.addWidget(focus)
        quick_status = str(buddy.get("quick_status") or "").strip()
        expires = str(buddy.get("quick_status_expires_at") or "")
        if quick_status and (not expires or expires > datetime.now().astimezone().isoformat()):
            quick = QLabel(f"状态：{quick_status[:40]}")
            quick.setStyleSheet("color:#b36b2c;font-size:11px;font-weight:600;")
            root.addWidget(quick)
        outfit = str(buddy.get("outfit_key") or "经典六毛")
        footer = QLabel(f"娃衣：{outfit}")
        self._footer_label = footer
        footer.setStyleSheet("color:#61727d;font-size:11px;")
        footer.setWordWrap(False)
        footer.setToolTip(f"当前娃衣：{outfit} · 可以直接对这位搭子串门、嘲讽或送补给")
        root.addWidget(footer)
        actions = QGridLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setHorizontalSpacing(4)
        actions.setVerticalSpacing(3)
        # The action follows the buddy's confirmed state: focus -> cheer;
        # rest/offline -> taunt.  The server repeats this check authoritatively
        # when the interaction is sent.
        action_specs = (
            ("visit", "串门"),
            ("cheer", _reaction_label(buddy)),
            ("food_coffee", "请咖啡"),
            ("food_milk_tea", "请奶茶"),
            ("food_tea", "敬茶"),
            ("food_cake", "请蛋糕"),
        )
        for index, (kind, label) in enumerate(action_specs):
            button = QPushButton(label)
            # Keep the compact two-row grid, but leave enough touch/trackpad
            # area for the supply actions on both Windows and macOS.
            button.setFixedHeight(32)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            button.setStyleSheet("font-size:11px;padding:2px 4px;border-radius:7px;")
            if kind.startswith("food_"):
                button.clicked.connect(lambda _checked=False, action_kind=kind: self._request_food(action_kind))
                if is_self:
                    button.setEnabled(False)
                    button.setToolTip("补给按钮只对房间里的其他搭子开放")
                self._food_buttons[kind] = button
            else:
                button.clicked.connect(lambda _checked=False, action=kind: self._request_interaction(action))
                if is_self:
                    button.setEnabled(False)
                    button.setToolTip("互动按钮只对房间里的其他搭子开放")
                self._buttons[kind] = button
            actions.addWidget(button, index // 3, index % 3)
        root.addLayout(actions)
        if not is_self:
            subscribe = QCheckBox("订阅开工/下班提醒")
            subscribe.setFixedHeight(18)
            subscribe.setChecked(bool(buddy.get("subscribed")))
            subscribe.stateChanged.connect(lambda state: self.subscription_requested.emit(self.buddy, bool(state)))
            root.addWidget(subscribe)

    def update_buddy(self, buddy: dict[str, Any]) -> None:
        """Update live status/time labels without rebuilding the card tree."""

        self.buddy = dict(buddy)
        uncertain = bool(buddy.get("presence_uncertain"))
        status = _presence_status(buddy)
        online_flag = buddy.get("online")
        online = (
            status != "offline"
            and (online_flag is None or bool(online_flag))
            and not bool(buddy.get("stale_presence"))
        )
        if status == "unknown":
            if bool(buddy.get("online")) and bool(buddy.get("working")):
                status_text = "正在工作（同步恢复中）"
            elif bool(buddy.get("online")):
                status_text = "在线待确认"
            else:
                status_text = "状态待确认"
        else:
            status_text = {"focus": "正在工作", "rest": "正在休息", "offline": "已离线"}[status]
        nickname = _owner_nickname(buddy)
        is_self = bool(buddy.get("is_self"))
        self._headline_label.setText(
            f"{'🟡' if uncertain else '🟢' if online else '⚪'}  {_owner_label(buddy)}"
            f"{status_text}{'（我）' if is_self else ''}"
        )
        duration = buddy.get("today_seconds")
        week_duration = buddy.get("week_seconds")
        if uncertain:
            age = int(buddy.get("presence_age_seconds") or 0)
            age_text = f"约 {max(1, age // 60)} 分钟前" if age else "刚才"
            time_text = f"实时状态暂无法确认（最后确认{age_text}），正在自动恢复"
        elif buddy.get("stale_presence"):
            time_text = "离线缓存；上次状态不计入当前专注"
        else:
            today_text = "今日专注时长已隐藏" if duration is None else f"今日已专注 {format_work_duration(duration)}"
            week_text = "本周专注时长已隐藏" if week_duration is None else f"本周已专注 {format_work_duration(week_duration)}"
            time_text = f"{today_text}　·　{week_text}"
        self._focus_label.setText(time_text)
        outfit = str(buddy.get("outfit_key") or "经典六毛")
        self._footer_label.setText(f"娃衣：{outfit}")
        self._footer_label.setToolTip(f"当前娃衣：{outfit} · 可以直接对这位搭子串门、嘲讽或送补给")
        cheer = self._buttons.get("cheer")
        if cheer is not None and cheer.isEnabled():
            cheer.setText(_reaction_label(buddy))

    def _request_food(self, kind: str) -> None:
        now = time.monotonic()
        key = f"food:{kind}"
        remaining = self._cooldown_un…22539 tokens truncated…li_respond_visit", {"event_id": event_id, "accept": True})
                self.food_interaction_accepted.emit(visit)
            except SocialError as exc:
                self._auto_accepting_food.discard(event_id)
                self._error(exc)
                continue
            self.refresh()

    def _send_food_interaction(self, buddy: dict[str, Any], kind: str) -> None:
        if not self._require_login():
            return
        self.food_interaction_requested.emit(buddy, kind)

    def _send_phrase(self, phrase: str) -> None:
        """Send one short room phrase to a selected/first peer."""

        if not self._require_login() or not self.current_room_id:
            self._set_status("请先加入一个共同房间，再发送房间短语。", error=True)
            return
        people = getattr(self, "_room_people", [])
        target = next((person for person in people if not person.get("is_self")), None)
        if target is None:
            self._set_status("当前房间还没有可接收短语的搭子。", error=True)
            return
        nickname = _owner_nickname(target)
        event = {"room_id": self.current_room_id, "kind": "phrase", "target_id": str(target.get("user_id") or ""), "message": phrase[:80]}
        thread = SocialEventThread(self.client, event, self)
        self._event_threads.append(thread)
        thread.completed.connect(lambda: self._interaction_sent(nickname, "phrase"), Qt.ConnectionType.QueuedConnection)
        thread.failed.connect(lambda message: self._set_status(f"短语没有送出：{social_user_message(SocialError(str(message), kind='network'))}", error=True), Qt.ConnectionType.QueuedConnection)
        thread.finished.connect(lambda: self._event_thread_finished(thread), Qt.ConnectionType.QueuedConnection)
        self._set_status(f"正在向 {nickname} 发送“{phrase}”…")
        thread.start()

    def _set_focus_task(self) -> None:
        title, ok = QInputDialog.getText(self, "一次只盯一件事", "目标：", text="完成当前最重要的一件事")
        if not ok or not title.strip():
            return
        minutes, ok = QInputDialog.getInt(self, "任务倒计时", "距离截止还有多少分钟（0 表示不倒计时）：", 60, 0, 7 * 24 * 60, 5)
        if not ok:
            return
        self.focus_task_requested.emit(title.strip()[:120], minutes)
        self._set_status("本轮任务已保存到本机，计时和倒计时会共用这一项目标。")

    def _set_tomorrow_review(self) -> None:
        title, ok = QInputDialog.getText(self, "轻量复盘", "明天打开时最先做什么？")
        if not ok:
            return
        self.tomorrow_review_requested.emit(title.strip()[:160])
        self._set_status("明天第一件事已记在本机。")

    def _set_room_schedule(self) -> None:
        if not self._require_login() or not self.current_room_id:
            return
        start, ok = QInputDialog.getText(self, "一起开工/收工", "开工时间（HH:MM）：", text="21:00")
        if not ok:
            return
        end, ok = QInputDialog.getText(self, "一起开工/收工", "收工时间（HH:MM）：", text="23:00")
        if not ok:
            return
        try:
            datetime.strptime(start.strip(), "%H:%M")
            datetime.strptime(end.strip(), "%H:%M")
        except ValueError:
            self._set_status("时间请填写成 HH:MM，例如 21:00。", error=True)
            return
        try:
            setter = getattr(self.client, "set_room_schedule", None)
            if callable(setter):
                setter(room_id=self.current_room_id, start_at=start.strip(), end_at=end.strip(), enabled=True)
            else:
                self.client.rpc("lili_set_room_schedule", {"p_room_id": self.current_room_id, "p_start_at": start.strip(), "p_end_at": end.strip(), "p_enabled": True})
            self._set_status(f"已设定 {start.strip()} 一起开工，{end.strip()} 一起收工。")
            self._refresh_selected_room()
        except SocialError as exc:
            self._error(exc)

    def _set_room_challenge(self) -> None:
        if not self._require_login() or not self.current_room_id:
            return
        title, ok = QInputDialog.getText(self, "共同挑战", "挑战名称：", text="今晚一起完成 4 小时")
        if not ok or not title.strip():
            return
        hours, ok = QInputDialog.getInt(self, "共同挑战", "共同专注小时数：", 4, 1, 72, 1)
        if not ok:
            return
        rounds, ok = QInputDialog.getInt(self, "共同挑战", "每位成员至少完成几轮：", 3, 1, 30, 1)
        if not ok:
            return
        try:
            setter = getattr(self.client, "set_room_challenge", None)
            if callable(setter):
                setter(room_id=self.current_room_id, title=title.strip()[:80], target_seconds=hours * 3600, target_rounds=rounds)
            else:
                self.client.rpc("lili_set_room_challenge", {"p_room_id": self.current_room_id, "p_title": title.strip()[:80], "p_target_seconds": hours * 3600, "p_target_rounds": rounds})
            self._set_status("共同挑战已保存，完成时会写入房间动态。")
            self._refresh_selected_room()
        except SocialError as exc:
            self._error(exc)

    def _interaction_sent(self, nickname: str, kind: str) -> None:
        labels = {
            "poke": "戳了一下", "cheer": "送上加油", "taunt": "发起嘲讽", "encouragement": "送来鼓励",
            "drink": "递了一杯奶茶", "phrase": "发送了快速短语",
        }
        suffix = (
            "；连续有效工作 20 分钟即可赎身。"
            if kind == "taunt"
            else "；鼓励状态最多持续 1 小时，期间暂停工作会立即结束。"
            if kind == "encouragement"
            else "；对方房间动态会显示这次互动。"
        )
        self._set_status(f"{PET_NAME}已向 {nickname} {labels.get(kind, '送出互动')}{suffix}")
        QTimer.singleShot(0, self._refresh_selected_room)

    def _event_thread_finished(self, thread: SocialEventThread) -> None:
        if thread in self._event_threads:
            self._event_threads.remove(thread)
        thread.deleteLater()

    def _render_room_people(self, people: list[dict[str, Any]]) -> None:
        if not hasattr(self, "room_members"):
            return
        self._room_people = list(people)
        valid_people = [
            dict(buddy) for buddy in people
            if isinstance(buddy, dict)
            and str(buddy.get("user_id") or buddy.get("id") or "").strip()
        ]
        ordered_ids = [
            str(buddy.get("user_id") or buddy.get("id") or "").strip()
            for buddy in valid_people
        ]
        existing_ids = list(self._room_pet_card_widgets)
        if ordered_ids != existing_ids:
            self.room_members.clear()
            self._room_pet_card_widgets.clear()
            for buddy_id, buddy in zip(ordered_ids, valid_people):
                item = QListWidgetItem()
                widget = RoomPetCardWidget(buddy, self.room_members)
                widget.interaction_requested.connect(self._send_interaction)
                widget.food_interaction_requested.connect(self._send_food_interaction)
                item.setData(Qt.ItemDataRole.UserRole, buddy)
                self.room_members.addItem(item)
                self.room_members.setItemWidget(item, widget)
                self._set_buddy_item_height(item, widget)
                self._room_pet_card_widgets[buddy_id] = (item, widget)
            if not valid_people:
                empty = QListWidgetItem("加入房间后，这里会显示一起专注的六毛和累计时长。")
                empty.setFlags(Qt.ItemFlag.NoItemFlags)
                self.room_members.addItem(empty)
            self._fit_list_height(self.room_members, 120, 360)
        else:
            for buddy_id, buddy in zip(ordered_ids, valid_people):
                item, widget = self._room_pet_card_widgets[buddy_id]
                item.setData(Qt.ItemDataRole.UserRole, buddy)
                widget.update_buddy(buddy)

    def _render_room_activity(self, entries: list[Any]) -> None:
        if not hasattr(self, "room_activity"):
            return
        visible_entries = list(entries[:3])
        activity_signature = json.dumps(
            visible_entries,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        if activity_signature == getattr(self, "_room_activity_signature", None):
            return
        self._room_activity_signature = activity_signature
        self.room_activity.clear()
        # A room is a stage, not an audit log. Keep only the newest three
        # lightweight events; older history remains available from the server.
        for entry in visible_entries:
            if isinstance(entry, dict):
                stamp = _format_beijing_time(str(entry.get("created_at") or ""))
                text = str(entry.get("text") or entry.get("message") or "")
                if not text:
                    actor = _owner_label({
                        "private_note_name": entry.get("actor_private_note_name"),
                        "pet_name": entry.get("actor_pet_name") or entry.get("pet_name"),
                        "owner_nickname": entry.get("owner_nickname"),
                        "nickname": entry.get("nickname") or entry.get("actor_nickname"),
                    })
                    target = entry.get("target_private_note_name") or entry.get("target_owner_nickname") or entry.get("target_nickname")
                    target_record = {
                        "private_note_name": entry.get("target_private_note_name"),
                        "pet_name": entry.get("target_pet_name"),
                        "owner_nickname": entry.get("target_owner_nickname"),
                        "nickname": entry.get("target_nickname"),
                    }
                    target_text = f" → {_owner_label(target_record)}" if target else ""
                    kind_text = {
                        "join": "进入房间", "leave": "离开房间", "focus_start": "开始专注",
                        "focus_pause": "暂停休息", "focus_finish": "完成一轮",
                        "poke": "戳了一下", "cheer": "送上加油", "drink": "递了一杯奶茶",
                        "phrase": "发送了快速短语", "challenge_complete": "完成了共同挑战",
                        "schedule_start": "一起开工", "schedule_end": "一起收工",
                        "goal_set": "设置了共同目标",
                    }.get(str(entry.get("kind")), "更新了状态")
                    text = f"{actor}{target_text} {kind_text}"
                if stamp:
                    text = f"{stamp}  {text}"
            else:
                text = str(entry)
            if text:
                self.room_activity.addItem(text)
        if self.room_activity.count() == 0:
            self.room_activity.addItem("房间动态会显示开始专注、完成一轮和六毛互动。")

    def _render_wealth_leaderboard(self, rows: list[Any]) -> None:
        if not hasattr(self, "wealth_leaderboard"):
            return
        leaderboard_signature = json.dumps(
            rows[:20],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        render_signature = (
            leaderboard_signature,
            bool(self._leaderboard_error),
            bool(self._leaderboard_loaded),
        )
        if render_signature == getattr(self, "_leaderboard_render_signature", None):
            return
        self._leaderboard_render_signature = render_signature
        self.wealth_leaderboard.clear()
        for index, row in enumerate(rows[:20], 1):
            if not isinstance(row, dict):
                continue
            nickname = _owner_label(row)
            if bool(row.get("is_self")) and not nickname.endswith("（我）"):
                nickname += "（我）"
            week_seconds = _leaderboard_focus_seconds(row)
            self.wealth_leaderboard.addItem(
                f"{index}. {nickname}　本周专注 {format_work_duration(week_seconds)}"
            )
        if self.wealth_leaderboard.count() == 0:
            if self._leaderboard_error:
                self.wealth_leaderboard.addItem("本周专注排行榜暂时没有同步成功，请稍后重试。")
            elif not self._leaderboard_loaded:
                self.wealth_leaderboard.addItem("正在加载本周专注排行榜…")
            else:
                self.wealth_leaderboard.addItem("暂无可展示的榜单成员。")

    def _decorate_leaderboard_rows(self, rows: Any) -> list[dict[str, Any]]:
        """Apply this viewer's private buddy remarks to raw leaderboard rows.

        The leaderboard RPC intentionally returns only public nicknames.  A
        private remark is looked up from the already-authorized dashboard
        snapshot and overlaid in memory, so it can never leak to another
        account or be written to the shared leaderboard data.
        """

        note_by_user: dict[str, str] = dict(self._private_note_by_user)

        def collect(items: Any) -> None:
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                note = str(item.get("private_note_name") or "").strip()
                if not note:
                    continue
                for field in ("user_id", "buddy_user_id", "buddy_id", "peer_id", "owner_id", "sender_id", "receiver_id"):
                    value = item.get(field)
                    if value is not None and str(value).strip():
                        note_by_user[str(value)] = note[:40]
                        break

        for key in ("buddies", "room_people", "active_visits", "visits", "requests", "leaderboard"):
            collect(self.data.get(key))
        current_room = self.data.get("current_room")
        if isinstance(current_room, dict):
            for key in ("room_people", "active_visits", "visits"):
                collect(current_room.get(key))

        own_id = _session_user_id(self.client)
        if not own_id:
            me = self.data.get("me") if isinstance(self.data.get("me"), dict) else {}
            own_id = str(me.get("user_id") or me.get("id") or "").strip()
        decorated: list[dict[str, Any]] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            copy = dict(row)
            user_id = next(
                (
                    str(copy.get(field)).strip()
                    for field in ("user_id", "buddy_user_id", "buddy_id", "peer_id", "owner_id", "id")
                    if copy.get(field) is not None
                ),
                "",
            )
            copy["is_self"] = bool(copy.get("is_self")) or bool(own_id and user_id == own_id)
            if copy["is_self"]:
                me = self.data.get("me") if isinstance(self.data.get("me"), dict) else {}
                public_name = self.owner_nickname or clean_owner_nickname(
                    me.get("owner_nickname")
                    or me.get("nickname")
                    or me.get("display_name")
                )
                if public_name:
                    copy["owner_nickname"] = public_name
                    copy["nickname"] = public_name
            private_note = note_by_user.get(user_id)
            if private_note:
                copy["private_note_name"] = private_note
            else:
                # Never trust a private label that arrived with the raw RPC;
                # only the current viewer's authorized dashboard can supply it.
                copy.pop("private_note_name", None)
            decorated.append(copy)
        provider = self._local_focus_week_seconds_provider
        local_seconds = self._local_focus_week_seconds
        if callable(provider):
            try:
                local_seconds = max(0, int(provider()))
                self._local_focus_week_seconds = local_seconds
            except (TypeError, ValueError, OverflowError):
                pass
        if own_id and local_seconds is not None:
            own_row = next((row for row in decorated if str(row.get("user_id") or "") == own_id), None)
            if own_row is None:
                own_row = {
                    "user_id": own_id,
                    "is_self": True,
                    "week_seconds": int(local_seconds),
                }
                decorated.append(own_row)
            else:
                own_row["is_self"] = True
                own_row["week_seconds"] = int(local_seconds)
        decorated.sort(
            key=lambda row: (
                -_leaderboard_focus_seconds(row),
                str(row.get("owner_nickname") or row.get("nickname") or ""),
            )
        )
        return decorated

    def _add_inbox_item(
        self,
        kind: str,
        data: dict[str, Any],
        title: str,
        detail: str,
        actions: tuple[str, ...],
    ) -> None:
        """Add an actionable card while preserving the list item's payload."""

        item = QListWidgetItem(title)
        item.setData(Qt.ItemDataRole.UserRole, (kind, data))
        widget = InboxEntryWidget(title, detail, actions, self.inbox)
        self.inbox.addItem(item)
        self.inbox.setItemWidget(item, widget)
        item.setSizeHint(QSize(0, max(86, widget.sizeHint().height())))
        widget.accept_requested.connect(lambda item=item: self._inbox_item_action(item, "accept"))
        widget.reject_requested.connect(lambda item=item: self._inbox_item_action(item, "reject"))
        widget.cancel_requested.connect(lambda item=item: self._inbox_item_action(item, "cancel"))

    def _inbox_item_action(self, item: QListWidgetItem, action: str) -> None:
        self.inbox.setCurrentItem(item)
        if action == "accept":
            self._accept_inbox()
        elif action == "reject":
            self._reject_inbox()
        else:
            self._cancel_buddy_request()

    def _refresh_room_goal_text(self) -> None:
        if not hasattr(self, "room_goal"):
            return
        schedule = self._room_schedule_state
        if schedule:
            now_text = _beijing_now().strftime("%H:%M")
            for key, label in (("start_at", "一起开工"), ("end_at", "一起收工")):
                marker = f"{key}:{now_text}"
                if str(schedule.get(key) or "") == now_text and marker != self._last_ritual_notice:
                    self._last_ritual_notice = marker
                    self.room_ritual_due.emit(label)
        goal = self._room_goal_state
        if not goal:
            self.room_goal.setText("尚未设置共同目标；房间成员可以在这里设定任务和倒计时。")
            return
        title = str(goal.get("title") or "一起专注")
        target = int(goal.get("target_seconds") or goal.get("target_minutes", 0) * 60)
        completed = int(goal.get("completed_seconds") or goal.get("current_seconds") or 0)
        due = str(goal.get("due_at") or "")
        remaining = ""
        if due:
            try:
                due_dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
                if due_dt.tzinfo is None:
                    due_dt = due_dt.astimezone()
                seconds = max(0, int((due_dt - _beijing_now()).total_seconds()))
                remaining = f" · 倒计时 {format_work_duration(seconds)}"
            except ValueError:
                pass
        progress = f"{format_work_duration(completed)} / {format_work_duration(target)}" if target else "共同进行中"
        self.room_goal.setText(f"共同任务：{title} · {progress}{remaining}")

    def _set_room_goal(self) -> None:
        if not self._require_login() or not self.current_room_id:
            return
        title, ok = QInputDialog.getText(self, "设置共同目标", "共同任务名称：", text="完成这一轮专注")
        if not ok or not title.strip():
            return
        minutes, ok = QInputDialog.getInt(self, "设置倒计时", "共同专注分钟数：", 50, 1, 24 * 60, 5)
        if not ok:
            return
        self._begin_action("正在保存共同任务…")
        try:
            due_at = (_beijing_now() + timedelta(minutes=minutes)).isoformat()
            setter = getattr(self.client, "set_room_goal", None)
            if callable(setter):
                setter(room_id=self.current_room_id, title=title.strip()[:80], target_seconds=minutes * 60, due_at=due_at)
            else:
                self.client.rpc("lili_set_room_goal", {"p_room_id": self.current_room_id, "p_title": title.strip()[:80], "p_target_seconds": minutes * 60, "p_due_at": due_at})
            self._end_action()
            self._set_status("共同任务已更新，房间成员会看到同一个倒计时。")
            self._refresh_selected_room()
        except SocialError as exc:
            self._error(exc)

    def _leave_room(self) -> None:
        if not self._require_login() or not self.current_room_id:
            return
        room_id = self.current_room_id
        summary = self.data.get("room_summary") or (self.data.get("current_room") or {}).get("room_summary") or {}
        room_name = str((self.data.get("current_room") or {}).get("name") or "当前自习室")
        try:
            leaver = getattr(self.client, "leave_room", None)
            if callable(leaver):
                leaver(room_id=room_id)
            else:
                self.client.rpc("lili_leave_room", {"p_room_id": room_id})
            self.current_room_id = None
            self._room_selection_explicit = False
            self.room_changed.emit(None)
            self._set_status("已离开当前自习室，本次共同专注已保留在房间动态中。")
            if isinstance(summary, dict) and summary:
                QMessageBox.information(
                    self,
                    "本次自习室总结",
                    f"{room_name}\n\n"
                    f"今日共同专注：{format_work_duration(int(summary.get('today_shared_focus_seconds') or 0))}\n"
                    f"累计共同专注：{format_work_duration(int(summary.get('cumulative_shared_focus_seconds') or summary.get('shared_focus_seconds') or 0))}\n"
                    f"参与成员：{int(summary.get('member_count') or 0)} 人\n"
                    f"离开后可再次用房间码加入。",
                )
            self.refresh()
        except SocialError as exc:
            self._error(exc)

    def _mine_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(12)
        self.account_stack = QStackedWidget()
        self.account_stack.setMinimumSize(0, 0)
        self.account_stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.account_stack.addWidget(self._auth_card())
        self.account_stack.addWidget(self._profile_card())
        layout.addWidget(self.account_stack)
        preview_card, preview_layout = self._card(
            "登录后可以做什么",
            "账号只用于搭子与私人自习室；聊天、计时、动作和离线陪伴不登录也能使用。",
        )
        preview_layout.addWidget(QLabel("• 添加搭子并查看在线状态\n• 创建私人专注房间\n• 接收串门邀请并一起计时"))
        layout.addWidget(preview_card)
        layout.addStretch()
        return self._scroll_page(page)

    def _auth_card(self) -> QWidget:
        card, layout = self._card(
            "账号",
            "邮箱只用于登录；密码不会保存在 Lili。网络暂时不可达时会显示最近状态，恢复后自动同步。",
        )
        auth_tabs = QTabWidget()
        auth_tabs.setMinimumSize(0, 0)
        auth_tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        auth_tabs.tabBar().setExpanding(False)
        login = QWidget(); login_layout = QVBoxLayout(login); login_form = QFormLayout()
        login.setMinimumSize(0, 0)
        login.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.login_email = QLineEdit(); self.login_password = QLineEdit(); self.login_password.setEchoMode(QLineEdit.EchoMode.Password)
        login_form.addRow("邮箱", self.login_email); login_form.addRow("密码", self.login_password)
        login_layout.addLayout(login_form); self.login_button = QPushButton("登录")
        self.login_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.login_button.clicked.connect(self._login); login_layout.addWidget(self.login_button); login_layout.addStretch()
        self.forgot_password_button = QPushButton("忘记密码？")
        self.forgot_password_button.setObjectName("link")
        self.forgot_password_button.clicked.connect(self._request_password_reset)
        login_layout.addWidget(self.forgot_password_button)
        register = QWidget(); register_layout = QVBoxLayout(register); register_form = QFormLayout()
        register.setMinimumSize(0, 0)
        register.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.signup_nickname = QLineEdit(self.owner_nickname or "搭子"); self.signup_email = QLineEdit(); self.signup_password = QLineEdit(); self.signup_password.setEchoMode(QLineEdit.EchoMode.Password)
        register_form.addRow("主人称呼", self.signup_nickname); register_form.addRow("邮箱", self.signup_email); register_form.addRow("密码", self.signup_password)
        register_layout.addLayout(register_form); self.signup_button = QPushButton("注册")
        self.signup_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.signup_button.clicked.connect(self._signup); register_layout.addWidget(self.signup_button)
        self.signup_resend_button = QPushButton("重新发送确认邮件")
        self.signup_resend_button.setVisible(False)
        self.signup_resend_button.clicked.connect(self._resend_confirmation)
        register_layout.addWidget(self.signup_resend_button)
        register_layout.addWidget(QLabel("163、学校邮箱未收到时，请先查垃圾邮件/广告邮件。若邮箱已经注册，重复注册不会再次创建账号或覆盖密码，请切换到登录，或使用“忘记密码？”重置。"))
        register_layout.addStretch()
        auth_tabs.addTab(login, "登录"); auth_tabs.addTab(register, "注册")
        layout.addWidget(auth_tabs)
        return card

    def _profile_card(self) -> QWidget:
        card, layout = self._card("我的账号", "管理搭子码、可见性和串门权限。")
        self.identity = QLabel(); self.identity.setStyleSheet("font-size:18px;font-weight:650;"); self.identity.setWordWrap(True)
        identity_row = QHBoxLayout()
        identity_row.setSpacing(8)
        identity_row.addWidget(self.identity, 1)
        self.copy_buddy_code_button = QPushButton("复制搭子码")
        self.copy_buddy_code_button.setEnabled(False)
        self.copy_buddy_code_button.setToolTip("复制你的 8 位搭子码，发给想一起自习的朋友。")
        self.copy_buddy_code_button.clicked.connect(self._copy_buddy_code)
        identity_row.addWidget(self.copy_buddy_code_button)
        layout.addLayout(identity_row)
        self.owner_name_edit = QLineEdit()
        self.owner_name_edit.setMaxLength(24)
        self.owner_name_edit.setPlaceholderText("例如：小梁、mianmian")
        self.owner_name_edit.setToolTip("这里只填写“谁家的”主人名；“家的六毛”是固定名称。")
        self.owner_name_edit.textChanged.connect(self._preview_owner_nickname)
        owner_name_row = QHBoxLayout()
        owner_name_row.addWidget(QLabel("六毛主人名"))
        owner_name_row.addWidget(self.owner_name_edit, 1)
        layout.addLayout(owner_name_row)
        self.hidden = QCheckBox("隐身")
        self.exact = QCheckBox("显示准确时长")
        self.visits_allowed = QCheckBox("允许搭子串门")
        self.wealth_opt_in = QCheckBox("参加本周专注排行榜")
        self.wealth_opt_in.setChecked(True)
        self.wealth_opt_in.setToolTip("默认参加；仅已接受的搭子可见，可随时关闭。")
        layout.addWidget(self.hidden); layout.addWidget(self.exact); layout.addWidget(self.visits_allowed); layout.addWidget(self.wealth_opt_in)
        layout.addWidget(QLabel("搭子互动："))
        self.interaction_mode = QComboBox()
        self.interaction_mode.addItem("欢迎互动", "welcome")
        self.interaction_mode.addItem("专注优先（推荐）", "focus_priority")
        self.interaction_mode.addItem("免打扰", "do_not_disturb")
        self.interaction_mode.setMinimumWidth(0)
        self.interaction_mode.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.interaction_mode.setToolTip("决定好友敬茶、请吃蛋糕、请奶茶或邀请开工时如何到达你的六毛。")
        layout.addWidget(self.interaction_mode)
        save = QPushButton("保存隐私设置"); save.clicked.connect(self._save_profile)
        security = QPushButton("账号与安全…"); security.clicked.connect(self._open_account_security)
        logout = QPushButton("退出账号"); logout.clicked.connect(self._logout)
        for button in (save, security, logout):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            layout.addWidget(button)
        layout.addStretch()
        return card

    def _copy_buddy_code(self) -> None:
        me = self.data.get("me") or {}
        code = str(me.get("invite_code") or "").strip().upper()
        if not code or code == "--------":
            self._set_status("当前还没有可复制的搭子码，请先登录并刷新。", error=True)
            return
        clipboard = QApplication.clipboard()
        if clipboard is None:
            self._set_status("系统剪贴板暂不可用，请稍后再试。", error=True)
            return
        clipboard.setText(code)
        self._set_status(f"搭子码 {code} 已复制。")
        self.copy_buddy_code_button.setText("已复制")
        QTimer.singleShot(1600, lambda: self.copy_buddy_code_button.setText("复制搭子码"))

    def _open_account_security(self) -> None:
        if not self._require_login():
            return
        email = self._account_email or str(getattr(self.client, "account_email", "") or "").strip()
        dialog = AccountSecurityDialog(self.client, email, self)
        dialog.logout_requested.connect(self._logout)
        dialog.account_deleted.connect(self._account_deleted_from_security)
        dialog.exec()

    def _account_deleted_from_security(self) -> None:
        self.client.sign_out()
        self.data = {}
        self._muted_buddy_ids.clear()
        self._account_email = ""
        self._update_account_state()
        self.account_state_changed.emit(False)
        self._set_status("账号已注销，六毛继续离线陪伴。")

    def _request_password_reset(self) -> None:
        initial = self.login_email.text().strip()
        dialog = PasswordResetDialog(self.client, initial, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._account_email = dialog.email
            self._update_account_state()
            self.account_state_changed.emit(True)
            self._set_status("密码已修改成功，现在可以使用新密码进入自习室。")
            self.refresh()

    def _password_reset_completed(self) -> None:
        self._end_action()
        self._set_status("如果该邮箱已注册，我们会向其发送密码重置邮件；请检查收件箱和垃圾邮件。")
        QMessageBox.information(self, "密码重置邮件已提交", "如果该邮箱已注册，我们会向其发送密码重置邮件。请检查收件箱和垃圾邮件。")

    def _password_reset_failed(self, error: object) -> None:
        self._end_action()
        exc = error if isinstance(error, Exception) else SocialError(str(error), kind="network")
        self._error(exc)

    def _password_reset_thread_finished(self, thread: SocialPasswordResetThread) -> None:
        if self._password_reset_thread is thread:
            self._password_reset_thread = None
        if hasattr(self, "forgot_password_button"):
            self.forgot_password_button.setEnabled(True)
        thread.deleteLater()

    def _open_relogin(self) -> None:
        self.tabs.setCurrentIndex(3)
        if hasattr(self, "login_email"):
            self.login_email.setFocus()

    def _set_status(self, message: str, *, error: bool = False, relogin: bool = False) -> None:
        signature = (str(message), bool(error), bool(relogin))
        if signature == getattr(self, "_status_signature", None):
            return
        self._status_signature = signature
        self.status_label.setText(message)
        color = "#a33a3a" if error else "#087f74"
        background = "#f7e5e5" if error else "#e1efec"
        self.status_label.setStyleSheet(f"background:{background};color:{color};border-radius:9px;padding:7px 10px;")
        self.relogin_button.setVisible(bool(relogin))

    def _begin_action(self, message: str) -> None:
        self._set_status(message)
        if QApplication.overrideCursor() is None:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()

    @staticmethod
    def _end_action() -> None:
        if QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()

    def _require_login(self) -> bool:
        if self.client.signed_in:
            return True
        self.tabs.setCurrentIndex(3)
        self._set_status("请先在“我的”页面登录；其他离线功能仍可正常使用。", error=True)
        return False

    def _update_account_state(self) -> None:
        self.account_stack.setCurrentIndex(1 if self.client.signed_in else 0)
        if not self.client.signed_in:
            self._fill_signed_out_placeholders()

    def _fill_signed_out_placeholders(self) -> None:
        self.buddies.clear(); self.buddies.addItem("登录后，这里会显示搭子的在线与专注状态。")
        self.inbox.clear(); self.inbox.addItem("登录后可接收搭子申请与串门邀请。")
        if hasattr(self, "copy_buddy_code_button"):
            self.copy_buddy_code_button.setEnabled(False)
            self.copy_buddy_code_button.setText("复制搭子码")
        if hasattr(self, "owner_name_edit"):
            self.owner_name_edit.blockSignals(True)
            self.owner_name_edit.clear()
            self.owner_name_edit.blockSignals(False)
            self.owner_nickname = ""
            self._owner_nickname_dirty = False
            self._render_self_identity()
        if hasattr(self, "inbox_accept_button"):
            self._update_inbox_actions(None, None)
        self.rooms.clear(); self.rooms.addItem("登录后可创建或加入私人自习室。")
        self._fit_list_height(self.buddies, 46, 360)
        self._fit_list_height(self.rooms, 52, 140)
        if hasattr(self, "room_members"):
            self._render_room_people([])
        if hasattr(self, "room_activity"):
            self._render_room_activity([])
        if hasattr(self, "wealth_leaderboard"):
            self._leaderboard_rows = []
            self._leaderboard_loaded = False
            self._leaderboard_error = False
            self._render_wealth_leaderboard(self._leaderboard_rows)

    def _update_inbox_actions(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        """Keep actions inside each card; the old duplicate footer is gone."""

        # The list rows now own their Accept/Reject/Cancel buttons.  Keeping
        # this slot connected preserves selection handling for older callers,
        # but it deliberately does not create or reveal a second action bar.
        del current, _previous

    def _error(self, exc: Exception) -> None:
        self._end_action()
        raw = str(exc)
        kind = str(getattr(exc, "kind", "") or "").casefold()
        error_code = str(getattr(exc, "error_code", "") or "").casefold()
        retryable = bool(getattr(exc, "retryable", False))
        is_auth = kind.startswith("auth") or error_code in {
            "refresh_token_already_used",
            "invalid_refresh_token",
            "invalid_grant",
            "email_not_confirmed",
        }
        LOGGER.warning(
            "social room operation failed kind=%s endpoint=%s status=%s retryable=%s: %s",
            getattr(exc, "kind", "unknown"),
            getattr(exc, "endpoint", ""),
            getattr(exc, "status", None),
            retryable,
            raw,
        )
        message = "共同房间状态保存失败，请稍后重试。" if "ambiguous" in raw.lower() or "room_id" in raw.lower() else social_user_message(exc)
        self._set_status(message, error=True, relogin=is_auth)
        if is_auth or retryable:
            return
        QMessageBox.warning(self, "六毛搭子自习室", message)

    def _signup(self) -> None:
        if self._signup_thread is not None and self._signup_thread.isRunning():
            return
        email = self.signup_email.text().strip()
        password = self.signup_password.text()
        nickname = self.signup_nickname.text().strip()
        self._begin_action("正在创建账号…")
        self.signup_button.setEnabled(False)
        thread = SocialSignupThread(self.client, email, password, nickname, self)
        self._signup_thread = thread
        thread.completed.connect(self._signup_completed)
        thread.failed.connect(self._signup_failed)
        thread.finished.connect(lambda: self._signup_thread_finished(thread), Qt.ConnectionType.QueuedConnection)
        thread.start()

    def _signup_completed(self, result: object) -> None:
        self._end_action()
        if isinstance(result, SignupResult):
            self._pending_signup_email = result.email
            self.signup_resend_button.setVisible(
                result.confirmation_pending or (result.existing_account and not result.email_confirmed)
            )
            self.login_email.setText(result.email)
        if isinstance(result, SignupResult) and result.session_active:
            self._update_account_state()
            self.refresh()
            self.account_state_changed.emit(True)
            self._set_status("注册并登录成功，六毛自习室已准备好。")
            self._record_login_streak()
        elif isinstance(result, SignupResult) and result.existing_account:
            if result.email_confirmed:
                message = (
                    f"账号 {result.email} 已经注册并完成验证。\n\n"
                    "重复注册不会修改原账号密码，请切换到“登录”页，使用该邮箱最初设置的密码登录。"
                )
                title = "账号已存在"
                status = "该邮箱已注册，请使用原密码登录；重复注册不会修改已有密码。"
            else:
                message = (
                    f"账号 {result.email} 可能已经注册。\n\n"
                    "为保护账号隐私，服务器不会在这里直接透露确认状态。重复注册不会修改原账号密码，"
                    "请先使用该邮箱原来设置的密码登录；如果之前没有完成确认，请检查收件箱和垃圾邮件，"
                    "或点击“重新发送确认邮件”，完成确认后再登录。"
                )
                title = "账号可能已存在"
                status = "该邮箱可能已注册，请不要重复注册；先使用原密码登录或重新发送确认邮件。"
            self._set_status(status)
            QMessageBox.information(self, title, message)
        elif isinstance(result, SignupResult) and result.confirmation_pending:
            self._set_status("注册成功，确认邮件已提交；请点击邮件后回到这里登录。")
            QMessageBox.information(
                self,
                "注册成功，请确认邮箱",
                f"账号 {result.email} 已创建。\n\n"
                "请打开确认邮件中的链接。链接会跳转到六毛项目页面；这表示邮箱确认已完成，"
                "不是失败。然后回到 Lili，在“登录”页输入邮箱和密码即可。\n\n"
                "如果 163 或学校邮箱暂时没有收到，请检查垃圾邮件/广告邮件，稍后点击“重新发送确认邮件”。",
            )
        elif result:
            # Compatibility path for legacy backends that still return a
            # plain truthy value. SignupResult itself is handled explicitly
            # above so a no-session response cannot log the user in accidentally.
            self._update_account_state()
            self.refresh()
            self.account_state_changed.emit(True)
            self._set_status("注册并登录成功，六毛自习室已准备好。")
        else:
            self._set_status("注册请求已提交，请到邮箱确认后回来登录。")
            QMessageBox.information(
                self,
                "请确认邮箱",
                "注册请求已提交。请到邮箱完成确认，然后回到这里登录。\n\n"
                "确认页会打开六毛项目页面，不需要启动 localhost 服务。",
            )

    def _signup_failed(self, error: object) -> None:
        exc = error if isinstance(error, Exception) else SocialError(str(error), kind="network")
        if str(getattr(exc, "kind", "") or "").casefold() == "signup_timeout":
            self._pending_signup_email = self.signup_email.text().strip()
            self.login_email.setText(self._pending_signup_email)
            self.signup_resend_button.setVisible(bool(self._pending_signup_email))
        self._error(exc)

    def _signup_thread_finished(self, thread: SocialSignupThread) -> None:
        if self._signup_thread is thread:
            self._signup_thread = None
        self.signup_button.setEnabled(True)
        thread.deleteLater()

    def _resend_confirmation(self) -> None:
        if self._resend_thread is not None and self._resend_thread.isRunning():
            return
        email = (self._pending_signup_email or self.signup_email.text()).strip()
        self._begin_action("正在重新发送确认邮件…")
        self.signup_resend_button.setEnabled(False)
        thread = SocialResendConfirmationThread(self.client, email, self)
        self._resend_thread = thread
        thread.completed.connect(self._resend_completed)
        thread.failed.connect(self._resend_failed)
        thread.finished.connect(lambda: self._resend_thread_finished(thread), Qt.ConnectionType.QueuedConnection)
        thread.start()

    def _resend_completed(self) -> None:
        self._end_action()
        self._set_status("确认邮件已重新提交，请稍后检查收件箱和垃圾邮件。")
        QMessageBox.information(
            self,
            "确认邮件已重发",
            "邮件已重新提交。163、学校邮箱可能需要几分钟；如果仍未收到，需要管理员为 Supabase Auth 配置自定义 SMTP。",
        )

    def _resend_failed(self, error: object) -> None:
        exc = error if isinstance(error, Exception) else SocialError(str(error), kind="network")
        self._error(exc)

    def _resend_thread_finished(self, thread: SocialResendConfirmationThread) -> None:
        if self._resend_thread is thread:
            self._resend_thread = None
        self.signup_resend_button.setEnabled(True)
        thread.deleteLater()

    def _record_login_streak(self) -> None:
        if not self.client.signed_in:
            return
        if self._login_streak_thread is not None and self._login_streak_thread.isRunning():
            return
        thread = SocialLoginStreakThread(self.client, self)
        self._login_streak_thread = thread
        thread.completed.connect(self._login_streak_completed)
        thread.failed.connect(self._login_streak_failed)
        thread.finished.connect(lambda: self._login_streak_thread_finished(thread), Qt.ConnectionType.QueuedConnection)
        thread.start()

    def _login_streak_completed(self, result: dict) -> None:
        payload = dict(result or {})
        self.login_streak_updated.emit(payload)
        days = login_streak_days(payload)
        if payload.get("newly_unlocked"):
            self._set_status("连续登录 3 天，已解锁新娃衣「三日连登搭子」！")
        elif login_reward_granted(payload):
            self._set_status("连续登录奖励已解锁；当前连续登录 %d 天。" % days)

    def _login_streak_failed(self, error: object) -> None:
        # This is an optional reward side effect. Do not show a red auth error
        # after the user has already logged in successfully.
        LOGGER.info("login streak unavailable: %s", error)

    def _login_streak_thread_finished(self, thread: SocialLoginStreakThread) -> None:
        if self._login_streak_thread is thread:
            self._login_streak_thread = None
        thread.deleteLater()

    def _login(self) -> None:
        if self._login_thread is not None and self._login_thread.isRunning():
            return
        email = self.login_email.text().strip()
        password = self.login_password.text()
        if not email or not password:
            self._error(SocialError("请输入邮箱和密码。", kind="validation"))
            return
        self._begin_action("正在登录搭子自习室…")
        self.login_button.setEnabled(False)
        thread = SocialLoginThread(self.client, email, password, self)
        self._login_thread = thread
        thread.completed.connect(self._login_completed)
        thread.failed.connect(self._login_failed)
        thread.finished.connect(lambda: self._login_thread_finished(thread), Qt.ConnectionType.QueuedConnection)
        thread.start()

    def _login_completed(self, result: object = None) -> None:
        self._end_action()
        self._account_email = self.login_email.text().strip()
        self._update_account_state()
        self.tabs.setCurrentIndex(0)
        self.refresh()
        payload = dict(result or {}) if isinstance(result, dict) else {}
        self.login_streak_updated.emit(payload)
        if payload.get("newly_unlocked"):
            self._set_status("登录成功；连续登录 3 天，已解锁新娃衣「三日连登搭子」！")
        elif login_reward_granted(payload):
            self._set_status(
                "登录成功；三日连登娃衣已解锁。当前连续登录 %d 天。"
                % login_streak_days(payload)
            )
        else:
            self._set_status("登录成功，邮箱确认已完成。")
        self.account_state_changed.emit(True)

    def _login_failed(self, error: object) -> None:
        self._end_action()
        exc = error if isinstance(error, Exception) else SocialError(str(error), kind="network")
        self._error(exc)

    def _login_thread_finished(self, thread: SocialLoginThread) -> None:
        if self._login_thread is thread:
            self._login_thread = None
        self.login_button.setEnabled(True)
        thread.deleteLater()

    def _logout(self) -> None:
        self.client.sign_out(); self.data = {}; self._muted_buddy_ids.clear(); self._buddy_presence_versions.clear(); self._update_account_state(); self.account_state_changed.emit(False); self._set_status("已退出账号，六毛继续离线陪伴。")

    def refresh(self) -> None:
        if self._closed:
            return
        if not self._require_login(): return
        self._start_dashboard_refresh(
            self.current_room_id,
            "正在刷新搭子与专注状态…",
            force_auxiliary_refresh=True,
        )

    def _apply_presence_sequence_fence(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Prevent an older per-buddy snapshot from moving live fields back."""

        volatile_fields = (
            "working", "session_active", "status", "online", "session_id",
            "session_started_at", "session_seconds", "today_seconds", "week_seconds",
            "last_seen_at", "status_updated_at", "server_updated_at", "sequence",
        )
        lists: list[Any] = [
            payload.get("buddies"),
            payload.get("room_people"),
            payload.get("active_visits"),
        ]
        current_room = payload.get("current_room")
        if isinstance(current_room, dict):
            lists.extend((current_room.get("room_people"), current_room.get("active_visits")))

        for items in lists:
            if not isinstance(items, list):
                continue
            for index, raw_item in enumerate(items):
                if not isinstance(raw_item, dict):
                    continue
                buddy_id = str(raw_item.get("user_id") or raw_item.get("peer_id") or "").strip()
                if not buddy_id:
                    continue
                try:
                    sequence = max(0, int(raw_item.get("sequence") or 0))
                except (TypeError, ValueError, OverflowError):
                    sequence = 0
                previous = self._buddy_presence_versions.get(buddy_id)
                if previous is not None and previous[0] > sequence:
                    preserved = dict(raw_item)
                    for field in volatile_fields:
                        if field in previous[1]:
                            preserved[field] = previous[1][field]
                    items[index] = preserved
                    # A mixed-version response may omit sequence entirely.
                    # It is still older than a previously fenced response and
                    # must not regress live fields.
                    continue
                if sequence:
                    self._buddy_presence_versions[buddy_id] = (sequence, dict(raw_item))
        return payload

    def _render_inbox_items(self) -> None:
        """Rebuild the inbox only when invitation data actually changed."""

        self.inbox.clear()
        for request in self.data.get("requests") or []:
            if _notification_sender_id(request) in self._muted_buddy_ids:
                continue
            self._add_inbox_item(
                "buddy", request, f"搭子申请 · {_owner_label(request)}",
                "对方想和你成为搭子；接受后可以看到彼此的自习状态。",
                ("接受", "拒绝"),
            )
        for request in self.data.get("outgoing_requests") or []:
            if not isinstance(request, dict):
                continue
            self._add_inbox_item(
                "buddy_outgoing", request,
                f"已发出的搭子申请 · {_owner_label(request)}",
                "等待对方回应；如果不想继续，可以撤回这条申请。",
                ("撤回申请",),
            )
        labels = {
            "food_coffee": "☕ 一起开工邀请",
            "food_milk_tea": "🧋 一起休息邀请",
            "food_tea": "🍵 敬茶",
            "food_cake": "🍰 庆祝邀请",
            "food_cake_share": "🍰 请你一起吃蛋糕",
        }
        for visit in self.data.get("visits") or []:
            if _notification_sender_id(visit) in self._muted_buddy_ids:
                continue
            visit_kind = str(visit.get("kind") or "visit")
            self._add_inbox_item(
                "food" if visit_kind.startswith("food_") else "visit",
                visit,
                f"{labels.get(visit_kind, '串门邀请')} · {_owner_label(visit)}",
                "对方正在等你的决定；接受后六毛会把这次互动标记为已处理。",
                ("接受", "拒绝"),
            )
        for request in self.data.get("achievement_witness_requests") or []:
            if not isinstance(request, dict):
                continue
            title = str(request.get("name") or "未命名成果")[:90]
            self._add_inbox_item(
                "achievement_witness",
                request,
                f"成果见证 · {_owner_label(request)}",
                f"{title} · 接受后会记录这次成果见证，并发放固定奖励。",
                ("接受", "拒绝"),
            )
        if self.inbox.count() == 0:
            empty = QListWidgetItem("当前没有待处理申请或串门，新的邀请会显示在这里。")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.inbox.addItem(empty)

    def apply_dashboard(self, data: dict[str, Any] | None) -> None:
        """Render a dashboard already fetched by the background sync thread.

        Heartbeats run off the UI thread.  Previously the completed payload was
        only consumed for visit notifications, leaving the visible room cards
        on the previous (often resting) state until the user clicked refresh.
        """

        payload = dict(data) if isinstance(data, dict) else {}
        previous_data = self.data if isinstance(self.data, dict) else {}
        active_user_id = _session_user_id(self.client)
        payload_me = payload.get("me") if isinstance(payload.get("me"), dict) else {}
        payload_user_id = str(payload_me.get("user_id") or "").strip()
        if active_user_id and payload_user_id and active_user_id != payload_user_id:
            # A late response from the previous account must never repaint the
            # new account's room cards, presence, or private labels.
            LOGGER.warning(
                "ignored dashboard for another account active=%s payload=%s",
                active_user_id,
                payload_user_id,
            )
            return

        payload, partial = _merge_dashboard_snapshot(previous_data, payload)
        if partial:
            LOGGER.warning(
                "partial social dashboard preserved last core snapshot keys=%s",
                sorted(str(key) for key in payload.keys()),
            )
        payload = self._apply_presence_sequence_fence(payload)

        snapshot_notes = _private_notes_from_dashboard(payload)
        if payload.get("_private_notes_loaded"):
            self._private_note_by_user = snapshot_notes
            self._private_notes_loaded = True
        elif snapshot_notes:
            self._private_note_by_user.update(snapshot_notes)
        for user_id in payload.get("_private_notes_deleted") or []:
            self._private_note_by_user.pop(str(user_id), None)
        _apply_buddy_private_notes(payload, self._private_note_by_user)

        # A server-confirmed direct render can supersede the bootstrap read.
        # A last-known-good cache must not: it paints immediately, then the
        # scheduled background read remains responsible for replacing it when
        # the network is available again.
        if payload.get("data_source") != "local_cache" and not payload.get("is_stale"):
            self._initial_refresh_timer.stop()
        self.data = dict(payload)
        # Older deployed dashboard functions return the last closed counter
        # while a peer is still working.  Decorate every visible peer row with
        # the response timestamp and the backend's source marker so the active
        # interval is shown immediately without double-counting a canonical
        # interval-union response.
        dashboard_timestamp = str(
            self.data.get("server_timestamp")
            or self.data.get("_server_timestamp")
            or ""
        )
        dashboard_totals_source = str(
            self.data.get("focus_totals_source")
            or self.data.get("_focus_totals_source")
            or ""
        )
        for field in ("buddies", "room_people", "active_visits"):
            rows = self.data.get(field)
            if not isinstance(rows, list):
                continue
            self.data[field] = [
                _project_legacy_live_focus_totals(
                    {
                        **row,
                        "_server_timestamp": row.get("_server_timestamp") or dashboard_timestamp,
                        "_focus_totals_source": row.get("_focus_totals_source") or dashboard_totals_source,
                    },
                    server_timestamp=dashboard_timestamp,
                    totals_source=dashboard_totals_source,
                )
                for row in rows
                if isinstance(row, dict)
            ]
        current_room = self.data.get("current_room")
        if isinstance(current_room, dict) and isinstance(current_room.get("room_people"), list):
            current_room = dict(current_room)
            current_room["room_people"] = [
                _project_legacy_live_focus_totals(
                    {
                        **row,
                        "_server_timestamp": row.get("_server_timestamp") or dashboard_timestamp,
                        "_focus_totals_source": row.get("_focus_totals_source") or dashboard_totals_source,
                    },
                    server_timestamp=dashboard_timestamp,
                    totals_source=dashboard_totals_source,
                )
                for row in current_room["room_people"]
                if isinstance(row, dict)
            ]
            self.data["current_room"] = current_room
        self._refresh_multi_device_focus_hint()
        self._muted_buddy_ids = {
            str(item).strip()
            for item in (self.data.get("muted_buddy_ids") or [])
            if str(item).strip()
        }
        # Resolve the current account identity before decorating the separate
        # leaderboard response. Otherwise the first render can label the
        # owner with the generic fallback until a second refresh arrives.
        me=self.data.get("me") or {}
        if "owner_nickname" in me and not self._owner_nickname_dirty:
            remote_owner_nickname = clean_owner_nickname(me.get("owner_nickname"))
            pending_owner_nickname = self._pending_owner_nickname
            if pending_owner_nickname is not _NO_PENDING_OWNER_NICKNAME:
                if remote_owner_nickname == pending_owner_nickname:
                    self._pending_owner_nickname = _NO_PENDING_OWNER_NICKNAME
                else:
                    # A dashboard cache may still contain the pre-save value.
                    # Keep the just-saved local value until a server response
                    # confirms it, including an intentional empty clear.
                    remote_owner_nickname = str(pending_owner_nickname)
            self.owner_nickname = remote_owner_nickname
            self.owner_name_edit.blockSignals(True)
            self.owner_name_edit.setText(self.owner_nickname)
            self.owner_name_edit.blockSignals(False)
        # Missing is not empty: heartbeat payloads may omit this optional RPC
        # while the room dashboard remains healthy.  Preserve the last known
        # board until an explicit ``leaderboard=[]`` arrives.
        if "leaderboard" in self.data:
            self._leaderboard_rows = self._decorate_leaderboard_rows(self.data.get("leaderboard") or [])
            self._leaderboard_loaded = True
            self._leaderboard_error = False
            self._render_wealth_leaderboard(self._leaderboard_rows)
        me_presence = self.data.get("me_presence") or {}
        own_label = social_pet_label(self.owner_nickname or me.get("nickname") or me.get("display_name"))
        invite_code = str(me.get("invite_code") or "--------").strip().upper()
        self.identity.setText(f"{own_label} · 我的搭子码：{invite_code}")
        if hasattr(self, "copy_buddy_code_button"):
            self.copy_buddy_code_button.setEnabled(bool(invite_code and invite_code != "--------"))
        for request in self.data.get("requests") or []:
            if not isinstance(request, dict) or _notification_sender_id(request) in self._muted_buddy_ids:
                continue
            request_id = str(request.get("id") or "")
            if request_id and request_id not in self._seen_buddy_request_ids:
                self._seen_buddy_request_ids.add(request_id)
                self.buddy_request_received.emit(dict(request))
        self.hidden.setChecked(me.get("visibility") == "hidden"); self.exact.setChecked(bool(me.get("show_exact_time",True))); self.visits_allowed.setChecked(bool(me.get("allow_visits",True))); self.wealth_opt_in.setChecked(_wealth_leaderboard_enabled(me))
        mode = str(me.get("buddy_interaction_mode") or "focus_priority")
        mode_index = self.interaction_mode.findData(mode)
        self.interaction_mode.setCurrentIndex(mode_index if mode_index >= 0 else 1)
        people=(self.data.get("buddies") or [])+(self.data.get("room_people") or [])
        seen=set()
        unique_people = []
        for buddy in people:
            if not isinstance(buddy, dict):
                continue
            buddy = dict(buddy)
            buddy_id = str(buddy.get("user_id") or buddy.get("id") or "")
            if buddy_id in seen:
                continue
            buddy["notifications_muted"] = bool(
                buddy.get("notifications_muted") or buddy_id in self._muted_buddy_ids
            )
            seen.add(buddy_id)
            unique_people.append(buddy)
        unique_people.sort(key=cmp_to_key(_compare_buddies))
        ordered_ids = [str(item.get("user_id") or item.get("id") or "") for item in unique_people]
        reuse_buddy_cards = bool(unique_people) and (
            ordered_ids == list(self._buddy_card_widgets)
            and all(
                self._buddy_card_structure.get(buddy_id) == self._buddy_structure_key(buddy)
                for buddy_id, buddy in zip(ordered_ids, unique_people)
            )
        )
        if not reuse_buddy_cards:
            self.buddies.clear()
            self._buddy_card_widgets.clear()
            self._buddy_card_structure.clear()
        working_count = 0
        last_confirmed_working_count = 0
        presence_uncertain = bool(self.data.get("_presence_grace_active"))
        for buddy in unique_people:
            buddy_id = str(buddy.get("user_id") or buddy.get("id") or "")
            if buddy.get("subscribed") and not buddy.get("notifications_muted"):
                previous_buddies = {
                    str(item.get("user_id")): item
                    for item in (previous_data.get("buddies") or [])
                    if isinstance(item, dict)
                }
                previous = previous_buddies.get(str(buddy.get("user_id")))
                if previous is not None and _presence_status(previous) != _presence_status(buddy):
                    state_text = "开始专注" if _presence_status(buddy) == "focus" else "结束专注"
                    self.buddy_subscription_notice.emit(f"{_owner_label(buddy)} {state_text}了。")
            status = _presence_status(buddy)
            # A transport outage must not turn an unknown state into a false
            # zero. During the short cache grace window, show the last
            # confirmed working count with an explicit uncertainty label.
            if bool(buddy.get("presence_uncertain")):
                presence_uncertain = True
            if status == "focus":
                working_count += 1
            if not bool(buddy.get("stale_presence")) and _presence_working(buddy):
                last_confirmed_working_count += 1
            if reuse_buddy_cards:
                item, buddy_widget = self._buddy_card_widgets[buddy_id]
                item.setData(Qt.ItemDataRole.UserRole, buddy)
                buddy_widget.update_buddy(buddy)
            else:
                item=QListWidgetItem(); item.setData(Qt.ItemDataRole.UserRole,buddy); self.buddies.addItem(item)
                buddy_widget = BuddyCardWidget(buddy, self.buddies)
                buddy_widget.interaction_requested.connect(self._send_interaction)
                buddy_widget.food_interaction_requested.connect(self._send_food_interaction)
                buddy_widget.interaction_blocked.connect(lambda message: self._set_status(message, error=True))
                buddy_widget.subscription_requested.connect(self._set_subscription)
                self.buddies.setItemWidget(item, buddy_widget)
                self._set_buddy_item_height(item, buddy_widget)
            self._buddy_card_widgets[buddy_id] = (item, buddy_widget)
            self._buddy_card_structure[buddy_id] = self._buddy_structure_key(buddy)
        local_today = self._local_today_seconds()
        me_seconds = (
            local_today
            if local_today is not None
            else int(me_presence.get("today_seconds") or me.get("today_seconds") or 0)
        )
        self.study_summary.setText(
            _study_focus_summary_text(
                last_confirmed_working_count if presence_uncertain else working_count,
                me_seconds,
                presence_uncertain=presence_uncertain,
            )
        )
        self._refresh_own_focus_labels()
        if not seen:
            empty = QListWidgetItem("还没有搭子。点击上面的“用搭子码添加”，一起工作时这里会显示今天和本周的专注时长。")
            empty.setFlags(Qt.ItemFlag.NoItemFlags); self.buddies.addItem(empty)
        self._fit_list_height(self.buddies, 46, 360)
        inbox_source = {
            "requests": self.data.get("requests") or [],
            "outgoing_requests": self.data.get("outgoing_requests") or [],
            "visits": self.data.get("visits") or [],
            "achievement_witness_requests": self.data.get("achievement_witness_requests") or [],
            "muted": sorted(self._muted_buddy_ids),
        }
        inbox_signature = json.dumps(
            inbox_source,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        inbox_changed = inbox_signature != self._inbox_signature
        if inbox_changed:
            self._render_inbox_items()
            self._inbox_signature = inbox_signature

        if hasattr(self, "recent_interactions"):
            shares = self.data.get("cake_shares") or []
            recent_signature = json.dumps(
                shares,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            )
            if recent_signature != self._recent_interactions_signature:
                self.recent_interactions.clear()
                for share in shares:
                    if not isinstance(share, dict):
                        continue
                    members = [item for item in (share.get("members") or []) if isinstance(item, dict)]
                    accepted = sum(str(item.get("status") or "") == "accepted" for item in members)
                    total = len(members)
                    message = str(share.get("message") or "今天值得庆祝一下")[:80]
                    self.recent_interactions.addItem(
                        f"🍰 今日蛋糕 · 已邀请 {total} 人 · 已接受 {accepted}/{total}\n{message}"
                    )
                self._recent_interactions_signature = recent_signature
        self._update_inbox_actions(self.inbox.currentItem(), None)
        if inbox_changed:
            QTimer.singleShot(0, self._auto_accept_light_food_interactions)
        rooms = list(self.data.get("rooms") or [])
        previous_room_id = self.current_room_id
        room_was_selected = bool(previous_room_id)
        self._applying_dashboard = True
        room_signature = json.dumps(
            rooms,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        if room_signature != self._room_list_signature:
            # Rebuilding the list is an internal render operation. Suppress
            # the transient selection signals; otherwise every dashboard
            # response can schedule another network sync.
            self.rooms.blockSignals(True)
            self.rooms.clear()
            for room in rooms:
                room_item = QListWidgetItem(
                    f"{room.get('name')} · {room.get('members')} 人"
                )
                room_item.setData(Qt.ItemDataRole.UserRole, room)
                self.rooms.addItem(room_item)
            if self.rooms.count() == 0:
                empty_room = QListWidgetItem("还没有私人自习室；创建后可把房间码发给搭子。")
                empty_room.setFlags(Qt.ItemFlag.NoItemFlags)
                self.rooms.addItem(empty_room)
                self.current_room_id = None
                self._room_selection_explicit = False
            else:
                # A server membership is not an active desktop selection. The
                # user must explicitly choose a room after opening the window.
                selected = -1
                if self._room_selection_explicit and previous_room_id:
                    for index, room in enumerate(rooms):
                        if self._room_id_from_payload(room) == previous_room_id:
                            selected = index
                            break
                if selected >= 0:
                    self.rooms.setCurrentRow(selected)
                    self.current_room_id = self._room_id_from_payload(rooms[selected])
                else:
                    self.rooms.setCurrentRow(-1)
                    self.current_room_id = None
            self.rooms.blockSignals(False)
            self._room_list_signature = room_signature
            self._fit_list_height(self.rooms, 52, 140)
        self._applying_dashboard = False
        if self.current_room_id != previous_room_id:
            self.room_changed.emit(self.current_room_id)
        # The room-scoped endpoint is authoritative for members and events.
        # Keep the legacy top-level fields as a compatibility fallback for
        # older proxy deployments and the offline UI tests.
        room_detail = self.data.get("current_room") or {}
        if not isinstance(room_detail, dict):
            room_detail = {}
        if self.current_room_id and self.current_room_id != previous_room_id and not room_detail:
            self._room_refresh_timer.start(0)
        room_people = list(room_detail.get("room_people") or self.data.get("room_people") or []) if self.current_room_id else []
        server_timestamp = str(self.data.get("server_timestamp") or self.data.get("_server_timestamp") or "")
        if server_timestamp:
            room_people = [
                _project_legacy_live_focus_totals(
                    {**person, "_server_timestamp": server_timestamp},
                    server_timestamp=server_timestamp,
                    totals_source=dashboard_totals_source,
                )
                for person in room_people
                if isinstance(person, dict)
            ]
        # Always render the local member as well.  The old SQL function only
        # returned peers, which made the room look like everybody was resting
        # when the local timer was the only state visible in the UI.
        local_status = self._focus_snapshot
        local_presence = dict(me_presence)
        if isinstance(local_status, dict):
            local_presence = {**local_presence, **local_status}
        elif local_status is not None:
            local_presence = {
                **local_presence,
                "status": getattr(local_status, "status", "idle"),
                "working": bool(getattr(local_status, "is_running", False)),
                "session_seconds": int(getattr(local_status, "session_seconds", 0)),
                "today_seconds": int(getattr(local_status, "today_seconds", 0)),
            }
        if isinstance(self._focus_analytics, dict):
            local_presence.update({
                "today_interruptions": int(self._focus_analytics.get("today_interruptions") or 0),
                "longest_continuous_seconds": int(self._focus_analytics.get("longest_continuous_seconds") or 0),
            })
        local_presence.update({
            "user_id": str(me.get("user_id") or me.get("id") or "me"),
            "owner_nickname": self.owner_nickname or clean_owner_nickname(
                me.get("owner_nickname") or me.get("nickname") or me.get("display_name")
            ),
            "nickname": self.owner_nickname or str(
                me.get("nickname") or me.get("display_name") or "搭子"
            ),
            "pet_name": me.get("pet_name"),
            # The profile is the durable same-account outfit.  Presence is a
            # per-device heartbeat and can briefly belong to an older client.
            "outfit_key": str(me.get("outfit_key") or me_presence.get("outfit_key") or self.outfit_key or ""),
            "online": True,
            "is_self": True,
        })
        if self.current_room_id:
            room_people = [local_presence] + [p for p in room_people if str(p.get("user_id")) != str(local_presence.get("user_id"))]
        else:
            room_people = []
        self._render_room_people(room_people)
        goal = room_detail.get("room_goal") or self.data.get("room_goal") or {}
        summary = room_detail.get("room_summary") or self.data.get("room_summary") or {}

        self.study_summary.setText(
            _study_focus_summary_text(
                last_confirmed_working_count if presence_uncertain else working_count,
                me_seconds,
                presence_uncertain=presence_uncertain,
            )
        )
        if isinstance(summary, dict) and summary:
            self.room_summary.setText(_room_focus_summary_text(summary, len(room_people)))
        elif hasattr(self, "room_summary"):
            self.room_summary.setText("你当前没有加入工作间。创建工作间或输入房间码加入后，这里才会显示共同状态。")
        self._room_goal_state = dict(goal) if isinstance(goal, dict) else {}
        schedule = room_detail.get("room_schedule") or self.data.get("room_schedule") or {}
        challenge = room_detail.get("room_challenge") or self.data.get("room_challenge") or {}
        self._room_schedule_state = dict(schedule) if isinstance(schedule, dict) else {}
        self._room_challenge_state = dict(challenge) if isinstance(challenge, dict) else {}
        self.room_goal_button.setEnabled(bool(self.current_room_id))
        if hasattr(self, "room_schedule_button"):
            self.room_schedule_button.setEnabled(bool(self.current_room_id))
        if hasattr(self, "room_challenge_button"):
            self.room_challenge_button.setEnabled(bool(self.current_room_id))
        if hasattr(self, "room_start_prompt_button"):
            self.room_start_prompt_button.setEnabled(bool(self.current_room_id))
        if hasattr(self, "room_invite_button"):
            self.room_invite_button.setEnabled(bool(self.current_room_id))
        self.room_leave_button.setEnabled(bool(self.current_room_id))
        self._refresh_room_goal_text()
        if hasattr(self, "room_ritual"):
            if self._room_schedule_state:
                self.room_ritual.setText(
                    f"共同开工/收工：{self._room_schedule_state.get('start_at', '--:--')} 开工 · "
                    f"{self._room_schedule_state.get('end_at', '--:--')} 收工"
                )
            else:
                self.room_ritual.setText("共同开工/收工：未设置")
        if hasattr(self, "room_challenge"):
            if self._room_challenge_state:
                self.room_challenge.setText(
                    f"共同挑战：{self._room_challenge_state.get('title', '一起完成')} · "
                    f"{format_work_duration(int(self._room_challenge_state.get('target_seconds') or 0))} · "
                    f"每人 {int(self._room_challenge_state.get('target_rounds') or 0)} 轮"
                )
            else:
                self.room_challenge.setText("共同挑战：未设置")
        activity = list(room_detail.get("room_activity") or self.data.get("room_activity") or self.data.get("activity") or [])
        me_id = str(me.get("user_id") or me.get("id") or "")
        for event in activity:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("id") or "")
            target_id = str(event.get("target_id") or "")
            if event_id and event_id not in self._seen_room_event_ids:
                self._seen_room_event_ids.add(event_id)
                is_target = target_id == me_id or (
                    not target_id and str(event.get("target_owner_nickname") or event.get("target_nickname") or "") == str(me.get("owner_nickname") or me.get("nickname") or "")
                )
                actor_id = str(event.get("actor_id") or "")
                if (
                    not self.data.get("_sync_offline")
                    and me_id
                    and is_target
                    and actor_id != me_id
                    and actor_id not in self._muted_buddy_ids
                ):
                    self.room_event_received.emit(dict(event))
        self._render_room_activity(activity)
        active = [
            item for item in (self.data.get("active_visits") or [])
            if _notification_sender_id(item) not in self._muted_buddy_ids
        ]
        if active and not self.data.get("_sync_offline"): self.active_visit.emit(active[0])
        state = str(self.data.get("_connection_state") or "")
        if self.data.get("_room_endpoint_unavailable"):
            self._set_status("账号与搭子已同步，但当前部署缺少自习室详情接口；隐私设置仍已保存。", error=False)
        elif self.data.get("_presence_grace_active") or state == "DEGRADED":
            self._set_status(PRESENCE_RECOVERY_STATUS)
        elif state == "AUTH_ERROR":
            self._set_status("自习室登录状态失效，请重新登录；本地专注与桌宠功能仍可继续使用。", error=True, relogin=True)
        elif self.data.get("_sync_offline") or state == "OFFLINE":
            age = int(self.data.get("_sync_age_minutes") or 0)
            age_text = f"约 {age} 分钟前" if age else "刚才"
            # Local focus is independent from room synchronization.  A focus
            # click can legitimately happen while the user has not selected a
            # room, so an unavailable dashboard must not make the local timer
            # look broken or claim that a room connection is required.
            if room_was_selected or self.current_room_id:
                self._set_status(
                    f"当前无法连接自习室，已显示{age_text}的本地状态；网络恢复后会自动同步。"
                )
            else:
                local_status = self._focus_snapshot
                if isinstance(local_status, dict):
                    local_focus_active = str(local_status.get("status") or "") == "focus"
                else:
                    local_focus_active = bool(getattr(local_status, "is_running", False))
                if local_focus_active:
                    self._set_status(
                        "本地专注已开始；自习室实时同步暂不可用，已保留搭子缓存，网络恢复后自动同步。"
                    )
                else:
                    self._set_status(
                        "自习室实时同步暂不可用；本地功能不受影响，已保留搭子缓存，网络恢复后自动同步。"
                    )
        elif state == "DEGRADED":
            self._set_status("自习室已连接，实时同步暂时不可用，继续重新连接。")
        elif state == "ONLINE":
            if not self.current_room_id and not self._room_selection_explicit:
                self._set_status("自习室已连接；当前未加入共同自习室，本地搭子数据仍可用。")
            else:
                self._set_status("自习室已连接，房间状态已同步。")
        else:
            self._set_status("已刷新，页面内容是最新的。")

    def _save_profile(self) -> None:
        if not self._require_login(): return
        self._begin_action("正在保存隐私设置…")
        try:
            me=self.data.get("me") or {}
            # The account nickname is the profile's existing identity.  The
            # editable Phase 3 field is only the optional owner_nickname part
            # of “XX家的六毛”; it must never be sent as pet_name or replace
            # the account nickname.
            account_nickname = str(
                me.get("nickname") or me.get("display_name") or "搭子"
            ).replace("\x00", "").strip()[:24] or "搭子"
            owner_nickname = clean_owner_nickname(self.owner_name_edit.text())
            self.client.update_profile(nickname=account_nickname,visibility="hidden" if self.hidden.isChecked() else "friends",show_exact_time=self.exact.isChecked(),allow_visits=self.visits_allowed.isChecked(),outfit_key=self.outfit_key,wealth_leaderboard_enabled=self.wealth_opt_in.isChecked(),wealth_leaderboard_preference_set=True,owner_nickname=owner_nickname)
            self.owner_nickname = owner_nickname
            self._owner_nickname_dirty = False
            self._pending_owner_nickname = owner_nickname
            me["nickname"] = account_nickname
            me["owner_nickname"] = owner_nickname or None
            self.data["me"] = me
            self._render_self_identity()
            self.client.rpc("lili_set_buddy_interaction_mode", {"p_mode": str(self.interaction_mode.currentData() or "focus_priority")})
            self.refresh()
        except SocialError as exc: self._error(exc)

    def _set_subscription(self, buddy: dict[str, Any], enabled: bool) -> None:
        if not self._require_login():
            return
        buddy_id = str(buddy.get("user_id") or buddy.get("id") or "")
        if not buddy_id:
            return
        try:
            setter = getattr(self.client, "set_buddy_subscription", None)
            muted = bool(buddy.get("notifications_muted") or buddy_id in self._muted_buddy_ids)
            if callable(setter):
                setter(buddy_id=buddy_id, on_focus_start=enabled, on_focus_end=enabled, muted=muted)
            else:
                self.client.rpc("lili_set_buddy_subscription", {"p_buddy_id": buddy_id, "p_on_focus_start": enabled, "p_on_focus_end": enabled, "p_muted": muted})
            self._set_status("搭子状态订阅已开启。" if enabled else "搭子状态订阅已关闭。")
        except SocialError as exc:
            self._error(exc)

    def _set_buddy_muted(self, buddy: dict[str, Any], muted: bool) -> None:
        if not self._require_login():
            return
        buddy_id = str(buddy.get("user_id") or buddy.get("id") or "")
        if not buddy_id:
            return
        try:
            subscribed = bool(buddy.get("subscribed"))
            setter = getattr(self.client, "set_buddy_subscription", None)
            if callable(setter):
                setter(
                    buddy_id=buddy_id,
                    on_focus_start=subscribed,
                    on_focus_end=subscribed,
                    muted=bool(muted),
                )
            else:
                self.client.rpc(
                    "lili_set_buddy_subscription",
                    {
                        "p_buddy_id": buddy_id,
                        "p_on_focus_start": subscribed,
                        "p_on_focus_end": subscribed,
                        "p_muted": bool(muted),
                    },
                )
            if muted:
                self._muted_buddy_ids.add(buddy_id)
            else:
                self._muted_buddy_ids.discard(buddy_id)
            self._set_status("已开启消息免打扰。" if muted else "已关闭消息免打扰。")
            self.refresh()
        except SocialError as exc:
            self._error(exc)

    def _remove_buddy(self, buddy: dict[str, Any]) -> None:
        if not self._require_login():
            return
        buddy_id = str(buddy.get("user_id") or buddy.get("id") or "")
        if not buddy_id:
            return
        answer = QMessageBox.question(
            self,
            "删除搭子",
            f"确定删除“{_owner_label(buddy)}”吗？\n\n双方的搭子关系和通知设置会删除，但不会删除你自己的待办、专注记录或聊天记录。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._begin_action("正在删除搭子关系…")
        try:
            self.client.rpc("lili_remove_buddy", {"p_buddy_id": buddy_id})
            self._muted_buddy_ids.discard(buddy_id)
            self._end_action()
            self._set_status("搭子已删除。")
            self.refresh()
        except SocialError as exc:
            self._error(exc)

    def _buddy_context_menu(self, position) -> None:
        """Keep private labels in the buddy list, where their scope is clear."""

        item = self.buddies.itemAt(position)
        if item is None:
            return
        buddy = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(buddy, dict) or buddy.get("is_self"):
            return
        buddy_id = str(buddy.get("user_id") or buddy.get("id") or "")
        if not buddy_id:
            return
        menu = QMenu(self)
        edit = menu.addAction("修改私人备注…")
        if str(buddy.get("private_note_name") or "").strip():
            clear = menu.addAction("清空私人备注")
        else:
            clear = None
        menu.addSeparator()
        muted = bool(buddy.get("notifications_muted") or buddy_id in self._muted_buddy_ids)
        mute = menu.addAction("关闭消息免打扰" if muted else "消息免打扰")
        menu.addSeparator()
        remove = menu.addAction("删除搭子")
        chosen = menu.exec(self.buddies.viewport().mapToGlobal(position))
        if chosen is edit:
            self._edit_buddy_private_note(buddy)
        elif clear is not None and chosen is clear:
            self._save_buddy_private_note(buddy, "")
        elif chosen is mute:
            self._set_buddy_muted(buddy, not muted)
        elif chosen is remove:
            self._remove_buddy(buddy)

    def _edit_buddy_private_note(self, buddy: dict[str, Any]) -> None:
        current = str(buddy.get("private_note_name") or "").strip()
        value, accepted = QInputDialog.getText(
            self,
            "修改搭子备注",
            "仅你可见的备注名：",
            QLineEdit.EchoMode.Normal,
            current,
        )
        if accepted:
            self._save_buddy_private_note(buddy, value)

    def _update_private_note_snapshot(self, buddy_id: str, note: str) -> None:
        """Update every local projection so the label is immediately consistent."""

        self._private_notes_loaded = True
        if note:
            self._private_note_by_user[buddy_id] = note[:40]
        else:
            self._private_note_by_user.pop(buddy_id, None)

        def update(items: Any) -> None:
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                ids = {
                    str(item.get(field))
                    for field in ("user_id", "buddy_user_id", "buddy_id", "peer_id", "owner_id", "sender_id", "receiver_id")
                    if item.get(field) is not None
                }
                if buddy_id in ids:
                    if note:
                        item["private_note_name"] = note
                    else:
                        item.pop("private_note_name", None)

        for key in ("buddies", "room_people", "active_visits", "visits", "requests", "leaderboard"):
            update(self.data.get(key))
        current_room = self.data.get("current_room")
        if isinstance(current_room, dict):
            for key in ("room_people", "active_visits", "visits"):
                update(current_room.get(key))

    def _save_buddy_private_note(self, buddy: dict[str, Any], value: str) -> None:
        if not self._require_login():
            return
        buddy_id = str(buddy.get("user_id") or buddy.get("id") or "")
        if not buddy_id:
            return
        note = str(value or "").strip()[:40]
        self._begin_action("正在保存私人备注…")
        try:
            self.client.rpc(
                "lili_set_buddy_private_note",
                {"p_buddy_id": buddy_id, "p_private_note_name": note},
            )
            self._update_private_note_snapshot(buddy_id, note)
            # Keep an explicit rename/clear authoritative across an offline
            # restart. This only updates the account-scoped display cache; it
            # never writes a relationship, profile, or focus record.
            client_notes = getattr(self.client, "_private_note_by_user", None)
            if isinstance(client_notes, dict):
                client_notes.clear()
                client_notes.update(self._private_note_by_user)
            remember = getattr(self.client, "_remember_dashboard", None)
            if callable(remember) and isinstance(self.data, dict):
                remember(self.current_room_id, self.data)
            self._end_action()
            self.apply_dashboard(self.data)
            self._set_status("私人备注已保存；只有你能看到。" if note else "私人备注已清空；对方昵称保持不变。")
        except SocialError as exc:
            self._error(exc)

    def _add_buddy(self) -> None:
        if not self._require_login():
            return
        dialog = BuddyCodeDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        code = dialog.code
        if len(code) != 8:
            self._error(SocialError("请输入 8 位搭子码。", kind="validation"))
            return
        self._begin_action("正在查找搭子…")
        thread = SocialBuddyRpcThread(self.client, "lili_lookup_buddy_by_code", {"code": code}, self)
        self._buddy_rpc_threads.append(thread)
        thread.completed.connect(lambda payload, value=code: self._buddy_lookup_completed(value, payload), Qt.ConnectionType.QueuedConnection)
        thread.failed.connect(self._buddy_rpc_failed)
        thread.finished.connect(lambda current=thread: self._buddy_rpc_finished(current), Qt.ConnectionType.QueuedConnection)
        thread.start()

    def _buddy_lookup_completed(self, code: str, payload: object) -> None:
        self._end_action()
        if not isinstance(payload, dict):
            self._error(SocialError("搭子资料返回异常，请稍后重试。", kind="network", retryable=True))
            return
        state = str(payload.get("state") or "available")
        owner = _owner_label(payload)
        if state == "self":
            self._set_status("这是你自己的六毛，不能添加自己。", error=True)
            return
        if state == "accepted":
            self._set_status(f"{owner}已经是你的搭子。")
            return
        if state == "pending":
            self._set_status(
                f"查找完成：你之前已经向{owner}发送过申请；本次没有重复发送。"
                "请到“互动”页查看或撤回。"
            )
            return
        if state == "incoming":
            self.tabs.setCurrentIndex(1)
            self._set_status(f"{owner}已经向你发送申请，请到“互动”页处理。")
            return

        profile_text = f"找到：{owner}"
        nickname = _owner_nickname(payload)
        if nickname and nickname != owner.replace("家的六毛", ""):
            profile_text += f"\n昵称：{nickname}"
        outfit = str(payload.get("outfit_key") or "").strip()
        if outfit:
            profile_text += f"\n娃衣：{outfit[:60]}"
        profile_text += "\n\n确认后才会发送搭子申请。"
        confirm = BuddyProfileDialog(profile_text, self)
        if confirm.exec() == QDialog.DialogCode.Accepted:
            self._send_buddy_request(code)

    def _send_buddy_request(self, code: str) -> None:
        self._begin_action("正在发送搭子申请…")
        thread = SocialBuddyRpcThread(self.client, "lili_add_buddy_by_code", {"code": code}, self)
        self._buddy_rpc_threads.append(thread)
        thread.completed.connect(self._buddy_request_completed)
        thread.failed.connect(self._buddy_rpc_failed)
        thread.finished.connect(lambda current=thread: self._buddy_rpc_finished(current), Qt.ConnectionType.QueuedConnection)
        thread.start()

    def _buddy_request_completed(self, payload: object) -> None:
        self._end_action()
        state = str(payload.get("state") or "pending") if isinstance(payload, dict) else "pending"
        owner = _owner_label(payload if isinstance(payload, dict) else {})
        if state == "accepted":
            message = f"{owner}已经是你的搭子，无需重复添加。"
        elif state == "incoming":
            self.tabs.setCurrentIndex(1)
            message = f"{owner}已经向你发送申请，请到“互动”页处理。"
        elif state == "pending":
            message = f"已向{owner}发送搭子申请，等待对方回应。"
        else:
            message = "搭子申请状态已更新，请刷新互动页。"
        self._set_status(message)
        self.refresh()

    def _buddy_rpc_failed(self, error: object) -> None:
        exc = error if isinstance(error, Exception) else SocialError(str(error), kind="network")
        self._error(exc)

    def _buddy_rpc_finished(self, thread: SocialBuddyRpcThread) -> None:
        if thread in self._buddy_rpc_threads:
            self._buddy_rpc_threads.remove(thread)
        thread.deleteLater()

    def _send_visit(self) -> None:
        if not self._require_login(): return
        item=self.buddies.currentItem()
        if not item: return self._error(SocialError("请先选择一位搭子。"))
        buddy = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(buddy, dict): return self._error(SocialError("请先选择一位搭子。"))
        self._begin_action("六毛正在准备出发…")
        try:
            self.client.rpc("lili_send_visit",{"target":buddy["user_id"],"visit_kind":"visit"}); self._end_action(); self._set_status("六毛已经出发，等待对方接受串门。"); QMessageBox.information(self,"已出发","六毛已经出发，等待对方接受串门。")
        except SocialError as exc: self._error(exc)
    def _accept_inbox(self) -> None:
        if not self._require_login(): return
        item=self.inbox.currentItem()
        if not item: return self._error(SocialError("请先选择一项申请或串门。"))
        kind,data=item.data(Qt.ItemDataRole.UserRole)
        if kind == "buddy_outgoing":
            return self._set_status("这是你发出的申请，请等待对方回应或选择撤回。")
        self._begin_action("正在处理选中的申请…")
        try:
            if kind=="buddy":
                self.client.rpc("lili_respond_buddy",{"request_id":data["id"],"accept":True})
            elif kind == "achievement_witness":
                self.client.rpc("lili_respond_achievement_witness", {"p_achievement_id": data["achievement_id"], "p_accept": True})
            else:
                self.client.rpc("lili_respond_visit",{"event_id":data["id"],"accept":True})
                if kind == "food":
                    self.food_interaction_accepted.emit(data)
            self.refresh()
        except SocialError as exc: self._error(exc)

    def _reject_inbox(self) -> None:
        if not self._require_login():
            return
        item = self.inbox.currentItem()
        if item is None:
            return self._error(SocialError("请先选择一项申请或串门。"))
        kind, data = item.data(Qt.ItemDataRole.UserRole)
        if kind == "buddy_outgoing":
            return self._cancel_buddy_request()
        self._begin_action("正在处理选中的申请…")
        try:
            if kind == "buddy":
                self.client.rpc("lili_respond_buddy", {"request_id": data["id"], "accept": False})
            elif kind == "achievement_witness":
                self.client.rpc("lili_respond_achievement_witness", {"p_achievement_id": data["achievement_id"], "p_accept": False})
            else:
                self.client.rpc("lili_respond_visit", {"event_id": data["id"], "accept": False})
            self.refresh()
        except SocialError as exc:
            self._error(exc)

    def _cancel_buddy_request(self) -> None:
        if not self._require_login():
            return
        item = self.inbox.currentItem()
        if item is None:
            return self._error(SocialError("请先选择一条我发出的搭子申请。", kind="validation"))
        payload = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(payload, tuple) or len(payload) != 2 or payload[0] != "buddy_outgoing":
            return self._error(SocialError("当前选中项不是待撤回的搭子申请。", kind="validation"))
        data = payload[1] if isinstance(payload[1], dict) else {}
        request_id = str(data.get("id") or "")
        if not request_id:
            return self._error(SocialError("搭子申请编号缺失，请刷新互动页。", kind="validation"))
        self._begin_action("正在撤回搭子申请…")
        try:
            self.client.rpc("lili_cancel_buddy_request", {"request_id": request_id})
            self._end_action()
            self._set_status("搭子申请已撤回。")
            self.refresh()
        except SocialError as exc:
            self._error(exc)
    def _create_room(self) -> None:
        if not self._require_login(): return
        name,ok=QInputDialog.getText(self,"创建自习室","自习室名称：",text="安静工作间")
        if ok and name:
            self._begin_action("正在创建自习室…")
            try: self.client.rpc("lili_create_room",{"room_name":name}); self.refresh(); self._set_status("自习室已创建，可以分享房间码了。")
            except SocialError as exc: self._error(exc)
    def _join_room(self) -> None:
        if not self._require_login(): return
        code,ok=QInputDialog.getText(self,"加入自习室","输入 8 位房间码：")
        if ok and code:
            self._begin_action("正在加入自习室…")
            try: self.client.rpc("lili_join_room",{"code":code}); self.refresh(); self._set_status("已加入自习室。")
            except SocialError as exc: self._error(exc)
