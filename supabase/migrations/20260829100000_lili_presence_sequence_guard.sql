-- Presence is a liveness record, never a focus-duration ledger.
--
-- This migration supersedes the earlier atomic RPC.  The old RPC accepted
-- client-computed today/week totals and could let a delayed heartbeat replace
-- a later finalize.  Keep the durable FocusSession segment tables as the only
-- source for time statistics; this row only describes the current live
-- episode and its server-side freshness.

alter table public.lili_focus_presence
  add column if not exists session_id text,
  add column if not exists presence_sequence bigint not null default 0;

-- Repair malformed rows before installing the invariant.  Existing releases
-- also installed a BEFORE trigger that requires auth.uid(); migrations do
-- not have an authenticated request, so suppress user triggers only for this
-- deterministic cleanup and restore them immediately afterwards.
alter table public.lili_focus_presence disable trigger user;
update public.lili_focus_presence
set
  working = false,
  session_active = false,
  session_id = null,
  session_started_at = null,
  updated_at = now(),
  last_seen = now()
where not (
  coalesce(session_active, false)
  and coalesce(working, false)
  and session_id is not null
  and session_started_at is not null
);
alter table public.lili_focus_presence enable trigger user;

alter table public.lili_focus_presence
  drop constraint if exists lili_focus_presence_live_state_check;

alter table public.lili_focus_presence
  add constraint lili_focus_presence_live_state_check
  check (
    (
      coalesce(session_active, false)
      and coalesce(working, false)
      and session_id is not null
      and session_started_at is not null
    )
    or (
      not coalesce(session_active, false)
      and not coalesce(working, false)
      and session_id is null
      and session_started_at is null
    )
  );

create index if not exists lili_focus_presence_sequence_idx
  on public.lili_focus_presence (user_id, presence_sequence desc);

create or replace function public.lili_touch_presence_server_timestamp()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  new.last_seen := now();
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

drop trigger if exists lili_presence_server_timestamp
  on public.lili_focus_presence;
create trigger lili_presence_server_timestamp
before insert or update on public.lili_focus_presence
for each row execute function public.lili_touch_presence_server_timestamp();

-- The old device-lease trigger required a client-only claim flag.  The new
-- liveness contract intentionally has no such duration/claim field: the
-- authenticated RPC plus the monotonic sequence is the ordering boundary.
-- Retain device_id as diagnostic metadata without letting that legacy trigger
-- reject a valid heartbeat from a second installation.
drop trigger if exists lili_guard_device_session
  on public.lili_focus_presence;

drop function if exists public.lili_upsert_focus_presence(
  boolean, boolean, text, text, timestamptz, date, integer, text, uuid,
  text, timestamptz, text, boolean
);

create or replace function public.lili_upsert_focus_presence(
  p_working boolean,
  p_session_active boolean,
  p_session_id text,
  p_session_started_at timestamptz,
  p_device_id text,
  p_sequence bigint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  me uuid := (select auth.uid());
  clean_active boolean;
  clean_session_id text;
  clean_started_at timestamptz;
  clean_device_id text := left(btrim(coalesce(p_device_id, '')), 120);
  incoming_sequence bigint := greatest(1, coalesce(p_sequence, 0));
  accepted boolean := false;
  result_row public.lili_focus_presence;
begin
  if me is null then
    raise exception '请先登录';
  end if;

  clean_active := coalesce(p_working, false)
    and coalesce(p_session_active, false)
    and nullif(btrim(coalesce(p_session_id, '')), '') is not null
    and p_session_started_at is not null;
  clean_session_id := case
    when clean_active then left(btrim(p_session_id), 160)
    else null
  end;
  clean_started_at := case when clean_active then p_session_started_at else null end;

  -- The sequence is the ordering fence.  A late packet, including one from
  -- before a finalize request, can never move the row backwards.  When an
  -- active heartbeat belongs to the same episode, its original start is
  -- preserved even if a client accidentally sends a different timestamp.
  insert into public.lili_focus_presence(
    user_id,
    working,
    session_active,
    session_id,
    session_started_at,
    device_id,
    presence_sequence,
    work_state,
    pause_reason,
    last_seen,
    updated_at
  ) values (
    me,
    clean_active,
    clean_active,
    clean_session_id,
    clean_started_at,
    clean_device_id,
    incoming_sequence,
    case when clean_active then 'working' else 'idle' end,
    null,
    now(),
    now()
  )
  on conflict (user_id) do update set
    working = excluded.working,
    session_active = excluded.session_active,
    session_id = excluded.session_id,
    session_started_at = case
      when public.lili_focus_presence.session_active
       and excluded.session_active
       and public.lili_focus_presence.session_id = excluded.session_id
       and public.lili_focus_presence.session_started_at is not null
        then public.lili_focus_presence.session_started_at
      else excluded.session_started_at
    end,
    device_id = excluded.device_id,
    presence_sequence = excluded.presence_sequence,
    work_state = excluded.work_state,
    pause_reason = excluded.pause_reason,
    last_seen = now(),
    updated_at = now()
  where excluded.presence_sequence > public.lili_focus_presence.presence_sequence
  returning * into result_row;

  accepted := found;
  if not accepted then
    select * into result_row
    from public.lili_focus_presence
    where user_id = me;
  end if;

  if result_row.user_id is null then
    raise exception 'presence row was not created';
  end if;

  return jsonb_build_object(
    'accepted', accepted,
    'user_id', result_row.user_id,
    'working', result_row.working,
    'session_active', result_row.session_active,
    'session_id', result_row.session_id,
    'session_started_at', result_row.session_started_at,
    'last_seen_at', result_row.last_seen,
    'sequence', result_row.presence_sequence,
    'server_timestamp', now()
  );
end;
$$;

revoke execute on function public.lili_upsert_focus_presence(
  boolean, boolean, text, timestamptz, text, bigint
) from public, anon;
grant execute on function public.lili_upsert_focus_presence(
  boolean, boolean, text, timestamptz, text, bigint
) to authenticated;

revoke execute on function public.lili_touch_presence_server_timestamp()
  from public, anon, authenticated;

comment on column public.lili_focus_presence.presence_sequence is
  'Monotonic liveness version; it orders heartbeat/finalize state and is not a duration counter.';
