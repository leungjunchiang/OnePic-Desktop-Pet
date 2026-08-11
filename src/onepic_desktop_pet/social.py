"""Lili 搭子自习室的最小 Supabase 客户端。

只发送账号认证、昵称、六毛外观、工作状态、累计秒数、房间与串门事件。密码从不保存；
刷新令牌保存在系统凭据库。网络失败不会影响离线桌宠、计时、AI 或本地素材。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .resources import resource_path


class SocialError(RuntimeError):
    """面向用户的社交网络错误。"""


@dataclass
class SocialSession:
    access_token: str
    refresh_token: str
    user_id: str
    expires_at: float


class SocialClient:
    SERVICE_NAME = "LiliSocial"
    ACCOUNT_NAME = "supabase-session"

    def __init__(self, *, persist_tokens: bool = True) -> None:
        config = json.loads(resource_path("config/social_backend.json").read_text(encoding="utf-8"))
        self.url = str(config.get("url", "")).rstrip("/")
        self.key = str(config.get("publishable_key", ""))
        self.persist_tokens = persist_tokens
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
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
                message = data.get("msg") or data.get("message") or data.get("error_description") or raw
            except json.JSONDecodeError:
                message = raw or str(exc)
            raise SocialError(str(message)[:300]) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise SocialError("暂时连不上搭子自习室，六毛已继续离线陪伴。") from exc

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
        data = self._raw("POST", "/auth/v1/signup", {"email": email.strip(), "password": password, "data": {"nickname": nickname.strip()[:24] or "六毛搭子"}})
        return self._accept_auth(data)

    def sign_in(self, email: str, password: str) -> None:
        data = self._raw("POST", "/auth/v1/token?grant_type=password", {"email": email.strip(), "password": password})
        if not self._accept_auth(data):
            raise SocialError("登录没有成功，请检查邮箱确认或密码。")

    def sign_out(self) -> None:
        self._clear_session()

    def dashboard(self) -> dict[str, Any]:
        return self._raw("POST", "/rest/v1/rpc/lili_dashboard", {}, authenticated=True) or {}

    def rpc(self, name: str, body: dict[str, Any]) -> Any:
        return self._raw("POST", f"/rest/v1/rpc/{name}", body, authenticated=True)

    def update_profile(self, *, nickname: str, visibility: str, show_exact_time: bool, allow_visits: bool, outfit_key: str = "") -> None:
        if not self.session:
            raise SocialError("请先登录。")
        query = urllib.parse.urlencode({"user_id": f"eq.{self.session.user_id}"})
        self._raw("PATCH", f"/rest/v1/lili_profiles?{query}", {"nickname": nickname.strip()[:24] or "六毛搭子", "visibility": visibility, "show_exact_time": bool(show_exact_time), "allow_visits": bool(allow_visits), "outfit_key": outfit_key[:60], "updated_at": datetime.now().astimezone().isoformat()}, authenticated=True, extra_headers={"Prefer": "return=minimal"})

    def heartbeat(self, *, working: bool, today_seconds: int, session_started_at: str | None, outfit_key: str, room_id: str | None = None) -> None:
        if not self.session:
            return
        body = {"user_id": self.session.user_id, "working": bool(working), "session_started_at": session_started_at, "focus_date": datetime.now().date().isoformat(), "today_seconds": min(86400, max(0, int(today_seconds))), "outfit_key": outfit_key[:60], "room_id": room_id, "last_seen": datetime.now().astimezone().isoformat(), "updated_at": datetime.now().astimezone().isoformat()}
        self._raw("POST", "/rest/v1/lili_focus_presence?on_conflict=user_id", body, authenticated=True, extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
