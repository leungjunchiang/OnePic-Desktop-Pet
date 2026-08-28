"""Tests for Supabase-first routing and the CloudBase proxy fallback."""

import json
import io
import pytest
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
from pathlib import Path

import onepic_desktop_pet.social as social_module
from onepic_desktop_pet.social import (
    BackendRouteManager,
    AuthSessionManager,
    HttpSocialBackend,
    PRESENCE_GRACE_SECONDS,
    SocialClient,
    SocialError,
    SocialSession,
    SignupResult,
    _apply_buddy_private_notes,
    _heartbeat_payload,
    _normalise_never_seen_presence,
    normalize_email,
    social_user_message,
)
from onepic_desktop_pet.resources import clear_content_overlay_cache, set_content_update_root


def test_heartbeat_payload_drops_local_only_focus_fields() -> None:
    payload = _heartbeat_payload(
        {
            "working": True,
            "today_seconds": 120,
            "session_started_at": "2026-08-21T08:00:00+08:00",
            "outfit_key": "hour-01",
            "room_id": "room-1",
            "quick_status": "再卷30分钟",
            "quick_status_expires_at": None,
            "session_active": True,
            "work_state": "working",
            "pause_reason": None,
            "personal_state": {"today_seconds": 120},
        }
    )

    assert payload == {
        "working": True,
        "session_active": True,
        "work_state": "working",
        "pause_reason": None,
        "today_seconds": 120,
        "session_started_at": "2026-08-21T08:00:00+08:00",
        "outfit_key": "hour-01",
        "room_id": "room-1",
        "quick_status": "再卷30分钟",
        "quick_status_expires_at": None,
    }


def test_auth_session_manager_single_flight_refreshes_once_for_concurrent_callers():
    manager = AuthSessionManager(
        service_name="LiliSocialTest",
        account_name="single-flight",
        persist_tokens=False,
    )
    manager.adopt(SocialSession("old-access", "old-refresh", "user-1", time.time() - 1, 7))
    calls: list[str] = []
    barrier = threading.Barrier(5)

    def refresh(current: SocialSession) -> dict:
        calls.append(current.refresh_token)
        time.sleep(0.05)
        return {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
            "user": {"id": current.user_id},
        }

    results: list[SocialSession | None] = []

    def worker() -> None:
        barrier.wait()
        results.append(manager.get_valid_session(refresh, requested_by="test-worker"))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert calls == ["old-refresh"]
    assert len(results) == 4
    assert {result.refresh_token for result in results if result is not None} == {"new-refresh"}


def test_ephemeral_auth_managers_do_not_reuse_another_clients_session():
    first = AuthSessionManager(
        service_name="LiliSocialTest",
        account_name="ephemeral-isolation",
        persist_tokens=False,
    )
    first.adopt(SocialSession("old-access", "old-refresh", "user-1", time.time() + 3600, 1))

    second = AuthSessionManager(
        service_name="LiliSocialTest",
        account_name="ephemeral-isolation",
        persist_tokens=False,
    )

    assert second.current() is None


def test_refresh_token_reuse_error_has_user_safe_message():
    assert social_user_message(
        SocialError(
            '{"code":400,"error_code":"refresh_token_already_used"}',
            kind="auth_refresh_reused",
            error_code="refresh_token_already_used",
        )
    ) == "登录状态已失效，请重新登录。"


def test_authenticated_401_refreshes_once_and_retries_request(monkeypatch):
    """A server-side access-token rejection must recover via refresh token."""

    import onepic_desktop_pet.social as social_module

    backend = HttpSocialBackend(
        "https://supabase.example.test",
        client_key="sb_publishable_test",
        persist_tokens=False,
        transport="direct",
    )
    backend.auth_manager.adopt(
        SocialSession("old-access", "old-refresh", "user-1", time.time() + 3600, 1)
    )
    backend.session = backend.auth_manager.current()
    requests: list[str] = []

    class Response:
        headers = {}

        def __init__(self, payload: dict):
            self.payload = json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self.payload

    def fake_urlopen(request, **_kwargs):
        path = urllib.parse.urlsplit(request.full_url).path
        requests.append(path)
        if path == "/rest/v1/test" and requests.count(path) == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                401,
                "Unauthorized",
                {},
                io.BytesIO(b'{"code":"PGRST301","message":"JWT expired"}'),
            )
        if path == "/auth/v1/token":
            return Response(
                {
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "expires_in": 3600,
                    "user": {"id": "user-1"},
                }
            )
        return Response({"ok": True})

    monkeypatch.setattr(social_module, "_verified_urlopen", fake_urlopen)
    assert backend._raw("GET", "/rest/v1/test", authenticated=True) == {"ok": True}
    assert requests == ["/rest/v1/test", "/auth/v1/token", "/rest/v1/test"]
    assert backend.auth_manager.current().access_token == "new-access"


def test_private_buddy_notes_decorate_all_local_dashboard_projections():
    data = {
        "buddies": [{"user_id": "buddy-1", "owner_nickname": "公开昵称"}],
        "room_people": [{"user_id": "buddy-1", "owner_nickname": "公开昵称"}],
        "current_room": {
            "room_people": [{"user_id": "buddy-1", "owner_nickname": "公开昵称"}],
            "room_activity": [{"actor_id": "buddy-1", "target_id": "buddy-2", "owner_nickname": "公开昵称"}],
        },
    }

    _apply_buddy_private_notes(data, {"buddy-1": "论文搭子", "buddy-2": "小梁"})

    assert data["buddies"][0]["private_note_name"] == "论文搭子"
    assert data["room_people"][0]["private_note_name"] == "论文搭子"
    assert data["current_room"]["room_people"][0]["private_note_name"] == "论文搭子"
    assert data["current_room"]["room_activity"][0]["actor_private_note_name"] == "论文搭子"
    assert data["current_room"]["room_activity"][0]["target_private_note_name"] == "小梁"


def test_macos_social_transport_uses_verified_certificate_context(monkeypatch):
    """Finder-launched macOS builds must pass a real verifying SSL context."""

    import onepic_desktop_pet.social as social_module

    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, **kwargs):
        captured["request"] = request
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr(social_module.sys, "platform", "darwin")
    monkeypatch.setattr(social_module.urllib.request, "urlopen", fake_urlopen)
    request = social_module.urllib.request.Request("https://example.test/health")

    with social_module._verified_urlopen(request, timeout=3.0):
        pass

    assert captured["timeout"] == 3.0
    context = captured["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


class FakeTransport:
    def __init__(self, name: str, *, fail_dashboard: int = 0, fail_health: int = 0, fail_sign_in: int = 0):
        self.name = name
        self.base_url = f"https://{name}.example.test"
        self.session = SocialSession("token", "refresh", "user-1", 9_999_999_999) if name == "direct" else None
        self.calls = []
        self.fail_dashboard = fail_dashboard
        self.fail_health = fail_health
        self.fail_sign_in = fail_sign_in

    @property
    def signed_in(self):
        return self.session is not None

    def health(self):
        self.calls.append("health")
        if self.fail_health:
            self.fail_health -= 1
            raise SocialError("timeout", kind="timeout", retryable=True)
        return {"ok": True, "backend": self.name}

    def dashboard(self, room_id=None, allow_cache=True):
        self.calls.append(("dashboard", room_id))
        if self.fail_dashboard:
            self.fail_dashboard -= 1
            raise SocialError("timeout", kind="timeout", retryable=True)
        return {"rooms": [], "server_timestamp": "2026-08-14T00:00:00+00:00"}

    def sign_in(self, email, password):
        self.calls.append("sign_in")
        if self.fail_sign_in:
            self.fail_sign_in -= 1
            raise SocialError("timeout", kind="timeout", retryable=True)
        self.session = SocialSession("proxy-token", "proxy-refresh", "user-1", 9_999_999_999)

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None
        return call

    def _save_session(self):
        return None


def test_production_config_uses_supabase_direct_without_proxy():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "config" / "social_backend.json").read_text(encoding="utf-8"))
    assert config["social_backend"] == "supabase_direct"
    assert config["supabase_url"].startswith("https://")
    assert config["supabase_publishable_key"].startswith("sb_publishable_")
    assert config["social_api_base_url"] == ""
    assert "service_role" not in json.dumps(config).lower()


def test_backend_config_ignores_legacy_content_overlay(tmp_path, monkeypatch):
    overlay_root = tmp_path / "content_updates"
    overlay_config = overlay_root / "versions" / "v0.23.60-legacy" / "config" / "social_backend.json"
    overlay_config.parent.mkdir(parents=True)
    overlay_config.write_text(
        json.dumps(
            {
                "social_api_base_url": "https://legacy.example.test/proxy",
                "supabase_url": "https://zkgctfntrioffpifiggk.supabase.co",
                "supabase_publishable_key": "sb_publishable_test",
                "social_backend": "direct_with_cloudbase_fallback",
            }
        ),
        encoding="utf-8",
    )
    (overlay_root / "active.json").write_text(
        json.dumps({"content_version": "v0.23.60", "directory": "v0.23.60-legacy"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    set_content_update_root(overlay_root)
    try:
        client = SocialClient(persist_tokens=False)
        assert client.backend_name == "Supabase Direct"
        assert client.backend_endpoint == "https://zkgctfntrioffpifiggk.supabase.co"
        assert client._manager.proxy is None
    finally:
        set_content_update_root(None)
        clear_content_overlay_cache()


def test_route_manager_retries_direct_once_then_switches_only_network_failures():
    direct = FakeTransport("direct", fail_dashboard=2)
    proxy = FakeTransport("proxy")
    proxy.session = direct.session
    manager = BackendRouteManager(direct, proxy, persist_state=False)
    result = manager.request("dashboard", room_id="room-1")
    assert result["rooms"] == []
    assert direct.calls == [("dashboard", "room-1"), ("dashboard", "room-1")]
    assert proxy.calls == [("dashboard", "room-1")]
    assert manager.current_route == BackendRouteManager.CLOUDBASE_PROXY


def test_route_manager_never_switches_for_business_auth_errors():
    direct = FakeTransport("direct")
    proxy = FakeTransport("proxy")

    def denied(*_args, **_kwargs):
        raise SocialError("forbidden", kind="auth", status=403)

    direct.dashboard = denied
    manager = BackendRouteManager(direct, proxy, persist_state=False)
    try:
        manager.request("dashboard", room_id="room-1")
    except SocialError as exc:
        assert exc.status == 403
    else:
        raise AssertionError("expected business error")
    assert manager.current_route == BackendRouteManager.DIRECT_SUPABASE
    assert proxy.calls == []


def test_health_check_is_one_lightweight_request_and_does_not_load_dashboard():
    direct = FakeTransport("direct")
    proxy = FakeTransport("proxy")
    manager = BackendRouteManager(direct, proxy, persist_state=False)
    client = SocialClient(backend=manager, persist_tokens=False)
    result = client.diagnose_connection()
    assert result["connection_state"] == "ONLINE"
    assert direct.calls == ["health"]
    assert proxy.calls == []


def test_health_rechecks_direct_when_previous_route_was_proxy():
    direct = FakeTransport("direct")
    proxy = FakeTransport("proxy")
    manager = BackendRouteManager(direct, proxy, persist_state=False)
    manager.current_route = BackendRouteManager.CLOUDBASE_PROXY
    client = SocialClient(backend=manager, persist_tokens=False)
    result = client.diagnose_connection()
    assert result["connection_state"] == "ONLINE"
    assert direct.calls == ["health"]
    assert proxy.calls == []
    assert manager.current_route == BackendRouteManager.DIRECT_SUPABASE


def test_health_uses_proxy_only_after_two_direct_network_failures():
    direct = FakeTransport("direct", fail_health=2)
    proxy = FakeTransport("proxy")
    manager = BackendRouteManager(direct, proxy, persist_state=False)
    manager.current_route = BackendRouteManager.CLOUDBASE_PROXY
    client = SocialClient(backend=manager, persist_tokens=False)
    result = client.diagnose_connection()
    assert result["connection_state"] == "ONLINE"
    assert direct.calls == ["health", "health"]
    assert proxy.calls == ["health"]
    assert manager.current_route == BackendRouteManager.CLOUDBASE_PROXY


def test_saved_proxy_route_is_only_a_hint_and_startup_stays_direct(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    state_path = tmp_path / "Lili" / "social-route.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"route": BackendRouteManager.CLOUDBASE_PROXY}), encoding="utf-8")
    manager = BackendRouteManager(FakeTransport("direct"), FakeTransport("proxy"), persist_state=True)
    assert manager.current_route == BackendRouteManager.DIRECT_SUPABASE
    assert manager.last_route_hint == BackendRouteManager.CLOUDBASE_PROXY


def test_sign_in_rechecks_direct_before_using_stale_proxy_route():
    direct = FakeTransport("direct")
    proxy = FakeTransport("proxy")
    manager = BackendRouteManager(direct, proxy, persist_state=False)
    manager.current_route = BackendRouteManager.CLOUDBASE_PROXY
    manager.request("sign_in", "a@example.com", "secret123")
    assert direct.calls == ["health", "sign_in"]
    assert proxy.calls == []
    assert manager.current_route == BackendRouteManager.DIRECT_SUPABASE


def test_sign_in_does_not_submit_credentials_twice_before_proxy_fallback():
    direct = FakeTransport("direct", fail_sign_in=1)
    proxy = FakeTransport("proxy")
    manager = BackendRouteManager(direct, proxy, persist_state=False)
    manager.request("sign_in", "a@example.com", "secret123")
    assert direct.calls == ["health", "sign_in"]
    assert proxy.calls == ["sign_in"]
    assert manager.current_route == BackendRouteManager.CLOUDBASE_PROXY


def test_production_client_exposes_active_session_for_owner_nickname_sync():
    direct = FakeTransport("direct")
    proxy = FakeTransport("proxy")
    manager = BackendRouteManager(direct, proxy, persist_state=False)
    client = SocialClient(backend=manager, persist_tokens=False)

    assert client.signed_in is True
    assert client.session is direct.session
    assert client.session.user_id == "user-1"


def test_http_backend_uses_direct_supabase_paths():
    class Recording(HttpSocialBackend):
        def __init__(self):
            super().__init__("https://supabase.example.test", client_key="sb_publishable_test", persist_tokens=False, transport="direct")
            self.calls = []

        def _raw(self, method, path, body=None, *, authenticated=False, extra_headers=None, timeout=None):
            self.calls.append((method, path, body, authenticated, extra_headers))
            if path.startswith("/auth/v1/token"):
                return {"access_token": "a", "refresh_token": "r", "expires_in": 3600, "user": {"id": "u"}}
            return {}

    backend = Recording()
    backend.sign_in("a@example.com", "secret123")
    backend.health()
    backend.dashboard("room-1")
    backend.heartbeat(working=True, today_seconds=60, session_started_at=None, outfit_key="", room_id="room-1")
    paths = [call[1] for call in backend.calls]
    assert paths == ["/auth/v1/token?grant_type=password", "/auth/v1/health", "/rest/v1/rpc/lili_dashboard", "/rest/v1/rpc/lili_room_dashboard", "/rest/v1/rpc/lili_room_room_rituals", "/rest/v1/rpc/lili_buddy_requests", "/rest/v1/lili_focus_presence?on_conflict=user_id"]
    heartbeat_call = backend.calls[-1]
    heartbeat_body = heartbeat_call[2]
    assert "last_seen" not in heartbeat_body
    assert "updated_at" not in heartbeat_body
    assert heartbeat_call[4] == {"Prefer": "resolution=merge-duplicates,return=minimal"}


def test_heartbeat_refreshes_transport_session_from_auth_manager():
    class Recording(HttpSocialBackend):
        def __init__(self):
            super().__init__(
                "https://supabase.example.test",
                client_key="sb_publishable_test",
                persist_tokens=False,
                transport="direct",
            )
            self.calls = []

        def _raw(self, method, path, body=None, **kwargs):
            self.calls.append((method, path, body, kwargs))
            return {}

    backend = Recording()
    session = SocialSession("access", "refresh", "user-1", 9_999_999_999)
    backend.auth_manager.adopt(session)
    backend.session = None

    backend.heartbeat(
        working=False,
        session_active=False,
        today_seconds=0,
        session_started_at=None,
        outfit_key="",
        room_id=None,
    )

    assert backend.session is session
    assert backend.calls
    assert backend.calls[0][1] == "/rest/v1/lili_focus_presence?on_conflict=user_id"


def test_never_seen_peer_totals_are_zeroed_without_touching_seen_peers():
    data = {
        "buddies": [
            {
                "user_id": "new-user",
                "last_seen_at": None,
                "status_updated_at": None,
                "today_seconds": 3600,
                "week_seconds": 7200,
                "session_seconds": 30,
            },
            {
                "user_id": "seen-user",
                "last_seen_at": "2026-08-28T10:00:00+00:00",
                "status_updated_at": "2026-08-28T10:00:00+00:00",
                "today_seconds": 3600,
                "week_seconds": 7200,
                "session_seconds": 30,
            },
        ]
    }

    _normalise_never_seen_presence(data)

    assert data["buddies"][0]["today_seconds"] == 0
    assert data["buddies"][0]["week_seconds"] == 0
    assert data["buddies"][0]["session_seconds"] == 0
    assert data["buddies"][0]["presence_never_seen"] is True
    assert data["buddies"][1]["today_seconds"] == 3600
    assert data["buddies"][1]["week_seconds"] == 7200


def test_focus_today_migration_ignores_stale_presence_days_and_repairs_room_projection():
    root = Path(__file__).resolve().parents[1]
    migration = (
        root
        / "supabase"
        / "migrations"
        / "20260828000200_lili_focus_presence_date_consistency.sql"
    ).read_text(encoding="utf-8")

    assert "lili_effective_focus_today_seconds" in migration
    assert "f.focus_date = (now() at time zone 'Asia/Shanghai')::date" in migration
    assert "f.last_seen > now() - interval '2 minutes'" in migration
    assert "lili_normalize_focus_today_people" in migration
    assert "lili_zero_never_seen_presence" in migration
    assert "lili_room_dashboard_presence_base_20260828" in migration


def test_private_note_decoration_is_removed_when_note_rpc_returns_no_rows():
    data = {"buddies": [{"user_id": "buddy-1", "private_note_name": "旧备注", "nickname": "小梁"}]}

    _apply_buddy_private_notes(data, {})

    assert "private_note_name" not in data["buddies"][0]


def test_email_normalization_removes_copied_invisible_characters_and_normalizes_domain():
    assert normalize_email("  Alice@EXAMPLE.COM\u200b ") == "Alice@example.com"


def test_signup_reports_confirmation_pending_without_fabricating_a_session():
    class Recording(HttpSocialBackend):
        def __init__(self):
            super().__init__(
                "https://supabase.example.test",
                client_key="sb_publishable_test",
                persist_tokens=False,
                transport="direct",
                email_redirect_url="https://github.com/leungjunchiang/OnePic-Desktop-Pet",
            )
            self.calls = []

        def _raw(self, method, path, body=None, *, authenticated=False, extra_headers=None, timeout=None):
            self.calls.append((method, path, body, authenticated, extra_headers))
            if path.startswith("/auth/v1/signup"):
                return {
                    "access_token": "",
                    "user": {
                        "id": "pending-user",
                        "email": "Alice@example.com",
                        "email_confirmed_at": None,
                        "confirmation_sent_at": "2026-08-22T00:00:00Z",
                    },
                }
            return {}

    backend = Recording()
    result = backend.sign_up(" Alice@EXAMPLE.COM\u200b ", "secret123", "搭子")

    assert isinstance(result, SignupResult)
    assert result.created is True
    assert result.confirmation_pending is True
    assert result.session_active is False
    assert backend.signed_in is False
    assert backend.calls[0][1] == "/auth/v1/signup?redirect_to=https%3A%2F%2Fgithub.com%2Fleungjunchiang%2FOnePic-Desktop-Pet"


def test_repeated_signup_for_confirmed_account_is_not_treated_as_login():
    class Recording(HttpSocialBackend):
        def __init__(self):
            super().__init__(
                "https://supabase.example.test",
                client_key="sb_publishable_test",
                persist_tokens=False,
                transport="direct",
            )

        def _raw(self, method, path, body=None, *, authenticated=False, extra_headers=None, timeout=None):
            return {
                "user": {
                    "id": "existing-user",
                    "email": "Alice@example.com",
                    "email_confirmed_at": "2026-08-22T00:00:00Z",
                    "identities": [],
                },
                "session": None,
            }

    result = Recording().sign_up("Alice@example.com", "a-different-password", "搭子")

    assert result.existing_account is True
    assert result.email_confirmed is True
    assert result.confirmation_pending is False
    assert result.session_active is False
    assert result.created is False
    assert bool(result) is False


def test_repeated_signup_for_unconfirmed_account_requires_resend_instead_of_new_registration():
    class Recording(HttpSocialBackend):
        def __init__(self):
            super().__init__(
                "https://supabase.example.test",
                client_key="sb_publishable_test",
                persist_tokens=False,
                transport="direct",
            )

        def _raw(self, method, path, body=None, *, authenticated=False, extra_headers=None, timeout=None):
            return {
                "user": {
                    "id": "existing-pending-user",
                    "email": "Alice@example.com",
                    "email_confirmed_at": None,
                    "identities": [],
                },
                "session": None,
            }

    result = Recording().sign_up("Alice@example.com", "a-different-password", "搭子")

    assert result.existing_account is True
    assert result.email_confirmed is False
    assert result.confirmation_pending is False
    assert result.created is False


def test_resend_confirmation_uses_supabase_resend_endpoint_and_redirect():
    class Recording(HttpSocialBackend):
        def __init__(self):
            super().__init__(
                "https://supabase.example.test",
                client_key="sb_publishable_test",
                persist_tokens=False,
                transport="direct",
                email_redirect_url="https://github.com/leungjunchiang/OnePic-Desktop-Pet",
            )
            self.call = None

        def _raw(self, method, path, body=None, *, authenticated=False, extra_headers=None, timeout=None):
            self.call = (method, path, body, authenticated, extra_headers)
            return {}

    backend = Recording()
    assert backend.resend_confirmation("Alice@EXAMPLE.COM") is True
    assert backend.call[0:2] == ("POST", "/auth/v1/resend")
    assert backend.call[2] == {
        "type": "signup",
        "email": "Alice@example.com",
        "options": {"emailRedirectTo": "https://github.com/leungjunchiang/OnePic-Desktop-Pet"},
    }


def test_account_security_uses_direct_auth_endpoints_and_never_proxy():
    class Recording(HttpSocialBackend):
        def __init__(self):
            super().__init__(
                "https://supabase.example.test",
                client_key="sb_publishable_test",
                persist_tokens=False,
                transport="direct",
                password_reset_redirect_url="https://leungjunchiang.github.io/OnePic-Desktop-Pet/password-reset.html",
            )
            self.calls = []
            self.session = SocialSession("a", "r", "u", 9_999_999_999, 1, "Alice@example.com")
            self.auth_manager.adopt(self.session)

        def _raw(self, method, path, body=None, *, authenticated=False, extra_headers=None, timeout=None):
            self.calls.append((method, path, body, authenticated))
            if path == "/auth/v1/token?grant_type=password":
                return {
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "expires_in": 3600,
                    "user": {"id": "u", "email": "Alice@example.com"},
                }
            return {}

    backend = Recording()
    backend.change_password("old-secret", "new-secret-123")
    assert backend.request_password_reset("Alice@EXAMPLE.COM") is True
    backend.delete_account()
    assert backend.calls == [
        ("POST", "/auth/v1/token?grant_type=password", {"email": "Alice@example.com", "password": "old-secret"}, False),
        ("PUT", "/auth/v1/user", {"current_password": "old-secret", "password": "new-secret-123"}, True),
        ("POST", "/auth/v1/recover", {"email": "Alice@example.com", "redirect_to": "https://leungjunchiang.github.io/OnePic-Desktop-Pet/password-reset.html"}, False),
        ("POST", "/rest/v1/rpc/lili_delete_my_account", {}, True),
    ]


def test_password_recovery_uses_email_otp_then_authenticated_update():
    class Recording(HttpSocialBackend):
        def __init__(self):
            super().__init__(
                "https://supabase.example.test",
                client_key="sb_publishable_test",
                persist_tokens=False,
                transport="direct",
            )
            self.calls = []

        def _raw(self, method, path, body=None, *, authenticated=False, extra_headers=None, timeout=None):
            self.calls.append((method, path, body, authenticated))
            if path == "/auth/v1/verify":
                return {
                    "access_token": "recovery-access",
                    "refresh_token": "recovery-refresh",
                    "expires_in": 600,
                    "user": {"id": "u", "email": "Alice@example.com"},
                }
            return {}

    backend = Recording()
    assert backend.verify_password_reset_otp("Alice@EXAMPLE.COM", "123456") is True
    backend.set_password_after_reset("new-secret-123")
    assert backend.calls == [
        ("POST", "/auth/v1/verify", {"email": "Alice@example.com", "token": "123456", "type": "recovery"}, False),
        ("PUT", "/auth/v1/user", {"password": "new-secret-123"}, True),
    ]


def test_security_actions_are_not_replayed_to_proxy_after_direct_failure():
    direct = FakeTransport("direct")
    proxy = FakeTransport("proxy")
    manager = BackendRouteManager(direct, proxy, persist_state=False)
    direct.change_password = lambda *_args, **_kwargs: (_ for _ in ()).throw(SocialError("timeout", kind="timeout", retryable=True))

    with pytest.raises(SocialError):
        manager.request("change_password", "old", "new-password")

    assert not any(call == "change_password" for call in proxy.calls)


def test_direct_presence_heartbeat_uses_postgrest_upsert_header():
    class Recording(HttpSocialBackend):
        def __init__(self):
            super().__init__("https://supabase.example.test", client_key="sb_publishable_test", persist_tokens=False, transport="direct")
            self.headers = None
            self.session = SocialSession("a", "r", "u", 9_999_999_999)

        def _raw(self, method, path, body=None, *, authenticated=False, extra_headers=None, timeout=None):
            self.headers = extra_headers
            return {}

    backend = Recording()
    backend.heartbeat(working=True, today_seconds=60, session_started_at=None, outfit_key="", room_id="room-1")
    assert backend.headers == {"Prefer": "resolution=merge-duplicates,return=minimal"}


def test_presence_heartbeat_sends_stable_account_device_lease(monkeypatch, tmp_path):
    social_module._PRESENCE_DEVICE_STATE_CACHE.clear()
    monkeypatch.setattr(
        social_module,
        "account_local_data_path",
        lambda filename, account_id: tmp_path / f"{account_id}-{filename}",
    )

    class Recording(HttpSocialBackend):
        def __init__(self):
            super().__init__(
                "https://supabase.example.test",
                client_key="sb_publishable_test",
                persist_tokens=False,
                transport="direct",
            )
            self.session = SocialSession("a", "r", "lease-user", 9_999_999_999)
            self.bodies = []

        def _raw(self, method, path, body=None, **kwargs):
            self.bodies.append(body)
            return {}

    backend = Recording()
    backend.heartbeat(working=True, today_seconds=60, session_started_at=None, outfit_key="", room_id="room-1")
    backend.heartbeat(working=True, today_seconds=61, session_started_at=None, outfit_key="", room_id="room-1")

    first, second = backend.bodies
    assert len(first["device_id"]) == 32
    assert first["device_id"] == second["device_id"]
    assert first["device_claim"] is True
    assert second["device_claim"] is False
    social_module._PRESENCE_DEVICE_STATE_CACHE.clear()


def test_presence_freshness_is_server_authoritative_in_all_relays():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase" / "migrations" / "20260815000100_lili_presence_server_timestamp.sql").read_text(encoding="utf-8")
    cloudbase = (root / "relay" / "cloudbase-function" / "index.js").read_text(encoding="utf-8")
    edge = (root / "supabase" / "functions" / "lili-social-relay" / "index.ts").read_text(encoding="utf-8")
    worker = (root / "relay" / "cloudflare-worker" / "src" / "index.js").read_text(encoding="utf-8")
    assert "new.last_seen := now()" in migration
    assert "create trigger lili_presence_server_timestamp" in migration
    assert "last_seen: now" in cloudbase
    assert "last_seen: now" in edge
    assert "last_seen: now" in worker
    assert "String(body.last_seen || now)" not in cloudbase + edge + worker
    assert "device_id" in cloudbase + edge + worker
    assert "device_claim" in cloudbase + edge + worker


def test_cloudbase_presence_and_profile_proxy_preserve_upsert_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    cloudbase = (root / "relay" / "cloudbase-function" / "index.js").read_text(encoding="utf-8")
    assert 'Prefer: "resolution=merge-duplicates,return=minimal"' in cloudbase
    assert "wealth_leaderboard_enabled" in cloudbase
    assert "wealth_leaderboard_preference_set" in cloudbase


def test_personal_focus_and_outfit_state_is_server_merged_and_proxy_allowlisted() -> None:
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase" / "migrations" / "20260821000200_lili_personal_state_sync.sql").read_text(encoding="utf-8")
    assert "focus_lifetime_seconds" in migration
    assert "focus_today_seconds" in migration
    assert "create or replace function public.lili_sync_personal_state" in migration
    assert "greatest(p.focus_lifetime_seconds" in migration
    for path in (
        root / "relay" / "cloudbase-function" / "index.js",
        root / "supabase" / "functions" / "lili-social-relay" / "index.ts",
        root / "relay" / "cloudflare-worker" / "src" / "index.js",
    ):
        assert "lili_sync_personal_state" in path.read_text(encoding="utf-8")


def test_weekly_focus_sync_and_leaderboard_are_available_in_every_relay() -> None:
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase" / "migrations" / "20260821000400_lili_weekly_focus_dashboard.sql").read_text(encoding="utf-8")
    assert "focus_week_seconds" in migration
    assert "lili_focus_weekly_leaderboard" in migration
    assert "p_week_seconds" in migration
    assert "p.visibility <> 'friends' then 'offline'" in migration
    for path in (
        root / "supabase" / "functions" / "lili-social-relay" / "index.ts",
        root / "relay" / "cloudbase-function" / "index.js",
        root / "relay" / "cloudflare-worker" / "src" / "index.js",
    ):
        assert "lili_focus_weekly_leaderboard" in path.read_text(encoding="utf-8")


def test_daily_focus_history_is_account_scoped_and_allowlisted() -> None:
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase" / "migrations" / "20260822000300_lili_focus_daily_history.sql").read_text(encoding="utf-8")
    assert "create table if not exists public.lili_focus_daily" in migration
    assert "primary key (user_id, focus_date)" in migration
    assert "alter table public.lili_focus_daily enable row level security" in migration
    assert "lili_sync_focus_history" in migration
    assert "(select auth.uid())" in migration
    for path in (
        root / "supabase" / "functions" / "lili-social-relay" / "index.ts",
        root / "relay" / "cloudbase-function" / "index.js",
        root / "relay" / "cloudflare-worker" / "src" / "index.js",
    ):
        assert "lili_sync_focus_history" in path.read_text(encoding="utf-8")


def test_weekly_leaderboard_uses_canonical_daily_focus_totals() -> None:
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase" / "migrations" / "20260825075419_lili_focus_weekly_leaderboard_daily_source.sql").read_text(encoding="utf-8")
    assert "create or replace function public.lili_focus_weekly_leaderboard" in migration
    assert "public.lili_focus_daily" in migration
    assert "sum(greatest(0, least(86400, d.seconds)))" in migration
    assert "when coalesce(d.day_count, 0) > 0" in migration
    assert "least(604800" in migration


def test_latest_focus_stats_migration_uses_raw_interval_union_and_reconciles_caches() -> None:
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase" / "migrations" / "20260829000100_lili_focus_canonical_session_stats.sql").read_text(encoding="utf-8")

    assert "lili_focus_segment_is_valid" in migration
    assert "range_agg" in migration
    assert "extract(epoch from upper(r) - lower(r))" in migration
    assert "lili_effective_focus_stats" in migration
    assert "lili_effective_focus_week_seconds" in migration
    assert "lili_reconcile_focus_derived_totals" in migration
    assert "perform public.lili_reconcile_focus_derived_totals(current_user_id)" in migration
    assert "focus_week_seconds" in migration
    assert "-- Repair the active Beijing week, including zero days." in migration


def test_daily_focus_summary_is_permanent_but_sync_view_is_two_days() -> None:
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase" / "migrations" / "20260822000400_lili_focus_daily_visibility.sql").read_text(encoding="utf-8")
    assert "permanent, one-row-per-account-per-Beijing-day" in migration
    assert "'retention_days', 2" in migration
    assert "d.focus_date between today - 1 and today" in migration
    assert "delete from public.lili_focus_daily" not in migration.lower()


def test_room_focus_time_uses_session_ledger_not_legacy_accumulator():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase" / "migrations" / "20260815000200_lili_room_focus_ledger.sql").read_text(encoding="utf-8")
    assert "create table if not exists public.lili_room_focus_sessions" in migration
    assert "create unique index if not exists lili_room_focus_sessions_one_active_user" in migration
    assert "public.lili_room_focus_seconds(r.id)" in migration
    assert "update public.lili_room_focus_totals" in migration
    assert "lili_presence_focus_session" in migration


def test_private_buddy_note_migration_is_owner_scoped_and_proxy_allowlisted():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase" / "migrations" / "20260817000100_lili_buddy_private_notes.sql").read_text(encoding="utf-8")
    assert "create table if not exists public.lili_buddy_private_notes" in migration
    assert "alter table public.lili_buddy_private_notes enable row level security" in migration
    assert "(select auth.uid()) = owner_user_id" in migration
    assert "lili_set_buddy_private_note" in migration
    assert "lili_buddy_private_notes()" in migration
    for path in (
        root / "supabase" / "functions" / "lili-social-relay" / "index.ts",
        root / "relay" / "cloudbase-function" / "index.js",
        root / "relay" / "cloudflare-worker" / "src" / "index.js",
    ):
        source = path.read_text(encoding="utf-8")
        assert "lili_buddy_private_notes" in source
        assert "lili_set_buddy_private_note" in source


def test_buddy_controls_migration_and_proxy_routes_are_present():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase" / "migrations" / "20260822000100_lili_buddy_controls.sql").read_text(encoding="utf-8")
    assert "lili_remove_buddy" in migration
    assert "revoke execute on function public.lili_remove_buddy(uuid) from public, anon" in migration
    assert "'muted_buddy_ids'" in migration
    assert "'notifications_muted'" in migration
    for path in (
        root / "src" / "onepic_desktop_pet" / "social.py",
        root / "supabase" / "functions" / "lili-social-relay" / "index.ts",
        root / "relay" / "cloudbase-function" / "index.js",
        root / "relay" / "cloudflare-worker" / "src" / "index.js",
    ):
        source = path.read_text(encoding="utf-8")
        assert "lili_remove_buddy" in source
        assert "/buddies/remove" in source


def test_taunt_migration_is_persistent_and_presence_started():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase" / "migrations" / "20260825094744_lili_taunt_state.sql").read_text(encoding="utf-8")
    assert "create table if not exists public.lili_buddy_taunts" in migration
    assert "started_working_at" in migration
    assert "lili_mark_taunt_started_on_presence" in migration
    assert "lili_send_taunt" in migration
    assert "lili_taunt_state" in migration
    assert "interval '20 minutes'" in migration
    assert "lili_are_buddies" in migration
    for path in (
        root / "src" / "onepic_desktop_pet" / "social.py",
        root / "supabase" / "functions" / "lili-social-relay" / "index.ts",
        root / "relay" / "cloudbase-function" / "index.js",
        root / "relay" / "cloudflare-worker" / "src" / "index.js",
    ):
        source = path.read_text(encoding="utf-8")
        assert "lili_send_taunt" in source
        assert "lili_taunt_state" in source


def test_buddy_reaction_migration_supports_redeemable_taunts_and_encouragement():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase" / "migrations" / "20260825110000_lili_buddy_reactions.sql").read_text(encoding="utf-8")
    assert "lili_buddy_encouragements" in migration
    assert "lili_send_encouragement" in migration
    assert "lili_encouragement_state" in migration
    assert "lili_reaction_state" in migration
    assert "interval '1 hour'" in migration
    assert "每天最多嘲讽 3 次" in migration
    assert "对方正在被搭子抓包，等惩罚结束后再加油" in migration
    assert "interval '30 minutes'" in migration
    assert "worked_seconds" in migration
    for path in (
        root / "src" / "onepic_desktop_pet" / "social.py",
        root / "supabase" / "functions" / "lili-social-relay" / "index.ts",
        root / "relay" / "cloudbase-function" / "index.js",
        root / "relay" / "cloudflare-worker" / "src" / "index.js",
    ):
        source = path.read_text(encoding="utf-8")
        assert "lili_send_encouragement" in source
        assert "lili_encouragement_state" in source
        assert "lili_reaction_state" in source


def test_taunt_time_window_migration_switches_to_after_hours_encouragement():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase" / "migrations" / "20260825190000_lili_taunt_time_window.sql").read_text(encoding="utf-8")
    assert "local_minutes between 480 and 1350" in migration
    assert "kind', 'encouragement'" in migration
    assert "created_at >= day_start" in migration
    assert "lili_send_encouragement" in migration
    assert "taunt_window and not exists" in migration


def test_buddy_request_state_machine_is_idempotent_and_allowlisted():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase" / "migrations" / "20260822000200_lili_buddy_request_state_machine.sql").read_text(encoding="utf-8")
    assert "on conflict do nothing" in migration
    assert "lili_lookup_buddy_by_code" in migration
    assert "lili_buddy_requests" in migration
    assert "lili_cancel_buddy_request" in migration
    assert "status in ('pending', 'accepted', 'declined', 'rejected', 'cancelled')" in migration
    assert "on conflict do nothing" in migration
    for path in (
        root / "src" / "onepic_desktop_pet" / "social.py",
        root / "supabase" / "functions" / "lili-social-relay" / "index.ts",
        root / "relay" / "cloudbase-function" / "index.js",
        root / "relay" / "cloudflare-worker" / "src" / "index.js",
    ):
        source = path.read_text(encoding="utf-8")
        assert "lili_lookup_buddy_by_code" in source
        assert "lili_cancel_buddy_request" in source


def test_economy_migration_is_rls_scoped_and_friend_opt_in():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase" / "migrations" / "20260818000100_lili_economy_wallet.sql").read_text(encoding="utf-8")
    assert "create table if not exists public.lili_economy_events" in migration
    assert "alter table public.lili_economy_events enable row level security" in migration
    assert "user_id = auth.uid()" in migration
    assert "wealth_leaderboard_enabled" in migration
    assert "public.lili_are_buddies(auth.uid(), p.user_id)" in migration
    assert "lili_economy_leaderboard" in migration


def test_latest_economy_rules_default_leaderboard_and_witnessed_achievements():
    root = Path(__file__).resolve().parents[1]
    leaderboard = (root / "supabase" / "migrations" / "20260819000200_lili_wealth_leaderboard_default_opt_in.sql").read_text(encoding="utf-8")
    witness_base = (root / "supabase" / "migrations" / "20260819000300_lili_achievement_witnesses.sql").read_text(encoding="utf-8")
    witness = (root / "supabase" / "migrations" / "20260819000500_lili_achievement_manual_witnesses.sql").read_text(encoding="utf-8")
    assert "alter column wealth_leaderboard_enabled set default true" in leaderboard
    assert "where not wealth_leaderboard_preference_set" in leaderboard
    assert "period_income" in leaderboard
    assert "create table if not exists public.lili_achievement_claims" in witness_base
    assert "amount between 1 and 200" in witness
    assert "lili_respond_achievement_witness" in witness
    assert "lili_replace_achievement_witnesses" in witness
    assert "p_witness_ids" in witness
    assert "fixed_reward" in witness
    assert "reward', 200" in witness


def test_room_dashboard_exposes_today_and_cumulative_focus_metrics():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase" / "migrations" / "20260816000100_lili_room_focus_today_total.sql").read_text(encoding="utf-8")
    assert "lili_room_focus_seconds_today" in migration
    assert "today_shared_focus_seconds" in migration
    assert "cumulative_shared_focus_seconds" in migration
    assert "Asia/Shanghai" in migration


def test_new_cloudbase_function_is_proxy_not_a_database_client():
    root = Path(__file__).resolve().parents[1]
    source = (root / "relay" / "cloudbase-function" / "index.js").read_text(encoding="utf-8").lower()
    package = (root / "relay" / "cloudbase-function" / "package.json").read_text(encoding="utf-8").lower()
    assert "source_of_truth" in source
    assert "supabase" in source
    assert "cloudbase-store" not in source
    assert "@cloudbase/node-sdk" not in package


def test_recent_cached_dashboard_preserves_last_known_peer_presence():
    direct = FakeTransport("direct")
    proxy = FakeTransport("proxy")
    proxy.session = direct.session
    manager = BackendRouteManager(direct, proxy, persist_state=False)
    client = SocialClient(backend=manager, persist_tokens=False)
    client._dashboard_cache = {
        "user-1:room-1": {
            "account_id": "user-1",
            "saved_at": time.time() - 90,
            "data": {
                "buddies": [
                    {
                        "user_id": "buddy-1",
                        "owner_nickname": "小梁",
                        "online": True,
                        "working": True,
                        "status": "focus",
                        "today_seconds": 600,
                    }
                ],
                "room_summary": {"member_count": 2, "focus_count": 1},
            },
        }
    }

    cached = client.cached_dashboard("room-1")

    assert cached is not None
    assert cached["_connection_state"] == "DEGRADED"
    assert cached["_presence_grace_active"] is True
    assert client.connection_state == "DEGRADED"
    peer = cached["buddies"][0]
    assert peer["online"] is True
    assert peer["working"] is True
    assert peer["presence_uncertain"] is True
    assert peer.get("stale_presence") is not True
    assert cached["room_summary"]["presence_uncertain"] is True


def test_old_cached_dashboard_is_marked_offline_after_presence_grace():
    direct = FakeTransport("direct")
    proxy = FakeTransport("proxy")
    proxy.session = direct.session
    manager = BackendRouteManager(direct, proxy, persist_state=False)
    client = SocialClient(backend=manager, persist_tokens=False)
    client._dashboard_cache = {
        "user-1:room-1": {
            "account_id": "user-1",
            "saved_at": time.time() - PRESENCE_GRACE_SECONDS - 1,
            "data": {
                "buddies": [
                    {"user_id": "buddy-1", "online": True, "working": True, "status": "focus"}
                ]
            },
        }
    }

    cached = client.cached_dashboard("room-1")

    assert cached is not None
    assert cached["_connection_state"] == "OFFLINE"
    assert cached["_presence_grace_active"] is False
    assert client.connection_state == "OFFLINE"
    peer = cached["buddies"][0]
    assert peer["online"] is False
    assert peer["working"] is False
    assert peer["stale_presence"] is True
    assert peer["presence_uncertain"] is False


def test_dashboard_cache_rejects_unscoped_or_other_account_snapshots():
    direct = FakeTransport("direct")
    proxy = FakeTransport("proxy")
    proxy.session = direct.session
    manager = BackendRouteManager(direct, proxy, persist_state=False)
    client = SocialClient(backend=manager, persist_tokens=False)
    client._dashboard_cache = {
        "room-1": {"saved_at": time.time(), "data": {"buddies": []}},
        "user-2:room-1": {
            "account_id": "user-2",
            "saved_at": time.time(),
            "data": {"buddies": []},
        },
    }

    assert client.cached_dashboard("room-1") is None


def test_room_shared_state_migrations_are_retained_as_supabase_history():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase" / "migrations" / "20260813150000_lili_room_shared_state.sql").read_text(encoding="utf-8")
    assert "lili_room_dashboard" in migration
    assert "lili_room_events" in migration



def test_refresh_token_reuse_keeps_last_session_and_requests_relogin():
    manager = AuthSessionManager(
        service_name="LiliSocialTest",
        account_name="reuse-recovery",
        persist_tokens=False,
    )
    manager.adopt(SocialSession("old-access", "old-refresh", "user-1", time.time() - 1, 12))

    def refresh(_current: SocialSession) -> dict:
        raise SocialError(
            "Invalid Refresh Token: Already Used",
            kind="auth_refresh_reused",
            error_code="refresh_token_already_used",
        )

    with pytest.raises(SocialError):
        manager.get_valid_session(refresh, requested_by="test")
    assert manager.current() is not None
    assert manager.current().refresh_token == "old-refresh"
    assert manager.requires_relogin


def test_successful_password_login_clears_stale_relogin_marker():
    manager = AuthSessionManager(
        service_name="LiliSocialTest",
        account_name="relogin-recovery",
        persist_tokens=False,
    )
    manager.adopt(SocialSession("old-access", "old-refresh", "user-1", time.time() - 1, 1))

    def refresh(_current: SocialSession) -> dict:
        raise SocialError(
            "Invalid Refresh Token",
            kind="auth_refresh",
            error_code="invalid_refresh_token",
        )

    with pytest.raises(SocialError):
        manager.get_valid_session(refresh, requested_by="test")
    assert manager.requires_relogin

    recovered = manager.accept_auth(
        {
            "access_token": "fresh-access",
            "refresh_token": "fresh-refresh",
            "expires_in": 3600,
            "user": {"id": "user-1"},
        }
    )
    assert recovered is not None
    assert manager.requires_relogin is False
    assert manager.current().access_token == "fresh-access"

def test_presence_transitions_are_not_persisted_as_room_history():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase" / "migrations" / "20260818104607_lili_presence_event_retention.sql").read_text(encoding="utf-8")
    assert "drop trigger if exists lili_presence_room_event" in migration
    assert "where kind in ('join','leave','focus_start','focus_pause')" in migration
    assert "create trigger lili_presence_room_event" not in migration
    assert "focus_finish" in migration
    assert "challenge_complete" in migration

def test_signup_timeout_is_not_replayed_to_proxy():
    class SignupTimeoutTransport(FakeTransport):
        def sign_up(self, email, password, nickname):
            self.calls.append(("sign_up", email))
            raise SocialError(
                "SMTP request timed out",
                kind="signup_timeout",
                retryable=True,
                status=504,
            )

    direct = SignupTimeoutTransport("direct")
    proxy = FakeTransport("proxy")
    manager = BackendRouteManager(direct, proxy, persist_state=False)

    with pytest.raises(SocialError, match="SMTP request timed out"):
        manager.request("sign_up", "a@example.com", "secret123", "小梁")

    assert direct.calls == ["health", ("sign_up", "a@example.com")]
    assert proxy.calls == []


def test_signup_timeout_message_warns_against_duplicate_registration():
    message = social_user_message(
        SocialError(
            "SMTP request timed out",
            kind="signup_timeout",
            retryable=True,
            status=504,
        )
    )

    assert "不要重复注册" in message
    assert "重新发送确认邮件" in message

