"""验证搭子客户端只同步最小状态，并且数据库启用严格权限。"""

from pathlib import Path

from onepic_desktop_pet.social import SocialClient, SocialSession


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
    client.heartbeat(working=True, today_seconds=2520, session_started_at=None, outfit_key="wild-king")
    presence = client.calls[-1][2]
    assert set(presence) == {"user_id","working","session_started_at","focus_date","today_seconds","outfit_key","room_id","last_seen","updated_at"}
    assert not any(key in presence for key in ("password","task","chat","window_title"))


def test_social_schema_uses_rls_and_authenticated_functions() -> None:
    root = Path(__file__).resolve().parents[1]
    sql = (root / "supabase" / "migrations" / "202608110001_lili_social_rooms.sql").read_text(encoding="utf-8")
    for table in ("lili_profiles","lili_buddy_links","lili_study_rooms","lili_room_members","lili_focus_presence","lili_visit_events"):
        assert f"alter table public.{table} enable row level security" in sql
    assert "grant execute" in sql and "to authenticated" in sql
    assert "service_role" not in (root / "config" / "social_backend.json").read_text(encoding="utf-8")
