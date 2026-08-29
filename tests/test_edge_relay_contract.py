from pathlib import Path


def test_social_edge_relay_has_stable_routes_and_no_server_secret() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "supabase" / "functions" / "lili-social-relay" / "index.ts").read_text(encoding="utf-8")

    for route in ("/health", "/auth/signup", "/auth/resend", "/auth/signin", "/auth/refresh", "/dashboard", "/presence/heartbeat", "/rpc/"):
        assert route in source
    assert "function relativePath(url: URL)" in source
    assert "/functions/v1/" in source
    assert 'const FUNCTION_SLUGS = ["lili-social-relay-v2", "lili-social-relay"]' in source
    assert 'event: "route_not_found"' in source
    for operation in ("lili_room_dashboard", "lili_set_room_goal", "lili_set_room_schedule", "lili_set_room_challenge", "lili_leave_room"):
        assert operation in source
    assert "service_role" not in source
    assert "SUPABASE_SECRET_KEY" not in source
    assert "/rest/v1/rpc/lili_upsert_focus_presence" in source
    assert "p_session_started_at" in source
    assert "p_device_claim" in source
    assert "String(body.last_seen || now)" not in source
