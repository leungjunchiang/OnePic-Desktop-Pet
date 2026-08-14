�r�^�f��ئ{��y�'vî���"""Lili 搭子自习室的最小社交客户端与可替换网络后端。

只发送账号认证、昵称、六毛外观、工作状态、累计秒数、房间与串门事件。密码从不保存；
刷新令牌保存在系统凭据库。网络失败不会影响离线桌宠、计时、AI 或本地素材。
"""

from __future__ import annotations

import json
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
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.endpoint = endpoint
        self.retryable = retryable
        self.status = status


def _endpoint_host(base_url: str) -> str:
    parsed = urllib.parse.urlparse(str(base_url or ""))
    return parsed.netloc or "未配置"


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
        return SocialError(f"TLS/证书连接失败：无法安全连接自习室服务器（{host}）。", kind="tls", endpoint=host)
    return SocialError(f"网络不可达：无法连接自习室服务器（{host}）。", kind="network", endpoint=host, retryable=True)


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
    def dashboard(self, room_id: str | None = None) -> dict[str, Any]: ...
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
                raise SocialError("请先登录搭子自习室。")
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
        body = {"email": email.strip(), "password": password, "nickname": nickname.strip()[:24] or "搭子", "data": {"nickname": nickname.strip()[:24] or "搭子"}}
        if self.email_redirect_url:
            body["redirect_to"] = self.email_redirect_url
        return self._accept_auth(self._raw("POST", "/auth/signup", body))

    def sign_in(self, email: str, password: str) -> None:
        data = self._raw("POST", "/auth/signin", {"email": email.strip(), "password": password})
        if not self._accept_auth(data):
            raise SocialError("登录没有成功，请检查邮箱确认或密码。")

    def sign_out(self) -> None:
        self._clear_session()

    def health(self) -> dict[str, Any]:
        """Check the relay without requiring a user session."""
        return dict(self._raw("GET", "/health") or {})

    def dashboard(self, room_id: str | None = None) -> dict[str, Any]:
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
        self._raw("PATCH", "/profile", {"nickname": nickname.strip()[:24] or "搭子", "owner_nickname": nickname.strip()[:24], "visibility": visibility, "show_exact_time": bool(show_exact_time), "allow_visits": bool(allow_visits), "outfit_key": outfit_key[:60]}, authenticated=True)

    def update_owner_nickname(self, nickname: str) -> None:
        self._raw("PATCH", "/profile", {"nickname": nickname.strip()[:24] or "搭子", "owner_nickname": nickname.strip()[:24]}, authenticated=True)

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


class SocialClient:
    SERVICE_NAME = "LiliSocial"
    ACCOUNT_NAME = "supabase-session"

    def __init__(self, *, persist_tokens: bool = True, backend: SocialBackend | None = None) -> None:
        config = json.loads(resource_path("config/social_backend.json").read_text(encoding="utf-8"))
        self.url = str(config.get("url", "")).rstrip("/")
        self.key = str(config.get("publishable_key", ""))
        self.social_api_base_url = (
            os.environ.get("LILI_SOCIAL_API_BASE_URL", "").strip()
  ��-�G����ƭy�f._load_dashboard_cache()

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
        data["_sync_offline"] = True
        data["_sync_age_minutes"] = age_minutes
        data["_sync_error"] = self._last_error or "当前网络无法访问自习室服务"
        return data

    def _raw(self, method: str, path: str, body: Any = None, *, authenticated: bool = False, extra_headers: dict[str, str] | None = None) -> Any:
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"apikey": self.key, "Content-Type": "application/json", "Accept": "application/json"}
        if authenticated:
            self._ensure_fresh()
            if not self.session:
                raise SocialError("请先登录搭子自习室。")
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
            {"email": email.strip(), "password": password, "data": {"nickname": nickname.strip()[:24] or "搭子"}},
        )
        return self._accept_auth(data)

    def sign_in(self, email: str, password: str) -> None:
        if self._http_backend is not None:
            return self._http_backend.sign_in(email, password)
        data = self._raw("POST", "/auth/v1/token?grant_type=password", {"email": email.strip(), "password": password})
        if not self._accept_auth(data):
            raise SocialError("登录没有成功，请检查邮箱确认或密码。")

    def sign_out(self) -> None:
        if self._http_backend is not None:
            self._http_backend.sign_out()
            return
        self._clear_session()

    def dashboard(self, room_id: str | None = None) -> dict[str, Any]:
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
            self._remember_dashboard(room_id, result)
            return result
        except SocialError as exc:
            self._last_error = str(exc)
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
            raise SocialError("请先登录。")
        query = urllib.parse.urlencode({"user_id": f"eq.{self.session.user_id}"})
        clean = nickname.strip()[:24]
        self._raw("PATCH", f"/rest/v1/lili_profiles?{query}", {"nickname": clean or "搭子", "owner_nickname": clean, "visibility": visibility, "show_exact_time": bool(show_exact_time), "allow_visits": bool(allow_visits), "outfit_key": outfit_key[:60], "updated_at": datetime.now().astimezone().isoformat()}, authenticated=True, extra_headers={"Prefer": "return=minimal"})

    def heartbeat(self, *, working: bool, today_seconds: int, session_started_at: str | None, outfit_key: str, room_id: str | None = None, quick_status: str = "", quick_status_expires_at: str | None = None) -> None:
        if self._http_backend is not None:
            return self._http_backend.heartbeat(working=working, today_seconds=today_seconds, session_started_at=session_started_at, outfit_key=outfit_key, room_id=room_id, quick_status=quick_status, quick_status_expires_at=quick_status_expires_at)
        if not self.session:
            return
        body = {"user_id": self.session.user_id, "working": bool(working), "session_started_at": session_started_at, "focus_date": datetime.now().date().isoformat(), "today_seconds": min(86400, max(0, int(today_seconds))), "outfit_key": outfit_key[:60], "room_id": room_id, "quick_status": quick_status[:40], "quick_status_expires_at": quick_status_expires_at, "last_seen": datetime.now().astimezone().isoformat(), "updated_at": datetime.now().astimezone().isoformat()}
        self._raw("POST", "/rest/v1/lili_focus_presence?on_conflict=user_id", body, authenticated=True, extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"})

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
