Warning: truncated output (original token count: 60553)
Total output lines: 4883

"""搭子自习室界面、后台同步线程和双六毛本地串门窗口。

账号注册会明确显示“等待邮箱确认”状态，并允许用户重新发送确认邮件；
邮箱确认页打开项目页面后，用户回到这里即可登录，不会把“没有即时 session”误报成注册失败。
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
from typing import Any

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
    _dashboard_payload_has_core_shape,
    social_user_message,
)
from .config import PET_NAME, clean_owner_nickname, social_pet_label
from .focus_analytics import MAX_ANALYTICS_DAY_SECONDS
from .login_rewards import login_reward_granted, login_streak_days
from .work_timer import format_work_duration
from .lifecycle_log import lifecycle_log

LOGGER = logging.getLogger(__name__)


_DASHBOARD_IDENTITY_FIELDS = (
    "user_id",
    "id",
    "invite_code",
    "owner_nickname",
    "nickname",
)


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

    # A response with a sparse ``me`` mapping must not blank a durable invite
    # code or nickname.  Empty fields are treated as “not supplied” here; an
    # explicit rename is sent through the profile update path instead.
    old_me = previous.get("me") if isinstance(previous.get("me"), dict) else {}
    new_me = merged.get("me") if isinstance(merged.get("me"), dict) else {}
    if old_me and new_me:
        for field in _DASHBOARD_IDENTITY_FIELDS:
            if not str(new_me.get(field) or "").strip() and str(old_me.get(field) or "").strip():
                new_me[field] = old_me[field]
        merged["me"] = new_me

    if not complete:
        merged["_dashboard_partial"] = True
        merged["is_stale"] = True
        merged["data_source"] = "server_partial"
        merged["_data_source"] = "server_partial"
        merged["_sync_error"] = "服务器返回了不完整的社交快照，已保留上次正常数据。"
    else:
        merged.pop("_dashboard_partial", None)
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
        or record.get("owner_nickname")
        or record.get("nickname")
        or record.get("display_name")
        or "搭子"
    ).strip() or "搭子"


def _owner_label(record: dict[str, Any] | None) -> str:
    if isinstance(record, dict) and bool(record.get("is_self")):
        public_name = clean_owner_nickname(
            record.get("owner_nickname") or record.get("nickname") or record.get("display_name")
        )
        if not public_name or public_name == "搭子":
            return f"我的{PET_NAME}"
    return social_pet_label(_owner_nickname(record))


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


def _live_session_seconds(record: dict[str, Any]) -> int | None:
    """Calculate a peer's current round from the server start timestamp."""
    if _presence_status(record) != "focus":
        return 0
    started = str(record.get("session_started_at") or "")
    if not started:
        value = record.get("session_seconds")
        return int(value) if value is not None else None
    try:
        stamp = str(record.get("_server_timestamp") or "")
        now = datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else datetime.now().astimezone()
        return max(0, int((now - datetime.fromisoformat(started.replace("Z", "+00:00"))).total_seconds()))
    except (TypeError, ValueError):
        value = record.get("session_seconds")
        return int(value) if value is not None else None


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
        payload = _heartbeat_payload(dict(presence))
        with self._condition:
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
                self._shutdown_payload = _heartbeat_payload(dict(final_presence))
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
            personal_state_result = None
            presence_context_updated: bool | None = None
            taunt_state_result = None
            encouragement_state_result = None
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
                        focus_segments_result = sync_rpc(
                            "lili_sync_focus_segments",
                            {"p_segments": personal_state.get("focus_segments") or []},
                        )
                    except (SocialError, AttributeError, TypeError) as exc:
                        # Older relays do not expose the raw-fact migration;
                        # the legacy profile/daily compatibility path remains
                        # usable until they are upgraded.
                        LOGGER.info("focus segment sync deferred: %s", exc)
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
            # Fetching it on every five-second dashboard cycle caused a slow
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
            if isinstance(data, dict) and self.request_generation:
                data = dict(data)
                data["_request_generation"] = self.request_generation
            self.completed.emit(data)
        except SocialError as exc:
            cached_loader = getattr(self.client, "cached_dashboard", None)
            cached = cached_loader(self.presence.get("room_id")) if callable(cached_loader) else None
            if cached is not None:
                if presence_context_updated is not None:
                    cached = dict(cached)
                    cached["_presence_context_updated"] = presence_context_updated
                if self.request_generation:
                    cached = dict(cached)
                    cached["_request_generation"] = self.request_generation
                self.completed.emit(cached)
            else:
                self.failed.emit(str(exc))


class SocialDashboardThread(QThread):
    """Fetch one dashboard without blocking the Qt GUI thread."""

    completed = Signal(dict)
    failed = Signal(object)

    def __init__(self, client: SocialClient, room_id: str | None, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.room_id = room_id

    def run(self) -> None:
        try:
            try:
                data = self.client.dashboard(room_id=self.room_id)
            except TypeError:
                # Keep small offline/test backends compatible with the room
                # scoped dashboard while the real request stays off the GUI.
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

    def __init__(self, client: SocialClient, room_id: str | None = None, p…40553 tokens truncated…   def apply_dashboard(self, data: dict[str, Any] | None) -> None:
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

        # A direct render can happen immediately after construction (for
        # example when the owner restores a cached snapshot). Do not let the
        # one-shot 50 ms bootstrap refresh arrive afterward and overwrite that
        # newer view with its older in-flight result.
        self._initial_refresh_timer.stop()
        self.data = dict(payload)
        self._muted_buddy_ids = {
            str(item).strip()
            for item in (self.data.get("muted_buddy_ids") or [])
            if str(item).strip()
        }
        # Missing is not empty: heartbeat payloads may omit this optional RPC
        # while the room dashboard remains healthy.  Preserve the last known
        # board until an explicit ``leaderboard=[]`` arrives.
        if "leaderboard" in self.data:
            self._leaderboard_rows = self._decorate_leaderboard_rows(self.data.get("leaderboard") or [])
            self._leaderboard_loaded = True
            self._leaderboard_error = False
            self._render_wealth_leaderboard(self._leaderboard_rows)
        me=self.data.get("me") or {}
        if not self.owner_nickname:
            self.owner_nickname = clean_owner_nickname(me.get("owner_nickname") or me.get("nickname"))
        me_presence = self.data.get("me_presence") or {}
        own_label = social_pet_label(self.owner_nickname or me.get("nickname"))
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
            # A transport outage must not turn the last peer state into a
            # misleading active count; the card itself still shows the
            # preserved/stale status details.
            working_count += int(_presence_status(buddy) == "focus")
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
            f"现在 {working_count} 位搭子正在专注　·　"
            f"我的今日专注 {format_work_duration(me_seconds)}"
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
            room_people = [{**person, "_server_timestamp": server_timestamp} for person in room_people]
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
            f"现在 {working_count} 位搭子正在专注　·　"
            f"我的今日专注 {format_work_duration(me_seconds)}"
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
            self._set_status(
                "自习室连接暂时不稳定，搭子最近状态仍保留显示；正在自动恢复实时同步。"
            )
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
                        "本地专注已开始；你还没有加入自习室，搭子状态会在网络恢复后自动同步。"
                    )
                else:
                    self._set_status(
                        "你还没有加入自习室；本地功能不受影响，联网后搭子状态会自动同步。"
                    )
        elif state == "DEGRADED":
            self._set_status("自习室已连接，实时同步暂时不可用，继续重新连接。")
        elif state == "ONLINE":
            self._set_status("自习室已连接，房间状态已同步。")
        else:
            self._set_status("已刷新，页面内容是最新的。")

    def _save_profile(self) -> None:
        if not self._require_login(): return
        self._begin_action("正在保存隐私设置…")
        try:
            me=self.data.get("me") or {}
            # Privacy changes must not use the display fallback (“搭子”) as a
            # nickname write. On a fresh machine that fallback is only a
            # renderer default; sending it would replace an existing durable
            # social identity while the user is merely changing visibility.
            nickname = str(
                self.owner_nickname
                or me.get("owner_nickname")
                or ""
            ).strip()
            self.client.update_profile(nickname=nickname,visibility="hidden" if self.hidden.isChecked() else "friends",show_exact_time=self.exact.isChecked(),allow_visits=self.visits_allowed.isChecked(),outfit_key=self.outfit_key,wealth_leaderboard_enabled=self.wealth_opt_in.isChecked(),wealth_leaderboard_preference_set=True)
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

        def update(items: Any) -> None:
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                ids = {
                    str(item.get(field))
                    for field in ("user_id", "buddy_id", "peer_id", "sender_id", "receiver_id")
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

