-- Allow one account to keep independent liveness tuples on several devices.
--
-- FocusSession facts and every duration/statistics function remain unchanged.
-- lili_focus_presence stays as the account-level compatibility projection read
-- by the existing dashboard, room, visit, reaction and taunt functions.

create table if not exists public.lili_focus_device_presence (
  user_id uuid not null references auth.users(id) on delete cascade,
  device_id text not null,
  working boolean not null default false,
  session_active boolean not null default false,
  session_id text,
  session_started_at timestamptz,
  presence_sequence bigint not null default 0,
  last_seen timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (user_id, device_id),
  constraint lili_focus_device_presence_device_id_check
    check (char_length(device_id) between 1 and 120),
  constraint lili_focus_device_presence_live_state_check check (
    (
      working
      and session_active
      and session_id is not null
      and session_started_at is not null
    )
    or (
      not working
      and not session_active
      and session_id is null
      and session_started_at is null
    )
  )
);

create index if not exists lili_focus_device_presence_fresh_idx
  on public.lili_focus_device_presence(user_id, last_seen desc);
create index if not exists lili_focus_device_presence_working_idx
  on public.lili_focus_device_presence(user_id, session_started_at)
  where working and session_active;

alter table public.lili_focus_device_presence enable row level security;
revoke all on table public.lili_focus_device_presence from public, anon, authenticated;

comment on table public.lili_focus_device_presence is
  'Per-device liveness only. Focus duration remains canonical in lili_focus_segments.';

-- Preserve a currently fresh compatibility row through deployment. Historical
-- presence is intentionally not copied; it is not a FocusSession fact.
insert into public.lili_focus_device_presence(
  user_id,
  device_id,
  working,
  session_active,
  session_id,
  session_started_at,
  presence_sequence,
  last_seen,
  updated_at
)
select
  f.user_id,
  coalesce(nullif(btrim(f.device_id), ''), 'legacy-account'),
  f.working and f.session_active and f.session_id is not null and f.session_started_at is not null,
  f.working and f.session_active and f.session_id is not null and f.session_started_at is not null,
  case
    when f.working and f.session_active and f.session_started_at is not null
      then coalesce(nullif(btrim(f.session_id), ''), 'legacy-account')
    else null
  end,
  case
    when f.working and f.session_active and f.session_id is not null
      then f.session_started_at
    else null
  end,
  greatest(1, f.presence_sequence),
  f.last_seen,
  f.updated_at
from public.lili_focus_presence f
where f.last_seen > now() - interval '2 minutes'
on conflict (user_id, device_id) do nothing;

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
  clean_device_id text := left(
    coalesce(nullif(btrim(coalesce(p_device_id, '')), ''), 'legacy-rpc'),
    120
  );
  incoming_sequence bigint := greatest(1, coalesce(p_sequence, 0));
  accepted boolean := false;
  device_row public.lili_focus_device_presence;
  account_before public.lili_focus_presence;
  account_row public.lili_focus_presence;
  representative public.lili_focus_device_presence;
  latest_device public.lili_focus_device_presence;
  active_device_count integer := 0;
  working_device_count integer := 0;
  account_sequence bigint := 1;
  account_session_id text;
  account_session_started_at timestamptz;
  account_device_id text;
begin
  if me is null then
    raise exception 'authentication required';
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

  -- Serialize the aggregate for one account while retaining an independent
  -- monotonic sequence fence for each installation.
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(me::text, 0)
  );

  insert into public.lili_focus_device_presence(
    user_id,
    device_id,
    working,
    session_active,
    session_id,
    session_started_at,
    presence_sequence,
    last_seen,
    updated_at
  ) values (
    me,
    clean_device_id,
    clean_active,
    clean_active,
    clean_session_id,
    clean_started_at,
    incoming_sequence,
    now(),
    now()
  )
  on conflict (user_id, device_id) do update set
    working = excluded.working,
    session_active = excluded.session_active,
    session_id = excluded.session_id,
    session_started_at = case
      when public.lili_focus_device_presence.session_active
       and excluded.session_active
       and public.lili_focus_device_presence.session_id = excluded.session_id
       and public.lili_focus_device_presence.session_started_at is not null
        then public.lili_focus_device_presence.session_started_at
      else excluded.session_started_at
    end,
    presence_sequence = excluded.presence_sequence,
    last_seen = now(),
    updated_at = now()
  where excluded.presence_sequence > public.lili_focus_device_presence.presence_sequence
  returning * into device_row;

  accepted := found;
  if not accepted then
    select * into device_row
    from public.lili_focus_device_presence d
    where d.user_id = me and d.device_id = clean_device_id;
    select * into account_before
    from public.lili_focus_presence f
    where f.user_id = me;
    return jsonb_build_object(
      'accepted', false,
      'user_id', me,
      'device_id', clean_device_id,
      'device_online', device_row.last_seen > now() - interval '2 minutes',
      'device_working', coalesce(device_row.working, false),
      'account_online', coalesce(account_before.last_seen > now() - interval '2 minutes', false),
      'account_working', coalesce(account_before.working, false),
      'working', coalesce(account_before.working, false),
      'session_active', coalesce(account_before.session_active, false),
      'session_id', account_before.session_id,
      'session_started_at', account_before.session_started_at,
      'sequence', coalesce(device_row.presence_sequence, 0),
      'account_sequence', coalesce(account_before.presence_sequence, 0),
      'server_timestamp', now()
    );
  end if;

  delete from public.lili_focus_device_presence d
  where d.user_id = me
    and d.last_seen <= now() - interval '7 days';

  select
    count(*)::integer,
    count(*) filter (where d.working and d.session_active)::integer
  into active_device_count, working_device_count
  from public.lili_focus_device_presence d
  where d.user_id = me
    and d.last_seen > now() - interval '2 minutes';

  select * into representative
  from public.lili_focus_device_presence d
  where d.user_id = me
    and d.last_seen > now() - interval '2 minutes'
    and d.working
    and d.session_active
  order by d.session_started_at, d.device_id
  limit 1;

  select * into latest_device
  from public.lili_focus_device_presence d
  where d.user_id = me
    and d.last_seen > now() - interval '2 minutes'
  order by d.last_seen desc, d.device_id
  limit 1;

  select * into account_before
  from public.lili_focus_presence f
  where f.user_id = me
  for update;

  account_sequence := greatest(
    incoming_sequence,
    coalesce(account_before.presence_sequence, 0) + 1
  );
  if working_device_count > 0 then
    -- Keep one account-level live episode continuous while devices overlap.
    -- Switching the representative device must not split room presence or
    -- create a second account session.
    if account_before.user_id is not null
       and account_before.working
       and account_before.session_active
       and account_before.last_seen > now() - interval '2 minutes' then
      account_session_id := account_before.session_id;
      account_session_started_at := account_before.session_started_at;
      account_device_id := account_before.device_id;
    else
      account_session_id := representative.session_id;
      account_session_started_at := representative.session_started_at;
      account_device_id := representative.device_id;
    end if;
  else
    account_session_id := null;
    account_session_started_at := null;
    account_device_id := coalesce(latest_device.device_id, clean_device_id);
  end if;

  insert into public.lili_focus_presence(
    user_id,
    working,
    session_active,
    session_id,
    session_started_at,
    device_id,
    device_claim,
    presence_sequence,
    work_state,
    pause_reason,
    last_seen,
    updated_at
  ) values (
    me,
    working_device_count > 0,
    working_device_count > 0,
    account_session_id,
    account_session_started_at,
    account_device_id,
    false,
    account_sequence,
    case when working_device_count > 0 then 'working' else 'idle' end,
    null,
    now(),
    now()
  )
  on conflict (user_id) do update set
    working = excluded.working,
    session_active = excluded.session_active,
    session_id = excluded.session_id,
    session_started_at = excluded.session_started_at,
    device_id = excluded.device_id,
    device_claim = false,
    presence_sequence = excluded.presence_sequence,
    work_state = excluded.work_state,
    pause_reason = null,
    last_seen = now(),
    updated_at = now()
  returning * into account_row;

  return jsonb_build_object(
    'accepted', true,
    'user_id', me,
    'device_id', clean_device_id,
    'device_online', true,
    'device_working', device_row.working,
    'device_session_active', device_row.session_active,
    'device_session_id', device_row.session_id,
    'account_online', active_device_count > 0,
    'account_working', account_row.working,
    'active_device_count', active_device_count,
    'working_device_count', working_device_count,
    -- Existing clients treat these fields as the account compatibility tuple.
    'working', account_row.working,
    'session_active', account_row.session_active,
    'session_id', account_row.session_id,
    'session_started_at', account_row.session_started_at,
    'last_seen_at', account_row.last_seen,
    -- Sequence remains device-scoped so a restarted client can adopt/retry.
    'sequence', device_row.presence_sequence,
    'account_sequence', account_row.presence_sequence,
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

-- Pre-RPC clients still write the compatibility row. Mirror that one legacy
-- installation into the device table, then derive NEW from all fresh devices
-- so an idle legacy heartbeat cannot clear a modern device that is working.
create or replace function public.lili_guard_legacy_direct_presence_write()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  me uuid := (select auth.uid());
  request_path text := coalesce(current_setting('request.path', true), '');
  direct_presence_write boolean := request_path ~ '(^|/)lili_focus_presence$';
  legacy_sequence bigint := 1;
  active_device_count integer := 0;
  working_device_count integer := 0;
  representative public.lili_focus_device_presence;
  latest_device public.lili_focus_device_presence;
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

  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(me::text, 0)
  );
  select coalesce(max(d.presence_sequence), 0) + 1
  into legacy_sequence
  from public.lili_focus_device_presence d
  where d.user_id = me and d.device_id = 'legacy-direct';

  insert into public.lili_focus_device_presence(
    user_id,
    device_id,
    working,
    session_active,
    session_id,
    session_started_at,
    presence_sequence,
    last_seen,
    updated_at
  ) values (
    me,
    'legacy-direct',
    coalesce(new.working, false),
    coalesce(new.working, false),
    case
      when coalesce(new.working, false)
        then coalesce(nullif(btrim(new.session_id), ''), 'legacy-direct')
      else null
    end,
    case
      when coalesce(new.working, false)
        then coalesce(new.session_started_at, now())
      else null
    end,
    legacy_sequence,
    now(),
    now()
  )
  on conflict (user_id, device_id) do update set
    working = excluded.working,
    session_active = excluded.session_active,
    session_id = excluded.session_id,
    session_started_at = case
      when public.lili_focus_device_presence.session_active
       and excluded.session_active
       and public.lili_focus_device_presence.session_id = excluded.session_id
        then public.lili_focus_device_presence.session_started_at
      else excluded.session_started_at
    end,
    presence_sequence = excluded.presence_sequence,
    last_seen = now(),
    updated_at = now();

  select
    count(*)::integer,
    count(*) filter (where d.working and d.session_active)::integer
  into active_device_count, working_device_count
  from public.lili_focus_device_presence d
  where d.user_id = me
    and d.last_seen > now() - interval '2 minutes';

  select * into representative
  from public.lili_focus_device_presence d
  where d.user_id = me
    and d.last_seen > now() - interval '2 minutes'
    and d.working
    and d.session_active
  order by d.session_started_at, d.device_id
  limit 1;

  select * into latest_device
  from public.lili_focus_device_presence d
  where d.user_id = me
    and d.last_seen > now() - interval '2 minutes'
  order by d.last_seen desc, d.device_id
  limit 1;

  new.user_id := me;
  new.working := working_device_count > 0;
  new.session_active := working_device_count > 0;
  if working_device_count > 0 then
    if tg_op = 'UPDATE'
       and old.working
       and old.session_active
       and old.last_seen > now() - interval '2 minutes' then
      new.session_id := old.session_id;
      new.session_started_at := old.session_started_at;
      new.device_id := old.device_id;
    else
      new.session_id := representative.session_id;
      new.session_started_at := representative.session_started_at;
      new.device_id := representative.device_id;
    end if;
  else
    new.session_id := null;
    new.session_started_at := null;
    new.device_id := coalesce(latest_device.device_id, 'legacy-direct');
  end if;
  new.device_claim := false;
  new.presence_sequence := case
    when tg_op = 'UPDATE' then greatest(1, coalesce(old.presence_sequence, 0) + 1)
    else greatest(1, legacy_sequence)
  end;
  new.work_state := case when working_device_count > 0 then 'working' else 'idle' end;
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

revoke execute on function public.lili_guard_legacy_direct_presence_write()
  from public, anon, authenticated;

-- Decorate the existing dashboard without rebuilding its social/name chain.
do $$
begin
  if to_regprocedure('public.lili_dashboard()') is not null
     and to_regprocedure('public.lili_dashboard_multidevice_base_20260830()') is null then
    execute 'alter function public.lili_dashboard() rename to lili_dashboard_multidevice_base_20260830';
  end if;
end;
$$;

create or replace function public.lili_dashboard()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  me_id uuid := (select auth.uid());
  payload jsonb;
  active_device_count integer := 0;
  working_device_count integer := 0;
begin
  if me_id is null then
    raise exception 'authentication required';
  end if;
  payload := public.lili_dashboard_multidevice_base_20260830();
  select
    count(*)::integer,
    count(*) filter (where d.working and d.session_active)::integer
  into active_device_count, working_device_count
  from public.lili_focus_device_presence d
  where d.user_id = me_id
    and d.last_seen > now() - interval '2 minutes';
  if jsonb_typeof(payload -> 'me_presence') = 'object' then
    payload := jsonb_set(payload, '{me_presence,account_online}', to_jsonb(active_device_count > 0), true);
    payload := jsonb_set(payload, '{me_presence,account_working}', to_jsonb(working_device_count > 0), true);
    payload := jsonb_set(payload, '{me_presence,active_device_count}', to_jsonb(active_device_count), true);
    payload := jsonb_set(payload, '{me_presence,working_device_count}', to_jsonb(working_device_count), true);
  end if;
  return payload;
end;
$$;

revoke execute on function public.lili_dashboard_multidevice_base_20260830()
  from public, anon, authenticated;
revoke execute on function public.lili_dashboard() from public, anon;
grant execute on function public.lili_dashboard() to authenticated;

comment on function public.lili_upsert_focus_presence(
  boolean, boolean, text, timestamptz, text, bigint
) is 'Updates one device heartbeat and projects any-device state into lili_focus_presence; never writes focus duration.';
