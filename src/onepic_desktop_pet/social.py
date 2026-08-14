"""Lili æ­å­è‡ªä¹ å®¤çš„æœ€å°ç¤¾äº¤å®¢æˆ·ç«¯ä¸Žå¯æ›¿æ¢ç½‘ç»œåŽç«¯ã€‚

åªå‘é€è´¦å·è®¤è¯ã€æ˜µç§°ã€å…­æ¯›å¤–è§‚ã€å·¥ä½œçŠ¶æ€ã€ç´¯è®¡ç§’æ•°ã€æˆ¿é—´ä¸Žä¸²é—¨äº‹ä»¶ã€‚å¯†ç ä»Žä¸ä¿å­˜ï¼›
åˆ·æ–°ä»¤ç‰Œä¿å­˜åœ¨ç³»ç»Ÿå‡­æ®åº“ã€‚ç½‘ç»œå¤±è´¥ä¸ä¼šå½±å“ç¦»çº¿æ¡Œå® ã€è®¡æ—¶ã€AI æˆ–æœ¬åœ°ç´ æã€‚
"""

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
from datetime import datetime
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
    """é¢å‘ç”¨æˆ·çš„ç¤¾äº¤ç½‘ç»œé”™è¯¯ï¼Œå¹¶ä¿ç•™å¯è®°å½•çš„è¯Šæ–­åˆ†ç±»ã€‚"""

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
    return parsed.netloc or "æœªé…ç½®"


def _network_error(exc: BaseException, base_url: str) -> SocialError:
    """æŠŠ urllib/Windows é”™è¯¯è½¬æˆç”¨æˆ·èƒ½é‡‡å–è¡ŒåŠ¨çš„åˆ†ç±»ã€‚"""

    reason = getattr(exc, "reason", exc)
    host = _endpoint_host(base_url)
    if isinstance(reason, socket.gaierror) or "getaddrinfo" in str(reason).lower():
        return SocialError(f"DNS è§£æžå¤±è´¥ï¼šæ‰¾ä¸åˆ°è‡ªä¹ å®¤æœåŠ¡å™¨ï¼ˆ{host}ï¼‰ã€‚", kind="dns", endpoint=host, retryable=True)
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).lower():
        return SocialError(f"è¿žæŽ¥è¶…æ—¶ï¼šè‡ªä¹ å®¤æœåŠ¡å™¨ï¼ˆ{host}ï¼‰æ²¡æœ‰åŠæ—¶å›žåº”ã€‚", kind="timeout", endpoint=host, retryable=True)
    if isinstance(reason, ConnectionRefusedError) or "refused" in str(reason).lower():
        return SocialError(f"æœåŠ¡å™¨æ‹’ç»è¿žæŽ¥ï¼šè¯·æ£€æŸ¥è‡ªä¹ å®¤ä¸­è½¬æœåŠ¡ï¼ˆ{host}ï¼‰æ˜¯å¦åœ¨çº¿ã€‚", kind="refused", endpoint=host, retryable=True)
    if isinstance(reason, ssl.SSLError) or "ssl" in str(reason).lower() or "certificate" in str(reason).lower():
        return SocialError(f"TLS/è¯ä¹¦è¿žæŽ¥å¤±è´¥ï¼šæ— æ³•å®‰å…¨è¿žæŽ¥è‡ªä¹ å®¤æœåŠ¡å™¨ï¼ˆ{host}ï¼‰ã€‚", kind="tls", endpoint=host)
    return SocialError(f"ç½‘ç»œä¸å¯è¾¾ï¼šæ— æ³•è¿žæŽ¥è‡ªä¹ å®¤æœåŠ¡å™¨ï¼ˆ{host}ï¼‰ã€‚", kind="network", endpoint=host, retryable=True)


def _social_request_timeout() -> float:
    """Keep an unreachable Supabase endpoint from freezing a user action."""

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

    The UI only needs these operations; it must not know whether the server
    is Supabase or our future lightweight Social API proxy.
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
    """Small REST backend for ``social_api_base_url``.

    This deliberately contains no Supabase table paths or service-role
    credentials.  A proxy can expose these stable routes and delegate to the
    existing Supabase project without requiring a desktop update.
    """

    SERVICE_NAME = "LiliSocial"
    ACCOUNT_NAME = "http-social-session"

    def __init__(self, base_url: str, *, client_key: str = "", persist_tokens: bool = True, email_redirect_url: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.client_key = client_key
        self.persist_tokens = persist_tokens
        self.email_redirect_url = email_redirect_url
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

    def _raw(self, method: str, path: str, body: Any = None, *, authenticated: bool = False) -> Any:
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.client_key:
            headers["X-Client-Key"] = self.client_key
        if authenticated:
            self._ensure_fresh()
            if not self.session:
                raise SocialError("è¯·å…ˆç™»å½•æ­å­è‡ªä¹ å®¤ã€‚")
            headers["Authorization"] = f"Bearer {self.session.access_token}"
        request = urllib.request.Request(f"{self.base_url}{path}", data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=_social_request_timeout()) as response:
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
        data = self._raw("POST", "/auth/refresh", {"refresh_token": self.session.refresh_token})
        if not self._accept_auth(data):
            self._clear_session()

    def sign_up(self, email: str, password: str, nickname: str) -> bool:
        if self._http_backend is not None:
            return self._http_backend.sign_up(email, password, nickname)
        body = {"email": email.strip(), "password": password, "nickname": nickname.strip()[:24] or "æ­å­", "data": {"nickname": nickname.strip()[:24] or "æ­å­"}}
        if self.email_redirect_url:
            body["redirect_to"] = self.email_redirect_url
        return self._accept_auth(self._raw("POST", "/auth/signup", body))

    def sign_in(self, email: str, password: str) -> None:
        data = self._raw("POST", "/auth/signin", {"email": email.strip(), "password": password})
        if not self._accept_auth(data):
            raise SocialError("ç™»å½•æ²¡æœ‰æˆåŠŸï¼Œè¯·æ£€æŸ¥é‚®ç®±ç¡®è®¤æˆ–å¯†ç ã€‚")

    def sign_out(self) -> None:
        self._clear_session()

    def health(self) -> dict[str, Any]:
        """Check the relay without requiring a user session."""
        return dict(self._raw("GET", "/health") or {})

    def dashboard(self, room_id: str | None = None, *, allow_cache: bool = True) -> dict[str, Any]:
        data = self._raw("GET", "/dashboard", authenticated=True) or {}
        if room_id:
            room = self._raw("GET", f"/rooms/{urllib.parse.quote(str(room_id), safe='')}", authenticated=True) or {}
            if isinstance(room, dict):
                data.update(room)
        return data

    def rpc(self, name: str, body: dict[str, Any]) -> Any:
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
        self._raw("PATCH", "/profile", {"nickname": nickname.strip()[:24] or "æ­å­", "owner_nickname": nickname.strip()[:24], "visibility": visibility, "show_exact_time": bool(show_exact_time), "allow_visits": bool(allow_visits), "outfit_key": outfit_key[:60]}, authenticated=True)

    def update_owner_nickname(self, nickname: str) -> None:
        self._raw("PATCH", "/profile", {"nickname": nickname.strip()[:24] or "æ­å­", "owner_nickname": nickname.strip()[:24]}, authenticated=True)

    def heartbeat(self, *, working: bool, today_seconds: int, session_started_at: str | None, outfit_key: str, room_id: str | None = None, quick_status: str = "", quick_status_expires_at: str | None = None) -> None:
        if not self.session:
            return
        now = datetime.now().astimezone()
        self._raw("POST", "/presence/heartbeat", {"working": bool(working), "today_seconds": min(86400, max(0, int(today_seconds))), "session_started_at": session_started_at, "focus_date": now.date().isoformat(), "outfit_key": outfit_key[:60], "room_id": room_id, "quick_status": quick_status[:40], "quick_status_expires_at": quick_status_expires_at, "last_seen": now.isoformat()}, authenticated=True)

    def send_interaction(self, *, target: str, kind: str, room_id: str | None = None) -> None:
        self._raw(
            "POST",
            "/rooms/interaction",
            {"p_target": target, "p_kind": kind, "p_room_id": room_id},
            authenticated=True,
        )

    def set_room_goal(self, *, room_id: str, title: str, target_seconds: int, due_at: str | None = None) -> None:
        self.rpc("lili_set_room_goal", {"p_room_id": room_id, "p_title": title, "p_target_seconds": int(target_seconds), "p_dßžö¶‰žËkºwµçy‘•Í­Ñ½Á}Á•Ðˆ(€€€€€€€É•ÑÕÉ¸É½½Ð€¼€‰1¥±¤ˆ€¼€‰Í½¥…°µ‘…Í¡‰½…Éµ…¡”¹©Í½¸ˆ((€€€‘•˜}±½…‘}‘…Í¡‰½…É‘}…¡”¡Í•±˜¤€´ø9½¹”è(€€€€€€€¥˜¹½ÐÍ•±˜¹Á•ÉÍ¥ÍÑ}Ñ½­•¹Ìè(€€€€€€€€€€€É•ÑÕÉ¸(€€€€€€€ÑÉäè(€€€€€€€€€€€É…Ü€ô©Í½¸¹±½…‘Ì¡Í•±˜¹}‘…Í¡‰½…É‘}…¡•}Á…Ñ  ¤¹É•…‘}Ñ•áÐ¡•¹½‘¥¹œô‰ÕÑ˜´àˆ¤¤(€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡É…Ü°‘¥Ð¤è(€€€€€€€€€€€€€€€Í•±˜¹}‘…Í¡‰½…É‘}…¡”€ôì(€€€€€€€€€€€€€€€€€€€ÍÑÈ¡­•ä¤èÙ…±Õ”(€€€€€€€€€€€€€€€€€€€™½È­•ä°Ù…±Õ”¥¸É…Ü¹¥Ñ•µÌ ¤(€€€€€€€€€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡Ù…±Õ”°‘¥Ð¤…¹¥Í¥¹ÍÑ…¹”¡Ù…±Õ”¹•Ð ‰‘…Ñ„ˆ¤°‘¥Ð¤(€€€€€€€€€€€€€€€ô(€€€€€€€•á•ÁÐ€¡=MÉÉ½È°Y…±Õ•ÉÉ½È°QåÁ•ÉÉ½È¤è(€€€€€€€€€€€Í•±˜¹}‘…Í¡‰½…É‘}…¡”€ôíô((€€€‘•˜}Í…Ù•}‘…Í¡‰½…É‘}…¡”¡Í•±˜¤€´ø9½¹”è(€€€€€€€¥˜¹½ÐÍ•±˜¹Á•ÉÍ¥ÍÑ}Ñ½­•¹Ìè(€€€€€€€€€€€É•ÑÕÉ¸(€€€€€€€Ñ…É•Ð€ôÍ•±˜¹}‘…Í¡‰½…É‘}…¡•}Á…Ñ  ¤(€€€€€€€Ñ•µÁ½É…Éä€ôÑ…É•Ð¹Ý¥Ñ¡}ÍÕ™™¥à ˆ¹©Í½¸¹ÑµÀˆ¤(€€€€€€€ÑÉäè(€€€€€€€€€€€Ñ…É•Ð¹Á…É•¹Ð¹µ­‘¥È¡Á…É•¹ÑÌõQÉÕ”°•á¥ÍÑ}½¬õQÉÕ”¤(€€€€€€€€€€€Ñ•µÁ½É…Éä¹ÝÉ¥Ñ•}Ñ•áÐ (€€€€€€€€€€€€€€€©Í½¸¹‘ÕµÁÌ¡Í•±˜¹}‘…Í¡‰½…É‘}…¡”°•¹ÍÕÉ•}…Í¥¤õ…±Í”¤°(€€€€€€€€€€€€€€€•¹½‘¥¹œô‰ÕÑ˜´àˆ°(€€€€€€€€€€€€¤(€€€€€€€€€€€Ñ•µÁ½É…Éä¹É•Á±…”¡Ñ…É•Ð¤(€€€€€€€•á•ÁÐ=MÉÉ½Èè(€€€€€€€€€€€€Œ…¡”™…¥±ÕÉ”µÕÍÐ¹•Ù•È‰É•…¬Ñ¡”±¥Ù”Íå¹ŒÁ…Ñ ¸(€€€€€€€€€€€É•ÑÕÉ¸((€€€‘•˜}É•µ•µ‰•É}‘…Í¡‰½…É¡Í•±˜°É½½µ}¥èÍÑÈð9½¹”°‘…Ñ„è‘¥ÑmÍÑÈ°¹åt¤€´ø9½¹”è(€€€€€€€­•ä€ôÍÑÈ¡É½½µ}¥½È€ˆˆ¤(€€€€€€€Í•±˜¹}‘…Í¡‰½…É‘}…¡•m­•åt€ôì(€€€€€€€€€€€€‰Í…Ù•‘}…ÐˆèÑ¥µ”¹Ñ¥µ” ¤°(€€€€€€€€€€€€‰‘…Ñ„ˆè©Í½¸¹±½…‘Ì¡©Í½¸¹‘ÕµÁÌ¡‘…Ñ„°•¹ÍÕÉ•}…Í¥¤õ…±Í”¤¤°(€€€€€€€ô(€€€€€€€Í•±˜¹}Í…Ù•}‘…Í¡‰½…É‘}…¡” ¤((€€€‘•˜…¡•‘}‘…Í¡‰½…É¡Í•±˜°É½½µ}¥èÍÑÈð9½¹”€ô9½¹”¤€´ø‘¥ÑmÍÑÈ°¹åtð9½¹”è(€€€€€€€€ˆˆ‰I•ÑÕÉ¸Ñ¡”±…Ñ•ÍÐÁ…å±½…™½È½™™±¥¹”É•¹‘•É¥¹œ°¥˜…Ù…¥±…‰±”¸ˆˆˆ((€€€€€€€­•ä€ôÍÑÈ¡É½½µ}¥½È€ˆˆ¤(€€€€€€€•¹ÑÉä€ôÍ•±˜¹}‘…Í¡‰½…É‘}…¡”¹•Ð¡­•ä¤(€€€€€€€¥˜•¹ÑÉä¥Ì9½¹”…¹­•äè(€€€€€€€€€€€•¹ÑÉä€ôÍ•±˜¹}‘…Í¡‰½…É‘}…¡”¹•Ð ˆˆ¤(€€€€€€€¥˜¹½Ð¥Í¥¹ÍÑ…¹”¡•¹ÑÉä°‘¥Ð¤½È¹½Ð¥Í¥¹ÍÑ…¹”¡•¹ÑÉä¹•Ð ‰‘…Ñ„ˆ¤°‘¥Ð¤è(€€€€€€€€€€€É•ÑÕÉ¸9½¹”(€€€€€€€‘…Ñ„€ô©Í½¸¹±½…‘Ì¡©Í½¸¹‘ÕµÁÌ¡•¹ÑÉål‰‘…Ñ„‰t°•¹ÍÕÉ•}…Í¥¤õ…±Í”¤¤(€€€€€€€Í…Ù•‘}…Ð€ô™±½…Ð¡•¹ÑÉä¹•Ð ‰Í…Ù•‘}…Ðˆ¤½È€À¤(€€€€€€€…•}µ¥¹ÕÑ•Ì€ôµ…à À°¥¹Ð ¡Ñ¥µ”¹Ñ¥µ” ¤€´Í…Ù•‘}…Ð¤€¼€ØÀ¤¤¥˜Í…Ù•‘}…Ð•±Í”€À(€€€€€€€Í•±˜¹}µ…É­}É•µ½Ñ•}ÁÉ•Í•¹•}ÍÑ…±”¡‘…Ñ„¤(€€€€€€€‘…Ñ…l‰}Íå¹}½™™±¥¹”‰t€ôQÉÕ”(€€€€€€€‘…Ñ…l‰}½¹¹•Ñ¥½¹}ÍÑ…Ñ”‰t€ô€‰=1%9ˆ(€€€€€€€‘…Ñ…l‰‘…Ñ…}Í½ÕÉ”‰t€ô€‰±½…±}…¡”ˆ(€€€€€€€‘…Ñ…l‰}‘…Ñ…}Í½ÕÉ”‰t€ô€‰±½…±}…¡”ˆ(€€€€€€€‘…Ñ…l‰}Íå¹}…•}µ¥¹ÕÑ•Ì‰t€ô…•}µ¥¹ÕÑ•Ì(€€€€€€€ÑÉäè(€€€€€€€€€€€‘…Ñ…l‰}Í•ÉÙ•É}Ñ¥µ•ÍÑ…µÀ‰t€ô‘…Ñ•Ñ¥µ”¹™É½µÑ¥µ•ÍÑ…µÀ¡Í…Ù•‘}…Ð¤¹…ÍÑ¥µ•é½¹” ¤¹¥Í½™½Éµ…Ð ¤¥˜Í…Ù•‘}…Ð•±Í”€ˆˆ(€€€€€€€•á•ÁÐ€¡=MÉÉ½È°=Ù•É™±½ÝÉÉ½È°Y…±Õ•ÉÉ½È¤è(€€€€€€€€€€€‘…Ñ…l‰}Í•ÉÙ•É}Ñ¥µ•ÍÑ…µÀ‰t€ô€ˆˆ(€€€€€€€‘…Ñ…l‰}Íå¹}•ÉÉ½È‰t€ôÍ•±˜¹}±…ÍÑ}•ÉÉ½È½È€‹–öO–&7žöGžîsš^ƒšÎW¢ºÿ¦^»¢«’æƒ–º“šr7–*„ˆ(€€€€€€€É•ÑÕÉ¸‘…Ñ„((€€€ÍÑ…Ñ¥µ•Ñ¡½(€€€‘•˜}µ…É­}É•µ½Ñ•}ÁÉ•Í•¹•}ÍÑ…±”¡‘…Ñ„è‘¥ÑmÍÑÈ°¹åt¤€´ø9½¹”è(€€€€€€€€ˆˆ‰9•Ù•ÈÉ•¹‘•È…¡•É•µ½Ñ”ÁÉ•Í•¹”…ÌÕÉÉ•¹Ð½¹±¥¹”…Ñ¥Ù¥Ñä¸ˆˆˆ((€€€€€€€‘•˜µ…É¬¡¥Ñ•µÌè¹ä¤€´ø9½¹”è(€€€€€€€€€€€¥˜¹½Ð¥Í¥¹ÍÑ…¹”¡¥Ñ•µÌ°±¥ÍÐ¤è(€€€€€€€€€€€€€€€É•ÑÕÉ¸(€€€€€€€€€€€™½È¥Ñ•´¥¸¥Ñ•µÌè(€€€€€€€€€€€€€€€¥˜¹½Ð¥Í¥¹ÍÑ…¹”¡¥Ñ•´°‘¥Ð¤½È¥Ñ•´¹•Ð ‰¥Í}Í•±˜ˆ¤è(€€€€€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€€€€€€€€€¥Ñ•µl‰½¹±¥¹”‰t€ô…±Í”(€€€€€€€€€€€€€€€¥Ñ•µl‰Ý½É­¥¹œ‰t€ô…±Í”(€€€€€€€€€€€€€€€¥Ñ•µl‰ÍÑ…ÑÕÌ‰t€ô€‰½™™±¥¹”ˆ(€€€€€€€€€€€€€€€¥Ñ•µl‰Í•ÍÍ¥½¹}Í•½¹‘Ì‰t€ô€À(€€€€€€€€€€€€€€€¥Ñ•µl‰Ñ½‘…å}Í•½¹‘Ì‰t€ô9½¹”(€€€€€€€€€€€€€€€¥Ñ•µl‰ÍÑ…±•}ÁÉ•Í•¹”‰t€ôQÉÕ”((€€€€€€€µ…É¬¡‘…Ñ„¹•Ð ‰‰Õ‘‘¥•Ìˆ¤¤(€€€€€€€µ…É¬¡‘…Ñ„¹•Ð ‰É½½µ}Á•½Á±”ˆ¤¤(€€€€€€€µ…É¬¡‘…Ñ„¹•Ð ‰…Ñ¥Ù•}Ù¥Í¥ÑÌˆ¤¤(€€€€€€€É½½´€ô‘…Ñ„¹•Ð ‰ÕÉÉ•¹Ñ}É½½´ˆ¤(€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡É½½´°‘¥Ð¤è(€€€€€€€€€€€µ…É¬¡É½½´¹•Ð ‰É½½µ}Á•½Á±”ˆ¤¤(€€€€€€€€€€€ÍÕµµ…Éä€ôÉ½½´¹•Ð ‰É½½µ}ÍÕµµ…Éäˆ¤(€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡ÍÕµµ…Éä°‘¥Ð¤è(€€€€€€€€€€€€€€€ÍÕµµ…Éål‰™½ÕÍ}½Õ¹Ð‰t€ô€À(€€€€€€€ÍÕµµ…Éä€ô‘…Ñ„¹•Ð ‰É½½µ}ÍÕµµ…Éäˆ¤(€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡ÍÕµµ…Éä°‘¥Ð¤è(€€€€€€€€€€€ÍÕµµ…Éål‰™½ÕÍ}½Õ¹Ð‰t€ô€À((€€€‘•˜}É…Ü¡Í•±˜°µ•Ñ¡½èÍÑÈ°Á…Ñ èÍÑÈ°‰½‘äè¹ä€ô9½¹”°€¨°…ÕÑ¡•¹Ñ¥…Ñ•è‰½½°€ô…±Í”°•áÑÉ…}¡•…‘•ÉÌè‘¥ÑmÍÑÈ°ÍÑÉtð9½¹”€ô9½¹”¤€´ø¹äè(€€€€€€€Á…å±½…€ô9½¹”¥˜‰½‘ä¥Ì9½¹”•±Í”©Í½¸¹‘ÕµÁÌ¡‰½‘ä°•¹ÍÕÉ•}…Í¥¤õ…±Í”¤¹•¹½‘” ‰ÕÑ˜´àˆ¤(€€€€€€€¡•…‘•ÉÌ€ôì‰…Á¥­•äˆèÍ•±˜¹­•ä°€‰½¹Ñ•¹ÐµQåÁ”ˆè€‰…ÁÁ±¥…Ñ¥½¸½©Í½¸ˆ°€‰•ÁÐˆè€‰…ÁÁ±¥…Ñ¥½¸½©Í½¸‰ô(€€€€€€€¥˜…ÕÑ¡•¹Ñ¥…Ñ•è(€€€€€€€€€€€Í•±˜¹}•¹ÍÕÉ•}™É•Í  ¤(€€€€€€€€€€€¥˜¹½ÐÍ•±˜¹Í•ÍÍ¥½¸è(€€€€€€€€€€€€€€€É…¥Í”M½¥…±ÉÉ½È ‹¢¾ß–#žfï–öWšB·–¶C¢«’æƒ–º“Žˆ¤(€€€€€€€€€€€¡•…‘•ÉÍl‰ÕÑ¡½É¥é…Ñ¥½¸‰t€ô˜‰	•…É•ÈíÍ•±˜¹Í•ÍÍ¥½¸¹…•ÍÍ}Ñ½­•¹ôˆ(€€€€€€€¥˜•áÑÉ…}¡•…‘•ÉÌè(€€€€€€€€€€€¡•…‘•ÉÌ¹ÕÁ‘…Ñ”¡•áÑÉ…}¡•…‘•ÉÌ¤(€€€€€€€É•ÅÕ•ÍÐ€ôÕÉ±±¥ˆ¹É•ÅÕ•ÍÐ¹I•ÅÕ•ÍÐ¡˜‰íÍ•±˜¹ÕÉ±õíÁ…Ñ¡ôˆ°‘…Ñ„õÁ…å±½…°¡•…‘•ÉÌõ¡•…‘•ÉÌ°µ•Ñ¡½õµ•Ñ¡½¤(€€€€€€€ÑÉäè(€€€€€€€€€€€Ý¥Ñ ÕÉ±±¥ˆ¹É•ÅÕ•ÍÐ¹ÕÉ±½Á•¸¡É•ÅÕ•ÍÐ°Ñ¥µ•½ÕÐõ}Í½¥…±}É•ÅÕ•ÍÑ}Ñ¥µ•½ÕÐ ¤¤…ÌÉ•ÍÁ½¹Í”è(€€€€€€€€€€€€€€€É…Ü€ôÉ•ÍÁ½¹Í”¹É•… ¤(€€€€€€€€€€€€€€€É•ÑÕÉ¸©Í½¸¹±½…‘Ì¡É…Ü¹‘•½‘” ‰ÕÑ˜´àˆ¤¤¥˜É…Ü•±Í”9½¹”(€€€€€€€•á•ÁÐÕÉ±±¥ˆ¹•ÉÉ½È¹!QQAÉÉ½È…Ì•áŒè(€€€€€€€€€€€É…Ü€ô•áŒ¹É•… ¤¹‘•½‘” ‰ÕÑ˜´àˆ°•ÉÉ½ÉÌô‰É•Á±…”ˆ¤(€€€€€€€€€€€ÑÉäè(€€€€€€€€€€€€€€€‘…Ñ„€ô©Í½¸¹±½…‘Ì¡É…Ü¤(€€€€€€€€€€€€€€€µ•ÍÍ…”€ô‘…Ñ„¹•Ð ‰µÍœˆ¤½È‘…Ñ„¹•Ð ‰µ•ÍÍ…”ˆ¤½È‘…Ñ„¹•Ð ‰•ÉÉ½É}‘•ÍÉ¥ÁÑ¥½¸ˆ¤½ÈÉ…Ü(€€€€€€€€€€€•á•ÁÐ©Í½¸¹)M=9•½‘•ÉÉ½Èè(€€€€€€€€€€€€€€€µ•ÍÍ…”€ôÉ…Ü½ÈÍÑÈ¡•áŒ¤(€€€€€€€€€€€ÍÑ…ÑÕÌ€ô¥¹Ð¡•áŒ¹½‘”¤(€€€€€€€€€€€­¥¹€ô€‰…ÕÑ ˆ¥˜ÍÑ…ÑÕÌ¥¸€ ÐÀÄ°€ÐÀÌ¤•±Í”€‰Í•ÉÙ•Èˆ¥˜ÍÑ…ÑÕÌ€øô€ÔÀÀ•±Í”€‰¡ÑÑÀˆ(€€€€€€€€€€€É…¥Í”M½¥…±ÉÉ½È (€€€€€€€€€€€€€€€ÍÑÈ¡µ•ÍÍ…”¥lèÌÀÁt°(€€€€€€€€€€€€€€€­¥¹õ­¥¹°(€€€€€€€€€€€€€€€•¹‘Á½¥¹Ðõ}•¹‘Á½¥¹Ñ}¡½ÍÐ¡Í•±˜¹ÕÉ°¤°(€€€€€€€€€€€€€€€É•ÑÉå…‰±”õÍÑ…ÑÕÌ€øô€ÔÀÀ°(€€€€€€€€€€€€€€€ÍÑ…ÑÕÌõÍÑ…ÑÕÌ°(€€€€€€€€€€€€¤™É½´•áŒ(€€€€€€€•á•ÁÐ€¡=MÉÉ½È°ÕÉ±±¥ˆ¹•ÉÉ½È¹UI1ÉÉ½È°Q¥µ•½ÕÑÉÉ½È¤…Ì•áŒè(€€€€€€€€€€€É…¥Í”}¹•ÑÝ½É­}•ÉÉ½È¡•áŒ°Í•±˜¹ÕÉ°¤™É½´•áŒ((€€€‘•˜}…•ÁÑ}…ÕÑ ¡Í•±˜°‘…Ñ„è‘¥ÑmÍÑÈ°¹åt¤€´ø‰½½°è(€€€€€€€Ñ½­•¸€ô‘…Ñ„¹•Ð ‰…•ÍÍ}Ñ½­•¸ˆ¤(€€€€€€€¥˜¹½ÐÑ½­•¸è(€€€€€€€€€€€É•ÑÕÉ¸…±Í”(€€€€€€€ÕÍ•È€ô‘…Ñ„¹•Ð ‰ÕÍ•Èˆ¤½Èíô(€€€€€€€Í•±˜¹Í•ÍÍ¥½¸€ôM½¥…±M•ÍÍ¥½¸¡ÍÑÈ¡Ñ½­•¸¤°ÍÑÈ¡‘…Ñ„¹•Ð ‰É•™É•Í¡}Ñ½­•¸ˆ°€ˆˆ¤¤°ÍÑÈ¡ÕÍ•È¹•Ð ‰¥ˆ°€ˆˆ¤¤°Ñ¥µ”¹Ñ¥µ” ¤€¬¥¹Ð¡‘…Ñ„¹•Ð ‰•áÁ¥É•Í}¥¸ˆ°€ÌØÀÀ¤¤¤(€€€€€€€Í•±˜¹}Í…Ù•}Í•ÍÍ¥½¸ ¤ìÉ•ÑÕÉ¸QÉÕ”((€€€‘•˜}•¹ÍÕÉ•}™É•Í ¡Í•±˜¤€´ø9½¹”è(€€€€€€€¥˜¹½ÐÍ•±˜¹Í•ÍÍ¥½¸½ÈÍ•±˜¹Í•ÍÍ¥½¸¹•áÁ¥É•Í}…Ð€øÑ¥µ”¹Ñ¥µ” ¤€¬€äÀè(€€€€€€€€€€€É•ÑÕÉ¸(€€€€€€€‘…Ñ„€ôÍ•±˜¹}É…Ü ‰A=MPˆ°€ˆ½…ÕÑ ½ØÄ½Ñ½­•¸ýÉ…¹Ñ}ÑåÁ”õÉ•™É•Í¡}Ñ½­•¸ˆ°ì‰É•™É•Í¡}Ñ½­•¸ˆèÍ•±˜¹Í•ÍÍ¥½¸¹É•™É•Í¡}Ñ½­•¹ô¤(€€€€€€€¥˜¹½ÐÍ•±˜¹}…•ÁÑ}…ÕÑ ¡‘…Ñ„¤è(€€€€€€€€€€€Í•±˜¹}±•…É}Í•ÍÍ¥½¸ ¤((€€€‘•˜Í¥¹}ÕÀ¡Í•±˜°•µ…¥°èÍÑÈ°Á…ÍÍÝ½ÉèÍÑÈ°¹¥­¹…µ”èÍÑÈ¤€´ø‰½½°è(€€€€€€€€ŒÉ•‘¥É•Ñ}Ñ½€¥Ì„ÅÕ•ÉäÁ…É…µ•Ñ•È™½È½QÉÕ”ÌÍ¥¹ÕÀ•¹‘Á½¥¹Ð¸€%Ð(€€€€€€€€ŒµÕÍÐ…±Í¼‰”ÁÉ•Í•¹Ð¥¸MÕÁ…‰…Í”ÕÑ Ì…±±½Üµ±¥ÍÐìÑ¡”ÁÉ½‘ÕÑ¥½¸(€€€€€€€€ŒÁÉ½©•Ð¥Ì½¹™¥ÕÉ•Ý¥Ñ Ñ¡”Í…µ”UI0¸€A…ÍÍ¥¹œ¥Ð•áÁ±¥¥Ñ±ä­••ÁÌ(€€€€€€€€Œ™ÕÑÕÉ”‘…Í¡‰½…É¡…¹•Ì™É½´Í¥±•¹Ñ±äÉ•ÍÑ½É¥¹œ±½…±¡½ÍÐÉ•‘¥É•ÑÌ¸(€€€€€€€É•‘¥É•Ð€ôÕÉ±±¥ˆ¹Á…ÉÍ”¹ÕÉ±•¹½‘”¡ì‰É•‘¥É•Ñ}Ñ¼ˆèÍ•±˜¹•µ…¥±}É•‘¥É•Ñ}ÕÉ±ô¤(€€€€€€€‘…Ñ„€ôÍ•±˜¹}É…Ü (€€€€€€€€€€€€‰A=MPˆ°(€€€€€€€€€€€˜ˆ½…ÕÑ ½ØÄ½Í¥¹ÕÀýíÉ•‘¥É•Ñôˆ°(€€€€€€€€€€€ì‰•µ…¥°ˆè•µ…¥°¹ÍÑÉ¥À ¤°€‰Á…ÍÍÝ½ÉˆèÁ…ÍÍÝ½É°€‰‘…Ñ„ˆèì‰¹¥­¹…µ”ˆè¹¥­¹…µ”¹ÍÑÉ¥À ¥lèÈÑt½È€‹šB·–¶@‰õô°(€€€€€€€€¤(€€€€€€€É•ÑÕÉ¸Í•±˜¹}…•ÁÑ}…ÕÑ ¡‘…Ñ„¤((€€€‘•˜Í¥¹}¥¸¡Í•±˜°•µ…¥°èÍÑÈ°Á…ÍÍÝ½ÉèÍÑÈ¤€´ø9½¹”è(€€€€€€€¥˜Í•±˜¹}¡ÑÑÁ}‰…­•¹¥Ì¹½Ð9½¹”è(€€€€€€€€€€€É•ÑÕÉ¸Í•±˜¹}¡ÑÑÁ}‰…­•¹¹Í¥¹}¥¸¡•µ…¥°°Á…ÍÍÝ½É¤(€€€€€€€‘…Ñ„€ôÍ•±˜¹}É…Ü ‰A=MPˆ°€ˆ½…ÕÑ ½ØÄ½Ñ½­•¸ýÉ…¹Ñ}ÑåÁ”õÁ…ÍÍÝ½Éˆ°ì‰•µ…¥°ˆè•µ…¥°¹ÍÑÉ¥À ¤°€‰Á…ÍÍÝ½ÉˆèÁ…ÍÍÝ½É‘ô¤(€€€€€€€¥˜¹½ÐÍ•±˜¹}…•ÁÑ}…ÕÑ ¡‘…Ñ„¤è(€€€€€€€€€€€É…¥Í”M½¥…±ÉÉ½È ‹žfï–öWšÊ‡šr'š"C–*¾ò3¢¾ßšŽš~—¦
»žºÇž†»¢º“š"[–¾ž‚Žˆ¤((€€€‘•˜Í¥¹}½ÕÐ¡Í•±˜¤€´ø9½¹”è(€€€€€€€¥˜Í•±˜¹}¡ÑÑÁ}‰…­•¹¥Ì¹½Ð9½¹”è(€€€€€€€€€€€Í•±˜¹}¡ÑÑÁ}‰…­•¹¹Í¥¹}½ÕÐ ¤(€€€€€€€€€€€É•ÑÕÉ¸(€€€€€€€Í•±˜¹}±•…É}Í•ÍÍ¥½¸ ¤((€€€‘•˜‘…Í¡‰½…É¡Í•±˜°É½½µ}¥èÍÑÈð9½¹”€ô9½¹”°€¨°…±±½Ý}…¡”è‰½½°€ôQÉÕ”¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€€€€€Í•±˜¹½¹¹•Ñ¥½¸¹Í•Ð ‰=99Q%9ˆ°‘…Ñ…}Í½ÕÉ”õÍ•±˜¹½¹¹•Ñ¥½¸¹‘…Ñ…}Í½ÕÉ”°É•…±Ñ¥µ•}ÍÑ…Ñ”ô‰Á½±±¥¹œˆ¤(€€€€€€€ÑÉäè(€€€€€€€€€€€¥˜Í•±˜¹}¡ÑÑÁ}‰…­•¹¥Ì¹½Ð9½¹”è(€€€€€€€€€€€€€€€‘…Ñ„€ôÍ•±˜¹}¡ÑÑÁ}‰…­•¹¹‘…Í¡‰½…É¡É½½µ}¥õÉ½½µ}¥¤(€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€‘…Ñ„€ôÍ•±˜¹}É…Ü ‰A=MPˆ°€ˆ½É•ÍÐ½ØÄ½ÉÁŒ½±¥±¥}‘…Í¡‰½…Éˆ°íô°…ÕÑ¡•¹Ñ¥…Ñ•õQÉÕ”¤½Èíô(€€€€€€€€€€€€€€€¥˜É½½µ}¥è(€€€€€€€€€€€€€€€€€€€É½½´€ôÍ•±˜¹}É…Ü (€€€€€€€€€€€€€€€€€€€€€€€€‰A=MPˆ°(€€€€€€€€€€€€€€€€€€€€€€€€ˆ½É•ÍÐ½ØÄ½ÉÁŒ½±¥±¥}É½½µ}‘…Í¡‰½…Éˆ°(€€€€€€€€€€€€€€€€€€€€€€€ì‰Á}É½½µ}¥ˆèÉ½½µ}¥‘ô°(€€€€€€€€€€€€€€€€€€€€€€€…ÕÑ¡•¹Ñ¥…Ñ•õQÉÕ”°(€€€€€€€€€€€€€€€€€€€€¤½Èíô(€€€€€€€€€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡É½½´°‘¥Ð¤è(€€€€€€€€€€€€€€€€€€€€€€€‘…Ñ„¹ÕÁ‘…Ñ”¡É½½´¤(€€€€€€€€€€€€€€€€€€€ÑÉäè(€€€€€€€€€€€€€€€€€€€€€€€É¥ÑÕ…±Ì€ôÍ•±˜¹}É…Ü (€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰A=MPˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€ˆ½É•ÍÐ½ØÄ½ÉÁŒ½±¥±¥}É½½µ}É½½µ}É¥ÑÕ…±Ìˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€ì‰Á}É½½µ}¥ˆèÉ½½µ}¥‘ô°(€€€€€€€€€€€€€€€€€€€€€€€€€€€…ÕÑ¡•¹Ñ¥…Ñ•õQÉÕ”°(€€€€€€€€€€€€€€€€€€€€€€€€¤½Èíô(€€€€€€€€€€€€€€€€€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡É¥ÑÕ…±Ì°‘¥Ð¤è(€€€€€€€€€€€€€€€€€€€€€€€€€€€‘…Ñ„¹ÕÁ‘…Ñ”¡É¥ÑÕ…±Ì¤(€€€€€€€€€€€€€€€€€€€•á•ÁÐM½¥…±ÉÉ½Èè(€€€€€€€€€€€€€€€€€€€€€€€€Œ=±‘•È‘•Á±½å•ÁÉ½©•ÑÌµ…ä¹½Ð¡…Ù”Ñ¡”½ÁÑ¥½¹…°É¥ÑÕ…°(€€€€€€€€€€€€€€€€€€€€€€€€Œµ¥É…Ñ¥½¸å•ÐìÑ¡”½É”É½½´‘…Í¡‰½…ÉÉ•µ…¥¹ÌÕÍ…‰±”¸(€€€€€€€€€€€€€€€€€€€€€€€Á…ÍÌ(€€€€€€€€€€€É•ÍÕ±Ð€ô‘¥Ð¡‘…Ñ„½Èíô¤(€€€€€€€€€€€Í•±˜¹}±…ÍÑ}•ÉÉ½È€ô€ˆˆ(€€€€€€€€€€€É•ÍÕ±Ñl‰}½¹¹•Ñ¥½¹}ÍÑ…Ñ”‰t€ô€‰=91%9ˆ(€€€€€€€€€€€É•ÍÕ±Ñl‰‘…Ñ…}Í½ÕÉ”‰t€ô€‰Í•ÉÙ•Èˆ(€€€€€€€€€€€É•ÍÕ±Ñl‰}‘…Ñ…}Í½ÕÉ”‰t€ô€‰Í•ÉÙ•Èˆ(€€€€€€€€€€€É•ÍÕ±Ñl‰}Í•ÉÙ•É}Ñ¥µ•ÍÑ…µÀ‰t€ô‘…Ñ•Ñ¥µ”¹¹½Ü ¤¹…ÍÑ¥µ•é½¹” ¤¹¥Í½™½Éµ…Ð ¤(€€€€€€€€€€€Í•±˜¹½¹¹•Ñ¥½¸¹Í•Ð (€€€€€€€€€€€€€€€€‰=91%9ˆ°(€€€€€€€€€€€€€€€‘…Ñ…}Í½ÕÉ”ô‰Í•ÉÙ•Èˆ°(€€€€€€€€€€€€€€€É•…±Ñ¥µ•}ÍÑ…Ñ”ô‰Á½±±¥¹œˆ°(€€€€€€€€€€€€€€€Í•ÉÙ•É}Ñ¥µ•ÍÑ…µÀõÉ•ÍÕ±Ñl‰}Í•ÉÙ•É}Ñ¥µ•ÍÑ…µÀ‰t°(€€€€€€€€€€€€¤(€€€€€€€€€€€Í•±˜¹}É•µ•µ‰•É}‘…Í¡‰½…É¡É½½µ}¥°É•ÍÕ±Ð¤(€€€€€€€€€€€É•ÑÕÉ¸É•ÍÕ±Ð(€€€€€€€•á•ÁÐM½¥…±ÉÉ½È…Ì•áŒè(€€€€€€€€€€€Í•±˜¹}±…ÍÑ}•ÉÉ½È€ôÍÑÈ¡•áŒ¤(€€€€€€€€€€€Í•±˜¹½¹¹•Ñ¥½¸¹Í•Ð ‰=1%9ˆ°‘…Ñ…}Í½ÕÉ”ô‰±½…±}…¡”ˆ°É•…±Ñ¥µ•}ÍÑ…Ñ”ô‰Õ¹…Ù…¥±…‰±”ˆ¤(€€€€€€€€€€€¥˜¹½Ð…±±½Ý}…¡”è(€€€€€€€€€€€€€€€É…¥Í”(€€€€€€€€€€€…¡•€ôÍ•±˜¹…¡•‘}‘…Í¡‰½…É¡É½½µ}¥¤(€€€€€€€€€€€¥˜…¡•¥Ì¹½Ð9½¹”è(€€€€€€€€€€€€€€€É•ÑÕÉ¸…¡•(€€€€€€€€€€€É…¥Í”((€€€‘•˜ÉÁŒ¡Í•±˜°¹…µ”èÍÑÈ°‰½‘äè‘¥ÑmÍÑÈ°¹åt¤€´ø¹äè(€€€€€€€¥˜Í•±˜¹}¡ÑÑÁ}‰…­•¹¥Ì¹½Ð9½¹”è(€€€€€€€€€€€É•ÑÕÉ¸Í•±˜¹}¡ÑÑÁ}‰…­•¹¹ÉÁŒ¡¹…µ”°‰½‘ä¤(€€€€€€€É•ÑÕÉ¸Í•±˜¹}É…Ü ‰A=MPˆ°˜ˆ½É•ÍÐ½ØÄ½ÉÁŒ½í¹…µ•ôˆ°‰½‘ä°…ÕÑ¡•¹Ñ¥…Ñ•õQÉÕ”¤((€€€‘•˜ÕÁ‘…Ñ•}ÁÉ½™¥±”¡Í•±˜°€¨°¹¥­¹…µ”èÍÑÈ°Ù¥Í¥‰¥±¥ÑäèÍÑÈ°Í¡½Ý}•á…Ñ}Ñ¥µ”è‰½½°°…±±½Ý}Ù¥Í¥ÑÌè‰½½°°½ÕÑ™¥Ñ}­•äèÍÑÈ€ô€ˆˆ¤€´ø9½¹”è(€€€€€€€¥˜Í•±˜¹}¡ÑÑÁ}‰…­•¹¥Ì¹½Ð9½¹”è(€€€€€€€€€€€É•ÑÕÉ¸Í•±˜¹}¡ÑÑÁ}‰…­•¹¹ÕÁ‘…Ñ•}ÁÉ½™¥±”¡¹¥­¹…µ”õ¹¥­¹…µ”°Ù¥Í¥‰¥±¥ÑäõÙ¥Í¥‰¥±¥Ñä°Í¡½Ý}•á…Ñ}Ñ¥µ”õÍ¡½Ý}•á…Ñ}Ñ¥µ”°…±±½Ý}Ù¥Í¥ÑÌõ…±±½Ý}Ù¥Í¥ÑÌ°½ÕÑ™¥Ñ}­•äõ½ÕÑ™¥Ñ}­•ä¤(€€€€€€€¥˜¹½ÐÍ•±˜¹Í•ÍÍ¥½¸è(€€€€€€€€€€€É…¥Í”M½¥…±ÉÉ½È ‹¢¾ß–#žfï–öWŽˆ¤(€€€€€€€ÅÕ•Éä€ôÕÉ±±¥ˆ¹Á…ÉÍ”¹ÕÉ±•¹½‘”¡ì‰ÕÍ•É}¥ˆè˜‰•Ä¹íÍ•±˜¹Í•ÍÍ¥½¸¹ÕÍ•É}¥‘ô‰ô¤(€€€€€€€±•…¸€ô¹¥­¹…µ”¹ÍÑÉ¥À ¥lèÈÑt(€€€€€€€Í•±˜¹}É…Ü ‰AQ ˆ°˜ˆ½É•ÍÐ½ØÄ½±¥±¥}ÁÉ½™¥±•ÌýíÅÕ•Éåôˆ°ì‰¹¥­¹…µ”ˆè±•…¸½È€‹šB·–¶@ˆ°€‰½Ý¹•É}¹¥­¹…µ”ˆè±•…¸°€‰Ù¥Í¥‰¥±¥ÑäˆèÙ¥Í¥‰¥±¥Ñä°€‰Í¡½Ý}•á…Ñ}Ñ¥µ”ˆè‰½½°¡Í¡½Ý}•á…Ñ}Ñ¥µ”¤°€‰…±±½Ý}Ù¥Í¥ÑÌˆè‰½½°¡…±±½Ý}Ù¥Í¥ÑÌ¤°€‰½ÕÑ™¥Ñ}­•äˆè½ÕÑ™¥Ñ}­•ålèØÁt°€‰ÕÁ‘…Ñ•‘}…Ðˆè‘…Ñ•Ñ¥µ”¹¹½Ü ¤¹…ÍÑ¥µ•é½¹” ¤¹¥Í½™½Éµ…Ð ¥ô°…ÕÑ¡•¹Ñ¥…Ñ•õQÉÕ”°•áÑÉ…}¡•…‘•ÉÌõì‰AÉ•™•Èˆè€‰É•ÑÕÉ¸õµ¥¹¥µ…°‰ô¤((€€€‘•˜¡•…ÉÑ‰•…Ð¡Í•±˜°€¨°Ý½É­¥¹œè‰½½°°Ñ½‘…å}Í•½¹‘Ìè¥¹Ð°Í•ÍÍ¥½¹}ÍÑ…ÉÑ•‘}…ÐèÍÑÈð9½¹”°½ÕÑ™¥Ñ}­•äèÍÑÈ°É½½µ}¥èÍÑÈð9½¹”€ô9½¹”°ÅÕ¥­}ÍÑ…ÑÕÌèÍÑÈ€ô€ˆˆ°ÅÕ¥­}ÍÑ…ÑÕÍ}•áÁ¥É•Í}…ÐèÍÑÈð9½¹”€ô9½¹”¤€´ø9½¹”è(€€€€€€€¥˜Í•±˜¹}¡ÑÑÁ}‰…­•¹¥Ì¹½Ð9½¹”è(€€€€€€€€€€€É•ÑÕÉ¸Í•±˜¹}¡ÑÑÁ}‰…­•¹¹¡•…ÉÑ‰•…Ð¡Ý½É­¥¹œõÝ½É­¥¹œ°Ñ½‘…å}Í•½¹‘ÌõÑ½‘…å}Í•½¹‘Ì°Í•ÍÍ¥½¹}ÍÑ…ÉÑ•‘}…ÐõÍ•ÍÍ¥½¹}ÍÑ…ÉÑ•‘}…Ð°½ÕÑ™¥Ñ}­•äõ½ÕÑ™¥Ñ}­•ä°É½½µ}¥õÉ½½µ}¥°ÅÕ¥­}ÍÑ…ÑÕÌõÅÕ¥­}ÍÑ…ÑÕÌ°ÅÕ¥­}ÍÑ…ÑÕÍ}•áÁ¥É•Í}…ÐõÅÕ¥­}ÍÑ…ÑÕÍ}•áÁ¥É•Í}…Ð¤(€€€€€€€¥˜¹½ÐÍ•±˜¹Í•ÍÍ¥½¸è(€€€€€€€€€€€É•ÑÕÉ¸(€€€€€€€‰½‘ä€ôì‰ÕÍ•É}¥ˆèÍ•±˜¹Í•ÍÍ¥½¸¹ÕÍ•É}¥°€‰Ý½É­¥¹œˆè‰½½°¡Ý½É­¥¹œ¤°€‰Í•ÍÍ¥½¹}ÍÑ…ÉÑ•‘}…ÐˆèÍ•ÍÍ¥½¹}ÍÑ…ÉÑ•‘}…Ð°€‰™½ÕÍ}‘…Ñ”ˆè‘…Ñ•Ñ¥µ”¹¹½Ü ¤¹‘…Ñ” ¤¹¥Í½™½Éµ…Ð ¤°€‰Ñ½‘…å}Í•½¹‘Ìˆèµ¥¸ àØÐÀÀ°µ…à À°¥¹Ð¡Ñ½‘…å}Í•½¹‘Ì¤¤¤°€‰½ÕÑ™¥Ñ}­•äˆè½ÕÑ™¥Ñ}­•ålèØÁt°€‰É½½µ}¥ˆèÉ½½µ}¥°€‰ÅÕ¥­}ÍÑ…ÑÕÌˆèÅÕ¥­}ÍÑ…ÑÕÍlèÐÁt°€‰ÅÕ¥­}ÍÑ…ÑÕÍ}•áÁ¥É•Í}…ÐˆèÅÕ¥­}ÍÑ…ÑÕÍ}•áÁ¥É•Í}…Ð°€‰±…ÍÑ}Í••¸ˆè‘…Ñ•Ñ¥µ”¹¹½Ü ¤¹…ÍÑ¥µ•é½¹” ¤¹¥Í½™½Éµ…Ð ¤°€‰ÕÁ‘…Ñ•‘}…Ðˆè‘…Ñ•Ñ¥µ”¹¹½Ü ¤¹…ÍÑ¥µ•é½¹” ¤¹¥Í½™½Éµ…Ð ¥ô(€€€€€€€Í•±˜¹}É…Ü ‰A=MPˆ°€ˆ½É•ÍÐ½ØÄ½±¥±¥}™½ÕÍ}ÁÉ•Í•¹”ý½¹}½¹™±¥ÐõÕÍ•É}¥ˆ°‰½‘ä°…ÕÑ¡•¹Ñ¥…Ñ•õQÉÕ”°•áÑÉ…}¡•…‘•ÉÌõì‰AÉ•™•Èˆè€‰É•Í½±ÕÑ¥½¸õµ•É”µ‘ÕÁ±¥…Ñ•Ì±É•ÑÕÉ¸õµ¥¹¥µ…°‰ô¤((€€€‘•˜ÕÁ‘…Ñ•}½Ý¹•É}¹¥­¹…µ”¡Í•±˜°¹¥­¹…µ”èÍÑÈ¤€´ø9½¹”è(€€€€€€€¥˜Í•±˜¹}¡ÑÑÁ}‰…­•¹¥Ì¹½Ð9½¹”è(€€€€€€€€€€€É•ÑÕÉ¸Í•±˜¹}¡ÑÑÁ}‰…­•¹¹ÕÁ‘…Ñ•}½Ý¹•É}¹¥­¹…µ”¡¹¥­¹…µ”¤(€€€€€€€¥˜¹½ÐÍ•±˜¹Í•ÍÍ¥½¸è(€€€€€€€€€€€É…¥Í”M½¥…±ÉÉ½È ‹¢¾ß–#žfï–öWŽˆ¤(€€€€€€€ÅÕ•Éä€ôÕÉ±±¥ˆ¹Á…ÉÍ”¹ÕÉ±•¹½‘”¡ì‰ÕÍ•É}¥ˆè˜‰•Ä¹íÍ•±˜¹Í•ÍÍ¥½¸¹ÕÍ•É}¥‘ô‰ô¤(€€€€€€€±•…¸€ô¹¥­¹…µ”¹ÍÑÉ¥À ¥lèÈÑt(€€€€€€€Í•±˜¹}É…Ü ‰AQ ˆ°˜ˆ½É•ÍÐ½ØÄ½±¥±¥}ÁÉ½™¥±•ÌýíÅÕ•Éåôˆ°ì‰¹¥­¹…µ”ˆè±•…¸½È€‹šB·–¶@ˆ°€‰½Ý¹•É}¹¥­¹…µ”ˆè±•…¸°€‰ÕÁ‘…Ñ•‘}…Ðˆè‘…Ñ•Ñ¥µ”¹¹½Ü ¤¹…ÍÑ¥µ•é½¹” ¤¹¥Í½™½Éµ…Ð ¥ô°…ÕÑ¡•¹Ñ¥…Ñ•õQÉÕ”°•áÑÉ…}¡•…‘•ÉÌõì‰AÉ•™•Èˆè€‰É•ÑÕÉ¸õµ¥¹¥µ…°‰ô¤((€€€‘•˜Í•¹‘}¥¹Ñ•É…Ñ¥½¸¡Í•±˜°€¨°Ñ…É•ÐèÍÑÈ°­¥¹èÍÑÈ°É½½µ}¥èÍÑÈð9½¹”€ô9½¹”¤€´ø9½¹”è(€€€€€€€¥˜Í•±˜¹}¡ÑÑÁ}‰…­•¹¥Ì¹½Ð9½¹”è(€€€€€€€€€€€É•ÑÕÉ¸Í•±˜¹}¡ÑÑÁ}‰…­•¹¹Í•¹‘}¥¹Ñ•É…Ñ¥½¸¡Ñ…É•ÐõÑ…É•Ð°­¥¹õ­¥¹°É½½µ}¥õÉ½½µ}¥¤(€€€€€€€Í•±˜¹ÉÁŒ (€€€€€€€€€€€€‰±¥±¥}Í•¹‘}¥¹Ñ•É…Ñ¥½¸ˆ°(€€€€€€€€€€€ì‰Á}Ñ…É•ÐˆèÑ…É•Ð°€‰Á}­¥¹ˆè­¥¹°€‰Á}É½½µ}¥ˆèÉ½½µ}¥‘ô°(€€€€€€€€¤((€€€‘•˜Í•Ñ}É½½µ}½…°¡Í•±˜°€¨°É½½µ}¥èÍÑÈ°Ñ¥Ñ±”èÍÑÈ°Ñ…É•Ñ}Í•½¹‘Ìè¥¹Ð°‘Õ•}…ÐèÍÑÈð9½¹”€ô9½¹”¤€´ø9½¹”è(€€€€€€€Í•±˜¹ÉÁŒ ‰±¥±¥}Í•Ñ}É½½µ}½…°ˆ°ì‰Á}É½½µ}¥ˆèÉ½½µ}¥°€‰Á}Ñ¥Ñ±”ˆèÑ¥Ñ±”°€‰Á}Ñ…É•Ñ}Í•½¹‘Ìˆè¥¹Ð¡Ñ…É•Ñ}Í•½¹‘Ì¤°€‰Á}‘Õ•}…Ðˆè‘Õ•}…Ñô¤((€€€‘•˜Í•Ñ}É½½µ}Í¡•‘Õ±”¡Í•±˜°€¨°É½½µ}¥èÍÑÈ°ÍÑ…ÉÑ}…ÐèÍÑÈ°•¹‘}…ÐèÍÑÈ°•¹…‰±•è‰½½°€ôQÉÕ”¤€´ø9½¹”è(€€€€€€€Í•±˜¹ÉÁŒ ‰±¥±¥}Í•Ñ}É½½µ}Í¡•‘Õ±”ˆ°ì‰Á}É½½µ}¥ˆèÉ½½µ}¥°€‰Á}ÍÑ…ÉÑ}…ÐˆèÍÑ…ÉÑ}…Ð°€‰Á}•¹‘}…Ðˆè•¹‘}…Ð°€‰Á}•¹…‰±•ˆè‰½½°¡•¹…‰±•¥ô¤((€€€‘•˜Í•Ñ}É½½µ}¡…±±•¹”¡Í•±˜°€¨°É½½µ}¥èÍÑÈ°Ñ¥Ñ±”èÍÑÈ°Ñ…É•Ñ}Í•½¹‘Ìè¥¹Ð°Ñ…É•Ñ}É½Õ¹‘Ìè¥¹Ð¤€´ø9½¹”è(€€€€€€€Í•±˜¹ÉÁŒ ‰±¥±¥}Í•Ñ}É½½µ}¡…±±•¹”ˆ°ì‰Á}É½½µ}¥ˆèÉ½½µ}¥°€‰Á}Ñ¥Ñ±”ˆèÑ¥Ñ±”°€‰Á}Ñ…É•Ñ}Í•½¹‘Ìˆè¥¹Ð¡Ñ…É•Ñ}Í•½¹‘Ì¤°€‰Á}Ñ…É•Ñ}É½Õ¹‘Ìˆè¥¹Ð¡Ñ…É•Ñ}É½Õ¹‘Ì¥ô¤((€€€‘•˜Í•Ñ}‰Õ‘‘å}ÍÕ‰ÍÉ¥ÁÑ¥½¸¡Í•±˜°€¨°‰Õ‘‘å}¥èÍÑÈ°½¹}™½ÕÍ}ÍÑ…ÉÐè‰½½°°½¹}™½ÕÍ}•¹è‰½½°°µÕÑ•è‰½½°€ô…±Í”¤€´ø9½¹”è(€€€€€€€Í•±˜¹ÉÁŒ ‰±¥±¥}Í•Ñ}‰Õ‘‘å}ÍÕ‰ÍÉ¥ÁÑ¥½¸ˆ°ì‰Á}‰Õ‘‘å}¥ˆè‰Õ‘‘å}¥°€‰Á}½¹}™½ÕÍ}ÍÑ…ÉÐˆè‰½½°¡½¹}™½ÕÍ}ÍÑ…ÉÐ¤°€‰Á}½¹}™½ÕÍ}•¹ˆè‰½½°¡½¹}™½ÕÍ}•¹¤°€‰Á}µÕÑ•ˆè‰½½°¡µÕÑ•¥ô¤((€€€‘•˜±•…Ù•}É½½´¡Í•±˜°€¨°É½½µ}¥èÍÑÈ¤€´ø9½¹”è(€€€€€€€Í•±˜¹ÉÁŒ ‰±¥±¥}±•…Ù•}É½½´ˆ°ì‰Á}É½½µ}¥ˆèÉ½½µ}¥‘ô¤((€€€‘•˜É•½É‘}É½½µ}•Ù•¹Ð¡Í•±˜°€¨°É½½µ}¥èÍÑÈ°­¥¹èÍÑÈ°Ñ…É•Ñ}¥èÍÑÈð9½¹”€ô9½¹”°µ•ÍÍ…”èÍÑÈ€ô€ˆˆ¤€´ø9½¹”è(€€€€€€€Í•±˜¹ÉÁŒ ‰±¥±¥}É•½É‘}É½½µ}•Ù•¹Ðˆ°ì‰Á}É½½µ}¥ˆèÉ½½µ}¥°€‰Á}­¥¹ˆè­¥¹°€‰Á}Ñ…É•Ñ}¥ˆèÑ…É•Ñ}¥°€‰Á}µ•ÍÍ…”ˆèµ•ÍÍ…•ô¤(