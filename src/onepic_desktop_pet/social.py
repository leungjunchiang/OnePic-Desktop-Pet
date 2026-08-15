"""Lili 鎼瓙鑷範瀹ょ殑鏈€灏忕ぞ浜ゅ鎴风涓庡彲鏇挎崲缃戠粶鍚庣銆?
鍙彂閫佽处鍙疯璇併€佹樀绉般€佸叚姣涘瑙傘€佸伐浣滅姸鎬併€佺疮璁＄鏁般€佹埧闂翠笌涓查棬浜嬩欢銆傚瘑鐮佷粠涓嶄繚瀛橈紱
鍒锋柊浠ょ墝淇濆瓨鍦ㄧ郴缁熷嚟鎹簱銆傜綉缁滃け璐ヤ笉浼氬奖鍝嶇绾挎瀹犮€佽鏃躲€丄I 鎴栨湰鍦扮礌鏉愩€?"""

from __future__ import annotations

import json
import logging
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol

from .resources import resource_path


LOGGER = logging.getLogger(__name__)

CONNECTION_STATES = {"CONNECTING", "ONLINE", "DEGRADED", "OFFLINE", "RECONNECTING"}


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
        elif self.state in {"OFFLINE", "RECONNECTING"}:
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
    """闈㈠悜鐢ㄦ埛鐨勭ぞ浜ょ綉缁滈敊璇紝骞朵繚鐣欏彲璁板綍鐨勮瘖鏂垎绫汇€?""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "unknown",
        endpoint: str = "",
        retryable: bool = False,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.endpoint = endpoint
        self.retryable = retryable
        self.status = status


def _endpoint_host(base_url: str) -> str:
    parsed = urllib.parse.urlparse(str(base_url or ""))
    return parsed.netloc or "鏈厤缃?


def _network_error(exc: BaseException, base_url: str) -> SocialError:
    """鎶?urllib/Windows 閿欒杞垚鐢ㄦ埛鑳介噰鍙栬鍔ㄧ殑鍒嗙被銆?""

    reason = getattr(exc, "reason", exc)
    host = _endpoint_host(base_url)
    if isinstance(reason, socket.gaierror) or "getaddrinfo" in str(reason).lower():
        return SocialError(f"DNS 瑙ｆ瀽澶辫触锛氭壘涓嶅埌鑷範瀹ゆ湇鍔″櫒锛坽host}锛夈€?, kind="dns", endpoint=host, retryable=True)
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).lower():
        return SocialError(f"杩炴帴瓒呮椂锛氳嚜涔犲鏈嶅姟鍣紙{host}锛夋病鏈夊強鏃跺洖搴斻€?, kind="timeout", endpoint=host, retryable=True)
    if isinstance(reason, ConnectionRefusedError) or "refused" in str(reason).lower():
        return SocialError(f"鏈嶅姟鍣ㄦ嫆缁濊繛鎺ワ細璇锋鏌ヨ嚜涔犲涓浆鏈嶅姟锛坽host}锛夋槸鍚﹀湪绾裤€?, kind="refused", endpoint=host, retryable=True)
    if isinstance(reason, ssl.SSLError) or "ssl" in str(reason).lower() or "certificate" in str(reason).lower():
        return SocialError(f"TLS/璇佷功杩炴帴澶辫触锛氭棤娉曞畨鍏ㄨ繛鎺ヨ嚜涔犲鏈嶅姟鍣紙{host}锛夈€?, kind="tls", endpoint=host)
    return SocialError(f"缃戠粶涓嶅彲杈撅細鏃犳硶杩炴帴鑷範瀹ゆ湇鍔″櫒锛坽host}锛夈€?, kind="network", endpoint=host, retryable=True)


def _social_request_timeout() -> float:
    """Keep an unreachable social endpoint from freezing a user action."""

    try:
        return min(
            8.0,
            max(2.0, float(os.environ.get("LILI_SOCIAL_TIMEOUT_SECONDS", "4"))),
        )
    except ValueError:
        return 4.0


@dataclass
class SocialSession:
    access_token: str
    refresh_token: str
    user_id: str
    expires_at: float


class SocialBackend(Protocol):
    """Transport-neutral social API used by the desktop UI.

    The UI only needs these operations; the route manager decides whether the
    request uses Supabase Direct or the CloudBase proxy.
    """

    @property
    def signed_in(self) -> bool: ...

    def sign_up(self, email: str, password: str, nickname: str) -> bool: ...
    def sign_in(self, email: str, password: str) -> None: ...
    def sign_out(self) -> None: ...
    def health(self) -> dict[str, Any]: ...
    def dashboard(self, room_id: str | None = None, *, allow_cache: bool = True) -> dict[str, Any]: ...
    def rpc(self, name: str, body: dict[str, Any]) -> Any: ...
    def update_profile(self, *, nickname: str, visibility: str, show_exact_time: bool, allow_visits: bool, outfit_key: str = "") -> None: ...
    def update_owner_nickname(self, nickname: str) -> None: ...
    def heartbeat(self, *, working: bool, today_seconds: int, session_started_at: str | None, outfit_key: str, room_id: str | None = None, quick_status: str = "", quick_status_expires_at: str | None = None) -> None: ...
    def send_interaction(self, *, target: str, kind: str, room_id: str | None = None) -> None: ...
    def record_room_event(self, *, room_id: str, kind: str, target_id: str | None = None, message: str = "") -> None: ...
    def set_room_goal(self, *, room_id: str, title: str, target_seconds: int, due_at: str | None = None) -> None: ...
    def set_room_schedule(self, *, room_id: str, start_at: str, end_at: str, enabled: bool = True) -> None: ...
    def set_room_challenge(self, *, room_id: str, title: str, target_seconds: int, target_rounds: int) -> None: ...
    def set_buddy_subscription(self, *, buddy_id: str, on_focus_start: bool, on_focus_end: bool, muted: bool = False) -> None: ...
    def leave_room(self, *, room_id: str) -> None: ...


class HttpSocialBackend:
    """REST transport for either Supabase Direct or the CloudBase proxy."""

    SERVICE_NAME = "LiliSocial"
    ACCOUNT_NAME = "supabase-session"

    def __init__(self, base_url: str, *, client_key: str = "", persist_tokens: bool = True, email_redirect_url: str = "", transport: str = "proxy") -> None:
        self.base_url = base_url.rstrip("/")
        self.client_key = client_key
        self.persist_tokens = persist_tokens
        self.email_redirect_url = email_redirect_url
        self.transport = transport if transport in {"direct", "proxy"} else "proxy"
        self.last_server_timestamp = ""
        self.session: SocialSession | None = None
        self._load_session()

    @property
    def signed_in(self) -> bool:
        return self.session is not None

    @staticmethod
    def _keyring():
        import keyring
        return keyring

    def _load_session(self) -> None:
        if not self.persist_tokens:
            return
        try:
            raw = self._keyring().get_password(self.SERVICE_NAME, self.ACCOUNT_NAME)
            if raw:
                data = json.loads(raw)
                self.session = SocialSession(
                    str(data["access_token"]), str(data.get("refresh_token", "")),
                    str(data.get("user_id", "")), float(data.get("expires_at", 0)),
                )
        except Exception:
            self.session = None

    def _save_session(self) -> None:
        if self.persist_tokens and self.session is not None:
            self._keyring().set_password(self.SERVICE_NAME, self.ACCOUNT_NAME, json.dumps(self.session.__dict__))

    def _clear_session(self) -> None:
        self.session = None
        if self.persist_tokens:
            try:
                self._keyring().delete_password(self.SERVICE_NAME, self.ACCOUNT_NAME)
            except Exception:
                pass

    def _raw(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        authenticated: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.client_key:
            headers["apikey" if self.transport == "direct" else "X-Client-Key"] = self.client_key
        if authenticated:
            self._ensure_fresh()
            if not self.session:
                raise SocialError("璇峰厛鐧诲綍鎼瓙鑷範瀹ゃ€?)
            headers["Authorization"] = f"Bearer {self.session.access_token}"
        if extra_headers:
            headers.update({str(key): str(value) for key, value in extra_headers.items()})
        request = urllib.request.Request(f"{self.base_url}{path}", data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=_social_request_timeout()) as response:
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
                message = data.get("message") or data.get("error") or raw
            except json.JSONDecodeError:
                message = raw or str(exc)
            status = int(exc.code)
            kind = "auth" if status in (401, 403) else "server" if status >= 500 else "http"
            raise SocialError(
                str(message)[:300],
                kind=kind,
                endpoint=_endpoint_host(self.base_url),
                retryable=status >= 500,
                status=status,
            ) from exc
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise _network_error(exc, self.base_url) from exc

    def _accept_auth(self, data: dict[str, Any] | None) -> bool:
        if not data or not data.get("access_token"):
            return False
        user = data.get("user") or {}
        self.session = SocialSession(
            str(data["access_token"]), str(data.get("refresh_token", "")),
            str(user.get("id", data.get("user_id", ""))),
            time.time() + int(data.get("expires_in", 3600)),
        )
        self._save_session()
        return True

    def _ensure_fresh(self) -> None:
        if not self.session or self.session.expires_at > time.time() + 90:
            return
        path = "/auth/v1/token?grant_type=refresh_token" if self.transport == "direct" else "/auth/refresh"
        data = self._raw("POST", path, {"refresh_token": self.session.refresh_token})
        if not self._accept_auth(data):
            self._clear_session()

    def sign_up(self, email: str, password: str, nickname: str) -> bool:
        body = {"email": email.strip(), "password": password, "nickname": nickname.strip()[:24] or "鎼瓙", "data": {"nickname": nickname.strip()[:24] or "鎼瓙"}}
        if self.email_redirect_url:
            body["redirect_to"] = self.email_redirect_url
        if self.transport == "direct":
            path = "/auth/v1/signup?" + urllib.parse.urlencode({"redirect_to": self.email_redirect_url}) if self.email_redirect_url else "/auth/v1/signup"
        else:
            path = "/auth/signup"
        return self._accept_auth(self._raw("POST", path, body if self.transport == "proxy" else {"email": body["email"], "password": body["password"], "data": body["data"]}))

    def sign_in(self, email: str, password: str) -> None:
        path = "/auth/v1/token?grant_type=password" if self.transport == "direct" else "/auth/signin"
        data = self._raw("POST", path, {"email": email.strip(), "password": password})
        if not self._accept_auth(data):
            raise SocialError("鐧诲綍娌℃湁鎴愬姛锛岃妫€鏌ラ偖绠辩‘璁ゆ垨瀵嗙爜銆?)

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
                room = self._raw("POST", "/rest/v1/rpc/lili_room_dashboard", {"p_room_id": room_id}, authenticated=True) or {}
                if isinstance(room, dict): data.update(room)
                try:
                    rituals = self._raw("POST", "/rest/v1/rpc/lili_room_room_rituals", {"p_room_id": room_id}, authenticated=True) or {}
                    if isinstance(rituals, dict): data.update(rituals)
                except SocialError as exc:
                    if exc.status >= 500 or exc.kind in {"dns", "timeout", "refused", "tls", "network", "server"}: raise
            result = dict(data)
            if self.last_server_timestamp:
                result.setdefault("server_timestamp", self.last_server_timestamp)
            return result
        path = "/dashboard"
        if room_id: path += "?room_id=" + urllib.parse.quote(str(room_id), safe="")
        result = dict(self._raw("GET", path, authenticated=True) or {})
        if self.last_server_timestamp:
            result.setdefault("server_timestamp", self.last_server_timestamp)
        return result

    def rpc(self, name: str, body: dict[str, Any]) -> Any:
        if self.transport == "direct":
            return self._raw("POST", f"/rest/v1/rpc/{urllib.parse.quote(name, safe='')}", body, authenticated=True)
        routes = {
            "lili_add_buddy_by_code": "/buddies/request",
            "lili_respond_buddy": "/buddies/accept",
            "lili_send_visit": "/visits/send",
            "lili_respond_visit": "/visits/accept",
            "lili_create_room": "/rooms/create",
            "lili_join_room": "/rooms/join",
            "lili_set_room_goal": "/rooms/goal",
            "lili_leave_room": "/rooms/leave",
            "lili_send_interaction": "/rooms/interaction",
            "lili_record_room_event": "/rooms/events",
        }
        return self._raw("POST", routes.get(name, f"/rpc/{name}"), body, authenticated=True)

    def update_profile(self, *, nickname: str, visibility: str, show_exact_time: bool, allow_visits: bool, outfit_key: str = "") -> None:
        body = {"nickname": nickname.strip()[:24] or "鎼瓙", "owner_nickname": nickname.strip()[:24], "visibility": visibility, "show_exact_time": bool(show_exact_time), "allow_visits": bool(allow_visits), "outfit_key": outfit_key[:60]}
        if self.transport == "direct":
            user_id = urllib.parse.quote(str(self.session.user_id if self.session else ""), safe="")
            self._raw("PATCH", f"/rest/v1/lili_profiles?user_id=eq.{user_id}", body, authenticated=True)
        else:
            self._raw("PATCH", "/profile", body, authenticated=True)

    def update_owner_nickname(self, nickname: str) -> None:
        body = {"nickname": nickname.strip()[:24] or "鎼瓙", "owner_nickname": nickname.strip()[:24]}
        if self.transport == "direct":
            user_id = urllib.parse.quote(str(self.session.user_id if self.session else ""), safe="")
            self._raw("PATCH", f"/rest/v1/lili_profiles?user_id=eq.{user_id}", body, authenticated=True)
        else:
            self._raw("PATCH", "/profile", body, authenticated=True)

    def heartbeat(self, *, working: bool, today_seconds: int, session_started_at: str | None, outfit_key: str, room_id: str | None = None, quick_status: str = "", quick_status_expires_at: str | None = None) -> None:
        if not self.session:
            return
        # Presence freshness is assigned by the Supabase database clock.  Do not
        # send a client last_seen value: a user's incorrect Windows clock would
        # otherwise make an active buddy look offline for the whole room.
        now = datetime.now().astimezone()
        body = {"working": bool(working), "today_seconds": min(86400, max(0, int(today_seconds))), "session_started_at": session_started_at, "focus_date": now.date().isoformat(), "outfit_key": outfit_key[:60], "room_id": room_id, "quick_status": quick_status[:40], "quick_status_expires_at": quick_status_expires_at}
        if self.transport == "direct":
            body["user_id"] = self.session.user_id
            # PostgREST only turns the on_conflict query into an upsert when
            # merge-duplicates is explicitly requested. Without this header,
            # every direct heartbeat after the first one fails with a primary
            # key violation and peers keep seeing this user as offline.
            self._raw(
                "POST",
                "/rest/v1/lili_focus_presence?on_conflict=user_id",
                body,
                authenticated=True,
                extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            )
        else:
            self._raw("POST", "/presence/heartbeat", body, authenticated=True)

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


# Kept as a compatibility implementation for older integrations.  The
# production alias at the end of this module uses SupabaseFirstSocialClient.
class LegacyDirectSocialClient:
    SERVICE_NAME = "LiliSocial"
    ACCOUNT_NAME = "supabase-session"

    def __init__(self, *, persist_tokens: bool = True, backend: SocialBackend | None = None) -> None:
        config = json.loads(resource_path("config/social_backend.json").read_text(encoding="utf-8"))
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
        self.persist_tokens = persist_tokens
        self._dashboard_cache: dict[str, dict[str, Any]] = {}
        self._last_error = ""
        self.connection = ConnectionStateStore()
        self.session: SocialSession | None = None
        self._http_backend: SocialBackend | None = backend
        if self._http_backend is None and self.social_api_base_url:
            self._http_backend = HttpSocialBackend(
                self.social_api_base_url,
                client_key=self.key,
                persist_tokens=persist_tokens,
                email_redirect_url=self.email_redirect_url,
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
            raise SocialError("褰撳墠鑷範瀹や腑杞湇鍔℃湭鎻愪緵鍋ュ悍妫€鏌ャ€?, kind="config")
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
            self.connection.set("OFFLINE", data_source="local_cache", realtime_state="unavailable")
            self._diagnostic_log("health", room_id, exc=exc, elapsed=time.monotonic() - started)
            raise

        if not self.signed_in:
            self.connection.set("DEGRADED", data_source="local_live", realtime_state="not_authenticated")
            result = {
                "connection_state": "DEGRADED",
                "data_source": "local_live",
                "realtime_state": "not_authenticated",
                "backend": backend_name,
                "service": service_endpoint,
                "checks": checks,
                "dashboard": None,
            }
            self._diagnostic_log("connection", room_id, result=result, elapsed=time.monotonic() - started)
            return result

        checks["authentication"] = {"ok": True}
        try:
            snapshot = self.dashboard(room_id, allow_cache=False)
            checks["room_snapshot"] = {"ok": True}
            checks["presence"] = {"ok": True, "source": "server_snapshot"}
            # The current product deliberately uses authenticated short
            # polling.  That is the realtime/sync mechanism we can prove here.
            checks["realtime"] = {
                "ok": True,
                "mode": str((health or {}).get("realtime") or "desktop short-polling"),
            }
            self.connection.set(
                "ONLINE",
                data_source="server",
                realtime_state="polling",
                server_timestamp=str(snapshot.get("_server_timestamp") or ""),
            )
            result = {
                "connection_state": "ONLINE",
                "data_source": "server",
                "realtime_state": "polling",
                "backend": backend_name,
                "service": service_endpoint,
                "checks": checks,
                "dashboard": snapshot,
            }
        except SocialError as exc:
            checks["room_snapshot"] = {"ok": False, "kind": exc.kind, "status": exc.status}
            state = "DEGRADED" if exc.kind == "auth" else "OFFLINE"
            realtime_state = "not_authenticated" if state == "DEGRADED" else "unavailable"
            self.connection.set(state, data_source="local_cache", realtime_state=realtime_state)
            cached = self.cached_dashboard(room_id)
            if cached is not None:
                cached["_connection_state"] = state
            result = {
                "connection_state": state,
                "data_source": "local_cache" if cached is not None else "none",
                "realtime_state": realtime_state,
                "backend": backend_name,
                "service": service_endpoint,
                "checks": checks,
                "dashboard": cached,
                "error": str(exc),
            }
        self._diagnostic_log("connection", room_id, result=result, elapsed=time.monotonic() - started)
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
        }
        LOGGER.info("study_room_diagnostic %s", json.dumps(entry, ensure_ascii=False, sort_keys=True))

    @property
    def signed_in(self) -> bool:
        return self._http_backend.signed_in if self._http_backend is not None else self.session is not None

    @staticmethod
    def _keyring():
        import keyring
        return keyring

    def _load_session(self) -> None:
        if not self.persist_tokens:
            return
        try:
            raw = self._keyring().get_password(self.SERVICE_NAME, self.ACCOUNT_NAME)
            if raw:
                data = json.loads(raw)
                self.session = SocialSession(
                    str(data["access_token"]), str(data["refresh_token"]),
                    str(data["user_id"]), float(data.get("expires_at", 0)),
                )
        except Exception:
            self.session = None

    def _save_session(self) -> None:
        if not self.persist_tokens or self.session is None:
            return
        self._keyring().set_password(self.SERVICE_NAME, self.ACCOUNT_NAME, json.dumps(self.session.__dict__))

    def _clear_session(self) -> None:
        self.session = None
        if self.persist_tokens:
            try:
                self._keyring().delete_password(self.SERVICE_NAME, self.ACCOUNT_NAME)
            except Exception:
                pass

    def _dashboard_cache_path(self) -> Path:
        """Return a local cache path that contains no access tokens."""

        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / ".desktop_pet"
        return root / "Lili" / "social-dashboard-cache.json"

    def _load_dashboard_cache(self) -> None:
        if not self.persist_tokens:
            return
        try:
            raw = json.loads(self._dashboard_cache_path().read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._dashboard_cache = {
                    str(key): value
                    for key, value in raw.items()
                    if isinstance(value, dict) and isinstance(value.get("data"), dict)
                }
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
        key = str(room_id or "")
        self._dashboard_cache[key] = {
            "saved_at": time.time(),
            "data": json.loads(json.dumps(data, ensure_ascii=False)),
        }
        self._save_dashboard_cache()

    def cached_dashboard(self, room_id: str | None = None) -> dict[str, Any] | None:
        """Return the latest payload for offline rendering, if available."""

        key = str(room_id or "")
        entry = self._dashboard_cache.get(key)
        if entry is None and key:
            entry = self._dashboard_cache.get("")
        if not isinstance(entry, dict) or not isinstance(entry.get("data"), dict):
            return None
        data = json.loads(json.dumps(entry["data"], ensure_ascii=False))
        saved_at = float(entry.get("saved_at") or 0)
        age_minutes = max(0, int((time.time() - saved_at) / 60)) if saved_at else 0
        self._mark_remote_presence_stale(data)
        data["_sync_offline"] = True
        data["_connection_state"] = "OFFLINE"
        data["data_source"] = "local_cache"
        data["_data_source"] = "local_cache"
        data["_sync_age_minutes"] = age_minutes
        try:
            data["_server_timestamp"] = datetime.fromtimestamp(saved_at).astimezone().isoformat() if saved_at else ""
        except (OSError, OverflowError, ValueError):
            data["_server_timestamp"] = ""
        data["_sync_error"] = self._last_error or "褰撳墠缃戠粶鏃犳硶璁块棶鑷範瀹ゆ湇鍔?
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

    def _raw(self, method: str, path: str, body: Any = None, *, authenticated: bool = False, extra_headers: dict[str, str] | None = None) -> Any:
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"apikey": self.key, "Content-Type": "application/json", "Accept": "application/json"}
        if authenticated:
            self._ensure_fresh()
            if not self.session:
                raise SocialError("璇峰厛鐧诲綍鎼瓙鑷範瀹ゃ€?)
            headers["Authorization"] = f"Bearer {self.session.access_token}"
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(f"{self.url}{path}", data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=_social_request_timeout()) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
                message = data.get("msg") or data.get("message") or data.get("error_description") or raw
            except json.JSONDecodeError:
                message = raw or str(exc)
            status = int(exc.code)
            kind = "auth" if status in (401, 403) else "server" if status >= 500 else "http"
            raise SocialError(
                str(message)[:300],
                kind=kind,
                endpoint=_endpoint_host(self.url),
                retryable=status >= 500,
                status=status,
            ) from exc
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise _network_error(exc, self.url) from exc

    def _accept_auth(self, data: dict[str, Any]) -> bool:
        token = data.get("access_token")
        if not token:
            return False
        user = data.get("user") or {}
        self.session = SocialSession(str(token), str(data.get("refresh_token", "")), str(user.get("id", "")), time.time() + int(data.get("expires_in", 3600)))
        self._save_session(); return True

    def _ensure_fresh(self) -> None:
        if not self.session or self.session.expires_at > time.time() + 90:
            return
        data = self._raw("POST", "/auth/v1/token?grant_type=refresh_token", {"refresh_token": self.session.refresh_token})
        if not self._accept_auth(data):
            self._clear_session()

    def sign_up(self, email: str, password: str, nickname: str) -> bool:
        # `redirect_to` is a query parameter for GoTrue's signup endpoint.  It
        # must also be present in Supabase Auth's allow-list; the production
        # project is configured with the same URL.  Passing it explicitly keeps
        # future dashboard changes from silently restoring localhost redirects.
        redirect = urllib.parse.urlencode({"redirect_to": self.email_redirect_url})
        data = self._raw(
            "POST",
            f"/auth/v1/signup?{redirect}",
            {"email": email.strip(), "password": password, "data": {"nickname": nickname.strip()[:24] or "鎼瓙"}},
        )
        return self._accept_auth(data)

    def sign_in(self, email: str, password: str) -> None:
        if self._http_backend is not None:
            return self._http_backend.sign_in(email, password)
        data = self._raw("POST", "/auth/v1/token?grant_type=password", {"email": email.strip(), "password": password})
        if not self._accept_auth(data):
            raise SocialError("鐧诲綍娌℃湁鎴愬姛锛岃妫€鏌ラ偖绠辩‘璁ゆ垨瀵嗙爜銆?)

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
                    room = self._raw(
                        "POST",
                        "/rest/v1/rpc/lili_room_dashboard",
                        {"p_room_id": room_id},
                        authenticated=True,
                    ) or {}
                    if isinstance(room, dict):
                        data.update(room)
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
            result = dict(data or {})
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

    def update_profile(self, *, nickname: str, visibility: str, show_exact_time: bool, allow_visits: bool, outfit_key: str = "") -> None:
        if self._http_backend is not None:
            return self._http_backend.update_profile(nickname=nickname, visibility=visibility, show_exact_time=show_exact_time, allow_visits=allow_visits, outfit_key=outfit_key)
        if not self.session:
            raise SocialError("璇峰厛鐧诲綍銆?)
        query = urllib.parse.urlencode({"user_id": f"eq.{self.session.user_id}"})
        clean = nickname.strip()[:24]
        self._raw("PATCH", f"/rest/v1/lili_profiles?{query}", {"nickname": clean or "鎼瓙", "owner_nickname": clean, "visibility": visibility, "show_exact_time": bool(show_exact_time), "allow_visits": bool(allow_visits), "outfit_key": outfit_key[:60], "updated_at": datetime.now().astimezone().isoformat()}, authenticated=True, extra_headers={"Prefer": "return=minimal"})

    def heartbeat(self, *, working: bool, today_seconds: int, session_started_at: str | None, outfit_key: str, room_id: str | None = None, quick_status: str = "", quick_status_expires_at: str | None = None) -> None:
        if self._http_backend is not None:
            return self._http_backend.heartbeat(working=working, today_seconds=today_seconds, session_started_at=session_started_at, outfit_key=outfit_key, room_id=room_id, quick_status=quick_status, quick_status_expires_at=quick_status_expires_at)
        if not self.session:
            return
        # Keep compatibility with the legacy direct client, but let the
        # server-side trigger own both freshness timestamps.
        body = {"user_id": self.session.user_id, "working": bool(working), "session_started_at": session_started_at, "focus_date": datetime.now().date().isoformat(), "today_seconds": min(86400, max(0, int(today_seconds))), "outfit_key": outfit_key[:60], "room_id": room_id, "quick_status": quick_status[:40], "quick_status_expires_at": quick_status_expires_at}
        self._raw("POST", "/rest/v1/lili_focus_presence?on_conflict=user_id", body, authenticated=True, extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"})

    def update_owner_nickname(self, nickname: str) -> None:
        if self._http_backend is not None:
            return self._http_backend.update_owner_nickname(nickname)
        if not self.session:
            raise SocialError("璇峰厛鐧诲綍銆?)
        query = urllib.parse.urlencode({"user_id": f"eq.{self.session.user_id}"})
        clean = nickname.strip()[:24]
        self._raw("PATCH", f"/rest/v1/lili_profiles?{query}", {"nickname": clean or "鎼瓙", "owner_nickname": clean, "updated_at": datetime.now().astimezone().isoformat()}, authenticated=True, extra_headers={"Prefer": "return=minimal"})

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
        config = json.loads(resource_path("config/social_backend.json").read_text(encoding="utf-8"))
        self.social_api_base_url = (os.environ.get("LILI_SOCIAL_API_BASE_URL", "").strip() or str(config.get("social_api_base_url", "")).strip()).rstrip("/")
        self.email_redirect_url = os.environ.get("LILI_AUTH_REDIRECT_URL", "").strip() or str(config.get("email_redirect_to", "")).strip()
        self.persist_tokens = persist_tokens
        self._dashboard_cache: dict[str, dict[str, Any]] = {}
        self._last_error = ""
        self.connection = ConnectionStateStore()
        self._http_backend: SocialBackend | None = backend
        if self._http_backend is None and self.social_api_base_url:
            self._http_backend = HttpSocialBackend(self.social_api_base_url, persist_tokens=persist_tokens, email_redirect_url=self.email_redirect_url)
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
            raise SocialError("鑷範瀹ゆ湇鍔″皻鏈厤缃€?, kind="config")
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
        root = Path(base) if base else Path.home() / ".desktop_pet"
        return root / "Lili" / "social-dashboard-cache.json"

    def _load_dashboard_cache(self) -> None:
        if not self.persist_tokens:
            return
        try:
            raw = json.loads(self._dashboard_cache_path().read_text(encoding="utf-8"))
            if isinstance(raw, dict): self._dashboard_cache = {str(k): v for k, v in raw.items() if isinstance(v, dict) and isinstance(v.get("data"), dict)}
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
        self._dashboard_cache[str(room_id or "")] = {"saved_at": time.time(), "data": json.loads(json.dumps(data, ensure_ascii=False))}
        self._save_dashboard_cache()

    @staticmethod
    def _mark_remote_presence_stale(data: dict[str, Any]) -> None:
        def mark(items: Any) -> None:
            if not isinstance(items, list): return
            for item in items:
                if not isinstance(item, dict) or item.get("is_self"): continue
                item.update({"online": False, "working": False, "status": "offline", "session_seconds": 0, "today_seconds": None, "stale_presence": True})
        mark(data.get("buddies")); mark(data.get("room_people")); mark(data.get("active_visits"))
        room = data.get("current_room")
        if isinstance(room, dict):
            mark(room.get("room_people"))
            if isinstance(room.get("room_summary"), dict): room["room_summary"]["focus_count"] = 0
        if isinstance(data.get("room_summary"), dict): data["room_summary"]["focus_count"] = 0

    def cached_dashboard(self, room_id: str | None = None) -> dict[str, Any] | None:
        key = str(room_id or ""); entry = self._dashboard_cache.get(key) or (self._dashboard_cache.get("") if key else None)
        if not isinstance(entry, dict) or not isinstance(entry.get("data"), dict): return None
        data = json.loads(json.dumps(entry["data"], ensure_ascii=False)); saved_at = float(entry.get("saved_at") or 0)
        self._mark_remote_presence_stale(data); data.update({"_sync_offline": True, "_connection_state": "OFFLINE", "data_source": "local_cache", "_data_source": "local_cache", "_sync_age_minutes": max(0, int((time.time() - saved_at) / 60)) if saved_at else 0, "_sync_error": self._last_error or "褰撳墠缃戠粶鏃犳硶璁块棶鑷範瀹ゆ湇鍔?})
        return data

    def diagnose_connection(self, room_id: str | None = None) -> dict[str, Any]:
        checks = {"edge_function": {"ok": False}, "authentication": {"ok": self.signed_in}, "room_snapshot": {"ok": False}, "presence": {"ok": False}, "realtime": {"ok": False}}
        try:
            health = self.health(); checks["edge_function"] = {"ok": True, "backend": health.get("backend", "supabase"), "transport": "https-rest"}
        except SocialError as exc:
            self.connection.set("OFFLINE", data_source="local_cache", realtime_state="unavailable")
            return {"connection_state": "OFFLINE", "data_source": "local_cache", "realtime_state": "unavailable", "backend": self.backend_name, "service": self.backend_endpoint, "checks": checks, "dashboard": self.cached_dashboard(room_id), "error": str(exc)}
        if not self.signed_in:
            self.connection.set("DEGRADED", data_source="local_live", realtime_state="not_authenticated")
            return {"connection_state": "DEGRADED", "data_source": "local_live", "realtime_state": "not_authenticated", "backend": self.backend_name, "service": self.backend_endpoint, "checks": checks, "dashboard": None}
        try:
            snapshot = self.dashboard(room_id, allow_cache=False); checks["room_snapshot"] = {"ok": True}; checks["presence"] = {"ok": True}; checks["realtime"] = {"ok": True, "mode": "desktop low-frequency polling"}
            return {"connection_state": "ONLINE", "data_source": "server", "realtime_state": "polling", "backend": self.backend_name, "service": self.backend_endpoint, "checks": checks, "dashboard": snapshot}
        except SocialError as exc:
            cached = self.cached_dashboard(room_id); self.connection.set("OFFLINE", data_source="local_cache", realtime_state="unavailable")
            return {"connection_state": "OFFLINE", "data_source": "local_cache" if cached else "none", "realtime_state": "unavailable", "backend": self.backend_name, "service": self.backend_endpoint, "checks": checks, "dashboard": cached, "error": str(exc)}

    def sign_up(self, email: str, password: str, nickname: str) -> bool:
        data = self._require_backend().sign_up(email, password, nickname)
        return bool(data)

    def sign_in(self, email: str, password: str) -> None:
        self._require_backend().sign_in(email, password)

    def sign_out(self) -> None:
        backend = self._http_backend
        if backend is not None: backend.sign_out()

    def dashboard(self, room_id: str | None = None, *, allow_cache: bool = True) -> dict[str, Any]:
        self.connection.set("CONNECTING", data_source=self.connection.data_source, realtime_state="polling")
        try:
            result = dict(self._require_backend().dashboard(room_id=room_id, allow_cache=allow_cache) or {})
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
    def update_profile(self, *, nickname: str, visibility: str, show_exact_time: bool, allow_visits: bool, outfit_key: str = "") -> None: self._require_backend().update_profile(nickname=nickname, visibility=visibility, show_exact_time=show_exact_time, allow_visits=allow_visits, outfit_key=outfit_key)
    def update_owner_nickname(self, nickname: str) -> None: self._require_backend().update_owner_nickname(nickname)
    def heartbeat(self, **kwargs: Any) -> None: self._require_backend().heartbeat(**kwargs)
    def send_interaction(self, **kwargs: Any) -> None: self._require_backend().send_interaction(**kwargs)
    def record_room_event(self, **kwargs: Any) -> None: self._require_backend().record_room_event(**kwargs)
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
    AUTH_METHODS = {"sign_in", "sign_up"}
    BUSINESS_METHODS = {"dashboard", "rpc", "update_profile", "update_owner_nickname", "heartbeat", "send_interaction", "record_room_event", "set_room_goal", "set_room_schedule", "set_room_challenge", "set_buddy_subscription", "leave_room", "sign_up", "sign_in"}
    DIRECT_RECOVERY_INTERVAL_SECONDS = 60.0

    def __init__(self, direct: HttpSocialBackend, proxy: HttpSocialBackend, *, persist_state: bool = True) -> None:
        self.direct = direct
        self.proxy = proxy
        self.persist_state = persist_state
        self.current_route = self.DIRECT_SUPABASE
        self.last_route_hint = self.DIRECT_SUPABASE
        self.last_latency_ms: float | None = None
        self.failure_count = 0
        self.success_count = 0
        self.last_switch_at = ""
        self._last_direct_probe = 0.0
        self._direct_recovery_successes = 0
        self._load_state()
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
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({
                "route": self.current_route,
                "last_latency_ms": self.last_latency_ms,
                "last_switch_at": self.last_switch_at,
                "failure_count": self.failure_count,
                "success_count": self.success_count,
            }, ensure_ascii=False), encoding="utf-8")
        except OSError:
            return

    @property
    def signed_in(self) -> bool:
        return bool(self.direct.session or self.proxy.session)

    @property
    def active(self) -> HttpSocialBackend:
        return self.direct if self.current_route == self.DIRECT_SUPABASE else self.proxy

    @property
    def backend_name(self) -> str:
        return "Supabase Direct" if self.current_route == self.DIRECT_SUPABASE else "CloudBase Proxy"

    @property
    def backend_endpoint(self) -> str:
        return self.active.base_url

    @staticmethod
    def _is_network_failure(exc: SocialError) -> bool:
        return exc.kind in BackendRouteManager.NETWORK_KINDS or exc.status in {502, 503, 504}

    @staticmethod
    def _sync_sessions(source: HttpSocialBackend, target: HttpSocialBackend) -> None:
        target.session = source.session

    def _switch(self, route: str) -> None:
        if self.current_route != route:
            self.current_route = route
            self.last_switch_at = datetime.now().astimezone().isoformat()
            self._save_state()

    def _mark_success(self, started: float) -> None:
        self.last_latency_ms = round((time.monotonic() - started) * 1000, 1)
        self.success_count += 1
        self.failure_count = 0
        self._save_state()

    def _mark_failure(self) -> None:
        self.failure_count += 1
        self._save_state()

    def _probe_direct_recovery(self) -> None:
        if self.current_route != self.CLOUDBASE_PROXY or time.monotonic() - self._last_direct_probe < self.DIRECT_RECOVERY_INTERVAL_SECONDS:
            return
        self._last_direct_probe = time.monotonic()
        try:
            self.direct.health()
            self._direct_recovery_successes += 1
            if self._direct_recovery_successes >= 2:
                self._switch(self.DIRECT_SUPABASE)
        except SocialError as exc:
            if self._is_network_failure(exc):
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
        self._switch(self.CLOUDBASE_PROXY)
        return self.proxy

    def _request_auth(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Authenticate Direct-first, falling back only on network failure.

        A failed login must not be submitted twice to Supabase.  We probe the
        route first, then send credentials once on that route; a network error
        may retry once on the proxy, while 401/403 and validation errors stop.
        """
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
            if not (backend is self.direct and self._is_network_failure(first)):
                if self._is_network_failure(first):
                    self._mark_failure()
                raise
            self._mark_failure()
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
        config = json.loads(resource_path("config/social_backend.json").read_text(encoding="utf-8"))
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
            direct = HttpSocialBackend(supabase_url, client_key=supabase_key, persist_tokens=persist_tokens, email_redirect_url=str(config.get("email_redirect_to", "")), transport="direct")
            proxy = HttpSocialBackend(proxy_url, client_key="", persist_tokens=False, email_redirect_url=str(config.get("email_redirect_to", "")), transport="proxy")
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
        return getattr(active, "session", None)

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
            self.connection.set("OFFLINE", data_source="local_cache" if cached else "none", realtime_state="unavailable")
            return {"connection_state": "OFFLINE", "data_source": "local_cache" if cached else "none", "realtime_state": "unavailable", "backend": self.backend_name, "service": self.backend_endpoint, "checks": checks, "dashboard": cached, "error": str(exc)}

    def sign_up(self, email: str, password: str, nickname: str) -> bool:
        return bool(self._manager.request("sign_up", email, password, nickname))

    def sign_in(self, email: str, password: str) -> None:
        # Treat sign-in as an account switch.  Do not let a failed attempt
        # leave a previous user's token active in the desktop session.
        self.sign_out()
        self._manager.request("sign_in", email, password)

    def sign_out(self) -> None:
        backend = self._manager.direct if hasattr(self._manager, "direct") else None
        if backend is not None: backend._clear_session()
        self._manager.proxy.session = None

    def dashboard(self, room_id: str | None = None, *, allow_cache: bool = True) -> dict[str, Any]:
        self.connection.set("CONNECTING", data_source=self.connection.data_source, realtime_state="polling")
        try:
            result = dict(self._manager.request("dashboard", room_id=room_id) or {})
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
    def set_room_goal(self, **kwargs: Any) -> None: self._manager.request("set_room_goal", **kwargs)
    def set_room_schedule(self, **kwargs: Any) -> None: self._manager.request("set_room_schedule", **kwargs)
    def set_room_challenge(self, **kwargs: Any) -> None: self._manager.request("set_room_challenge", **kwargs)
    def set_buddy_subscription(self, **kwargs: Any) -> None: self._manager.request("set_buddy_subscription", **kwargs)
    def leave_room(self, **kwargs: Any) -> None: self._manager.request("leave_room", **kwargs)


SocialClient = SupabaseFirstSocialClient

