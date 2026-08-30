-- Repair three production issues found through the two-account acceptance test:
-- 1. a restarted desktop could send a lower presence sequence forever;
-- 2. an idle account's NULL session_id could turn the whole dashboard into NULL;
-- 3. legacy naive Beijing timestamps were interpreted as UTC by Postgres.
--
-- Presence remains RPC-only and server-timestamped. Private buddy notes remain
-- viewer-scoped and are still overlaid by lili_buddy_private_notes().

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
  effective_sequence bigint;
  accepted boolean := false;
  current_row public.lili_focus_presence;
  result_row public.lili_focus_presence;
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

  -- Serialize one account's heartbeat and rebase a restarted instance when it
  -- presents the same durable device id. A different device may take over only
  -- after the old row is stale; a delayed packet from another active device
  -- still cannot move the tuple backwards.
  select * into current_row
  from public.lili_focus_presence f
  where f.user_id = me
  for update;

  effective_sequence := incoming_sequence;
  if found and incoming_sequence <= current_row.presence_sequence then
    if clean_device_id = coalesce(current_row.device_id, '')
       or current_row.last_seen <= now() - interval '2 minutes' then
      effective_sequence := current_row.presence_sequence + 1;
    end if;
  end if;

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
    effective_sequence,
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

create or replace function public.lili_dashboard_social_pet_names_base()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  payload jsonb;
  me_id uuid := (select auth.uid());
  me_presence public.lili_focus_presence;
begin
  if me_id is null then
    raise exception 'authentication required';
  end if;

  payload := public.lili_dashboard_presence_base_20260828();
  if payload is null then
    raise exception 'dashboard payload unavailable';
  end if;

  payload := jsonb_set(payload, '{buddies}', public.lili_zero_never_seen_presence(coalesce(payload -> 'buddies', '[]'::jsonb)), true);
  payload := jsonb_set(payload, '{room_people}', public.lili_zero_never_seen_presence(coalesce(payload -> 'room_people', '[]'::jsonb)), true);
  payload := jsonb_set(payload, '{active_visits}', public.lili_zero_never_seen_presence(coalesce(payload -> 'active_visits', '[]'::jsonb)), true);
  if jsonb_typeof(payload -> 'current_room') = 'object' then
    payload := jsonb_set(payload, '{current_room,room_people}', public.lili_zero_never_seen_presence(coalesce(payload -> 'current_room' -> 'room_people', '[]'::jsonb)), true);
  end if;

  payload := jsonb_set(payload, '{buddies}', public.lili_normalize_focus_today_people(coalesce(payload -> 'buddies', '[]'::jsonb)), true);
  payload := jsonb_set(payload, '{room_people}', public.lili_normalize_focus_today_people(coalesce(payload -> 'room_people', '[]'::jsonb)), true);
  payload := jsonb_set(payload, '{active_visits}', public.lili_normalize_focus_today_people(coalesce(payload -> 'active_visits', '[]'::jsonb)), true);
  if jsonb_typeof(payload -> 'current_room') = 'object' then
    payload := jsonb_set(payload, '{current_room,room_people}', public.lili_normalize_focus_today_people(coalesce(payload -> 'current_room' -> 'room_people', '[]'::jsonb)), true);
  end if;

  if jsonb_typeof(payload -> 'me_presence') = 'object' then
    payload := jsonb_set(payload, '{me_presence,today_seconds}', to_jsonb(public.lili_effective_focus_today_seconds(me_id)), true);
    payload := jsonb_set(payload, '{me_presence,week_seconds}', to_jsonb(public.lili_effective_focus_week_seconds(me_id)), true);
  end if;
  if jsonb_typeof(payload -> 'me') = 'object' then
    payload := jsonb_set(payload, '{me,focus_today_date}', to_jsonb((now() at time zone 'Asia/Shanghai')::date), true);
    payload := jsonb_set(payload, '{me,focus_today_seconds}', to_jsonb(public.lili_effective_focus_today_seconds(me_id)), true);
    payload := jsonb_set(payload, '{me,focus_week_start_date}', to_jsonb(date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date), true);
    payload := jsonb_set(payload, '{me,focus_week_seconds}', to_jsonb(public.lili_effective_focus_week_seconds(me_id)), true);
  end if;

  select * into me_presence
  from public.lili_focus_presence f
  where f.user_id = me_id;
  if found and jsonb_typeof(payload -> 'me_presence') = 'object' then
    -- to_jsonb(NULL) is SQL NULL, not JSON null. Passing it directly to
    -- jsonb_set returns SQL NULL and used to erase the entire dashboard for
    -- every signed-in account that was idle.
    payload := jsonb_set(
      payload,
      '{me_presence,session_id}',
      coalesce(to_jsonb(me_presence.session_id), 'null'::jsonb),
      true
    );
    payload := jsonb_set(payload, '{me_presence,sequence}', to_jsonb(coalesce(me_presence.presence_sequence, 0)), true);
    payload := jsonb_set(
      payload,
      '{me_presence,server_updated_at}',
      coalesce(to_jsonb(me_presence.updated_at), 'null'::jsonb),
      true
    );
  end if;
  return payload;
end;
$$;

revoke execute on function public.lili_dashboard_social_pet_names_base()
  from public, anon, authenticated;

alter table public.lili_focus_segments
  add column if not exists time_corrected_at timestamptz,
  add column if not exists time_correction_reason text;

create or replace function public.lili_parse_client_focus_timestamp(p_value text)
returns timestamptz
language plpgsql
immutable
strict
set search_path = ''
as $$
declare
  clean_value text := btrim(p_value);
begin
  if clean_value = '' then
    return null;
  end if;
  if clean_value ~* '(z|[+-][0-9]{2}:?[0-9]{2})$' then
    return clean_value::timestamptz;
  end if;
  return clean_value::timestamp without time zone at time zone 'Asia/Shanghai';
end;
$$;

revoke execute on function public.lili_parse_client_focus_timestamp(text)
  from public, anon, authenticated;

-- This exact legacy row is the one reported by the account owner. It represents
-- Beijing 00:14:40-01:30:41 but was stored as UTC. Resolve the account by its
-- stable email rather than hardcoding an Auth-generated UUID.
update public.lili_focus_segments s
set
  start_at = s.start_at - interval '8 hours',
  end_at = s.end_at - interval '8 hours',
  time_corrected_at = now(),
  time_correction_reason = 'legacy_naive_beijing_interpreted_as_utc',
  updated_at = now()
where s.user_id = (
    select u.id
    from auth.users u
    where lower(u.email) = lower('leungjunchiang@qq.com')
  )
  and s.segment_id = 'legacy:288'
  and s.start_at = timestamptz '2026-08-30 00:14:40.867574+00'
  and s.end_at = timestamptz '2026-08-30 01:30:41.867574+00';

create or replace function public.lili_sync_focus_segments(
  p_segments jsonb default '[]'::jsonb
) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  current_user_id uuid := (select auth.uid());
  today date := (now() at time zone 'Asia/Shanghai')::date;
  item jsonb;
  segment_key text;
  segment_session text;
  segment_start timestamptz;
  segment_end timestamptz;
  segment_device text;
  segment_completed boolean;
  segment_quality smallint;
  segment_task text;
  segment_interruptions smallint;
begin
  if current_user_id is null then
    raise exception 'authentication required for focus segment sync';
  end if;
  if jsonb_typeof(coalesce(p_segments, '[]'::jsonb)) <> 'array' then
    raise exception 'invalid focus segment payload';
  end if;

  for item in select value from jsonb_array_elements(coalesce(p_segments, '[]'::jsonb)) loop
    begin
      segment_key := left(btrim(coalesce(item->>'segment_id', '')), 160);
      segment_session := left(btrim(coalesce(item->>'session_id', '')), 160);
      segment_start := public.lili_parse_client_focus_timestamp(item->>'start_at');
      segment_end := public.lili_parse_client_focus_timestamp(nullif(item->>'end_at', ''));
      segment_device := left(btrim(coalesce(item->>'device_id', '')), 120);
      segment_completed := coalesce((item->>'completed')::boolean, false);
      segment_quality := greatest(0, least(100, coalesce((item->>'quality')::smallint, 0)));
      segment_task := left(coalesce(item->>'task', ''), 120);
      segment_interruptions := greatest(0, coalesce((item->>'interruptions')::smallint, 0));
    exception when others then
      segment_key := '';
      segment_start := null;
      segment_end := null;
    end;
    if segment_key <> ''
       and segment_start is not null
       and (segment_end is null or segment_end >= segment_start)
       and (segment_start at time zone 'Asia/Shanghai')::date between today - 400 and today then
      insert into public.lili_focus_segments (
        user_id, segment_id, session_id, start_at, end_at, device_id,
        completed, quality, task, interruptions, updated_at
      ) values (
        current_user_id, segment_key, coalesce(segment_session, ''), segment_start,
        segment_end, coalesce(segment_device, ''), segment_completed,
        segment_quality, coalesce(segment_task, ''), segment_interruptions, now()
      )
      on conflict (user_id, segment_id) do update set
        session_id = excluded.session_id,
        start_at = case
          when public.lili_focus_segments.time_corrected_at is not null
            then public.lili_focus_segments.start_at
          else excluded.start_at
        end,
        end_at = case
          when public.lili_focus_segments.time_corrected_at is not null
            then public.lili_focus_segments.end_at
          else excluded.end_at
        end,
        device_id = excluded.device_id,
        completed = excluded.completed,
        quality = excluded.quality,
        task = excluded.task,
        interruptions = excluded.interruptions,
        updated_at = now();
    end if;
  end loop;

  perform public.lili_reconcile_focus_derived_totals(current_user_id);

  return jsonb_build_object(
    'segments', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'segment_id', s.segment_id,
          'session_id', s.session_id,
          'start_at', s.start_at,
          'end_at', s.end_at,
          'device_id', s.device_id,
          'completed', s.completed,
          'quality', s.quality,
          'task', s.task,
          'interruptions', s.interruptions,
          'time_corrected_at', s.time_corrected_at,
          'time_correction_reason', s.time_correction_reason
        ) order by s.start_at
      )
      from public.lili_focus_segments s
      where s.user_id = current_user_id
        and (s.start_at at time zone 'Asia/Shanghai')::date between today - 400 and today
    ), '[]'::jsonb)
  );
end;
$$;

revoke execute on function public.lili_sync_focus_segments(jsonb) from public, anon;
grant execute on function public.lili_sync_focus_segments(jsonb) to authenticated;

comment on column public.lili_focus_segments.time_corrected_at is
  'Server-side timestamp correction marker; corrected boundaries are authoritative over stale client uploads.';
comment on function public.lili_parse_client_focus_timestamp(text) is
  'Parses explicit offsets as instants and legacy naive desktop values as Asia/Shanghai local time.';
