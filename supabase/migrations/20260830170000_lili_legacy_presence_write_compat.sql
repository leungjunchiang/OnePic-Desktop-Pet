-- Temporary compatibility for pre-RPC desktop builds that still upsert their
-- own lili_focus_presence row through PostgREST. Current builds remain RPC-only.
--
-- RLS restricts the write to auth.uid(). This trigger additionally replaces
-- client timestamps and ordering metadata, normalizes the active tuple, and
-- rejects room ids that do not belong to the caller.

create or replace function public.lili_guard_legacy_direct_presence_write()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  me uuid := (select auth.uid());
  request_path text := coalesce(current_setting('request.path', true), '');
  direct_presence_write boolean :=
    request_path ~ '(^|/)lili_focus_presence$';
begin
  if not direct_presence_write then
    return new;
  end if;
  if me is null then
    raise exception 'authentication required';
  end if;
  if new.user_id is distinct from me then
    raise exception 'presence ownership mismatch';
  end if;

  -- A legacy request has no trustworthy ordering fence. Serialize it after
  -- the current row and let the following server-timestamp trigger assign now().
  if tg_op = 'UPDATE' then
    new.user_id := old.user_id;
    new.presence_sequence := greatest(1, coalesce(old.presence_sequence, 0) + 1);
    new.device_id := coalesce(
      nullif(btrim(new.device_id), ''),
      nullif(btrim(old.device_id), ''),
      'legacy-direct'
    );
  else
    new.user_id := me;
    new.presence_sequence := greatest(1, coalesce(new.presence_sequence, 0));
    new.device_id := coalesce(nullif(btrim(new.device_id), ''), 'legacy-direct');
  end if;

  if coalesce(new.working, false) then
    new.working := true;
    new.session_active := true;
    new.session_id := coalesce(
      nullif(btrim(new.session_id), ''),
      case when tg_op = 'UPDATE' then nullif(btrim(old.session_id), '') end,
      'legacy-direct'
    );
    new.session_started_at := coalesce(
      new.session_started_at,
      case when tg_op = 'UPDATE' then old.session_started_at end,
      now()
    );
    new.work_state := 'working';
  else
    new.working := false;
    new.session_active := false;
    new.session_id := null;
    new.session_started_at := null;
    new.work_state := 'idle';
  end if;
  new.pause_reason := null;

  if new.room_id is not null
     and not exists (
       select 1
       from public.lili_room_members m
       where m.room_id = new.room_id
         and m.user_id = me
     ) then
    new.room_id := null;
  end if;
  return new;
end;
$$;

drop trigger if exists lili_presence_legacy_direct_guard
  on public.lili_focus_presence;
create trigger lili_presence_legacy_direct_guard
before insert or update on public.lili_focus_presence
for each row execute function public.lili_guard_legacy_direct_presence_write();

-- Existing owner-scoped INSERT/UPDATE RLS policies remain the authorization
-- boundary. No direct DELETE permission is restored, and anon stays read-only.
grant insert, update on table public.lili_focus_presence to authenticated;
revoke insert, update, delete on table public.lili_focus_presence from anon;

revoke execute on function public.lili_guard_legacy_direct_presence_write()
  from public, anon, authenticated;

comment on function public.lili_guard_legacy_direct_presence_write() is
  'Owner-only compatibility guard for pre-RPC desktop presence heartbeats.';
