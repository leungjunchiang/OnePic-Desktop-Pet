"""Lili 搭子自习室的最小社交客户端与可替换网络后端。

只发送账号认证、昵称、六毛外观、实时工作状态、累计秒数、房间与串门事件。工作心跳
包含会话/暂停状态并以短间隔刷新，避免正在工作时被误判为可嘲讽。密码从不保存；
刷新令牌保存在系统凭据库。邮箱注册明确区分“已创建、等待确认”和“已登录”，并支持
重新发送确认邮件；网络失败不会影响离线桌宠、计时、AI 或本地素材。
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import os
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Protocol
import unicodedata
import uuid

from .local_data import account_local_data_path, app_data_dir, read_json, write_json_atomic
from .resources import resource_path, resource_root
from .tls_support import tls_diagnostics, verified_ssl_context


LOGGER = logging.getLogger(__name__)

CONNECTION_STATES = {"CONNECTING", "ONLINE", "DEGRADED", "OFFLINE", "RECONNECTING"}
# A short dashboard outage must not turn the last confirmed remote presence
# into a false offline result.  The server itself uses a two-minute heartbeat
# freshness window; allowing one extra minute here covers a missed poll and
# the time needed for the next retry without claiming that the peer is live.
PRESENCE_GRACE_SECONDS = 180
BEIJING_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")

# Fields accepted by the durable focus-presence heartbeat endpoint. The
# desktop UI keeps additional local-only state in the same snapshot, so the
# transport must filter it before calling the backend.
HEARTBEAT_FIELDS = frozenset(
    {
        "working",
        "session_active",
        "work_state",
        "pause_reason",
        "today_seconds",
        "session_started_at",
        "outfit_key",
        "room_id",
        "quick_status",
        "quick_status_expires_at",
    }
)

_PRESENCE_TIMESTAMP_FIELDS = ("last_seen_at", "last_seen", "status_updated_at")
_PRESENCE_DEVICE_STATE_FILENAME = "social-device.json"
_PRESENCE_DEVICE_STATE_LOCK = threading.Lock()
_PRESENCE_DEVICE_STATE_CACHE: dict[str, dict[str, Any]] = {}


def _presence_device_credentials(user_id: str) -> tuple[str, bool]:
    """Return a stable per-account device id and whether it must claim the lease.

    The live database protects an account's presence row with a device lease.
    Older desktop builds omitted these fields, which made every heartbeat after
    the first claimed session fail with ``device_session_revoked``.  Keep the
    identifier in the account-scoped local directory so updating the app does
    not claim a new device on every launch, and only claim once per account.
    """

    account_id = str(user_id or "").strip()
    if not account_id:
        return uuid.uuid4().hex, True
    with _PRESENCE_DEVICE_STATE_LOCK:
        cached = _PRESENCE_DEVICE_STATE_CACHE.get(account_id)
        if cached:
            return str(cached["device_id"]), bool(cached.get("claim_pending", True))

        path = account_local_data_path(_PRESENCE_DEVICE_STATE_FILENAME, account_id)
        raw = read_json(path, {})
        device_id = str(raw.get("device_id") or "").strip()[:64] if isinstance(raw, dict) else ""
        if not device_id:
            device_id = uuid.uuid4().hex
        state = {
            "version": 1,
            "device_id": device_id,
            "claim_pending": bool(raw.get("claim_pending", True)) if isinstance(raw, dict) else True,
        }
        _PRESENCE_DEVICE_STATE_CACHE[account_id] = state
        try:
            write_json_atomic(path, state)
        except OSError:
            # The in-memory state still keeps one running process stable when
            # a locked-down portable install cannot write its app-data folder.
            LOGGER.debug("social device state could not be persisted", exc_info=True)
        return device_id, bool(state["claim_pending"])


def _mark_presence_device_claimed(user_id: str, device_id: str) -> None:
    """Mark a successful device lease so later heartbeats do not steal it."""

    account_id = str(user_id or "").strip()
    clean_device_id = str(device_id or "").strip()
    if not account_id or not clean_device_id:
        return
    with _PRESENCE_DEVICE_STATE_LOCK:
        state = _PRESENCE_DEVICE_STATE_CACHE.get(account_id)
        if not state or str(state.get("device_id")) != clean_device_id:
            return
        state["claim_pending"] = False
        try:
            write_json_atomic(account_local_data_path(_PRESENCE_DEVICE_STATE_FILENAME, account_id), state)
        except OSError:
            LOGGER.debug("social device claim state could not be persisted", exc_info=True)


def _mark_presence_device_claim_pending(user_id: str, device_id: str) -> None:
    """Allow one later heartbeat to reclaim a lease moved to another device."""

    account_id = str(user_id or "").strip()
    clean_device_id = str(device_id or "").strip()
    if not account_id or not clean_device_id:
        return
    with _PRESENCE_DEVICE_STATE_LOCK:
        state = _PRESENCE_DEVICE_STATE_CACHE.get(account_id)
        if not state or str(state.get("device_id")) != clean_device_id:
            return
        state["claim_pending"] = True
        try:
            write_json_atomic(account_local_data_path(_PRESENCE_DEVICE_STATE_FILENAME, account_id), state)
        except OSError:
            LOGGER.debug("social device reclaim state could not be persisted", exc_info=True)


def _session_user_id(client: Any) -> str:
    """Return the authenticated account id without ever guessing it.

    The desktop has both a route manager and individual HTTP transports.  A
    token refresh can update the shared auth manager before the currently
    selected transport has copied its ``session`` attribute, so callers must
    check both layers.  An empty string means that the client is not
    authenticated and must not be allowed to read an account-scoped cache.
    """

    try:
        session = getattr(client, "session", None)
    except Exception:
        session = None
    user_id = str(getattr(session, "user_id", "") or "").strip()
    if user_id:
        return user_id

    manager = getattr(client, "auth_manager", None)
    if isinstance(manager, AuthSessionManager):
        current = manager.current()
        user_id = str(getattr(current, "user_id", "") or "").strip()
        if user_id:
            return user_id

    backend = getattr(client, "_http_backend", None)
    try:
        session = getattr(backend, "session", None)
    except Exception:
        session = None
    return str(getattr(session, "user_id", "") or "").strip()


def _normalise_never_seen_presence(data: dict[str, Any]) -> dict[str, Any]:
    """Zero durable-looking totals for peers with no presence record.

    ``lili_dashboard`` returns a profile row even when a user has never sent
    a heartbeat.  In that case the profile's old focus totals are not proof
    that this account has ever logged in.  Current deployments include a
    nullable ``last_seen_at`` field specifically for this distinction.  Keep
    the rule at the client boundary as well so a desktop release remains safe
    while the database migration is rolling out.
    """

    def normalise(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict) or item.get("is_self"):
                continue
            fields_present = any(field in item for field in _PRESENCE_TIMESTAMP_FIELDS)
            never_seen = bool(item.get("presence_never_seen"))
            if not never_seen and not fields_present:
                continue
            has_seen = any(str(item.get(field) or "").strip() for field in _PRESENCE_TIMESTAMP_FIELDS)
            if never_seen or not has_seen:
                item.update(
                    {
                        "today_seconds": 0,
                        "week_seconds": 0,
                        "session_seconds": 0,
                        "presence_never_seen": True,
                    }
                )

    normalise(data.get("buddies"))
    normalise(data.get("room_people"))
    normalise(data.get("active_visits"))
    current_room = data.get("current_room")
    if isinstance(current_room, dict):
        normalise(current_room.get("room_people"))
        normalise(current_room.get("active_visits"))
    return data


def _heartbeat_payload(presence: dict[str, Any]) -> dict[str, Any]:
    """Select only fields supported by the social heartbeat API."""

    return {key: presence[key] for key in HEARTBEAT_FIELDS if key in presence}


def _atomic_presence_body(body: dict[str, Any]) -> dict[str, Any]:
    """Map the transport-neutral heartbeat to the atomic RPC parameters."""

    return {
        "p_working": bool(body.get("working")),
        "p_session_active": bool(body.get("session_active")),
        "p_work_state": str(body.get("work_state") or "idle")[:32],
        "p_pause_reason": body.get("pause_reason"),
        "p_session_started_at": body.get("session_started_at"),
        "p_focus_date": body.get("focus_date"),
        "p_today_seconds": int(body.get("today_seconds") or 0),
        "p_outfit_key": str(body.get("outfit_key") or "")[:60],
        "p_room_id": body.get("room_id"),
        "p_quick_status": str(body.get("quick_status") or "")[:40],
        "p_quick_status_expires_at": body.get("quick_status_expires_at"),
        "p_device_id": str(body.get("device_id") or "")[:120],
        "p_device_claim": bool(body.get("device_claim")),
    }


class ConnectionStateStore:
    """Single source of truth for the study-room transport state.

    The desktop currently uses an authenticated HTTPS snapshot plus short
    polling, not a separate websocket subscription.  A successful /health
    response therefore never proves that the room is online by itself.
    """

    def __init__(self) -> None:
        self.state = "OFFLINE"
        self.data_source = "local_cache"
        self.realtime_state = "not_started"
        self.last_success_at = ""
        self.last_failure_at = ""
        self.server_timestamp = ""

    def set(self, state: str, *, data_source: str, realtime_state: str = "not_started", server_timestamp: str = "") -> None:
        self.state = state if state in CONNECTION_STATES else "OFFLINE"
        self.data_source = data_source
        self.realtime_state = realtime_state
        if server_timestamp:
            self.server_timestamp = server_timestamp
        now = datetime.now().astimezone().isoformat()
        if self.state == "ONLINE":
            self.last_success_at = now
        elif self.state in {"OFFLINE", "RECONNECTING", "DEGRADED"}:
            self.last_failure_at = now

    def payload(self) -> dict[str, Any]:
        return {
            "connection_state": self.state,
            "data_source": self.data_source,
            "realtime_state": self.realtime_state,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "server_timestamp": self.server_timestamp,
        }


class SocialError(RuntimeError):
    """面向用户的社交网络错误，并保留可记录的诊断分类。"""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "unknown",
        endpoint: str = "",
        retryable: bool = False,
        status: int | None = None,
        error_code: str = "",
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.endpoint = endpoint
        self.retryable = retryable
        self.status = status
        self.error_code = error_code


def social_user_message(error: BaseException) -> str:
    """把 Supabase/网络异常翻译成不泄露 JSON 的用户提示。"""

    code = str(getattr(error, "error_code", "") or "").casefold()
    kind = str(getattr(error, "kind", "") or "").casefold()
    raw = str(error or "")
    lowered = raw.casefold()
    if kind == "signup_timeout":
        return "注册服务未在规定时间内返回，当前无法确认账号是否已创建；请不要重复注册或连续点击注册。先检查收件箱和垃圾邮件，稍后使用“重新发送确认邮件”；如果账号已经存在，请切换到登录或使用“忘记密码”。"
    if kind == "confirmation_timeout" or code == "request_timeout":
        return "确认邮件服务未在规定时间内返回，请不要连续注册或重复点击；稍后再点“重新发送确认邮件”，并检查收件箱和垃圾邮件。如果这个邮箱已经注册，请切换到登录或使用“忘记密码”。若新账号仍始终收不到邮件，需要管理员检查 Supabase Auth 的事务邮件 SMTP。"
    if "email address not authorized" in lowered or "email not authorized" in lowered:
        return "当前 Supabase 邮件服务只允许项目团队邮箱收信，163/学校邮箱不会收到邮件；请为 Supabase Auth 配置自定义 SMTP 后重试。"
    if "already confirmed" in lowered or "email already confirmed" in lowered:
        return "这个邮箱已经确认过，不需要继续等待注册邮件；请直接登录。忘记密码可以使用“忘记密码？”发送重置邮件。"
    if code in {"otp_expired", "otp_invalid", "invalid_otp", "one_time_token_not_found"} or "one-time token" in lowered or "otp" in lowered and ("expired" in lowered or "invalid" in lowered):
        return "验证码无效或已过期。请重新发送验证码，10 分钟内输入最新验证码。"
    if code in {"invalid_credentials", "invalid_login_credentials"} or "invalid login credentials" in lowered:
        return "登录失败：邮箱或密码不匹配。请确认邮箱已经完成验证，并使用该邮箱原来设置的密码；重复注册不会覆盖原密码。忘记密码请点击“忘记密码？”通过邮箱重置。"
    if code in {"weak_password", "password_too_short", "password_strength"} or "password is too weak" in lowered or "password should be at least" in lowered:
        return "新密码至少需要 8 位，请换一个更长的密码；建议同时包含字母和数字。"
    if code in {"reauthentication_needed", "current_password_required"} or "current password" in lowered or "reauthentication" in lowered:
        return "为了保护账号，请填写当前密码后再修改密码。"
    if "password" in lowered and ("same" in lowered or "match" in lowered or "identical" in lowered):
        return "两次输入的新密码不一致，请重新检查。"
    if code in {"same_password", "same_password_error"} or "different from the old password" in lowered:
        return "新密码不能和当前密码相同，请换一个密码。"
    if code == "email_not_confirmed" or "email not confirmed" in lowered:
        return "这个邮箱还没有完成确认，请先打开确认邮件中的链接，再回到六毛登录。"
    if "rate limit" in lowered or "too many requests" in lowered or getattr(error, "status", None) == 429:
        return "确认邮件发送次数已达到服务限额，请稍后再试；生产环境建议为 Supabase Auth 配置自定义 SMTP。"
    if "lili_buddy_pair_unique" in lowered or "duplicate key" in lowered:
        return "这位搭子已经存在或申请已处理，无需重复添加；请刷新互动页查看当前状态。"
    if "已经是你的搭子" in raw or "already buddies" in lowered:
        return "这位搭子已经在你的搭子列表中。"
    if "搭子申请已经处理" in raw or "request already" in lowered:
        return "这条搭子申请已经处理，请刷新互动页。"
    if code == "refresh_token_already_used" or kind in {"auth_refresh", "auth_refresh_reused"} or (
        "refresh token" in lowered and ("already used" in lowered or "invalid" in lowered)
    ):
        return "登录状态已失效，请重新登录。"
    if raw.lstrip().startswith("{") and ("error_code" in lowered or '"code"' in lowered):
        return "自习室登录状态需要重新验证，请重新登录。"
    return raw[:300] or "自习室连接失败，请稍后重试。"


def normalize_email(email: str) -> str:
    """Normalize copied email addresses without changing the local-part case."""

    value = unicodedata.normalize("NFKC", str(email or ""))
    value = "".join(char for char in value if unicodedata.category(char) not in {"Cc", "Cf"})
    value = value.strip()
    if "@" not in value:
        return value
    local, domain = value.rsplit("@", 1)
    return f"{local}@{domain.casefold()}"


def _missing_room_endpoint(error: BaseException) -> bool:
    """识别旧中转服务/旧数据库缺少房间详情 RPC 的情况。"""

    status = getattr(error, "status", None)
    code = str(getattr(error, "error_code", "") or "").casefold()
    raw = str(error or "").casefold()
    return status == 404 or code in {"pgrst202", "42883"} or "room_dashboard" in raw or "自习室接口" in raw


def _endpoint_host(base_url: str) -> str:
    parsed = urllib.parse.urlparse(str(base_url or ""))
    return parsed.netloc or "未配置"


def _private_notes_from_payload(payload: Any) -> dict[str, str]:
    """Normalize the private-note RPC response without exposing other users."""

    if isinstance(payload, dict):
        payload = payload.get("notes", payload)
    if isinstance(payload, dict):
        return {
            str(user_id): str(note).strip()[:40]
            for user_id, note in payload.items()
            if str(user_id).strip() and str(note).strip()
        }
    if isinstance(payload, list):
        result: dict[str, str] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            user_id = item.get("buddy_user_id") or item.get("user_id")
            note = item.get("private_note_name")
            if user_id and note:
                result[str(user_id)] = str(note).strip()[:40]
        return result
    return {}


def _apply_buddy_private_notes(data: dict[str, Any], notes: dict[str, str]) -> dict[str, Any]:
    """Decorate only the current user's dashboard snapshot with private labels."""

    def decorate(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            # A cached snapshot can still contain a note that the user has
            # since removed.  The RPC's absence of a row is authoritative;
            # clear the local-only decoration before applying current notes.
            item.pop("private_note_name", None)
            for field in ("user_id", "buddy_id", "peer_id", "sender_id", "receiver_id"):
                user_id = item.get(field)
                if user_id is not None and str(user_id) in notes:
                    item["private_note_name"] = notes[str(user_id)]
                    break

    # ``focus_leaderboard`` is fetched by a separate RPC after the dashboard
    # snapshot.  Decorating it here keeps private labels available even when
    # the relay replaces the dashboard's embedded leaderboard with that raw
    # RPC result.  The labels are applied only to the current user's payload;
    # they are never stored in the shared profile or sent back to peers.
    for key in ("buddies", "room_people", "active_visits", "visits", "requests", "leaderboard"):
        decorate(data.get(key))
    current_room = data.get("current_room")
    if isinstance(current_room, dict):
        decorate(current_room.get("room_people"))
        decorate(current_room.get("active_visits"))
        decorate(current_room.get("visits"))
        activity = current_room.get("room_activity")
        if isinstance(activity, list):
            for item in activity:
                if not isinstance(item, dict):
                    continue
                actor_id = item.get("actor_id")
                target_id = item.get("target_id")
                if actor_id is not None and str(actor_id) in notes:
                    item["actor_private_note_name"] = notes[str(actor_id)]
                if target_id is not None and str(target_id) in notes:
                    item["target_private_note_name"] = notes[str(target_id)]
    activity = data.get("room_activity")
    if isinstance(activity, list):
        for item in activity:
            if not isinstance(item, dict):
                continue
            actor_id = item.get("actor_id")
            target_id = item.get("target_id")
            if actor_id is not None and str(actor_id) in notes:
                item["actor_private_note_name"] = notes[str(actor_id)]
            if target_id is not None and str(target_id) in notes:
                item["target_private_note_name"] = notes[str(target_id)]
    return data


def _network_error(exc: BaseException, base_url: str) -> SocialError:
    """把 urllib/Windows 错误转成用户能采取行动的分类。"""

    reason = getattr(exc, "reason", exc)
    host = _endpoint_host(base_url)
    if isinstance(reason, socket.gaierror) or "getaddrinfo" in str(reason).lower():
        return SocialError(f"DNS 解析失败：找不到自习室服务器（{host}）。", kind="dns", endpoint=host, retryable=True)
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).lower():
        return SocialError(f"连接超时：自习室服务器（{host}）没有及时回应。", kind="timeout", endpoint=host, retryable=True)
    if isinstance(reason, ConnectionRefusedError) or "refused" in str(reason).lower():
        return SocialError(f"服务器拒绝连接：请检查自习室中转服务（{host}）是否在线。", kind="refused", endpoint=host, retryable=True)
    if isinstance(reason, ssl.SSLError) or "ssl" in str(reason).lower() or "certificate" in str(reason).lower():
        LOGGER.warning(
            "[StudyRoom TLS] endpoint=%s exception=%s diagnostics=%s",
            host,
            str(reason),
            tls_diagnostics(),
            exc_info=True,
        )
        return SocialError(f"TLS/证书连接失败：无法安全连接自习室服务器（{host}）。", kind="tls", endpoint=host, retryable=True)
    return SocialError(f"网络不可达：无法连接自习室服务器（{host}）。", kind="network", endpoint=host, retryable=True)


def _social_request_timeout() -> float:
    """Keep an unreachable social endpoint from freezing a user action."""

    try:
        return min(
            8.0,
            max(2.0, float(os.environ.get("LILI_SOCIAL_TIMEOUT_SECONDS", "4"))),
        )
    except ValueError:
        return 4.0


def _auth_request_timeout() -> float:
    """Allow SMTP-backed Auth requests more time than normal dashboard calls.

    Supabase can create the user and send the confirmation email before Auth
    returns. The hosted SMTP path has a hard server-side deadline around ten
    seconds, while healthy custom SMTP requests can still take longer than the
    normal four-second desktop request budget. Registration and resend calls
    therefore use a dedicated, bounded timeout and are never blindly replayed.
    """

    try:
        return min(
            45.0,
            max(8.0, float(os.environ.get("LILI_AUTH_TIMEOUT_SECONDS", "30"))),
        )
    except ValueError:
        return 30.0


def _verified_urlopen(request: urllib.request.Request, *, timeout: float):
    """Open a social request with an explicit verified context on macOS.

    Windows and Linux keep their normal urllib system behavior.  macOS uses
    an explicit verified system CA bundle, with the application-bundled
    certifi file as fallback, so Finder-launched frozen builds do not depend
    on a terminal-only Python installation.
    """

    if sys.platform == "darwin":
        return urllib.request.urlopen(request, timeout=timeout, context=verified_ssl_context())
    return urllib.request.urlopen(request, timeout=timeout)


@dataclass
class SocialSession:
    access_token: str
    refresh_token: str
    user_id: str
    expires_at: float
    generation: int = 0
    email: str = ""


@dataclass(frozen=True)
class SignupResult:
    """Result of an email signup, including the confirmation-pending state."""

    email: str
    user_id: str = ""
    session_active: bool = False
    confirmation_pending: bool = False
    confirmation_sent: bool = False
    existing_account: bool = False
    email_confirmed: bool = False
    redirect_url: str = ""

    @property
    def created(self) -> bool:
        # A user object without a session is also returned when Supabase
        # receives a repeated signup for an existing email. That response
        # must not be treated as a newly created/logged-in account.
        return bool(self.session_active or self.confirmation_pending)

    def __bool__(self) -> bool:
        """Keep compatibility with older callers that treated signup as bool."""

        return self.created


@dataclass
class _AuthState:
    """进程内共享的认证状态；同一凭据只允许一个刷新请求。"""

    condition: threading.Condition
    session: SocialSession | None = None
    loaded: bool = False
    refreshing: bool = False
    last_error: SocialError | None = None
    storage_error: str = ""
    generation: int = 0


class AuthSessionManager:
    """统一管理 Supabase session、轮换令牌和 single-flight 刷新。

    Direct、proxy、首页、自习室和后台同步都只能通过这个对象取得有效
    session。凭据库中使用一个 JSON 记录写入 access/refresh/expires/generation，
    避免只保存其中一半新令牌。
    """

    _registry: dict[tuple[str, str], _AuthState] = {}
    _registry_lock = threading.Lock()

    def __init__(self, *, service_name: str, account_name: str, persist_tokens: bool = True) -> None:
        self.service_name = service_name
        self.account_name = account_name
        self.persist_tokens = persist_tokens
        if persist_tokens:
            # Persisted credentials intentionally share one in-process state
            # so direct/proxy transports cannot rotate different refresh
            # tokens.  Ephemeral managers must not use ``id(self)`` as a
            # registry key: once an old manager is collected, Python may reuse
            # that id and leak its session into an unrelated new client.
            key = (service_name, account_name)
            with self._registry_lock:
                self._state = self._registry.setdefault(
                    key,
                    _AuthState(condition=threading.Condition(threading.RLock())),
                )
        else:
            self._state = _AuthState(condition=threading.Condition(threading.RLock()))
        self._load_latest()

    @staticmethod
    def _keyring():
        import keyring
        return keyring

    @property
    def storage_label(self) -> str:
        if sys.platform == "darwin":
            return f"macOS Keychain:{self.service_name}/{self.account_name}"
        if os.name == "nt":
            return f"Windows Credential Manager:{self.service_name}/{self.account_name}"
        return f"OS credential store:{self.service_name}/{self.account_name}"

    def diagnostics(self) -> dict[str, Any]:
        with self._state.condition:
            session = self._state.session
            return {
                "auth_state": "relogin_required" if self.requires_relogin else ("signed_in" if session else "signed_out"),
                "session_exists": bool(session),
                "session_expires_at": round(session.expires_at) if session else None,
                "refresh_in_progress": self._state.refreshing,
                "token_generation": session.generation if session else self._state.generation,
                "storage": self.storage_label,
                "storage_error": self._state.storage_error,
            }

    @property
    def requires_relogin(self) -> bool:
        error = self._state.last_error
        return bool(error and (
            error.kind in {"auth", "auth_refresh", "auth_refresh_reused"}
            or error.error_code in {"invalid_refresh_token", "refresh_token_already_used", "invalid_grant", "refresh_failed"}
        ) and not error.retryable)

    def _read_store(self) -> SocialSession | None:
        if not self.persist_tokens:
            return self._state.session
        try:
            raw = self._keyring().get_password(self.service_name, self.account_name)
            if not raw:
                return None
            data = json.loads(raw)
            return SocialSession(
                str(data["access_token"]),
                str(data.get("refresh_token", "")),
                str(data.get("user_id", "")),
                float(data.get("expires_at", 0)),
                int(data.get("generation", 0) or 0),
                str(data.get("email", "") or ""),
            )
        except Exception as exc:
            self._state.storage_error = type(exc).__name__
            LOGGER.warning(
                "auth session read failed storage=%s reason=%s",
                self.storage_label,
                type(exc).__name__,
            )
            return None

    def _load_latest(self) -> SocialSession | None:
        with self._state.condition:
            session = self._read_store()
            if session is not None:
                self._state.session = session
                self._state.generation = max(self._state.generation, session.generation)
            self._state.loaded = True
            LOGGER.info("auth state loaded %s", self.diagnostics())
            return self._state.session

    def current(self) -> SocialSession | None:
        with self._state.condition:
            if not self._state.loaded:
                self._load_latest()
            return self._state.session

    def adopt(self, session: SocialSession | None) -> None:
        """接纳旧兼容调用方手动设置的 session，不重复刷新。"""

        with self._state.condition:
            self._state.session = session
            if session is not None:
                self._state.generation = max(self._state.generation, session.generation)
            self._state.loaded = True

    def persist(self, session: SocialSession | None, *, log: bool = True) -> None:
        with self._state.condition:
            self._state.session = session
            self._state.loaded = True
            if session is not None:
                self._state.generation = max(self._state.generation, session.generation)
                if self.persist_tokens:
                    payload = json.dumps(
                        {
                            "schema_version": 1,
                            "access_token": session.access_token,
                            "refresh_token": session.refresh_token,
                            "user_id": session.user_id,
                            "expires_at": session.expires_at,
                            "generation": session.generation,
                            "email": session.email,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    try:
                        self._keyring().set_password(self.service_name, self.account_name, payload)
                        self._state.storage_error = ""
                    except Exception as exc:
                        self._state.storage_error = type(exc).__name__
                        LOGGER.warning(
                            "auth session persist failed storage=%s reason=%s",
                            self.storage_label,
                            type(exc).__name__,
                        )
                        raise SocialError(
                            "登录状态暂时无法保存，稍后会自动重试。",
                            kind="auth_storage",
                            retryable=True,
                        ) from exc
                    if log:
                        LOGGER.info("session persisted storage=%s token_generation=%s", self.storage_label, session.generation)

    def accept_auth(self, data: dict[str, Any] | None) -> SocialSession | None:
        if not data or not data.get("access_token"):
            return None
        user = data.get("user") or {}
        with self._state.condition:
            generation = max(self._state.generation, (self._state.session.generation if self._state.session else 0)) + 1
            session = SocialSession(
                str(data["access_token"]),
                str(data.get("refresh_token", "")),
                str(user.get("id", data.get("user_id", ""))),
                time.time() + int(data.get("expires_in", 3600)),
                generation,
                normalize_email(str(user.get("email") or data.get("email") or "")),
            )
            self.persist(session)
            # A successful password login is the recovery path after an
            # invalid/rotated refresh token.  Clear the old relogin marker or
            # ``signed_in`` would remain false even though Auth accepted the
            # new credentials.
            self._state.last_error = None
            return session

    def clear(self) -> None:
        with self._state.condition:
            self._state.session = None
            self._state.loaded = True
            self._state.last_error = None
            if self.persist_tokens:
                try:
                    self._keyring().delete_password(self.service_name, self.account_name)
                except Exception:
                    pass

    @staticmethod
    def _is_reuse_error(exc: BaseException) -> bool:
        code = str(getattr(exc, "error_code", "") or "").casefold()
        message = str(exc).casefold()
        return code == "refresh_token_already_used" or "refresh token" in message and "already used" in message

    @contextmanager
    def _refresh_file_lock(self):
        """Serialize refresh-token rotation across simultaneously running apps."""
        if not self.persist_tokens:
            yield
            return
        lock_path = app_data_dir() / "auth-session-refresh.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            handle.close()

    def get_valid_session(
        self,
        refresh: Callable[[SocialSession], dict[str, Any] | None],
        *,
        requested_by: str,
        safety_seconds: float = 90,
        force_refresh: bool = False,
    ) -> SocialSession | None:
        """Return a usable session; only one process may rotate the token.

        ``force_refresh`` is used after the server rejects an access token that
        the local expiry timestamp still considers valid.  This can happen
        after a desktop app was suspended, a JWT key/configuration change, or
        another client rotated the session.  It must still go through the
        same single-flight and cross-process lock as normal refreshes.
        """
        with self._state.condition:
            latest = self._read_store() if self.persist_tokens else self._state.session
            if latest is not None:
                self._state.session = latest
                self._state.generation = max(self._state.generation, latest.generation)
            session = self._state.session
            if session is None or (not force_refresh and session.expires_at > time.time() + safety_seconds):
                return session
            if self._state.refreshing:
                LOGGER.info(
                    "refresh joined existing request requested_by=%s token_generation=%s",
                    requested_by,
                    session.generation,
                )
                while self._state.refreshing:
                    self._state.condition.wait()
                latest = self._read_store() if self.persist_tokens else self._state.session
                if latest is not None and (
                    (not force_refresh and latest.expires_at > time.time() + safety_seconds)
                    or (force_refresh and latest.generation > session.generation)
                ):
                    self._state.session = latest
                    return latest
                if self._state.last_error is not None:
                    raise self._state.last_error
                return self._state.session
            self._state.refreshing = True
            self._state.last_error = None
            candidate = session
            LOGGER.info("refresh requested by=%s token_generation=%s", requested_by, candidate.generation)

        try:
            with self._refresh_file_lock():
                latest = self._read_store() if self.persist_tokens else self._state.session
                if latest is not None and (
                    (not force_refresh and latest.expires_at > time.time() + safety_seconds)
                    or (force_refresh and latest.generation > candidate.generation)
                ):
                    with self._state.condition:
                        self._state.session = latest
                        self._state.generation = max(self._state.generation, latest.generation)
                    LOGGER.info(
                        "refresh joined existing request requested_by=%s token_generation=%s shared_store=true",
                        requested_by,
                        latest.generation,
                    )
                    return latest
                if latest is not None:
                    candidate = latest
                if not candidate.refresh_token:
                    raise SocialError(
                        "登录状态已失效，请重新登录。",
                        kind="auth_refresh",
                        error_code="invalid_refresh_token",
                    )
                LOGGER.info("refresh started requested_by=%s token_generation=%s", requested_by, candidate.generation)
                data = refresh(candidate)
                if data and not data.get("refresh_token"):
                    data = {**data, "refresh_token": candidate.refresh_token}
                refreshed = self.accept_auth(data)
                if refreshed is None:
                    raise SocialError(
                        "登录状态已失效，请重新登录。",
                        kind="auth_refresh",
                        error_code="refresh_failed",
                    )
                LOGGER.info("refresh success requested_by=%s token_generation=%s", requested_by, refreshed.generation)
                return refreshed
        except SocialError as exc:
            if self._is_reuse_error(exc):
                latest = self._load_latest()
                if latest is not None and latest.generation > candidate.generation and latest.expires_at > time.time() + safety_seconds:
                    LOGGER.info(
                        "refresh success requested_by=%s token_generation=%s recovered_from_shared_store=true",
                        requested_by,
                        latest.generation,
                    )
                    return latest
                exc = SocialError(
                    "登录状态已失效，请重新登录。",
                    kind="auth_refresh_reused",
                    endpoint=getattr(exc, "endpoint", ""),
                    status=getattr(exc, "status", 400),
                    error_code="refresh_token_already_used",
                )
            with self._state.condition:
                self._state.last_error = exc
            raise exc
        except (OSError, RuntimeError) as exc:
            retryable = SocialError(
                "登录状态暂时无法恢复，网络恢复后会自动重试。",
                kind="auth_storage",
                retryable=True,
            )
            with self._state.condition:
                self._state.last_error = retryable
            raise retryable from exc
        finally:
            with self._state.condition:
                self._state.refreshing = False
                self._state.condition.notify_all()



class SocialBackend(Protocol):
    """Transport-neutral social API used by the desktop UI.

    The UI only needs these operations; the route manager decides whether the
    request uses Supabase Direct or the CloudBase proxy.
    """

    @property
    def signed_in(self) -> bool: ...

    def sign_up(self, email: str, password: str, nickname: str) -> SignupResult: ...
    def resend_confirmation(self, email: str) -> bool: ...
    def sign_in(self, email: str, password: str) -> None: ...
    def record_login_streak(self) -> dict[str, Any]: ...
    def sign_out(self) -> None: ...
    def change_password(self, current_password: str, new_password: str) -> None: ...
    def request_password_reset(self, email: str) -> bool: ...
    def verify_password_reset_otp(self, email: str, otp: str) -> bool: ...
    def set_password_after_reset(self, new_password: str) -> None: ...
    def delete_account(self) -> None: ...
    def health(self) -> dict[str, Any]: ...
    def dashboard(self, room_id: str | None = None, *, allow_cache: bool = True) -> dict[str, Any]: ...
    def rpc(self, name: str, body: dict[str, Any]) -> Any: ...
    def update_profile(self, *, nickname: str, visibility: str, show_exact_time: bool, allow_visits: bool, outfit_key: str = "", wealth_leaderboard_enabled: bool = True, wealth_leaderboard_preference_set: bool = True) -> None: ...
    def update_owner_nickname(self, nickname: str) -> None: ...
    def heartbeat(self, *, working: bool, session_active: bool = False, work_state: str = "idle", pause_reason: str | None = None, today_seconds: int, session_started_at: str | None, outfit_key: str, room_id: str | None = None, quick_status: str = "", quick_status_expires_at: str | None = None) -> None: ...
    def send_interaction(self, *, target: str, kind: str, room_id: str | None = None) -> None: ...
    def record_room_event(self, *, room_id: str, kind: str, target_id: str | None = None, message: str = "") -> None: ...
    def record_economy_event(self, *, event_id: str, category: str, amount: int, label: str, source_key: str, occurred_on: str) -> None: ...
    def economy_leaderboard(self, *, period: str = "month") -> list[dict[str, Any]]: ...
    def focus_leaderboard(self, *, period: str = "week") -> list[dict[str, Any]]: ...
    def set_room_goal(self, *, room_id: str, title: str, target_seconds: int, due_at: str | None = None) -> None: ...
    def set_room_schedule(self, *, room_id: str, start_at: str, end_at: str, enabled: bool = True) -> None: ...
    def set_room_challenge(self, *, room_id: str, title: str, target_seconds: int, target_rounds: int) -> None: ...
    def set_buddy_subscription(self, *, buddy_id: str, on_focus_start: bool, on_focus_end: bool, muted: bool = False) -> None: ...
    def leave_room(self, *, room_id: str) -> None: ...


class HttpSocialBackend:
    """REST transport for either Supabase Direct or the CloudBase proxy."""

    SERVICE_NAME = "LiliSocial"
    ACCOUNT_NAME = "supabase-session"

    def __init__(self, base_url: str, *, client_key: str = "", persist_tokens: bool = True, email_redirect_url: str = "", password_reset_redirect_url: str = "", transport: str = "proxy", auth_manager: AuthSessionManager | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.client_key = client_key
        self.persist_tokens = persist_tokens
        self.email_redirect_url = email_redirect_url
        self.password_reset_redirect_url = password_reset_redirect_url or email_redirect_url
        self.transport = transport if transport in {"direct", "proxy"} else "proxy"
        self.last_server_timestamp = ""
        self.session: SocialSession | None = None
        self.auth_manager = auth_manager or AuthSessionManager(
            service_name=self.SERVICE_NAME,
            account_name=self.ACCOUNT_NAME,
            persist_tokens=persist_tokens,
        )
        self._load_session()

    @property
    def signed_in(self) -> bool:
        return (self.session is not None or self.auth_manager.current() is not None) and not self.auth_manager.requires_relogin

    @property
    def account_email(self) -> str:
        session = self.auth_manager.current() or self.session
        return normalize_email(session.email) if session is not None else ""

    @staticmethod
    def _keyring():
        import keyring
        return keyring

    def _load_session(self) -> None:
        self.session = self.auth_manager.current()

    def _save_session(self) -> None:
        self.auth_manager.persist(self.session)

    def _clear_session(self) -> None:
        self.auth_manager.clear()
        self.session = None

    def _raw(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        authenticated: bool = False,
        extra_headers: dict[str, str] | None = None,
        timeout: float | None = None,
        _retry_auth: bool = True,
    ) -> Any:
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.client_key:
            headers["apikey" if self.transport == "direct" else "X-Client-Key"] = self.client_key
        if authenticated:
            self._ensure_fresh()
            self.session = self.auth_manager.current()
            if not self.session:
                raise SocialError("请先登录搭子自习室。")
            headers["Authorization"] = f"Bearer {self.session.access_token}"
        if extra_headers:
            headers.update({str(key): str(value) for key, value in extra_headers.items()})
        request = urllib.request.Request(f"{self.base_url}{path}", data=payload, headers=headers, method=method)
        try:
            with _verified_urlopen(
                request,
                timeout=_social_request_timeout() if timeout is None else timeout,
            ) as response:
                server_time = response.headers.get("X-Lili-Server-Time") or response.headers.get("Date")
                if server_time:
                    try:
                        parsed = datetime.fromisoformat(server_time.replace("Z", "+00:00"))
                    except ValueError:
                        parsed = parsedate_to_datetime(server_time)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
                    self.last_server_timestamp = parsed.astimezone().isoformat()
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
                error_code = str(data.get("error_code") or data.get("code") or "")
                message = data.get("message") or data.get("error_description") or data.get("error") or raw
            except json.JSONDecodeError:
                error_code = ""
                message = raw or str(exc)
            status = int(exc.code)
            is_refresh_endpoint = "/auth/v1/token" in path or path.rstrip("/") == "/auth/refresh"
            kind = "auth_refresh" if is_refresh_endpoint else "auth" if status in (401, 403) else "server" if status >= 500 else "http"
            if error_code in {"refresh_token_already_used", "invalid_refresh_token", "invalid_grant"} or (
                "invalid refresh token" in str(message).casefold()
                or ("refresh token" in str(message).casefold() and "already used" in str(message).casefold())
            ):
                message = "登录状态已失效，请重新登录。"
                kind = "auth_refresh_reused" if error_code == "refresh_token_already_used" else "auth_refresh"
            # A valid refresh token can outlive an access token that the
            # client still believes is valid.  Retry one authenticated request
            # after a forced refresh.  Without this, every dashboard/RPC call
            # turns a recoverable 401 into the red “请重新登录” banner.
            if authenticated and _retry_auth and status == 401 and not is_refresh_endpoint:
                previous = self.auth_manager.current()
                try:
                    self._ensure_fresh(force=True)
                    current = self.auth_manager.current()
                    if current is not None and (
                        previous is None or current.generation > previous.generation
                    ):
                        return self._raw(
                            method,
                            path,
                            body,
                            authenticated=authenticated,
                            extra_headers=extra_headers,
                            timeout=timeout,
                            _retry_auth=False,
                        )
                except SocialError:
                    raise
            raise SocialError(
                str(message)[:300],
                kind=kind,
                endpoint=_endpoint_host(self.base_url),
                retryable=status >= 500,
                status=status,
                error_code=error_code,
            ) from exc
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise _network_error(exc, self.base_url) from exc

    def _accept_auth(self, data: dict[str, Any] | None) -> bool:
        session = self.auth_manager.accept_auth(data)
        self.session = session
        return session is not None

    def _signup_result(self, data: dict[str, Any] | None, email: str) -> SignupResult:
        session = self.auth_manager.accept_auth(data)
        self.session = session
        payload = data if isinstance(data, dict) else {}
        user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        user_id = str(user.get("id") or payload.get("user_id") or (session.user_id if session else ""))
        email_confirmed = bool(user.get("email_confirmed_at"))
        # GoTrue returns an empty identities array for an existing account on
        # a repeated signup. The confirmed timestamp covers older responses
        # and makes the classification safe even when identities is omitted.
        existing_account = bool(
            user_id
            and session is None
            and (email_confirmed or user.get("identities") == [])
        )
        confirmation_pending = bool(user_id and session is None and not existing_account)
        return SignupResult(
            email=email,
            user_id=user_id,
            session_active=session is not None,
            confirmation_pending=confirmation_pending,
            confirmation_sent=bool(user.get("confirmation_sent_at")),
            existing_account=existing_account,
            email_confirmed=email_confirmed,
            redirect_url=self.email_redirect_url,
        )

    def _ensure_fresh(self, *, force: bool = False) -> None:
        managed = self.auth_manager.current()
        if self.session is not None and (managed is None or self.session.generation >= managed.generation):
            self.auth_manager.adopt(self.session)
        elif managed is not None:
            self.session = managed
        path = "/auth/v1/token?grant_type=refresh_token" if self.transport == "direct" else "/auth/refresh"
        session = self.auth_manager.get_valid_session(
            lambda current: self._raw("POST", path, {"refresh_token": current.refresh_token}, authenticated=False),
            requested_by=f"{self.transport}:authenticated-request",
            force_refresh=force,
        )
        self.session = session

    def sign_up(self, email: str, password: str, nickname: str) -> SignupResult:
        normalized_email = normalize_email(email)
        if not normalized_email or "@" not in normalized_email:
            raise SocialError("请输入有效的邮箱地址。", kind="validation")
        body = {"email": normalized_email, "password": password, "nickname": nickname.strip()[:24] or "搭子", "data": {"nickname": nickname.strip()[:24] or "搭子"}}
        if self.email_redirect_url:
            body["redirect_to"] = self.email_redirect_url
        if self.transport == "direct":
            path = "/auth/v1/signup?" + urllib.parse.urlencode({"redirect_to": self.email_redirect_url}) if self.email_redirect_url else "/auth/v1/signup"
        else:
            path = "/auth/signup"
        try:
            data = self._raw(
                "POST",
                path,
                body if self.transport == "proxy" else {"email": body["email"], "password": body["password"], "data": body["data"]},
                timeout=_auth_request_timeout(),
            )
        except SocialError as exc:
            # A Supabase signup can create the user before SMTP finishes. Do
            # not replay the password to another route after a timeout.
            if exc.kind == "timeout" or exc.kind == "server" or exc.status in {502, 503, 504}:
                raise SocialError(
                    "注册请求已等待邮件服务较长时间，服务器暂未返回结果。请不要重复注册；稍后点击“重新发送确认邮件”，并检查垃圾邮件/广告邮件。",
                    kind="signup_timeout",
                    endpoint=exc.endpoint or _endpoint_host(self.base_url),
                    retryable=True,
                    status=exc.status,
                    error_code=exc.error_code,
                ) from exc
            raise
        return self._signup_result(data, normalized_email)

    def resend_confirmation(self, email: str) -> bool:
        normalized_email = normalize_email(email)
        if not normalized_email or "@" not in normalized_email:
            raise SocialError("请输入有效的邮箱地址。", kind="validation")
        body: dict[str, Any] = {"type": "signup", "email": normalized_email}
        if self.email_redirect_url:
            body["options"] = {"emailRedirectTo": self.email_redirect_url}
        path = "/auth/v1/resend" if self.transport == "direct" else "/auth/resend"
        try:
            self._raw("POST", path, body, timeout=_auth_request_timeout())
        except SocialError as exc:
            if exc.kind == "timeout" or exc.kind == "server" or exc.status in {502, 503, 504}:
                raise SocialError(
                    "确认邮件服务响应较慢；请稍后再试，并检查垃圾邮件/广告邮件。",
                    kind="confirmation_timeout",
                    endpoint=exc.endpoint or _endpoint_host(self.base_url),
                    retryable=True,
                    status=exc.status,
                    error_code=exc.error_code,
                ) from exc
            raise
        return True

    def sign_in(self, email: str, password: str) -> None:
        path = "/auth/v1/token?grant_type=password" if self.transport == "direct" else "/auth/signin"
        data = self._raw("POST", path, {"email": normalize_email(email), "password": password}, timeout=_auth_request_timeout())
        if not self._accept_auth(data):
            raise SocialError("登录没有成功，请检查邮箱确认或密码。")

    def record_login_streak(self) -> dict[str, Any]:
        """Record one idempotent Beijing-calendar login for the signed-in account."""

        if self.transport != "direct":
            raise SocialError("连续登录奖励需要连接 Supabase，请稍后重试。", kind="config")
        data = self._raw(
            "POST",
            "/rest/v1/rpc/lili_record_login",
            {},
            authenticated=True,
            timeout=_auth_request_timeout(),
        )
        return dict(data or {}) if isinstance(data, dict) else {}

    def _require_direct_security_transport(self) -> None:
        if self.transport != "direct":
            raise SocialError(
                "账号安全操作需要直连 Supabase，请稍后重试。",
                kind="config",
            )

    def change_password(self, current_password: str, new_password: str) -> None:
        """Change a password through GoTrue's current-password check.

        Passwords are sent only to Supabase Auth over the direct HTTPS route;
        they are never written to the application's database or keyring.
        """

        self._require_direct_security_transport()
        if len(new_password) < 8:
            raise SocialError("新密码至少需要 8 位。", kind="weak_password", error_code="weak_password")
        if not current_password:
            raise SocialError("请输入当前密码。", kind="validation")
        email = self.account_email
        if not email:
            raise SocialError("当前登录状态缺少账号邮箱，请退出后重新登录再修改密码。", kind="auth")
        # Verify the current password with Supabase Auth itself before the
        # update. This keeps the safety property even when a hosted project's
        # optional current-password update flag has not been enabled yet.
        self.sign_in(email, current_password)
        self._raw(
            "PUT",
            "/auth/v1/user",
            {"current_password": current_password, "password": new_password},
            authenticated=True,
            timeout=_auth_request_timeout(),
        )

    def request_password_reset(self, email: str) -> bool:
        """Ask Supabase Auth to send a reset email with a neutral outcome."""

        self._require_direct_security_transport()
        normalized_email = normalize_email(email)
        if not normalized_email or "@" not in normalized_email:
            raise SocialError("请输入有效的邮箱地址。", kind="validation")
        body: dict[str, Any] = {"email": normalized_email}
        if self.password_reset_redirect_url:
            body["redirect_to"] = self.password_reset_redirect_url
        self._raw("POST", "/auth/v1/recover", body, timeout=_auth_request_timeout())
        return True

    def verify_password_reset_otp(self, email: str, otp: str) -> bool:
        """Verify a recovery OTP and adopt the short-lived recovery session."""

        self._require_direct_security_transport()
        normalized_email = normalize_email(email)
        token = str(otp or "").strip()
        if not normalized_email or "@" not in normalized_email:
            raise SocialError("请输入有效的邮箱地址。", kind="validation")
        if not token.isdigit() or not 6 <= len(token) <= 10:
            raise SocialError("请输入邮件中的数字验证码。", kind="validation")
        data = self._raw(
            "POST",
            "/auth/v1/verify",
            {"email": normalized_email, "token": token, "type": "recovery"},
            timeout=_auth_request_timeout(),
        )
        if not self._accept_auth(data if isinstance(data, dict) else {}):
            raise SocialError("验证码无效或已过期，请重新发送验证码。", kind="auth", error_code="otp_expired")
        return True

    def set_password_after_reset(self, new_password: str) -> None:
        """Set a new password using the recovery session created by the OTP."""

        self._require_direct_security_transport()
        if len(new_password) < 8:
            raise SocialError("新密码至少需要 8 位。", kind="weak_password", error_code="weak_password")
        self._raw(
            "PUT",
            "/auth/v1/user",
            {"password": new_password},
            authenticated=True,
            timeout=_auth_request_timeout(),
        )

    def delete_account(self) -> None:
        """Delete the current Auth user via a server-side security-definer RPC."""

        self._require_direct_security_transport()
        self._raw(
            "POST",
            "/rest/v1/rpc/lili_delete_my_account",
            {},
            authenticated=True,
            timeout=_auth_request_timeout(),
        )

    def sign_out(self) -> None:
        self._clear_session()

    def health(self) -> dict[str, Any]:
        """Perform one lightweight route health request."""
        path = "/auth/v1/health" if self.transport == "direct" else "/health"
        return dict(self._raw("GET", path) or {})

    def dashboard(self, room_id: str | None = None, *, allow_cache: bool = True) -> dict[str, Any]:
        if self.transport == "direct":
            data = self._raw("POST", "/rest/v1/rpc/lili_dashboard", {}, authenticated=True) or {}
            if room_id:
                try:
                    room = self._raw("POST", "/rest/v1/rpc/lili_room_dashboard", {"p_room_id": room_id}, authenticated=True) or {}
                    if isinstance(room, dict): data.update(room)
                except SocialError as exc:
                    if not _missing_room_endpoint(exc):
                        raise
                    # The base dashboard still contains buddies and privacy
                    # data. An old project must not make saving privacy
                    # settings look like a failed operation.
                    data["_room_endpoint_unavailable"] = True
                if not data.get("_room_endpoint_unavailable"):
                    try:
                        rituals = self._raw("POST", "/rest/v1/rpc/lili_room_room_rituals", {"p_room_id": room_id}, authenticated=True) or {}
                        if isinstance(rituals, dict): data.update(rituals)
                    except SocialError as exc:
                        if exc.status >= 500 or exc.kind in {"dns", "timeout", "refused", "tls", "network", "server"}: raise
            self._merge_buddy_requests(data)
            result = dict(data)
            if self.last_server_timestamp:
                result.setdefault("server_timestamp", self.last_server_timestamp)
            return result
        path = "/dashboard"
        if room_id: path += "?room_id=" + urllib.parse.quote(str(room_id), safe="")
        try:
            result = dict(self._raw("GET", path, authenticated=True) or {})
        except SocialError as exc:
            if not room_id or not _missing_room_endpoint(exc):
                raise
            # Old deployed relays sometimes do not recognize the room query
            # yet. Keep the account/buddy snapshot usable and mark only the
            # optional room detail as unavailable.
            result = dict(self._raw("GET", "/dashboard", authenticated=True) or {})
            result["_room_endpoint_unavailable"] = True
        self._merge_buddy_requests(result)
        if self.last_server_timestamp:
            result.setdefault("server_timestamp", self.last_server_timestamp)
        return result

    def _merge_buddy_requests(self, data: dict[str, Any]) -> None:
        """Add incoming/outgoing buddy requests without breaking old relays."""

        try:
            payload = self.rpc("lili_buddy_requests", {})
        except SocialError as exc:
            # The request-state migration can roll out after the desktop
            # binary. Existing incoming requests from lili_dashboard remain
            # usable while the optional outgoing list catches up.
            LOGGER.info("buddy request inbox unavailable kind=%s status=%s", exc.kind, exc.status)
            return
        if not isinstance(payload, dict):
            return
        incoming = payload.get("incoming")
        outgoing = payload.get("outgoing")
        if isinstance(incoming, list):
            data["requests"] = incoming
        if isinstance(outgoing, list):
            data["outgoing_requests"] = outgoing

    def rpc(self, name: str, body: dict[str, Any]) -> Any:
        if self.transport == "direct":
            return self._raw("POST", f"/rest/v1/rpc/{urllib.parse.quote(name, safe='')}", body, authenticated=True)
        routes = {
            "lili_add_buddy_by_code": "/buddies/request",
            "lili_lookup_buddy_by_code": "/buddies/lookup",
            "lili_buddy_requests": "/buddies/requests",
            "lili_respond_buddy": "/buddies/accept",
            "lili_cancel_buddy_request": "/buddies/cancel",
            "lili_remove_buddy": "/buddies/remove",
            "lili_set_buddy_subscription": "/buddies/subscription",
            "lili_send_visit": "/visits/send",
            "lili_respond_visit": "/visits/accept",
            "lili_send_taunt": "/buddies/taunt",
            "lili_taunt_state": "/buddies/taunt-state",
            "lili_send_encouragement": "/buddies/encouragement",
            "lili_encouragement_state": "/buddies/encouragement-state",
            "lili_reaction_state": "/buddies/reaction-state",
            "lili_create_room": "/rooms/create",
            "lili_join_room": "/rooms/join",
            "lili_set_room_goal": "/rooms/goal",
            "lili_leave_room": "/rooms/leave",
            "lili_send_interaction": "/rooms/interaction",
            "lili_send_food_interaction": "/rooms/food-interaction",
            "lili_create_cake_share": "/rooms/cake-share",
            "lili_set_buddy_interaction_mode": "/profile/interaction-mode",
            "lili_record_room_event": "/rooms/events",
            "lili_record_economy_event": "/economy/events",
            "lili_economy_leaderboard": "/economy/leaderboard",
            "lili_focus_weekly_leaderboard": "/leaderboard/focus-week",
            "lili_sync_focus_segments": "/rpc/lili_sync_focus_segments",
        }
        return self._raw("POST", routes.get(name, f"/rpc/{name}"), body, authenticated=True)

    def update_profile(self, *, nickname: str, visibility: str, show_exact_time: bool, allow_visits: bool, outfit_key: str = "", wealth_leaderboard_enabled: bool = True, wealth_leaderboard_preference_set: bool = True) -> None:
        body = {"nickname": nickname.strip()[:24] or "搭子", "owner_nickname": nickname.strip()[:24], "visibility": visibility, "show_exact_time": bool(show_exact_time), "allow_visits": bool(allow_visits), "outfit_key": outfit_key[:60], "wealth_leaderboard_enabled": bool(wealth_leaderboard_enabled), "wealth_leaderboard_preference_set": bool(wealth_leaderboard_preference_set)}
        if self.transport == "direct":
            user_id = urllib.parse.quote(str(self.session.user_id if self.session else ""), safe="")
            path = f"/rest/v1/lili_profiles?user_id=eq.{user_id}"
        else:
            path = "/profile"
        self._raw("PATCH", path, body, authenticated=True)

    def update_owner_nickname(self, nickname: str) -> None:
        body = {"nickname": nickname.strip()[:24] or "搭子", "owner_nickname": nickname.strip()[:24]}
        if self.transport == "direct":
            user_id = urllib.parse.quote(str(self.session.user_id if self.session else ""), safe="")
            self._raw("PATCH", f"/rest/v1/lili_profiles?user_id=eq.{user_id}", body, authenticated=True)
        else:
            self._raw("PATCH", "/profile", body, authenticated=True)

    def heartbeat(self, *, working: bool, session_active: bool = False, work_state: str = "idle", pause_reason: str | None = None, today_seconds: int, session_started_at: str | None, outfit_key: str, room_id: str | None = None, quick_status: str = "", quick_status_expires_at: str | None = None) -> None:
        # ``AuthSessionManager`` is the source of truth.  During login and
        # token refresh it can already hold a valid session while this
        # transport's compatibility attribute is still empty.  Returning at
        # that point silently drops the heartbeat and makes every peer see the
        # user as offline.
        managed_session = self.auth_manager.current()
        if managed_session is not None:
            self.session = managed_session
        if not self.session:
            LOGGER.info(
                "social heartbeat skipped authenticated=%s reason=no_session",
                self.signed_in,
            )
            return
        # Presence freshness is assigned by the Supabase database clock.  Do not
        # send a client last_seen value: a user's incorrect Windows clock would
        # otherwise make an active buddy look offline for the whole room.
        now = datetime.now(BEIJING_TIMEZONE)
        device_id, device_claim = _presence_device_credentials(self.session.user_id)
        body = {"working": bool(working), "session_active": bool(session_active), "work_state": str(work_state or "idle")[:32], "pause_reason": str(pause_reason or "")[:32] or None, "today_seconds": min(86400, max(0, int(today_seconds))), "session_started_at": session_started_at, "focus_date": now.date().isoformat(), "outfit_key": outfit_key[:60], "room_id": room_id, "quick_status": quick_status[:40], "quick_status_expires_at": quick_status_expires_at, "device_id": device_id, "device_claim": device_claim}
        if self.transport == "direct":
            try:
                self._raw(
                    "POST",
                    "/rest/v1/rpc/lili_upsert_focus_presence",
                    _atomic_presence_body(body),
                    authenticated=True,
                )
            except SocialError as exc:
                # Keep old deployed projects usable during the migration
                # rollout.  New projects take the atomic RPC path; only a
                # missing endpoint may use the legacy upsert fallback.
                if exc.status == 404 or "lili_upsert_focus_presence" in str(exc):
                    legacy_body = dict(body)
                    legacy_body["user_id"] = self.session.user_id
                    self._raw(
                        "POST",
                        "/rest/v1/lili_focus_presence?on_conflict=user_id",
                        legacy_body,
                        authenticated=True,
                        extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                    )
                else:
                    if "device_session_revoked" in str(exc).casefold() or str(exc.error_code).casefold() == "device_session_revoked":
                        _mark_presence_device_claim_pending(self.session.user_id, device_id)
                    raise
        else:
            try:
                self._raw("POST", "/presence/heartbeat", body, authenticated=True)
            except SocialError as exc:
                if "device_session_revoked" in str(exc).casefold() or str(exc.error_code).casefold() == "device_session_revoked":
                    _mark_presence_device_claim_pending(self.session.user_id, device_id)
                raise
        _mark_presence_device_claimed(self.session.user_id, device_id)

    def send_interaction(self, *, target: str, kind: str, room_id: str | None = None) -> None:
        if self.transport == "direct":
            self.rpc("lili_send_interaction", {"p_target": target, "p_kind": kind, "p_room_id": room_id})
            return
        self._raw(
            "POST",
            "/rooms/interaction",
            {"p_target": target, "p_kind": kind, "p_room_id": room_id},
            authenticated=True,
        )

    def set_room_goal(self, *, room_id: str, title: str, target_seconds: int, due_at: str | None = None) -> None:
        self.rpc("lili_set_room_goal", {"p_room_id": room_id, "p_title": title, "p_target_seconds": int(target_seconds), "p_due_at": due_at})

    def set_room_schedule(self, *, room_id: str, start_at: str, end_at: str, enabled: bool = True) -> None:
        self.rpc("lili_set_room_schedule", {"p_room_id": room_id, "p_start_at": start_at, "p_end_at": end_at, "p_enabled": bool(enabled)})

    def set_room_challenge(self, *, room_id: str, title: str, target_seconds: int, target_rounds: int) -> None:
        self.rpc("lili_set_room_challenge", {"p_room_id": room_id, "p_title": title, "p_target_seconds": int(target_seconds), "p_target_rounds": int(target_rounds)})

    def set_buddy_subscription(self, *, buddy_id: str, on_focus_start: bool, on_focus_end: bool, muted: bool = False) -> None:
        self.rpc("lili_set_buddy_subscription", {"p_buddy_id": buddy_id, "p_on_focus_start": bool(on_focus_start), "p_on_focus_end": bool(on_focus_end), "p_muted": bool(muted)})

    def leave_room(self, *, room_id: str) -> None:
        self.rpc("lili_leave_room", {"p_room_id": room_id})

    def record_room_event(self, *, room_id: str, kind: str, target_id: str | None = None, message: str = "") -> None:
        self.rpc("lili_record_room_event", {"p_room_id": room_id, "p_kind": kind, "p_target_id": target_id, "p_message": message})

    def record_economy_event(self, *, event_id: str, category: str, amount: int, label: str, source_key: str, occurred_on: str) -> None:
        self.rpc(
            "lili_record_economy_event",
            {
                "p_event_id": event_id,
                "p_category": category,
                "p_amount": int(amount),
                "p_label": label,
                "p_source_key": source_key,
                "p_occurred_on": occurred_on,
            },
        )

    def economy_leaderboard(self, *, period: str = "month") -> list[dict[str, Any]]:
        result = self.rpc("lili_economy_leaderboard", {"p_period": period})
        return list(result or []) if isinstance(result, list) else []

    def focus_leaderboard(self, *, period: str = "week") -> list[dict[str, Any]]:
        result = self.rpc("lili_focus_weekly_leaderboard", {"p_period": period})
        return list(result or []) if isinstance(result, list) else []


# Kept as a compatibility implementation for older integrations.  The
# production alias at the end of this module uses SupabaseFirstSocialClient.
class LegacyDirectSocialClient:
    SERVICE_NAME = "LiliSocial"
    ACCOUNT_NAME = "supabase-session"

    def __init__(self, *, persist_tokens: bool = True, backend: SocialBackend | None = None) -> None:
        # Backend credentials and endpoints are release-controlled.  Do not let
        # a user content overlay replace this file: an older executable may
        # interpret a newer overlay schema as a legacy CloudBase proxy.
        config = json.loads(
            (resource_root() / "config" / "social_backend.json").read_text(encoding="utf-8")
        )
        self.url = str(config.get("url", "")).rstrip("/")
        self.key = str(config.get("publishable_key", ""))
        self.social_api_base_url = (
            os.environ.get("LILI_SOCIAL_API_BASE_URL", "").strip()
            or str(config.get("social_api_base_url", "")).strip()
        ).rstrip("/")
        # Supabase uses this URL after email confirmation.  Keep it explicit so
        # desktop builds never inherit the hosted project's localhost default.
        self.email_redirect_url = (
            os.environ.get("LILI_AUTH_REDIRECT_URL", "").strip()
            or str(config.get("email_redirect_to", "")).strip()
            or "https://github.com/leungjunchiang/OnePic-Desktop-Pet"
        )
        self.password_reset_redirect_url = (
            os.environ.get("LILI_PASSWORD_RESET_REDIRECT_URL", "").strip()
            or str(config.get("password_reset_redirect_to", "")).strip()
            or self.email_redirect_url
        )
        self.persist_tokens = persist_tokens
        self._dashboard_cache: dict[str, dict[str, Any]] = {}
        self._last_error = ""
        self.connection = ConnectionStateStore()
        self.session: SocialSession | None = None
        self.auth_manager = AuthSessionManager(
            service_name=self.SERVICE_NAME,
            account_name=self.ACCOUNT_NAME,
            persist_tokens=persist_tokens,
        )
        self._http_backend: SocialBackend | None = backend
        if self._http_backend is None and self.social_api_base_url:
            self._http_backend = HttpSocialBackend(
                self.social_api_base_url,
                client_key=self.key,
                persist_tokens=persist_tokens,
                email_redirect_url=self.email_redirect_url,
                password_reset_redirect_url=self.password_reset_redirect_url,
            )
        if self._http_backend is None:
            self._load_session()
        self._load_dashboard_cache()

    @property
    def backend_name(self) -> str:
        return "http" if self._http_backend is not None else "supabase"

    @property
    def backend_endpoint(self) -> str:
        if self._http_backend is not None:
            return str(getattr(self._http_backend, "base_url", "") or "")
        return self.url

    def health(self) -> dict[str, Any]:
        """Probe the active social transport without requiring authentication."""
        if self._http_backend is not None:
            checker = getattr(self._http_backend, "health", None)
            if callable(checker):
                return dict(checker() or {})
            raise SocialError("当前自习室中转服务未提供健康检查。", kind="config")
        return dict(self._raw("GET", "/auth/v1/health") or {})

    @property
    def connection_state(self) -> str:
        return self.connection.state

    def diagnose_connection(self, room_id: str | None = None) -> dict[str, Any]:
        """Validate the complete study-room path, not just the public probe."""

        started = time.monotonic()
        probe_state = "RECONNECTING" if self.connection.state in {"OFFLINE", "DEGRADED"} and self.connection.last_failure_at else "CONNECTING"
        self.connection.set(probe_state, data_source=self.connection.data_source, realtime_state="checking")
        checks: dict[str, Any] = {
            "edge_function": {"ok": False},
            "authentication": {"ok": bool(self.signed_in)},
            "room_snapshot": {"ok": False},
            "presence": {"ok": False},
            "realtime": {"ok": False},
        }
        backend_name = self.backend_name
        service_endpoint = self.backend_endpoint
        try:
            health = self.health()
            checks["edge_function"] = {
                "ok": True,
                "backend": health.get("backend") or self.backend_name,
                "transport": health.get("transport") or "https-rest",
            }
            backend_name = str(health.get("backend") or backend_name)
            service_endpoint = str(health.get("service") or service_endpoint)
        except SocialError as exc:
            cached = self.cached_dashboard(room_id)
            self._diagnostic_log("health", room_id, result=cached, exc=exc, elapsed=time.monotonic() - started)
            raise

        # A network check is intentionally a public/lightweight probe.  It
        # must not turn into a dashboard request and therefore must not cause
        # an AuthSessionManager refresh merely because the user clicked the
        # diagnostic button.
        state = "ONLINE" if self.signed_in else "DEGRADED"
        realtime_state = "not_started" if self.signed_in else "not_authenticated"
        self.connection.set(state, data_source="local_live", realtime_state=realtime_state)
        result = {
            "connection_state": state,
            "data_source": "local_live",
            "realtime_state": realtime_state,
            "backend": backend_name,
            "service": service_endpoint,
            "checks": checks,
            "dashboard": None,
        }
        self._diagnostic_log("connection_probe", room_id, result=result, elapsed=time.monotonic() - started)
        return result

    def _diagnostic_log(self, request_type: str, room_id: str | None, *, result: dict[str, Any] | None = None, exc: SocialError | None = None, elapsed: float = 0.0) -> None:
        """Emit structured diagnostics without logging credentials or payloads."""

        entry = {
            "timestamp": datetime.now().astimezone().isoformat(),
            "connection_state": self.connection.state,
            "request_type": request_type,
            "url": self.backend_endpoint,
            "http_status": exc.status if exc else None,
            "latency_ms": round(max(0.0, elapsed) * 1000),
            "auth_state": "signed_in" if self.signed_in else "signed_out",
            "room_id": room_id or "",
            "realtime_state": self.connection.realtime_state,
            "last_success_at": self.connection.last_success_at,
            "last_failure_at": self.connection.last_failure_at,
            "data_source": (result or {}).get("data_source", self.connection.data_source),
            "error_kind": exc.kind if exc else "",
            "auth_diagnostics": self.auth_manager.diagnostics() if hasattr(self, "auth_manager") else {},
        }
        LOGGER.info("study_room_diagnostic %s", json.dumps(entry, ensure_ascii=False, sort_keys=True))

    @property
    def signed_in(self) -> bool:
        return self._http_backend.signed_in if self._http_backend is not None else (self.auth_manager.current() is not None and not self.auth_manager.requires_relogin)

    @staticmethod
    def _keyring():
        import keyring
        return keyring

    def _load_session(self) -> None:
        self.session = self.auth_manager.current()

    def _save_session(self) -> None:
        self.auth_manager.persist(self.session)

    def _clear_session(self) -> None:
        self.auth_manager.clear()
        self.session = None

    def _dashboard_cache_path(self) -> Path:
        """Return a local cache path that contains no access tokens."""

        base = os.environ.get("LOCALAPPDATA")
        return app_data_dir() / "social-dashboard-cache.json"

    def _load_dashboard_cache(self) -> None:
        if not self.persist_tokens:
            return
        try:
            raw = json.loads(self._dashboard_cache_path().read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                migrated: dict[str, dict[str, Any]] = {}
                for key, value in raw.items():
                    if not isinstance(value, dict) or not isinstance(value.get("data"), dict):
                        continue
                    payload = value["data"]
                    payload_me = payload.get("me") if isinstance(payload.get("me"), dict) else {}
                    account_id = str(value.get("account_id") or payload_me.get("user_id") or "").strip()
                    if not account_id:
                        # Old unscoped entries are deliberately discarded:
                        # they cannot be proven to belong to the next account.
                        continue
                    scoped_key = str(key)
                    if not scoped_key.startswith(f"{account_id}:"):
                        scoped_key = f"{account_id}:{scoped_key}"
                    entry = dict(value)
                    entry["account_id"] = account_id
                    migrated[scoped_key] = entry
                self._dashboard_cache = migrated
        except (OSError, ValueError, TypeError):
            self._dashboard_cache = {}

    def _save_dashboard_cache(self) -> None:
        if not self.persist_tokens:
            return
        target = self._dashboard_cache_path()
        temporary = target.with_suffix(".json.tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(self._dashboard_cache, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary.replace(target)
        except OSError:
            # Cache failure must never break the live sync path.
            return

    def _remember_dashboard(self, room_id: str | None, data: dict[str, Any]) -> None:
        account_id = _session_user_id(self)
        if not account_id:
            return
        key = f"{account_id}:{str(room_id or '')}"
        snapshot = json.loads(json.dumps(data, ensure_ascii=False))
        _normalise_never_seen_presence(snapshot)
        self._dashboard_cache[key] = {
            "account_id": account_id,
            "saved_at": time.time(),
            "data": snapshot,
        }
        self._save_dashboard_cache()

    def cached_dashboard(self, room_id: str | None = None) -> dict[str, Any] | None:
        """Return the latest payload for offline rendering, if available."""

        account_id = _session_user_id(self)
        if not account_id:
            return None
        key = f"{account_id}:{str(room_id or '')}"
        entry = self._dashboard_cache.get(key)
        if not isinstance(entry, dict) or not isinstance(entry.get("data"), dict):
            return None
        if str(entry.get("account_id") or account_id) != account_id:
            return None
        data = json.loads(json.dumps(entry["data"], ensure_ascii=False))
        payload_me = data.get("me") if isinstance(data.get("me"), dict) else {}
        payload_account_id = str(payload_me.get("user_id") or "").strip()
        if payload_account_id and payload_account_id != account_id:
            return None
        _normalise_never_seen_presence(data)
        # Private labels are local decorations and cannot be trusted when a
        # cached snapshot is restored.  The next live dashboard applies the
        # current account's notes again.
        _apply_buddy_private_notes(data, {})
        saved_at = float(entry.get("saved_at") or 0)
        age_seconds = max(0, int(time.time() - saved_at)) if saved_at else 0
        age_minutes = max(0, int(age_seconds / 60)) if saved_at else 0
        presence_grace = bool(saved_at and age_seconds <= PRESENCE_GRACE_SECONDS)
        if presence_grace:
            self._mark_remote_presence_uncertain(data, age_seconds)
        else:
            self._mark_remote_presence_stale(data)
        data["_sync_offline"] = True
        data["_connection_state"] = "DEGRADED" if presence_grace else "OFFLINE"
        data["_presence_grace_active"] = presence_grace
        data["_presence_uncertainty_seconds"] = age_seconds if presence_grace else 0
        data["data_source"] = "local_cache"
        data["_data_source"] = "local_cache"
        data["_sync_age_minutes"] = age_minutes
        try:
            data["_server_timestamp"] = datetime.fromtimestamp(saved_at).astimezone().isoformat() if saved_at else ""
        except (OSError, OverflowError, ValueError):
            data["_server_timestamp"] = ""
        data["_sync_error"] = self._last_error or "当前网络无法访问自习室服务"
        self.connection.set(
            "DEGRADED" if presence_grace else "OFFLINE",
            data_source="local_cache",
            realtime_state="polling_degraded" if presence_grace else "unavailable",
            server_timestamp=data["_server_timestamp"],
        )
        return data

    @staticmethod
    def _mark_remote_presence_stale(data: dict[str, Any]) -> None:
        """Never render cached remote presence as current online activity."""

        def mark(items: Any) -> None:
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict) or item.get("is_self"):
                    continue
                item["online"] = False
                item["working"] = False
                item["status"] = "offline"
                item["session_seconds"] = 0
                item["today_seconds"] = None
                item["stale_presence"] = True
                item["presence_uncertain"] = False

        mark(data.get("buddies"))
        mark(data.get("room_people"))
        mark(data.get("active_visits"))
        room = data.get("current_room")
        if isinstance(room, dict):
            mark(room.get("room_people"))
            summary = room.get("room_summary")
            if isinstance(summary, dict):
                summary["focus_count"] = 0
        summary = data.get("room_summary")
        if isinstance(summary, dict):
            summary["focus_count"] = 0

    @staticmethod
    def _mark_remote_presence_uncertain(data: dict[str, Any], age_seconds: int) -> None:
        """Keep the last confirmed state during a short transport outage.

        This is intentionally different from ``stale_presence``: a failed
        dashboard request says something about this client's transport, not
        that every peer left the room.  The UI can therefore show “状态待
        确认” instead of manufacturing an offline event from an old cache.
        """

        def mark(items: Any) -> None:
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict) or item.get("is_self"):
                    continue
                if item.get("stale_presence"):
                    continue
                item.pop("stale_presence", None)
                item["presence_uncertain"] = True
                item["presence_age_seconds"] = age_seconds

        mark(data.get("buddies"))
        mark(data.get("room_people"))
        mark(data.get("active_visits"))
        room = data.get("current_room")
        if isinstance(room, dict):
            mark(room.get("room_people"))
            summary = room.get("room_summary")
            if isinstance(summary, dict):
                summary["presence_uncertain"] = True
        summary = data.get("room_summary")
        if isinstance(summary, dict):
            summary["presence_uncertain"] = True

    def _raw(self, method: str, path: str, body: Any = None, *, authenticated: bool = False, extra_headers: dict[str, str] | None = None, timeout: float | None = None, _retry_auth: bool = True) -> Any:
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"apikey": self.key, "Content-Type": "application/json", "Accept": "application/json"}
        if authenticated:
            self._ensure_fresh()
            self.session = self.auth_manager.current()
            if not self.session:
                raise SocialError("请先登录搭子自习室。")
            headers["Authorization"] = f"Bearer {self.session.access_token}"
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(f"{self.url}{path}", data=payload, headers=headers, method=method)
        try:
            with _verified_urlopen(
                request,
                timeout=_social_request_timeout() if timeout is None else timeout,
            ) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
                message = data.get("msg") or data.get("message") or data.get("error_description") or raw
                error_code = str(data.get("error_code") or data.get("code") or "")
            except json.JSONDecodeError:
                message = raw or str(exc)
                error_code = ""
            status = int(exc.code)
            is_refresh_endpoint = "/auth/v1/token" in path or path.rstrip("/") == "/auth/refresh"
            kind = "auth_refresh" if is_refresh_endpoint else "auth" if status in (401, 403) else "server" if status >= 500 else "http"
            if error_code in {"refresh_token_already_used", "invalid_refresh_token", "invalid_grant"} or (
                "invalid refresh token" in str(message).casefold()
                or ("refresh token" in str(message).casefold() and "already used" in str(message).casefold())
            ):
                message = "登录状态已失效，请重新登录。"
                kind = "auth_refresh_reused" if error_code == "refresh_token_already_used" else "auth_refresh"
            if authenticated and _retry_auth and status == 401 and not is_refresh_endpoint:
                previous = self.auth_manager.current()
                try:
                    self._ensure_fresh(force=True)
                    current = self.auth_manager.current()
                    if current is not None and (
                        previous is None or current.generation > previous.generation
                    ):
                        return self._raw(
                            method,
                            path,
                            body,
                            authenticated=authenticated,
                            extra_headers=extra_headers,
                            timeout=timeout,
                            _retry_auth=False,
                        )
                except SocialError:
                    raise
            raise SocialError(
                str(message)[:300],
                kind=kind,
                endpoint=_endpoint_host(self.url),
                retryable=status >= 500,
                status=status,
                error_code=error_code,
            ) from exc
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise _network_error(exc, self.url) from exc

    def _accept_auth(self, data: dict[str, Any]) -> bool:
        session = self.auth_manager.accept_auth(data)
        self.session = session
        return session is not None

    def _signup_result(self, data: dict[str, Any] | None, email: str) -> SignupResult:
        session = self.auth_manager.accept_auth(data)
        self.session = session
        payload = data if isinstance(data, dict) else {}
        user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        user_id = str(user.get("id") or payload.get("user_id") or (session.user_id if session else ""))
        email_confirmed = bool(user.get("email_confirmed_at"))
        existing_account = bool(
            user_id
            and session is None
            and (email_confirmed or user.get("identities") == [])
        )
        confirmation_pending = bool(user_id and session is None and not existing_account)
        return SignupResult(
            email=email,
            user_id=user_id,
            session_active=session is not None,
            confirmation_pending=confirmation_pending,
            confirmation_sent=bool(user.get("confirmation_sent_at")),
            existing_account=existing_account,
            email_confirmed=email_confirmed,
            redirect_url=self.email_redirect_url,
        )

    def _ensure_fresh(self, *, force: bool = False) -> None:
        managed = self.auth_manager.current()
        if self.session is not None and (managed is None or self.session.generation >= managed.generation):
            self.auth_manager.adopt(self.session)
        elif managed is not None:
            self.session = managed
        session = self.auth_manager.get_valid_session(
            lambda current: self._raw("POST", "/auth/v1/token?grant_type=refresh_token", {"refresh_token": current.refresh_token}, authenticated=False),
            requested_by="legacy:supabase-request",
            force_refresh=force,
        )
        self.session = session

    def sign_up(self, email: str, password: str, nickname: str) -> SignupResult:
        # `redirect_to` is a query parameter for GoTrue's signup endpoint.  It
        # must also be present in Supabase Auth's allow-list; the production
        # project is configured with the same URL.  Passing it explicitly keeps
        # future dashboard changes from silently restoring localhost redirects.
        redirect = urllib.parse.urlencode({"redirect_to": self.email_redirect_url})
        normalized_email = normalize_email(email)
        if not normalized_email or "@" not in normalized_email:
            raise SocialError("请输入有效的邮箱地址。", kind="validation")
        data = self._raw(
            "POST",
            f"/auth/v1/signup?{redirect}",
            {"email": normalized_email, "password": password, "data": {"nickname": nickname.strip()[:24] or "搭子"}},
            timeout=_auth_request_timeout(),
        )
        return self._signup_result(data, normalized_email)

    def resend_confirmation(self, email: str) -> bool:
        normalized_email = normalize_email(email)
        if not normalized_email or "@" not in normalized_email:
            raise SocialError("请输入有效的邮箱地址。", kind="validation")
        self._raw(
            "POST",
            "/auth/v1/resend",
            {"type": "signup", "email": normalized_email, "options": {"emailRedirectTo": self.email_redirect_url}},
            timeout=_auth_request_timeout(),
        )
        return True

    def sign_in(self, email: str, password: str) -> None:
        if self._http_backend is not None:
            return self._http_backend.sign_in(email, password)
        data = self._raw("POST", "/auth/v1/token?grant_type=password", {"email": email.strip(), "password": password}, timeout=_auth_request_timeout())
        if not self._accept_auth(data):
            raise SocialError("登录没有成功，请检查邮箱确认或密码。")

    def record_login_streak(self) -> dict[str, Any]:
        if self._http_backend is not None:
            return dict(self._http_backend.record_login_streak() or {})
        return dict(self._raw(
            "POST",
            "/rest/v1/rpc/lili_record_login",
            {},
            authenticated=True,
            timeout=_auth_request_timeout(),
        ) or {})

    @property
    def account_email(self) -> str:
        if self._http_backend is not None:
            return str(getattr(self._http_backend, "account_email", "") or "")
        session = self.auth_manager.current() or self.session
        return normalize_email(session.email) if session is not None else ""

    def change_password(self, current_password: str, new_password: str) -> None:
        if self._http_backend is not None:
            return self._http_backend.change_password(current_password, new_password)
        if len(new_password) < 8:
            raise SocialError("新密码至少需要 8 位。", kind="weak_password", error_code="weak_password")
        email = self.account_email
        if not email:
            raise SocialError("当前登录状态缺少账号邮箱，请退出后重新登录再修改密码。", kind="auth")
        self.sign_in(email, current_password)
        self._raw(
            "PUT",
            "/auth/v1/user",
            {"current_password": current_password, "password": new_password},
            authenticated=True,
            timeout=_auth_request_timeout(),
        )

    def request_password_reset(self, email: str) -> bool:
        if self._http_backend is not None:
            return bool(self._http_backend.request_password_reset(email))
        normalized_email = normalize_email(email)
        if not normalized_email or "@" not in normalized_email:
            raise SocialError("请输入有效的邮箱地址。", kind="validation")
        self._raw(
            "POST",
            "/auth/v1/recover",
            {"email": normalized_email, "redirect_to": self.password_reset_redirect_url},
            timeout=_auth_request_timeout(),
        )
        return True

    def verify_password_reset_otp(self, email: str, otp: str) -> bool:
        if self._http_backend is not None:
            return bool(self._http_backend.verify_password_reset_otp(email, otp))
        normalized_email = normalize_email(email)
        token = str(otp or "").strip()
        if not normalized_email or "@" not in normalized_email:
            raise SocialError("请输入有效的邮箱地址。", kind="validation")
        if not token.isdigit() or not 6 <= len(token) <= 10:
            raise SocialError("请输入邮件中的数字验证码。", kind="validation")
        data = self._raw(
            "POST",
            "/auth/v1/verify",
            {"email": normalized_email, "token": token, "type": "recovery"},
            timeout=_auth_request_timeout(),
        )
        if not self._accept_auth(data if isinstance(data, dict) else {}):
            raise SocialError("验证码无效或已过期，请重新发送验证码。", kind="auth", error_code="otp_expired")
        return True

    def set_password_after_reset(self, new_password: str) -> None:
        if self._http_backend is not None:
            return self._http_backend.set_password_after_reset(new_password)
        if len(new_password) < 8:
            raise SocialError("新密码至少需要 8 位。", kind="weak_password", error_code="weak_password")
        self._raw(
            "PUT",
            "/auth/v1/user",
            {"password": new_password},
            authenticated=True,
            timeout=_auth_request_timeout(),
        )

    def delete_account(self) -> None:
        if self._http_backend is not None:
            return self._http_backend.delete_account()
        self._raw("POST", "/rest/v1/rpc/lili_delete_my_account", {}, authenticated=True, timeout=_auth_request_timeout())

    def sign_out(self) -> None:
        if self._http_backend is not None:
            self._http_backend.sign_out()
            return
        self._clear_session()

    def dashboard(self, room_id: str | None = None, *, allow_cache: bool = True) -> dict[str, Any]:
        self.connection.set("CONNECTING", data_source=self.connection.data_source, realtime_state="polling")
        try:
            if self._http_backend is not None:
                data = self._http_backend.dashboard(room_id=room_id)
            else:
                data = self._raw("POST", "/rest/v1/rpc/lili_dashboard", {}, authenticated=True) or {}
                if room_id:
                    try:
                        room = self._raw(
                            "POST",
                            "/rest/v1/rpc/lili_room_dashboard",
                            {"p_room_id": room_id},
                            authenticated=True,
                        ) or {}
                        if isinstance(room, dict):
                            data.update(room)
                    except SocialError as exc:
                        if not _missing_room_endpoint(exc):
                            raise
                        data["_room_endpoint_unavailable"] = True
                    if not data.get("_room_endpoint_unavailable"):
                        try:
                            rituals = self._raw(
                                "POST",
                                "/rest/v1/rpc/lili_room_room_rituals",
                                {"p_room_id": room_id},
                                authenticated=True,
                            ) or {}
                            if isinstance(rituals, dict):
                                data.update(rituals)
                        except SocialError:
                            # Older deployed projects may not have the optional ritual
                            # migration yet; the core room dashboard remains usable.
                            pass
            result = _normalise_never_seen_presence(dict(data or {}))
            self._last_error = ""
            result["_connection_state"] = "ONLINE"
            result["data_source"] = "server"
            result["_data_source"] = "server"
            result["_server_timestamp"] = datetime.now().astimezone().isoformat()
            self.connection.set(
                "ONLINE",
                data_source="server",
                realtime_state="polling",
                server_timestamp=result["_server_timestamp"],
            )
            self._remember_dashboard(room_id, result)
            return result
        except SocialError as exc:
            self._last_error = str(exc)
            self.connection.set("OFFLINE", data_source="local_cache", realtime_state="unavailable")
            if not allow_cache:
                raise
            cached = self.cached_dashboard(room_id)
            if cached is not None:
                return cached
            raise

    def rpc(self, name: str, body: dict[str, Any]) -> Any:
        if self._http_backend is not None:
            return self._http_backend.rpc(name, body)
        return self._raw("POST", f"/rest/v1/rpc/{name}", body, authenticated=True)

    def update_profile(self, *, nickname: str, visibility: str, show_exact_time: bool, allow_visits: bool, outfit_key: str = "", wealth_leaderboard_enabled: bool = True, wealth_leaderboard_preference_set: bool = True) -> None:
        if self._http_backend is not None:
            return self._http_backend.update_profile(nickname=nickname, visibility=visibility, show_exact_time=show_exact_time, allow_visits=allow_visits, outfit_key=outfit_key, wealth_leaderboard_enabled=wealth_leaderboard_enabled, wealth_leaderboard_preference_set=wealth_leaderboard_preference_set)
        if not self.session:
            raise SocialError("请先登录。")
        query = urllib.parse.urlencode({"user_id": f"eq.{self.session.user_id}"})
        clean = nickname.strip()[:24]
        path = f"/rest/v1/lili_profiles?{query}"
        body = {"nickname": clean or "搭子", "owner_nickname": clean, "visibility": visibility, "show_exact_time": bool(show_exact_time), "allow_visits": bool(allow_visits), "outfit_key": outfit_key[:60], "wealth_leaderboard_enabled": bool(wealth_leaderboard_enabled), "wealth_leaderboard_preference_set": bool(wealth_leaderboard_preference_set), "updated_at": datetime.now().astimezone().isoformat()}
        self._raw("PATCH", path, body, authenticated=True, extra_headers={"Prefer": "return=minimal"})

    def heartbeat(self, *, working: bool, session_active: bool = False, work_state: str = "idle", pause_reason: str | None = None, today_seconds: int, session_started_at: str | None, outfit_key: str, room_id: str | None = None, quick_status: str = "", quick_status_expires_at: str | None = None) -> None:
        if self._http_backend is not None:
            return self._http_backend.heartbeat(working=working, session_active=session_active, work_state=work_state, pause_reason=pause_reason, today_seconds=today_seconds, session_started_at=session_started_at, outfit_key=outfit_key, room_id=room_id, quick_status=quick_status, quick_status_expires_at=quick_status_expires_at)
        if not self.session:
            return
        # Keep compatibility with the legacy direct client, but let the
        # server-side trigger own both freshness timestamps.
        device_id, device_claim = _presence_device_credentials(self.session.user_id)
        body = {"working": bool(working), "session_active": bool(session_active), "work_state": str(work_state or "idle")[:32], "pause_reason": str(pause_reason or "")[:32] or None, "session_started_at": session_started_at, "focus_date": datetime.now(BEIJING_TIMEZONE).date().isoformat(), "today_seconds": min(86400, max(0, int(today_seconds))), "outfit_key": outfit_key[:60], "room_id": room_id, "quick_status": quick_status[:40], "quick_status_expires_at": quick_status_expires_at, "device_id": device_id, "device_claim": device_claim}
        try:
            self._raw("POST", "/rest/v1/rpc/lili_upsert_focus_presence", _atomic_presence_body(body), authenticated=True)
        except SocialError as exc:
            if exc.status == 404 or "lili_upsert_focus_presence" in str(exc):
                legacy_body = dict(body)
                legacy_body["user_id"] = self.session.user_id
                self._raw("POST", "/rest/v1/lili_focus_presence?on_conflict=user_id", legacy_body, authenticated=True, extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
            else:
                if "device_session_revoked" in str(exc).casefold() or str(exc.error_code).casefold() == "device_session_revoked":
                    _mark_presence_device_claim_pending(self.session.user_id, device_id)
                raise
        _mark_presence_device_claimed(self.session.user_id, device_id)

    def update_owner_nickname(self, nickname: str) -> None:
        if self._http_backend is not None:
            return self._http_backend.update_owner_nickname(nickname)
        if not self.session:
            raise SocialError("请先登录。")
        query = urllib.parse.urlencode({"user_id": f"eq.{self.session.user_id}"})
        clean = nickname.strip()[:24]
        self._raw("PATCH", f"/rest/v1/lili_profiles?{query}", {"nickname": clean or "搭子", "owner_nickname": clean, "updated_at": datetime.now().astimezone().isoformat()}, authenticated=True, extra_headers={"Prefer": "return=minimal"})

    def send_interaction(self, *, target: str, kind: str, room_id: str | None = None) -> None:
        if self._http_backend is not None:
            return self._http_backend.send_interaction(target=target, kind=kind, room_id=room_id)
        self.rpc(
            "lili_send_interaction",
            {"p_target": target, "p_kind": kind, "p_room_id": room_id},
        )

    def set_room_goal(self, *, room_id: str, title: str, target_seconds: int, due_at: str | None = None) -> None:
        self.rpc("lili_set_room_goal", {"p_room_id": room_id, "p_title": title, "p_target_seconds": int(target_seconds), "p_due_at": due_at})

    def set_room_schedule(self, *, room_id: str, start_at: str, end_at: str, enabled: bool = True) -> None:
        self.rpc("lili_set_room_schedule", {"p_room_id": room_id, "p_start_at": start_at, "p_end_at": end_at, "p_enabled": bool(enabled)})

    def set_room_challenge(self, *, room_id: str, title: str, target_seconds: int, target_rounds: int) -> None:
        self.rpc("lili_set_room_challenge", {"p_room_id": room_id, "p_title": title, "p_target_seconds": int(target_seconds), "p_target_rounds": int(target_rounds)})

    def set_buddy_subscription(self, *, buddy_id: str, on_focus_start: bool, on_focus_end: bool, muted: bool = False) -> None:
        self.rpc("lili_set_buddy_subscription", {"p_buddy_id": buddy_id, "p_on_focus_start": bool(on_focus_start), "p_on_focus_end": bool(on_focus_end), "p_muted": bool(muted)})

    def leave_room(self, *, room_id: str) -> None:
        self.rpc("lili_leave_room", {"p_room_id": room_id})

    def record_room_event(self, *, room_id: str, kind: str, target_id: str | None = None, message: str = "") -> None:
        self.rpc("lili_record_room_event", {"p_room_id": room_id, "p_kind": kind, "p_target_id": target_id, "p_message": message})


class DashboardCacheClientBase:
    """Shared local dashboard-cache helpers; it is not a backend transport."""

    ACCOUNT_NAME = "supabase-session"

    def __init__(self, *, persist_tokens: bool = True, backend: SocialBackend | None = None) -> None:
        # Backend credentials and endpoints are release-controlled.  Do not let
        # a user content overlay replace this file: an older executable may
        # interpret a newer overlay schema as a legacy CloudBase proxy.
        config = json.loads(
            (resource_root() / "config" / "social_backend.json").read_text(encoding="utf-8")
        )
        self.social_api_base_url = (os.environ.get("LILI_SOCIAL_API_BASE_URL", "").strip() or str(config.get("social_api_base_url", "")).strip()).rstrip("/")
        self.email_redirect_url = os.environ.get("LILI_AUTH_REDIRECT_URL", "").strip() or str(config.get("email_redirect_to", "")).strip()
        self.password_reset_redirect_url = os.environ.get("LILI_PASSWORD_RESET_REDIRECT_URL", "").strip() or str(config.get("password_reset_redirect_to", "")).strip() or self.email_redirect_url
        self.persist_tokens = persist_tokens
        self._dashboard_cache: dict[str, dict[str, Any]] = {}
        self._last_error = ""
        self.connection = ConnectionStateStore()
        self._http_backend: SocialBackend | None = backend
        if self._http_backend is None and self.social_api_base_url:
            self._http_backend = HttpSocialBackend(self.social_api_base_url, persist_tokens=persist_tokens, email_redirect_url=self.email_redirect_url, password_reset_redirect_url=self.password_reset_redirect_url)
        self._load_dashboard_cache()

    @property
    def backend_name(self) -> str:
        return "social-proxy" if self._http_backend is not None else "unavailable"

    @property
    def backend_endpoint(self) -> str:
        return str(getattr(self._http_backend, "base_url", "") or self.social_api_base_url)

    @property
    def signed_in(self) -> bool:
        return bool(self._http_backend is not None and self._http_backend.signed_in)

    def _require_backend(self) -> SocialBackend:
        if self._http_backend is None:
            raise SocialError("自习室服务尚未配置。", kind="config")
        return self._http_backend

    def health(self) -> dict[str, Any]:
        return dict(self._require_backend().health() or {})

    @property
    def connection_state(self) -> str:
        return self.connection.state

    @property
    def server_clock_offset_seconds(self) -> float:
        value = self.connection.server_timestamp
        if not value:
            return 0.0
        try:
            return (datetime.fromisoformat(value.replace("Z", "+00:00")) - datetime.now().astimezone()).total_seconds()
        except (TypeError, ValueError):
            return 0.0

    def server_now(self) -> datetime:
        return datetime.now().astimezone() + timedelta(seconds=self.server_clock_offset_seconds)

    def _dashboard_cache_path(self) -> Path:
        base = os.environ.get("LOCALAPPDATA")
        return app_data_dir() / "social-dashboard-cache.json"

    def _load_dashboard_cache(self) -> None:
        if not self.persist_tokens:
            return
        try:
            raw = json.loads(self._dashboard_cache_path().read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                migrated: dict[str, dict[str, Any]] = {}
                for key, value in raw.items():
                    if not isinstance(value, dict) or not isinstance(value.get("data"), dict):
                        continue
                    payload = value["data"]
                    payload_me = payload.get("me") if isinstance(payload.get("me"), dict) else {}
                    account_id = str(value.get("account_id") or payload_me.get("user_id") or "").strip()
                    if not account_id:
                        # Legacy entries had no account identity and must not
                        # be shown after another user signs in.
                        continue
                    scoped_key = str(key)
                    if not scoped_key.startswith(f"{account_id}:"):
                        scoped_key = f"{account_id}:{scoped_key}"
                    entry = dict(value)
                    entry["account_id"] = account_id
                    migrated[scoped_key] = entry
                self._dashboard_cache = migrated
        except (OSError, ValueError, TypeError):
            self._dashboard_cache = {}

    def _save_dashboard_cache(self) -> None:
        if not self.persist_tokens:
            return
        target = self._dashboard_cache_path(); temporary = target.with_suffix(".json.tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(self._dashboard_cache, ensure_ascii=False), encoding="utf-8")
            temporary.replace(target)
        except OSError:
            pass

    def _remember_dashboard(self, room_id: str | None, data: dict[str, Any]) -> None:
        account_id = _session_user_id(self)
        if not account_id:
            return
        snapshot = json.loads(json.dumps(data, ensure_ascii=False))
        _normalise_never_seen_presence(snapshot)
        self._dashboard_cache[f"{account_id}:{str(room_id or '')}"] = {
            "account_id": account_id,
            "saved_at": time.time(),
            "data": snapshot,
        }
        self._save_dashboard_cache()

    @staticmethod
    def _mark_remote_presence_stale(data: dict[str, Any]) -> None:
        def mark(items: Any) -> None:
            if not isinstance(items, list): return
            for item in items:
                if not isinstance(item, dict) or item.get("is_self"): continue
                item.update({"online": False, "working": False, "status": "offline", "session_seconds": 0, "today_seconds": None, "stale_presence": True, "presence_uncertain": False})
        mark(data.get("buddies")); mark(data.get("room_people")); mark(data.get("active_visits"))
        room = data.get("current_room")
        if isinstance(room, dict):
            mark(room.get("room_people"))
            if isinstance(room.get("room_summary"), dict): room["room_summary"]["focus_count"] = 0
        if isinstance(data.get("room_summary"), dict): data["room_summary"]["focus_count"] = 0

    @staticmethod
    def _mark_remote_presence_uncertain(data: dict[str, Any], age_seconds: int) -> None:
        """Keep recent peer state visible while the dashboard transport recovers."""

        def mark(items: Any) -> None:
            if not isinstance(items, list): return
            for item in items:
                if not isinstance(item, dict) or item.get("is_self") or item.get("stale_presence"): continue
                item.pop("stale_presence", None)
                item["presence_uncertain"] = True
                item["presence_age_seconds"] = age_seconds
        mark(data.get("buddies")); mark(data.get("room_people")); mark(data.get("active_visits"))
        room = data.get("current_room")
        if isinstance(room, dict):
            mark(room.get("room_people"))
            if isinstance(room.get("room_summary"), dict): room["room_summary"]["presence_uncertain"] = True
        if isinstance(data.get("room_summary"), dict): data["room_summary"]["presence_uncertain"] = True

    def cached_dashboard(self, room_id: str | None = None) -> dict[str, Any] | None:
        account_id = _session_user_id(self)
        if not account_id:
            return None
        key = f"{account_id}:{str(room_id or '')}"
        entry = self._dashboard_cache.get(key)
        if not isinstance(entry, dict) or not isinstance(entry.get("data"), dict): return None
        if str(entry.get("account_id") or account_id) != account_id:
            return None
        data = json.loads(json.dumps(entry["data"], ensure_ascii=False))
        payload_me = data.get("me") if isinstance(data.get("me"), dict) else {}
        payload_account_id = str(payload_me.get("user_id") or "").strip()
        if payload_account_id and payload_account_id != account_id:
            return None
        _normalise_never_seen_presence(data)
        _apply_buddy_private_notes(data, {})
        saved_at = float(entry.get("saved_at") or 0)
        age_seconds = max(0, int(time.time() - saved_at)) if saved_at else 0
        presence_grace = bool(saved_at and age_seconds <= PRESENCE_GRACE_SECONDS)
        if presence_grace: self._mark_remote_presence_uncertain(data, age_seconds)
        else: self._mark_remote_presence_stale(data)
        data.update({"_sync_offline": True, "_connection_state": "DEGRADED" if presence_grace else "OFFLINE", "_presence_grace_active": presence_grace, "_presence_uncertainty_seconds": age_seconds if presence_grace else 0, "data_source": "local_cache", "_data_source": "local_cache", "_sync_age_minutes": max(0, int(age_seconds / 60)) if saved_at else 0, "_sync_error": self._last_error or "当前网络无法访问自习室服务"})
        self.connection.set(
            "DEGRADED" if presence_grace else "OFFLINE",
            data_source="local_cache",
            realtime_state="polling_degraded" if presence_grace else "unavailable",
            server_timestamp=str(data.get("_server_timestamp") or ""),
        )
        return data

    def diagnose_connection(self, room_id: str | None = None) -> dict[str, Any]:
        checks = {"edge_function": {"ok": False}, "authentication": {"ok": self.signed_in}, "room_snapshot": {"ok": False}, "presence": {"ok": False}, "realtime": {"ok": False}}
        try:
            health = self.health(); checks["edge_function"] = {"ok": True, "backend": health.get("backend", "supabase"), "transport": "https-rest"}
        except SocialError as exc:
            cached = self.cached_dashboard(room_id)
            state = str((cached or {}).get("_connection_state") or "OFFLINE")
            realtime_state = "polling_degraded" if state == "DEGRADED" else "unavailable"
            self.connection.set(state, data_source="local_cache" if cached else "none", realtime_state=realtime_state)
            return {"connection_state": state, "data_source": "local_cache" if cached else "none", "realtime_state": realtime_state, "backend": self.backend_name, "service": self.backend_endpoint, "checks": checks, "dashboard": cached, "error": str(exc)}
        if not self.signed_in:
            self.connection.set("DEGRADED", data_source="local_live", realtime_state="not_authenticated")
            return {"connection_state": "DEGRADED", "data_source": "local_live", "realtime_state": "not_authenticated", "backend": self.backend_name, "service": self.backend_endpoint, "checks": checks, "dashboard": None}
        try:
            snapshot = self.dashboard(room_id, allow_cache=False); checks["room_snapshot"] = {"ok": True}; checks["presence"] = {"ok": True}; checks["realtime"] = {"ok": True, "mode": "desktop low-frequency polling"}
            return {"connection_state": "ONLINE", "data_source": "server", "realtime_state": "polling", "backend": self.backend_name, "service": self.backend_endpoint, "checks": checks, "dashboard": snapshot}
        except SocialError as exc:
            cached = self.cached_dashboard(room_id)
            state = str((cached or {}).get("_connection_state") or "OFFLINE")
            realtime_state = "polling_degraded" if state == "DEGRADED" else "unavailable"
            self.connection.set(state, data_source="local_cache" if cached else "none", realtime_state=realtime_state)
            return {"connection_state": state, "data_source": "local_cache" if cached else "none", "realtime_state": realtime_state, "backend": self.backend_name, "service": self.backend_endpoint, "checks": checks, "dashboard": cached, "error": str(exc)}

    def sign_up(self, email: str, password: str, nickname: str) -> SignupResult:
        return self._require_backend().sign_up(email, password, nickname)

    def resend_confirmation(self, email: str) -> bool:
        return self._require_backend().resend_confirmation(email)

    def sign_in(self, email: str, password: str) -> None:
        self._require_backend().sign_in(normalize_email(email), password)

    def record_login_streak(self) -> dict[str, Any]:
        return dict(self._require_backend().record_login_streak() or {})

    @property
    def account_email(self) -> str:
        return str(getattr(self._http_backend, "account_email", "") or "")

    def change_password(self, current_password: str, new_password: str) -> None:
        self._require_backend().change_password(current_password, new_password)

    def request_password_reset(self, email: str) -> bool:
        return bool(self._require_backend().request_password_reset(email))

    def delete_account(self) -> None:
        self._require_backend().delete_account()

    def sign_out(self) -> None:
        backend = self._http_backend
        if backend is not None: backend.sign_out()

    def dashboard(self, room_id: str | None = None, *, allow_cache: bool = True) -> dict[str, Any]:
        self.connection.set("CONNECTING", data_source=self.connection.data_source, realtime_state="polling")
        try:
            result = _normalise_never_seen_presence(
                dict(self._require_backend().dashboard(room_id=room_id, allow_cache=allow_cache) or {})
            )
            server_timestamp = str(result.get("server_timestamp") or result.get("_server_timestamp") or datetime.now().astimezone().isoformat())
            result.update({"_connection_state": "ONLINE", "data_source": "server", "_data_source": "server", "_server_timestamp": server_timestamp})
            self.connection.set("ONLINE", data_source="server", realtime_state="polling", server_timestamp=server_timestamp)
            self._last_error = ""; self._remember_dashboard(room_id, result); return result
        except SocialError as exc:
            self._last_error = str(exc); self.connection.set("OFFLINE", data_source="local_cache", realtime_state="unavailable")
            if not allow_cache: raise
            cached = self.cached_dashboard(room_id)
            if cached is not None: return cached
            raise

    def rpc(self, name: str, body: dict[str, Any]) -> Any: return self._require_backend().rpc(name, body)
    def update_profile(self, *, nickname: str, visibility: str, show_exact_time: bool, allow_visits: bool, outfit_key: str = "", wealth_leaderboard_enabled: bool = True, wealth_leaderboard_preference_set: bool = True) -> None: self._require_backend().update_profile(nickname=nickname, visibility=visibility, show_exact_time=show_exact_time, allow_visits=allow_visits, outfit_key=outfit_key, wealth_leaderboard_enabled=wealth_leaderboard_enabled, wealth_leaderboard_preference_set=wealth_leaderboard_preference_set)
    def update_owner_nickname(self, nickname: str) -> None: self._require_backend().update_owner_nickname(nickname)
    def heartbeat(self, **kwargs: Any) -> None: self._require_backend().heartbeat(**kwargs)
    def send_interaction(self, **kwargs: Any) -> None: self._require_backend().send_interaction(**kwargs)
    def record_room_event(self, **kwargs: Any) -> None: self._require_backend().record_room_event(**kwargs)
    def record_economy_event(self, **kwargs: Any) -> None: self._require_backend().record_economy_event(**kwargs)
    def economy_leaderboard(self, **kwargs: Any) -> list[dict[str, Any]]: return self._require_backend().economy_leaderboard(**kwargs)
    def focus_leaderboard(self, **kwargs: Any) -> list[dict[str, Any]]: return self._require_backend().focus_leaderboard(**kwargs)
    def set_room_goal(self, **kwargs: Any) -> None: self._require_backend().set_room_goal(**kwargs)
    def set_room_schedule(self, **kwargs: Any) -> None: self._require_backend().set_room_schedule(**kwargs)
    def set_room_challenge(self, **kwargs: Any) -> None: self._require_backend().set_room_challenge(**kwargs)
    def set_buddy_subscription(self, **kwargs: Any) -> None: self._require_backend().set_buddy_subscription(**kwargs)
    def leave_room(self, **kwargs: Any) -> None: self._require_backend().leave_room(**kwargs)


class BackendRouteManager:
    """Prefer Supabase Direct and use CloudBase only as an HTTP proxy fallback."""

    DIRECT_SUPABASE = "DIRECT_SUPABASE"
    CLOUDBASE_PROXY = "CLOUDBASE_PROXY"
    NETWORK_KINDS = {"dns", "timeout", "refused", "tls", "network", "server"}
    AUTH_METHODS = {"sign_in", "sign_up", "resend_confirmation", "change_password", "request_password_reset", "verify_password_reset_otp", "set_password_after_reset", "delete_account"}
    SECURITY_METHODS = {"change_password", "request_password_reset", "verify_password_reset_otp", "set_password_after_reset", "delete_account"}
    DIRECT_ONLY_METHODS = {"record_login_streak"}
    BUSINESS_METHODS = {"dashboard", "rpc", "update_profile", "update_owner_nickname", "heartbeat", "send_interaction", "record_room_event", "record_economy_event", "economy_leaderboard", "focus_leaderboard", "set_room_goal", "set_room_schedule", "set_room_challenge", "set_buddy_subscription", "leave_room", "record_login_streak", "sign_up", "sign_in", "resend_confirmation", "change_password", "request_password_reset", "verify_password_reset_otp", "set_password_after_reset", "delete_account"}
    DIRECT_RECOVERY_INTERVAL_SECONDS = 60.0

    def __init__(self, direct: HttpSocialBackend, proxy: HttpSocialBackend | None, *, persist_state: bool = True) -> None:
        self.direct = direct
        self.proxy = proxy
        self.persist_state = persist_state
        # The dashboard worker and the independent heartbeat worker may call
        # this manager concurrently. Protect route telemetry/session choice
        # without locking around network I/O, so a slow dashboard can never
        # hold the heartbeat behind it.
        self._state_lock = threading.RLock()
        self.current_route = self.DIRECT_SUPABASE
        self.last_route_hint = self.DIRECT_SUPABASE
        self.last_latency_ms: float | None = None
        self.failure_count = 0
        self.success_count = 0
        self.last_switch_at = ""
        self._last_direct_probe = 0.0
        self._direct_recovery_successes = 0
        self._load_state()
        # Direct and proxy are transports, not separate auth owners.  They
        # must share the same manager so a fallback request cannot refresh an
        # old copy of the rotating refresh token.
        shared_auth = getattr(self.direct, "auth_manager", None)
        if isinstance(shared_auth, AuthSessionManager) and isinstance(getattr(self.proxy, "auth_manager", None), AuthSessionManager):
            self.proxy.auth_manager = shared_auth
            self.proxy.session = shared_auth.current()
        self._sync_sessions(self.direct, self.proxy)

    def _state_path(self) -> Path:
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / ".desktop_pet"
        return root / "Lili" / "social-route.json"

    def _load_state(self) -> None:
        if not self.persist_state:
            return
        try:
            data = json.loads(self._state_path().read_text(encoding="utf-8"))
            # The saved route is telemetry only.  A VPN, DNS route, or network
            # can change between launches, so never use yesterday's fallback as
            # today's active route.  Every new process starts Direct-first and
            # lets the first lightweight request select the actual route.
            if data.get("route") in {self.DIRECT_SUPABASE, self.CLOUDBASE_PROXY}:
                self.last_route_hint = str(data["route"])
            self.last_latency_ms = float(data["last_latency_ms"]) if data.get("last_latency_ms") is not None else None
            self.failure_count = int(data.get("failure_count") or 0)
            self.success_count = int(data.get("success_count") or 0)
            self.last_switch_at = str(data.get("last_switch_at") or "")
        except (OSError, ValueError, TypeError):
            return

    def _save_state(self) -> None:
        if not self.persist_state:
            return
        target = self._state_path()
        with self._state_lock:
            payload = {
                "route": self.current_route,
                "last_latency_ms": self.last_latency_ms,
                "last_switch_at": self.last_switch_at,
                "failure_count": self.failure_count,
                "success_count": self.success_count,
            }
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            return

    @property
    def signed_in(self) -> bool:
        manager = getattr(self.direct, "auth_manager", None)
        fallback_session = self.proxy.session if self.proxy is not None else None
        return bool((manager.current() if isinstance(manager, AuthSessionManager) else (self.direct.session or fallback_session)) and not (manager.requires_relogin if isinstance(manager, AuthSessionManager) else False))

    @property
    def active(self) -> HttpSocialBackend:
        with self._state_lock:
            route = self.current_route
        return self.direct if route == self.DIRECT_SUPABASE or self.proxy is None else self.proxy

    @property
    def backend_name(self) -> str:
        with self._state_lock:
            route = self.current_route
        return "Supabase Direct" if route == self.DIRECT_SUPABASE or self.proxy is None else "CloudBase Proxy"

    @property
    def backend_endpoint(self) -> str:
        return self.active.base_url

    @staticmethod
    def _is_network_failure(exc: SocialError) -> bool:
        return exc.kind in BackendRouteManager.NETWORK_KINDS or exc.status in {502, 503, 504}

    @staticmethod
    def _sync_sessions(source: HttpSocialBackend, target: HttpSocialBackend | None) -> None:
        if target is None:
            return
        manager = getattr(source, "auth_manager", None)
        if isinstance(manager, AuthSessionManager) and isinstance(getattr(target, "auth_manager", None), AuthSessionManager):
            target.auth_manager = manager
            target.session = manager.current()
            return
        target.session = source.session

    def _switch(self, route: str) -> None:
        changed = False
        with self._state_lock:
            if self.current_route != route:
                self.current_route = route
                self.last_switch_at = datetime.now().astimezone().isoformat()
                changed = True
        if changed:
            self._save_state()

    def _mark_success(self, started: float) -> None:
        with self._state_lock:
            self.last_latency_ms = round((time.monotonic() - started) * 1000, 1)
            self.success_count += 1
            self.failure_count = 0
        self._save_state()

    def _mark_failure(self) -> None:
        with self._state_lock:
            self.failure_count += 1
        self._save_state()

    def _probe_direct_recovery(self) -> None:
        with self._state_lock:
            if (
                self.proxy is None
                or self.current_route != self.CLOUDBASE_PROXY
                or time.monotonic() - self._last_direct_probe < self.DIRECT_RECOVERY_INTERVAL_SECONDS
            ):
                return
            self._last_direct_probe = time.monotonic()
        try:
            self.direct.health()
            with self._state_lock:
                self._direct_recovery_successes += 1
                recovered = self._direct_recovery_successes >= 2
            if recovered:
                self._switch(self.DIRECT_SUPABASE)
        except SocialError as exc:
            if self._is_network_failure(exc):
                with self._state_lock:
                    self._direct_recovery_successes = 0

    def health(self) -> dict[str, Any]:
        """Select the current route with a lightweight Supabase-first probe.

        The normal path is exactly one request.  Only when Direct is actually
        unreachable do we retry Direct once and then probe the CloudBase proxy.
        This makes the visible route follow the current network instead of a
        stale persisted fallback.
        """
        started = time.monotonic()
        try:
            result = self.direct.health()
            self._switch(self.DIRECT_SUPABASE)
            self._direct_recovery_successes = 0
            self._mark_success(started)
            return {**result, "route": self.current_route, "route_label": self.backend_name}
        except SocialError as first:
            if not self._is_network_failure(first):
                raise
            self._mark_failure()
            try:
                result = self.direct.health()
                self._switch(self.DIRECT_SUPABASE)
                self._direct_recovery_successes = 0
                self._mark_success(started)
                return {**result, "route": self.current_route, "route_label": self.backend_name}
            except SocialError as second:
                if not self._is_network_failure(second):
                    raise
                self._mark_failure()
                if self.proxy is None:
                    raise second
                result = self.proxy.health()
                self._switch(self.CLOUDBASE_PROXY)
                self._mark_success(started)
                return {**result, "route": self.current_route, "route_label": self.backend_name}

    def _select_auth_route(self) -> HttpSocialBackend:
        """Choose a route before login without replaying credentials blindly."""
        previous_route = self.current_route
        # Do not persist this temporary choice until the probe succeeds.  This
        # prevents a stale proxy state from surviving a changed VPN/network.
        self.current_route = self.DIRECT_SUPABASE
        last_network_error: SocialError | None = None
        for _ in range(2):
            started = time.monotonic()
            try:
                self.direct.health()
                self._switch(self.DIRECT_SUPABASE)
                self._mark_success(started)
                return self.direct
            except SocialError as exc:
                if not self._is_network_failure(exc):
                    self.current_route = previous_route
                    raise
                self._mark_failure()
                last_network_error = exc
        if self.proxy is None:
            if last_network_error is not None:
                raise last_network_error
            raise SocialError("Supabase 服务暂时不可用，请稍后重试。")
        self._switch(self.CLOUDBASE_PROXY)
        return self.proxy

    def _request_auth(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Authenticate Direct-first, falling back only on network failure.

        A failed login must not be submitted twice to Supabase.  We probe the
        route first, then send credentials once on that route; a network error
        may retry once on the proxy, while 401/403 and validation errors stop.
        """
        # Password changes, recovery requests, and account deletion are never
        # replayed to or routed through the legacy proxy. This keeps passwords
        # inside Supabase Auth and avoids duplicate destructive operations after
        # a timeout.
        if method in self.SECURITY_METHODS:
            backend = self.direct
            self._sync_sessions(self.direct, self.proxy)
            started = time.monotonic()
            try:
                result = getattr(backend, method)(*args, **kwargs)
                self._mark_success(started)
                return result
            except SocialError:
                self._mark_failure()
                raise

        backend = self._select_auth_route()
        self._sync_sessions(self.direct if backend is self.direct else self.proxy, backend)
        started = time.monotonic()
        try:
            result = getattr(backend, method)(*args, **kwargs)
            if backend is self.proxy:
                self._sync_sessions(self.proxy, self.direct)
                self.direct._save_session()
            else:
                self._sync_sessions(self.direct, self.proxy)
            self._mark_success(started)
            return result
        except SocialError as first:
            # Signup/resend can create the Auth user before SMTP returns. Never
            # replay these timeout results to the proxy, or the same password
            # request may create a duplicate-account response or send twice.
            if method in {"sign_up", "resend_confirmation"} and first.kind in {"signup_timeout", "confirmation_timeout"}:
                self._mark_failure()
                raise
            if not (backend is self.direct and self._is_network_failure(first)):
                if self._is_network_failure(first):
                    self._mark_failure()
                raise
            self._mark_failure()
            if self.proxy is None:
                raise first
            self._switch(self.CLOUDBASE_PROXY)
            result = getattr(self.proxy, method)(*args, **kwargs)
            self._sync_sessions(self.proxy, self.direct)
            self.direct._save_session()
            self._mark_success(started)
            return result

    def request(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if method not in self.BUSINESS_METHODS:
            raise AttributeError(method)
        if method in self.AUTH_METHODS:
            return self._request_auth(method, *args, **kwargs)
        if method in self.DIRECT_ONLY_METHODS:
            # The login streak is an account reward, not room content. Never
            # route it to the legacy proxy or silently count a second login.
            started = time.monotonic()
            result = getattr(self.direct, method)(*args, **kwargs)
            self._mark_success(started)
            return result
        self._probe_direct_recovery()
        backend = self.active
        self._sync_sessions(self.direct if backend is self.direct else self.proxy, backend)
        started = time.monotonic()
        try:
            result = getattr(backend, method)(*args, **kwargs)
            if backend is self.proxy:
                self._sync_sessions(self.proxy, self.direct)
                self.direct._save_session()
            else:
                self._sync_sessions(self.direct, self.proxy)
            self._mark_success(started)
            return result
        except SocialError as first:
            if not (backend is self.direct and self._is_network_failure(first)):
                if self._is_network_failure(first): self._mark_failure()
                raise
            self._mark_failure()
            try:
                result = getattr(self.direct, method)(*args, **kwargs)
                self._mark_success(started)
                self._sync_sessions(self.direct, self.proxy)
                return result
            except SocialError as second:
                if not self._is_network_failure(second):
                    raise
                self._mark_failure()
                if self.proxy is None:
                    raise second
                self._switch(self.CLOUDBASE_PROXY)
                self._sync_sessions(self.direct, self.proxy)
                result = getattr(self.proxy, method)(*args, **kwargs)
                self._sync_sessions(self.proxy, self.direct)
                self.direct._save_session()
                self._mark_success(started)
                return result


class SupabaseFirstSocialClient(DashboardCacheClientBase):
    """Production social client with one Supabase source of truth."""

    ACCOUNT_NAME = "supabase-session"

    def __init__(self, *, persist_tokens: bool = True, backend: SocialBackend | None = None) -> None:
        # Backend credentials and endpoints are release-controlled.  Do not let
        # a user content overlay replace this file: an older executable may
        # interpret a newer overlay schema as a legacy CloudBase proxy.
        config = json.loads(
            (resource_root() / "config" / "social_backend.json").read_text(encoding="utf-8")
        )
        supabase_url = os.environ.get("LILI_SUPABASE_URL", "").strip() or str(config.get("supabase_url", "")).strip()
        supabase_key = os.environ.get("LILI_SUPABASE_PUBLISHABLE_KEY", "").strip() or str(config.get("supabase_publishable_key", "")).strip()
        proxy_url = os.environ.get("LILI_CLOUDBASE_PROXY_URL", "").strip() or str(config.get("social_api_base_url", "")).strip()
        self.connection = ConnectionStateStore()
        self._last_error = ""
        self._dashboard_cache = {}
        self.persist_tokens = persist_tokens
        if backend is not None:
            self._manager = backend
        else:
            password_reset_redirect_url = str(config.get("password_reset_redirect_to", "")).strip() or str(config.get("email_redirect_to", ""))
            direct = HttpSocialBackend(supabase_url, client_key=supabase_key, persist_tokens=persist_tokens, email_redirect_url=str(config.get("email_redirect_to", "")), password_reset_redirect_url=password_reset_redirect_url, transport="direct")
            proxy_enabled = bool(proxy_url) and str(config.get("social_backend", "")).strip().lower() in {"direct_with_cloudbase_fallback", "cloudbase_proxy"}
            proxy = HttpSocialBackend(proxy_url, client_key="", persist_tokens=False, email_redirect_url=str(config.get("email_redirect_to", "")), password_reset_redirect_url=password_reset_redirect_url, transport="proxy") if proxy_enabled else None
            self._manager = BackendRouteManager(direct, proxy, persist_state=persist_tokens)
        self._load_dashboard_cache()

    @property
    def backend_name(self) -> str:
        return self._manager.backend_name

    @property
    def backend_endpoint(self) -> str:
        return self._manager.backend_endpoint

    @property
    def signed_in(self) -> bool:
        return self._manager.signed_in

    @property
    def session(self) -> SocialSession | None:
        """Expose the active Supabase session to local profile sync helpers.

        ``SupabaseFirstSocialClient`` owns the route manager, while the
        session itself lives on the currently active HTTP transport.  The
        desktop window needs the user id to persist ``owner_nickname`` after a
        local rename; without this bridge that sync silently returned before
        sending the profile update.
        """

        active = getattr(self._manager, "active", None)
        session = getattr(active, "session", None)
        if session is not None:
            return session
        # The route manager deliberately shares one AuthSessionManager between
        # Direct and proxy transports.  If a route switch happened between
        # two GUI ticks, the selected transport can lag behind that manager;
        # expose the authoritative session so the next heartbeat is not
        # skipped and account-scoped cache lookup remains correct.
        direct = getattr(self._manager, "direct", None)
        session = getattr(direct, "session", None)
        if session is not None:
            return session
        auth_manager = getattr(direct, "auth_manager", None)
        return auth_manager.current() if isinstance(auth_manager, AuthSessionManager) else None

    @property
    def connection_state(self) -> str:
        return self.connection.state

    def health(self) -> dict[str, Any]:
        return self._manager.health()

    def diagnose_connection(self, room_id: str | None = None) -> dict[str, Any]:
        del room_id
        checks = {"edge_function": {"ok": False}, "authentication": {"ok": self.signed_in}, "room_snapshot": {"ok": False}, "presence": {"ok": False}, "realtime": {"ok": False}}
        try:
            health = self.health()
            checks["edge_function"] = {"ok": True, "backend": health.get("backend", "supabase"), "transport": "https-rest"}
            self.connection.set("ONLINE" if self.signed_in else "DEGRADED", data_source="local_live", realtime_state="not_started")
            return {"connection_state": "ONLINE" if self.signed_in else "DEGRADED", "data_source": "local_live", "realtime_state": "not_started", "backend": self.backend_name, "service": self.backend_endpoint, "checks": checks, "dashboard": None}
        except SocialError as exc:
            cached = self.cached_dashboard(None)
            state = str((cached or {}).get("_connection_state") or "OFFLINE")
            realtime_state = "polling_degraded" if state == "DEGRADED" else "unavailable"
            self.connection.set(state, data_source="local_cache" if cached else "none", realtime_state=realtime_state)
            return {"connection_state": state, "data_source": "local_cache" if cached else "none", "realtime_state": realtime_state, "backend": self.backend_name, "service": self.backend_endpoint, "checks": checks, "dashboard": cached, "error": str(exc)}

    def sign_up(self, email: str, password: str, nickname: str) -> SignupResult:
        return self._manager.request("sign_up", email, password, nickname)

    def resend_confirmation(self, email: str) -> bool:
        return bool(self._manager.request("resend_confirmation", email))

    def sign_in(self, email: str, password: str) -> None:
        # Stage account switches: a mistyped password must not erase the last
        # usable session before Supabase has accepted the replacement.  The
        # successful auth response atomically replaces the stored session.
        self._manager.request("sign_in", normalize_email(email), password)

    def record_login_streak(self) -> dict[str, Any]:
        return dict(self._manager.request("record_login_streak") or {})

    @property
    def account_email(self) -> str:
        active = getattr(self._manager, "active", None)
        return normalize_email(str(getattr(active, "account_email", "") or ""))

    def change_password(self, current_password: str, new_password: str) -> None:
        self._manager.request("change_password", current_password, new_password)

    def request_password_reset(self, email: str) -> bool:
        return bool(self._manager.request("request_password_reset", normalize_email(email)))

    def verify_password_reset_otp(self, email: str, otp: str) -> bool:
        return bool(self._manager.request("verify_password_reset_otp", normalize_email(email), str(otp or "").strip()))

    def set_password_after_reset(self, new_password: str) -> None:
        self._manager.request("set_password_after_reset", new_password)

    def delete_account(self) -> None:
        self._manager.request("delete_account")

    def sign_out(self) -> None:
        backend = self._manager.direct if hasattr(self._manager, "direct") else None
        if backend is not None: backend._clear_session()
        if self._manager.proxy is not None:
            self._manager.proxy.session = None

    def dashboard(self, room_id: str | None = None, *, allow_cache: bool = True) -> dict[str, Any]:
        self.connection.set("CONNECTING", data_source=self.connection.data_source, realtime_state="polling")
        try:
            result = _normalise_never_seen_presence(
                dict(self._manager.request("dashboard", room_id=room_id) or {})
            )
            try:
                note_payload = self._manager.request("rpc", "lili_buddy_private_notes", {})
                _apply_buddy_private_notes(result, _private_notes_from_payload(note_payload))
            except SocialError as exc:
                # The migration can be applied after the desktop release. The
                # core dashboard must remain usable while that deployment is
                # catching up, so private labels fail closed to public names.
                LOGGER.info(
                    "buddy private notes unavailable kind=%s status=%s",
                    exc.kind,
                    exc.status,
                )
            try:
                witness_payload = self._manager.request("rpc", "lili_achievement_witness_inbox", {})
                result["achievement_witness_requests"] = (
                    witness_payload if isinstance(witness_payload, list) else []
                )
            except SocialError as exc:
                # This RPC is introduced by the manual-witness migration. A
                # client may meet an older backend during rollout; an empty
                # inbox is safer than breaking the whole social dashboard.
                LOGGER.info("achievement witness inbox unavailable kind=%s status=%s", exc.kind, exc.status)
            stamp = str(result.get("server_timestamp") or result.get("_server_timestamp") or datetime.now().astimezone().isoformat())
            result.update({"_connection_state": "ONLINE", "data_source": "server", "_data_source": "server", "_server_timestamp": stamp})
            self.connection.set("ONLINE", data_source="server", realtime_state="polling", server_timestamp=stamp)
            self._last_error = ""
            self._remember_dashboard(room_id, result)
            return result
        except SocialError as exc:
            self._last_error = str(exc)
            self.connection.set("OFFLINE", data_source="local_cache", realtime_state="unavailable")
            if allow_cache:
                cached = self.cached_dashboard(room_id)
                if cached is not None: return cached
            raise

    def rpc(self, name: str, body: dict[str, Any]) -> Any: return self._manager.request("rpc", name, body)
    def update_profile(self, **kwargs: Any) -> None: self._manager.request("update_profile", **kwargs)
    def update_owner_nickname(self, nickname: str) -> None: self._manager.request("update_owner_nickname", nickname)
    def heartbeat(self, **kwargs: Any) -> None: self._manager.request("heartbeat", **kwargs)
    def send_interaction(self, **kwargs: Any) -> None: self._manager.request("send_interaction", **kwargs)
    def record_room_event(self, **kwargs: Any) -> None: self._manager.request("record_room_event", **kwargs)
    def record_economy_event(self, **kwargs: Any) -> None: self._manager.request("record_economy_event", **kwargs)
    def economy_leaderboard(self, **kwargs: Any) -> list[dict[str, Any]]: return list(self._manager.request("economy_leaderboard", **kwargs) or [])
    def focus_leaderboard(self, **kwargs: Any) -> list[dict[str, Any]]: return list(self._manager.request("focus_leaderboard", **kwargs) or [])
    def set_room_goal(self, **kwargs: Any) -> None: self._manager.request("set_room_goal", **kwargs)
    def set_room_schedule(self, **kwargs: Any) -> None: self._manager.request("set_room_schedule", **kwargs)
    def set_room_challenge(self, **kwargs: Any) -> None: self._manager.request("set_room_challenge", **kwargs)
    def set_buddy_subscription(self, **kwargs: Any) -> None: self._manager.request("set_buddy_subscription", **kwargs)
    def leave_room(self, **kwargs: Any) -> None: self._manager.request("leave_room", **kwargs)


SocialClient = SupabaseFirstSocialClient
