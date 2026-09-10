-- Fix the calendar boundary conversion used by the canonical FocusSession
-- projections.  In PostgreSQL, `date AT TIME ZONE zone` produces a
-- timestamp-without-time-zone for a date value.  Assigning that result to a
-- timestamptz under the database's UTC session timezone shifted a Beijing
-- midnight to 08:00 Beijing, dropping the first eight hours of every day.
-- Always cast the date to timestamp before applying the zone so the boundary
-- is an actual Beijing-local midnight regardless of the database timezone.

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
  today_start timestamptz := (today::timestamp at time zone 'Asia/Shanghai');
  today_end timestamptz := ((today + 1)::timestamp at time zone 'Asia/Shanghai');
  week_start_at timestamptz := (week_start::timestamp at time zone 'Asia/Shanghai');
  week_end_at timestamptz := ((week_start + 7)::timestamp at time zone 'Asia/Shanghai');
  raw_source_active boolean := false;
  raw_week_evidence boolean := false;
  raw_today integer := 0;
  raw_week integer := 0;
begin
  if p_user_id is null then
    return jsonb_build_object(
      'today_seconds', 0,
      'week_seconds', 0,
      'source', 'none',
      'raw_source_active', false,
      'raw_week_evidence', false
    );
  end if;

  -- Any interval evidence disables legacy aggregate fallback.  Invalid facts
  -- are excluded by lili_focus_union_seconds rather than replaced by a
  -- cached profile maximum.
  select exists(
    select 1
    from public.lili_focus_segments s
    where s.user_id = p_user_id
      and s.start_at < now() + interval '2 minutes'
      and coalesce(s.end_at, now()) > ((today - 400)::timestamp at time zone 'Asia/Shanghai')
  ) into raw_source_active;

  select exists(
    select 1
    from public.lili_focus_segments s
    where s.user_id = p_user_id
      and s.start_at < week_end_at
      and coalesce(s.end_at, now()) > week_start_at
  ) into raw_week_evidence;

  raw_today := public.lili_focus_union_seconds(p_user_id, today_start, today_end);
  raw_week := public.lili_focus_union_seconds(p_user_id, week_start_at, week_end_at);

  return jsonb_build_object(
    'today_seconds', greatest(0, least(86400, raw_today)),
    'week_seconds', greatest(0, least(604800, raw_week)),
    'source', case
      when raw_source_active or raw_week_evidence then 'focus_segments'
      else 'none'
    end,
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
    return jsonb_build_object('source', 'none', 'repaired_days', 0, 'today_seconds', 0, 'week_seconds', 0);
  end if;
  if current_user_id is not null and current_user_id <> p_user_id then
    raise exception '只能回算当前账号的专注数据';
  end if;

  select exists(
    select 1
    from public.lili_focus_segments s
    where s.user_id = p_user_id
      and s.start_at < now() + interval '2 minutes'
      and coalesce(s.end_at, now()) > ((current_day - 400)::timestamp at time zone 'Asia/Shanghai')
  ) into raw_exists;

  if not raw_exists then
    update public.lili_focus_daily
    set seconds = 0, updated_at = now()
    where user_id = p_user_id
      and focus_date >= current_week
      and focus_date <= current_day;
    get diagnostics repaired_days = row_count;

    update public.lili_profiles
    set focus_today_date = current_day,
        focus_today_seconds = 0,
        focus_week_start_date = current_week,
        focus_week_seconds = 0,
        updated_at = now()
    where user_id = p_user_id;

    return jsonb_build_object(
      'source', 'none',
      'repaired_days', repaired_days,
      'today_seconds', 0,
      'week_seconds', 0
    );
  end if;

  insert into public.lili_focus_daily (user_id, focus_date, seconds, updated_at)
  select
    p_user_id,
    days.focus_date,
    public.lili_focus_union_seconds(
      p_user_id,
      (days.focus_date::timestamp at time zone 'Asia/Shanghai'),
      ((days.focus_date + 1)::timestamp at time zone 'Asia/Shanghai')
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

-- Evaluate the canonical week function once per leaderboard row.  This keeps
-- the card and leaderboard on exactly the same raw interval projection while
-- avoiding a second full range union for the ORDER BY expression.
create or replace function public.lili_focus_weekly_leaderboard(p_period text default 'week')
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  with rows as materialized (
    select
      p.user_id,
      public.lili_owner_nickname(p.user_id) as nickname,
      public.lili_effective_focus_week_seconds(p.user_id) as week_seconds
    from public.lili_profiles p
    where (p.wealth_leaderboard_enabled or not p.wealth_leaderboard_preference_set)
      and (p.user_id = (select auth.uid()) or public.lili_are_buddies((select auth.uid()), p.user_id))
  )
  select coalesce(jsonb_agg(
    jsonb_build_object(
      'user_id', user_id,
      'nickname', nickname,
      'week_start', date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date,
      'week_seconds', week_seconds
    )
    order by week_seconds desc, nickname
  ), '[]'::jsonb)
  from rows;
$$;

revoke execute on function public.lili_focus_weekly_leaderboard(text)
  from public, anon;
grant execute on function public.lili_focus_weekly_leaderboard(text) to authenticated;
