-- Context metadata must not keep an account alive.  The room/outfit RPC may
-- update the presence row, but only a liveness tuple change is a heartbeat.
-- Otherwise a delayed context retry could refresh last_seen without the
-- client actually sending a heartbeat.

create or replace function public.lili_touch_presence_server_timestamp()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  liveness_changed boolean := tg_op = 'INSERT';
begin
  if tg_op = 'UPDATE' then
    liveness_changed :=
      new.working is distinct from old.working
      or new.session_active is distinct from old.session_active
      or new.session_id is distinct from old.session_id
      or new.session_started_at is distinct from old.session_started_at
      or new.presence_sequence is distinct from old.presence_sequence
      or new.device_id is distinct from old.device_id;
  end if;

  if liveness_changed then
    new.last_seen := now();
  else
    -- Context-only updates preserve the last heartbeat timestamp exactly.
    new.last_seen := old.last_seen;
  end if;
  new.updated_at := now();

  -- The complete tuple is all-or-nothing.  Do not synthesize a start time
  -- from the request arrival time, and never leave a partial active row.
  if not (
    coalesce(new.session_active, false)
    and coalesce(new.working, false)
    and new.session_id is not null
    and new.session_started_at is not null
  ) then
    new.working := false;
    new.session_active := false;
    new.session_id := null;
    new.session_started_at := null;
  elsif new.session_started_at > now() + interval '2 minutes' then
    -- Guard against a broken client clock without changing a normal start.
    new.session_started_at := now();
  end if;
  return new;
end;
$$;

revoke execute on function public.lili_touch_presence_server_timestamp()
  from public, anon, authenticated;

