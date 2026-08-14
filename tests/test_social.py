"""验证搭子客户端只同步最小状态，并且数据库启用严格权限。"""

import json
from pathlib import Path

from onepic_desktop_pet.social import HttpSocialBackend, SocialClient, SocialError, SocialSession


class RecordingClient(SocialClient):
    def __init__(self) -> None:
        super().__init__(persist_tokens=False); self.calls = []

    def _raw(self, method, path, body=None, **kwargs):
        self.calls.append((method, path, body, kwargs))
        if "/signup" in path:
            return {"access_token":"a","refresh_token":"r","expires_in":3600,"user":{"id":"u"}}
        return {}


def test_signup_and_presence_never_include_password_or_task_content() -> None:
    client = RecordingClient()
    assert client.sign_up("a@example.com", "secret123", "小梁") is True
    signup_call = client.calls[0]
    assert "redirect_to=https%3A%2F%2Fgithub.com%2Fleungjunchiang%2FOnePic-Desktop-Pet" in signup_call[1]
    assert signup_call[2] == {"email": "a@example.com", "password": "secret123", "data": {"nickname": "小梁"}}
    client.heartbeat(working=True, today_seconds=2520, session_started_at=None, outfit_key="wild-king")
    presence = client.calls[-1][2]
    assert set(presence) == {"user_id","working","session_started_at","focus_date","today_seconds","outfit_key","room_id","quick_status","quick_status_expires_at","last_seen","updated_at"}
    assert not any(key in presence for key in ("password","task","chat","window_title"))


def test_social_schema_uses_rls_and_authenticated_functions() -> None:
    root = Path(__file__).resolve().parents[1]
    sql = (root / "supabase" / "migrations" / "202608110001_lili_social_rooms.sql").read_text(encoding="utf-8")
    for table in ("lili_profiles","lili_buddy_links","lili_study_rooms","lili_room_members","lili_focus_presence","lili_visit_events"):
        assert f"alter table public.{table} enable row level security" in sql
    assert "grant execute" in sql and "to authenticated" in sql
    assert "service_role" not in (root / "config" / "social_backend.json").read_text(encoding="utf-8")


def test_visit_dashboard_syncs_only_start_time_and_minimum_presence() -> None:
    root = Path(__file__).resolve().parents[1]
    migration = root / "supabase" / "migrations" / "20260811104519_lili_visit_started_at.sql"
    sql = migration.read_text(encoding="utf-8")
    assert "'visit_started_at'" in sql
    assert "coalesce(v.responded_at,v.created_at)" in sql
    assert "set search_path = ''" in sql
    assert "revoke execute on function public.lili_dashboard() from public, anon" in sql
    for forbidden in ("chat", "task", "window_title", "animation_frame"):
        assert forbidden not in sql


def test_focus_presence_dashboard_returns_explicit_current_status() -> None:
    root = Path(__file__).resolve().parents[1]
    migration = root / "supabase" / "migrations" / "20260813130000_lili_focus_presence_sync.sql"
    sql = migration.read_text(encoding="utf-8")
    assert "'me_presence'" in sql
    assert "'session_seconds'" in sql
    assert "'status'" in sql
    assert "'focus'" in sql and "'rest'" in sql and "'offline'" in sql
    assert "f.last_seen>now()-interval '2 minutes'" in sql


def test_social_helper_functions_are_executable_by_authenticated_rls() -> None:
    root = Path(__file__).resolve().parents[1]
    migration = root / "supabase" / "migrations" / "20260813134500_lili_social_helper_permissions.sql"
    sql = migration.read_text(encoding="utf-8")
    assert "grant execute on function public.lili_are_buddies(uuid, uuid) to authenticated" in sql
    assert "grant execute on function public.lili_share_room(uuid, uuid) to authenticated" in sql
    assert "anon" not in sql.lower()


class RecordingHttpBackend(HttpSocialBackend):
    def __init__(self) -> None:
        super().__init__("https://social.example.test", persist_tokens=False)
        self.calls = []

    def _raw(self, method, path, body=None, **kwargs):
        self.calls.append((method, path, body, kwargs))
        if path == "/auth/signin":
            return {"access_token": "a", "refresh_token": "r", "expires_in": 3600, "user": {"id": "u"}}
        return {}


def test_http_social_backend_uses_proxy_routes_without_supabase_paths() -> None:
    backend = RecordingHttpBackend()
    backend.sign_in("a@example.com", "secret123")
    backend.heartbeat(working=True, today_seconds=60, session_started_at=None, outfit_key="wild-king", room_id="room-1")
    backend.dashboard()
    backend.rpc("lili_send_visit", {"target": "u2", "visit_kind": "visit"})

    assert backend.calls[0][1] == "/auth/signin"
    assert backend.calls[1][0:2] == ("POST", "/presence/heartbeat")
    assert backend.calls[1][2]["focus_date"]
    assert backend.calls[2][0:2] == ("GET", "/dashboard")
    assert backend.calls[3][0:2] == ("POST", "/visits/send")
    assert all("supabase" not in str(call[1]).lower() for call in backend.calls)


def test_health_probe_identifies_active_transport_without_authentication() -> None:
    backend = RecordingHttpBackend()
    assert backend.health() == {}
    assert backend.calls[-1][0:2] == ("GET", "/health")

    client = RecordingClient()
    assert client.backend_name == "supabase"
    assert client.backend_endpoint.endswith("supabase.co")
    assert client.health() == {}
    assert client.calls[-1][0:2] == ("GET", "/auth/v1/health")


def test_social_config_exposes_optional_proxy_base_url() -> None:
    root = Path(__file__).resolve().parents[1]
    config = (root / "config" / "social_backend.json").read_text(encoding="utf-8")
    assert '"social_api_base_url"' in config
    assert '"social_backend"' in config


def test_published_config_uses_supabase_edge_relay_by_default() -> None:
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "config" / "social_backend.json").read_text(encoding="utf-8"))
    assert config["social_backend"] == "http"
    assert config["social_api_base_url"].endswith("/functions/v1/lili-social-relay-v2")


def test_dashboard_falls_back_to_last_payload_when_network_is_unavailable() -> None:
    client = RecordingClient()
    client.session = SocialSession("a", "r", "u", 9_999_999_999)
    payload = {"me": {"nickname": "小梁"}, "rooms": [{"id": "room-1"}]}
    client._raw = lambda *_args, **_kwargs: payload
    assert client.dashboard("room-1")["rooms"][0]["id"] == "room-1"

    def unavailable(*_args, **_kwargs):
        raise SocialError("网络不可达")

    client._raw = unavailable
    cached = client.dashboard("room-1")
    assert cached["_sync_offline"] is True
    assert cached["rooms"][0]["id"] == "room-1"


def test_room_shared_state_migration_scopes_members_goals_events_and_cooldown() -> None:
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase" / "migrations" / "20260813150000_lili_room_shared_state.sql").read_text(encoding="utf-8")
    assert "create table if not exists public.lili_room_goals" in migration
    assert "create table if not exists public.lili_room_events" in migration
    assert "lili_room_dashboard" in migration
    assert "互动太频繁，请稍后再试" in migration
    assert "lili_presence_room_event" in migration
    assert "revoke execute on function public.lili_room_dashboard(uuid) from public, anon" in migration
    totals = (root / "supabase" / "migrations" / "20260813153000_lili_room_focus_totals.sql").read_text(encoding="utf-8")
    assert "lili_room_focus_totals" in totals
    assert "cumulative_seconds" in totals


def test_owner_nickname_and_room_quick_status_migration_is_explicit() -> None:
    root = Path(__file__).resolve().parents[1]
    migration = next((root / "supabase" / "migrations").glob("*_owner_nickname_room_quick_status.sql"))
    sql = migration.read_text(encoding="utf-8")
    assert "add column if not exists owner_nickname" in sql
    assert "add column if not exists quick_status" in sql
    assert "quick_status_expires_at" in sql
    assert "p_room_id" in sql
    assert "on conflict on constraint lili_room_goals_pkey" in sql
    assert "on conflict on constraint lili_room_schedules_pkey" in sql
    assert "on conflict on constraint lili_room_challenges_pkey" in sql
    assert "on conflict on constraint lili_buddy_subscriptions_pkey" in sql
    assert "六毛搭子家的六毛" not in sql
