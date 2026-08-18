-- Presence freshness must come from the database clock, not a desktop clock.
-- This also repairs older clients that still send a stale/future last_seen.
create or replace function public.lili_touch_presence_server_timestamp()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  new.last_seen := now();
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists lili_presence_server_timestamp on public.lili_focus_presence;
create trigger lili_presence_server_timestamp
before insert or update on public.lili_focus_presence
for each row
execute function public.lili_touch_presence_server_timestamp();

revoke execute on function public.lili_touch_presence_server_timestamp() from public, anon, authenticated;
