"""Tests for Supabase-first routing and the CloudBase proxy fallback."""

import json
import ssl
import sys
import time
from pathlib import Path

from onepic_desktop_pet.social import (
    BackendRouteManager,
    HttpSocialBackend,
    PRESENCE_GRACE_SECONDS,
    SocialClient,
    SocialError,
    SocialSession,
)


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


def test_production_config_has_one_supabase_source_and_proxy_url():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "config" / "social_backend.json").read_text(encoding="utf-8"))
    assert config["social_backend"] == "direct_with_cloudbase_fallback"
    assert config["supabase_url"].startswith("https://")
    assert config["supabase_publishable_key"].startswith("sb_publishable_")
    assert ".tcloudbase.com/" in config["social_api_base_url"]
    assert "service_role" not in json.dumps(config).lower()


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

        def _raw(self, method, path, body=None, *, authenticated=False, extra_headers=None):
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
    assert paths == ["/auth/v1/token?grant_type=password", "/auth/v1/health", "/rest/v1/rpc/lili_dashboard", "/rest/v1/rpc/lili_room_dashboard", "/rest/v1/rpc/lili_room_room_rituals", "/rest/v1/lili_focus_presence?on_conflict=user_id"]
    heartbeat_call = backend.calls[-1]
    heartbeat_body = heartbeat_call[2]
    assert "last_seen" not in heartbeat_body
    assert "updated_at" not in heartbeat_body
    assert heartbeat_call[4] == {"Prefer": "resolution=merge-duplicates,return=minimal"}


def test_direct_presence_heartbeat_uses_postgrest_upsert_header():
    class Recording(HttpSocialBackend):
        def __init__(self):
            super().__init__("https://supabase.example.test", client_key="sb_publishable_test", persist_tokens=False, transport="direct")
            self.headers = None
            self.session = SocialSession("a", "r", "u", 9_999_999_999)

        def _raw(self, method, path, body=None, *, authenticated=False, extra_headers=None):
            self.headers = extra_headers
            return {}

    backend = Recording()
    backend.heartbeat(working=True, today_seconds=60, session_started_at=None, outfit_key="", room_id="room-1")
    assert backend.headers == {"Prefer": "resolution=merge-duplicates,return=minimal"}


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


def test_room_focus_time_uses_session_ledger_not_legacy_accumulator():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase" / "migrations" / "20260815000200_lili_room_focus_ledger.sql").read_text(encoding="utf-8")
    assert "create table if not exists public.lili_room_focus_sessions" in migration
    assert "create unique index if not exists lili_room_focus_sessions_one_active_user" in migration
    assert "public.lili_room_focus_seconds(r.id)" in migration
    assert "update public.lili_room_focus_totals" in migration
    assert "lili_presence_focus_session" in migration


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
        "room-1": {
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
        "room-1": {
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


def test_room_shared_state_migrations_are_retained_as_supabase_history():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase" / "migrations" / "20260813150000_lili_room_shared_state.sql").read_text(encoding="utf-8")
    assert "lili_room_dashboard" in migration
    assert "lili_room_events" in migration
