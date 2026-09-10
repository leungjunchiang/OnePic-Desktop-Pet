from pathlib import Path


def test_room_membership_is_exclusive_and_room_events_keep_identity() -> None:
    root = Path(__file__).resolve().parents[1]
    migration = next((root / "supabase" / "migrations").glob("*_exclusive_room_membership.sql"))
    sql = migration.read_text(encoding="utf-8")
    assert "create unique index if not exists lili_room_members_one_room_per_user" in sql
    assert "delete from public.lili_room_members m" in sql
    assert "create or replace function public.lili_create_room(room_name text)" in sql
    assert "create or replace function public.lili_join_room(code text)" in sql
    assert "'actor_id', e.actor_id" in sql
    assert "'target_id', e.target_id" in sql
    assert "'owner_nickname', public.lili_owner_nickname(p.user_id)" in sql
    assert "p.visibility = 'friends'" not in sql
