-- Preserve FocusSession continuity across calendar boundaries, reconnects and
-- compatibility presence projection updates.
--
-- This migration does not add a history source and does not change the
-- sealed-segment/delta/interval-union contract.  It only makes an active
-- per-device tuple auditable when a client really replaces it, and derives
-- the legacy account row from the current device projection instead of
-- retaining a stale tuple forever.

alter table public.lili_focus_device_presence
  add column if not exists session_replaced_at timestamptz;

create or replace function public.lili_capture_focus_device_projection_stop()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'UPDATE'
     and old.working
     and old.session_active
     and old.session_id is not null
     and old.session_started_at is not null
     and new.working
     and new.session_active
     and new.session_id is not null
     and new.session_started_at is not null
     and (
       old.session_id is distinct from new.session_id
       or old.session_started_at is distinct from new.session_started_at
     ) then
    -- Active -> active identity changes are the important forensic event.  Do
    -- not turn this into another duration source; retain only the previous
    -- tuple and its replacement time for diagnostics.
    new.last_session_id := old.session_id;
    new.last_session_started_at := old.session_started_at;
    new.last_session_ended_at := now();
    new.session_replaced_at := now();
  elsif tg_op = 'UPDATE'
        and old.working
        and old.session_active
        and old.session_id is not null
        and old.session_started_at is not null
        and not (
          new.working
          and new.session_active
          and new.session_id is not null
          and new.session_started_at is not null
        ) then
    new.last_session_id := old.session_id;
    new.last_session_started_at := old.session_started_at;
    new.last_session_ended_at := now();
  elsif new.working
        and new.session_active
        and new.session_id is not null
        and new.session_started_at is not null
        and not (
          tg_op = 'UPDATE'
          and old.working
          and old.session_active
          and old.session_id = new.session_id
          and old.session_started_at is not distinct from new.session_started_at
        ) then
    -- A genuinely new active row owns the current tuple.  The previous tuple
    -- remains in the explicit last_* audit fields only for the short bridge.
    new.last_session_id := null;
    new.last_session_started_at := null;
    new.last_session_ended_at := null;
  end if;
  return new;
end;
$$;

drop trigger if exists lili_capture_focus_device_projection_stop
  on public.lili_focus_device_presence;
create trigger lili_capture_focus_device_projection_stop
before insert or update on public.lili_focus_device_presence
for each row execute function public.lili_capture_focus_device_projection_stop();

revoke execute on function public.lili_capture_focus_device_projection_stop()
  from public, anon, authenticated;

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
  account_row public.lili_focus_presence;
  representative public.lili_focus_device_presence;
  latest_device public.lili_focus_device_presence;
  active_device_count integer := 0;
  working_device_count integer := 0;
  account_sequence bigint := 1;
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
    select * into account_row
    from public.lili_focus_presence f
    where f.user_id = me;
    return jsonb_build_object(
      'accepted', false,
      'user_id', me,
      'device_id', clean_device_id,
      'device_online', coalesce(device_row.last_seen > now() - interval '2 minutes', false),
      'device_working', coalesce(device_row.working, false),
      'account_online', coalesce(account_row.last_seen > now() - interval '2 minutes', false),
      'account_working', coalesce(account_row.working, false),
      'working', coalesce(account_row.working, false),
      'session_active', coalesce(account_row.session_active, false),
      'session_id', account_row.session_id,
      'session_started_at', account_row.session_started_at,
      'sequence', coalesce(device_row.presence_sequence, 0),
      'account_sequence', coalesce(account_row.presence_sequence, 0),
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

  account_sequence := incoming_sequence;
  select coalesce(max(f.presence_sequence), 0) + 1
  into account_sequence
  from public.lili_focus_presence f
  where f.user_id = me;

  -- The compatibility row is a projection of the current device rows.  Do
  -- not preserve a stale account tuple after the device tuple was replaced;
  -- canonical duration still reads only sealed facts + device presence.
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
    case when working_device_count > 0 then representative.session_id else null end,
    case when working_device_count > 0 then representative.session_started_at else null end,
    case
      when working_device_count > 0 then representative.device_id
      else coalesce(latest_device.device_id, clean_device_id)
    end,
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
    'working', account_row.working,
    'session_active', account_row.session_active,
    'session_id', account_row.session_id,
    'session_started_at', account_row.session_started_at,
    'last_seen_at', account_row.last_seen,
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

comment on function public.lili_upsert_focus_presence(
  boolean, boolean, text, timestamptz, text, bigint
) is
  'Per-device focus liveness with stable same-session start, active identity-change audit, and a non-canonical compatibility projection.';
