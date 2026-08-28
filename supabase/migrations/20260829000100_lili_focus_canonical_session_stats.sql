-- Canonical focus statistics
--
-- lili_focus_segments is the only source used for users that have interval
-- facts.  lili_focus_daily and lili_profiles remain compatibility caches for
-- accounts that have not migrated to interval sync yet.  Every metric below
-- uses the same clipped interval union, so overlap, duplicate checkpoints and
-- a segment crossing Beijing midnight cannot inflate a day or a week.

create or replace function public.lili_focus_segment_is_valid(
  p_start_at timestamptz,
  p_end_at timestamptz,
  p_now timestamptz default now()
) returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select p_start_at is not null
    and p_start_at <= p_now + interval '2 minutes'
    and (p_end_at is null or p_end_at <= p_now + interval '2 minutes')
    and coalesce(p_end_at, p_now) >= p_start_at
    and extract(epoch from coalesce(p_end_at, p_now) - p_start_at)
      between 0 and 86400;
$$;

revoke execute on function public.lili_focus_segment_is_valid(timestamptz, timestamptz, timestamptz)
  from public, anon, authenticated;

create or replace function public.lili_focus_union_seconds(
  p_user_id uuid,
  p_start_at timestamptz,
  p_end_at timestamptz
) returns integer
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(
    sum(extract(epoch from upper(r) - lower(r)))::integer,
    0
  )
  from (
    select unnest(range_agg(tstzrange(
      greatest(s.start_at, p_start_at),
      least(coalesce(s.end_at, now()), p_end_at),
      '[)'
    ))) as r
    from public.lili_focus_segments s
    where s.user_id = p_user_id
      and s.start_at < p_end_at
      and coalesce(s.end_at, now()) > p_start_at
      and public.lili_focus_segment_is_valid(s.start_at, s.end_at, now())
  ) merged;
$$;

revoke execute on function public.lili_focus_union_seconds(uuid, timestamptz, timestamptz)
  from public, anon, authenticated;

create or replace function public.lili_effective_focus_stats(p_user_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  now_beijing timestamp := (now() at time zone 'Asia/Shanghai');
  today date := now_beijing::date;
  week_start date := date_trunc('week', now_beijing)::date;
  today_start timestamptz := today at time zone 'Asia/Shanghai';
  today_end timestamptz := (today + 1) at time zone 'Asia/Shanghai';
  week_start_at timestamptz := week_start at time zone 'Asia/Shanghai';
  week_end_at timestamptz := (week_start + 7) at time zone 'Asia/Shanghai';
  raw_source_active boolean := false;
  raw_week_evidence boolean := false;
  raw_today integer := 0;
  raw_week integer := 0;
  legacy_today integer := 0;
  legacy_week integer := 0;
  today_seconds integer := 0;
  week_seconds integer := 0;
begin
  if p_user_id is null then
    return jsonb_build_object(
      'today_seconds', 0,
      'week_seconds', 0,
      'source', 'none',
      'raw_source_active', false
    );
  end if;

  -- A row in the interval ledger, including an invalid row, disables legacy
  -- aggregate fallback for the affected account.  Otherwise a corrupt raw
  -- row could be hidden by an old five-hour profile maximum.
  select exists(
    select 1
    from public.lili_focus_segments s
    where s.user_id = p_user_id
      and s.start_at < now() + interval '2 minutes'
      and coalesce(s.end_at, now()) > ((today - 400) at time zone 'Asia/Shanghai')
  )
  into raw_source_active;

  select exists(
    select 1
    from public.lili_focus_segments s
    where s.user_id = p_user_id
      and s.start_at < week_end_at
      and coalesce(s.end_at, now()) > week_start_at
  )
  into raw_week_evidence;

  raw_today := public.lili_focus_union_seconds(p_user_id, today_start, today_end);
  raw_week := public.lili_focus_union_seconds(p_user_id, week_start_at, week_end_at);

  select coalesce(
    (
      select greatest(0, least(86400, d.seconds))
      from public.lili_focus_daily d
      where d.user_id = p_user_id and d.focus_date = today
    ),
    (
      select greatest(0, least(86400, p.focus_today_seconds))
      from public.lili_profiles p
      where p.user_id = p_user_id and p.focus_today_date = today
    ),
    0
  )
  into legacy_today;

  select coalesce(
    (
      select greatest(0, least(604800, sum(greatest(0, least(86400, d.seconds)))))::integer
      from public.lili_focus_daily d
      where d.user_id = p_user_id
        and d.focus_date >= week_start
        and d.focus_date < week_start + 7
    ),
    (
      select greatest(0, least(604800, p.focus_week_seconds))
      from public.lili_profiles p
      where p.user_id = p_user_id and p.focus_week_start_date = week_start
    ),
    0
  )
  into legacy_week;

  today_seconds := case
    when raw_source_active then raw_today
    else legacy_today
  end;
  week_seconds := case
    when raw_source_active or raw_week_evidence then raw_week
    else legacy_week
  end;

  return jsonb_build_object(
    'today_seconds', greatest(0, least(86400, today_seconds)),
    'week_seconds', greatest(0, least(604800, week_seconds)),
    'source', case when raw_source_active or raw_week_evidence then 'focus_segments' else 'legacy_cache' end,
    'raw_source_active', raw_source_active,
    'raw_week_evidence', raw_week_evidence
  );
end;
$$;

revoke execute on function public.lili_effective_focus_stats(uuid)
  from public, anon, authenticated;

create or replace function public.lili_effective_focus_today_seconds(p_user_id uuid)
returns integer
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce((public.lili_effective_focus_stats(p_user_id)->>'today_seconds')::integer, 0);
$$;

revoke execute on function public.lili_effective_focus_today_seconds(uuid)
  from public, anon, authenticated;

create or replace function public.lili_effective_focus_week_seconds(p_user_id uuid)
returns integer
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce((public.lili_effective_focus_stats(p_user_id)->>'week_seconds')::integer, 0);
$$;

revoke execute on function public.lili_effective_focus_week_seconds(uuid)
  from public, anon, authenticated;

create or replace function public.lili_reconcile_focus_derived_totals(p_user_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  current_day date := (now() at time zone 'Asia/Shanghai')::date;
  current_week date := date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date;
  current_user_id uuid := (select auth.uid());
  raw_exists boolean;
  repaired_days integer := 0;
  stats jsonb;
begin
  if p_user_id is null then
    return jsonb_build_object('source', 'none', 'repaired_days', 0);
  end if;
  if current_user_id is not null and current_user_id <> p_user_id then
    raise exception '只能回算当前账号的专注数据';
  end if;

  select exists(
    select 1 from public.lili_focus_segments s
    where s.user_id = p_user_id
      and s.start_at < now() + interval '2 minutes'
      and coalesce(s.end_at, now()) > ((current_day - 400) at time zone 'Asia/Shanghai')
  ) into raw_exists;

  if not raw_exists then
    return jsonb_build_object('source', 'legacy_cache', 'repaired_days', 0);
  end if;

  -- Repair the active Beijing week, including zero days.  This clears a
  -- synthetic midnight row without touching older daily-only history.
  insert into public.lili_focus_daily (user_id, focus_date, seconds, updated_at)
  select
    p_user_id,
    days.focus_date,
    public.lili_focus_union_seconds(
      p_user_id,
      days.focus_date at time zone 'Asia/Shanghai',
      (days.focus_date + 1) at time zone 'Asia/Shanghai'
    ),
    now()
  from (
    select gs::date as focus_date
    from generate_series(current_week, current_day, interval '1 day') gs
  ) days
  on conflict (user_id, focus_date) do update
    set seconds = excluded.seconds,
        updated_at = now();
  get diagnostics repaired_days = row_count;

  stats := public.lili_effective_focus_stats(p_user_id);
  update public.lili_profiles
  set focus_today_date = current_day,
      focus_today_seconds = greatest(0, least(86400, (stats->>'today_seconds')::integer)),
      focus_week_start_date = current_week,
      focus_week_seconds = greatest(0, least(604800, (stats->>'week_seconds')::integer)),
      updated_at = now()
  where user_id = p_user_id;

  return jsonb_build_object(
    'source', 'focus_segments',
    'repaired_days', repaired_days,
    'today_seconds', (stats->>'today_seconds')::integer,
    'week_seconds', (stats->>'week_seconds')::integer
  );
end;
$$;

revoke execute on function public.lili_reconcile_focus_derived_totals(uuid)
  from public, anon, authenticated;

-- Make every segment sync finish by rebuilding the compatibility projections.
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
    raise exception '需要登录后才能同步专注区间';
  end if;
  if jsonb_typeof(coalesce(p_segments, '[]'::jsonb)) <> 'array' then
    raise exception '专注区间格式无效';
  end if;

  for item in select value from jsonb_array_elements(coalesce(p_segments, '[]'::jsonb)) loop
    begin
      segment_key := left(btrim(coalesce(item->>'segment_id', '')), 160);
      segment_session := left(btrim(coalesce(item->>'session_id', '')), 160);
      segment_start := (item->>'start_at')::timestamptz;
      segment_end := nullif(item->>'end_at', '')::timestamptz;
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
        start_at = excluded.start_at,
        end_at = excluded.end_at,
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
          'interruptions', s.interruptions
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

-- The card and leaderboard now use the same week function.
create or replace function public.lili_focus_weekly_leaderboard(p_period text default 'week') returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(jsonb_agg(
    jsonb_build_object(
      'user_id', p.user_id,
      'nickname', public.lili_owner_nickname(p.user_id),
      'week_start', date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date,
      'week_seconds', public.lili_effective_focus_week_seconds(p.user_id)
    )
    order by public.lili_effective_focus_week_seconds(p.user_id) desc,
             public.lili_owner_nickname(p.user_id)
  ), '[]'::jsonb)
  from public.lili_profiles p
  where (p.wealth_leaderboard_enabled or not p.wealth_leaderboard_preference_set)
    and (p.user_id = (select auth.uid()) or public.lili_are_buddies((select auth.uid()), p.user_id));
$$;

revoke execute on function public.lili_focus_weekly_leaderboard(text) from public, anon;
grant execute on function public.lili_focus_weekly_leaderboard(text) to authenticated;

-- Extend the existing dashboard normalizer to repair week_seconds as well as
-- today_seconds.  The wrapper remains responsible for the never-seen and
-- room projections introduced by the previous migration.
create or replace function public.lili_normalize_focus_today_people(p_people jsonb)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  item jsonb;
  result jsonb := '[]'::jsonb;
  raw_user_id text;
  person_id uuid;
  can_show_exact_time boolean;
begin
  for item in
    select value from jsonb_array_elements(coalesce(p_people, '[]'::jsonb))
  loop
    raw_user_id := nullif(coalesce(item ->> 'user_id', item ->> 'peer_id'), '');
    person_id := null;
    if raw_user_id is not null then
      begin
        person_id := raw_user_id::uuid;
      exception when invalid_text_representation then
        person_id := null;
      end;
    end if;

    if person_id is not null then
      select p.show_exact_time and p.visibility = 'friends'
        into can_show_exact_time
        from public.lili_profiles p
        where p.user_id = person_id;

      if found then
        item := jsonb_set(
          item,
          '{today_seconds}',
          case when can_show_exact_time
            then to_jsonb(public.lili_effective_focus_today_seconds(person_id))
            else 'null'::jsonb end,
          true
        );
        item := jsonb_set(
          item,
          '{week_seconds}',
          case when can_show_exact_time
            then to_jsonb(public.lili_effective_focus_week_seconds(person_id))
            else 'null'::jsonb end,
          true
        );
      end if;
    end if;
    result := result || jsonb_build_array(item);
  end loop;
  return result;
end;
$$;

revoke execute on function public.lili_normalize_focus_today_people(jsonb)
  from public, anon, authenticated;

-- Keep the existing dashboard wrapper but make its own week projection use
-- the same canonical function as cards and the leaderboard.
create or replace function public.lili_dashboard() returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  payload jsonb;
  me_id uuid := (select auth.uid());
begin
  if me_id is null then
    raise exception '需要登录';
  end if;

  payload := public.lili_dashboard_presence_base_20260828();
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
  return payload;
end;
$$;

revoke execute on function public.lili_dashboard() from public, anon;
grant execute on function public.lili_dashboard() to authenticated;

