-- Preserve server-side daily totals from older desktop releases when their
-- raw FocusSegment upload path was not available.  This is a bounded
-- compatibility read: it never creates synthetic intervals and never uses
-- the profile week counter.  Closed raw intervals remain authoritative when
-- present; a daily row is used only for the current Beijing week and only if
-- it was written during that week.

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
  day_cursor date;
  raw_day integer;
  legacy_day integer;
  effective_today integer := 0;
  raw_today integer := 0;
  raw_week integer := 0;
  effective_week integer := 0;
  legacy_week_used boolean := false;
  raw_source_active boolean := false;
  raw_week_evidence boolean := false;
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

  raw_today := public.lili_focus_union_seconds(p_user_id, today_start, today_end);
  raw_week := public.lili_focus_union_seconds(p_user_id, week_start_at, week_end_at);
  raw_source_active := raw_today > 0;
  raw_week_evidence := raw_week > 0;

  day_cursor := week_start;
  while day_cursor <= today loop
    raw_day := public.lili_focus_union_seconds(
      p_user_id,
      (day_cursor::timestamp at time zone 'Asia/Shanghai'),
      ((day_cursor + 1)::timestamp at time zone 'Asia/Shanghai')
    );
    select case
      when d.updated_at >= week_start_at
        then greatest(0, least(86400, coalesce(d.seconds, 0)))
      else 0
    end
    into legacy_day
    from public.lili_focus_daily d
    where d.user_id = p_user_id
      and d.focus_date = day_cursor;
    legacy_day := coalesce(legacy_day, 0);

    effective_week := effective_week + greatest(raw_day, legacy_day);
    if legacy_day > raw_day then
      legacy_week_used := true;
    end if;
    if day_cursor = today then
      effective_today := greatest(raw_day, legacy_day);
    end if;
    day_cursor := day_cursor + 1;
  end loop;

  return jsonb_build_object(
    'today_seconds', greatest(0, least(86400, effective_today)),
    'week_seconds', greatest(0, least(604800, effective_week)),
    'source', case
      when legacy_week_used and (raw_week_evidence or raw_source_active)
        then 'canonical_interval_union_legacy_daily_compat'
      when legacy_week_used then 'legacy_daily_compat'
      when raw_week_evidence or raw_source_active then 'canonical_interval_union'
      else 'none'
    end,
    'raw_source_active', raw_source_active,
    'raw_week_evidence', raw_week_evidence,
    'legacy_daily_compat_used', legacy_week_used
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
  select coalesce(
    (public.lili_effective_focus_stats(p_user_id)->>'today_seconds')::integer,
    0
  );
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
  select coalesce(
    (public.lili_effective_focus_stats(p_user_id)->>'week_seconds')::integer,
    0
  );
$$;

revoke execute on function public.lili_effective_focus_week_seconds(uuid)
  from public, anon, authenticated;

comment on function public.lili_effective_focus_stats(uuid) is
  'Canonical account focus totals with bounded legacy daily compatibility; raw intervals win per Beijing day and no synthetic intervals are created.';

-- A stale/short compatibility payload must never roll a larger server day or
-- week back after pause/resume.  Raw interval reconciliation remains the
-- correction path for new clients.
create or replace function public.lili_sync_personal_state(
  p_focus_date date default ((now() at time zone 'Asia/Shanghai'))::date,
  p_today_seconds integer default 0,
  p_lifetime_seconds bigint default 0,
  p_outfit_key text default null,
  p_outfit_set boolean default false,
  p_week_start date default (date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date),
  p_week_seconds integer default 0
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  current_user_id uuid := (select auth.uid());
  target_date date := coalesce(p_focus_date, (now() at time zone 'Asia/Shanghai')::date);
  target_week date := coalesce(p_week_start, date_trunc('week', target_date)::date);
  incoming_today integer := greatest(0, least(86400, coalesce(p_today_seconds, 0)));
  incoming_week integer := greatest(0, least(604800, coalesce(p_week_seconds, 0)));
  merged_today integer;
  merged_lifetime bigint;
  merged_week integer;
  merged_outfit text;
begin
  if current_user_id is null then
    raise exception '需要登录';
  end if;

  update public.lili_profiles p
  set focus_today_seconds = case
        when target_date > p.focus_today_date then incoming_today
        when target_date = p.focus_today_date
          then greatest(coalesce(p.focus_today_seconds, 0), incoming_today)
        else p.focus_today_seconds
      end,
      focus_today_date = greatest(p.focus_today_date, target_date),
      focus_lifetime_seconds = greatest(
        p.focus_lifetime_seconds,
        greatest(0, coalesce(p_lifetime_seconds, 0))
      ),
      focus_week_seconds = case
        when target_week > p.focus_week_start_date then incoming_week
        when target_week = p.focus_week_start_date
          then greatest(coalesce(p.focus_week_seconds, 0), incoming_week)
        else p.focus_week_seconds
      end,
      focus_week_start_date = greatest(p.focus_week_start_date, target_week),
      outfit_key = case
        when coalesce(p_outfit_set, false)
          then left(btrim(coalesce(p_outfit_key, '')), 60)
        else p.outfit_key
      end,
      updated_at = now()
  where p.user_id = current_user_id
  returning p.focus_today_seconds, p.focus_lifetime_seconds,
            p.focus_week_seconds, p.outfit_key
    into merged_today, merged_lifetime, merged_week, merged_outfit;

  if not found then
    raise exception '搭子资料不存在';
  end if;

  insert into public.lili_focus_daily (user_id, focus_date, seconds, updated_at)
  values (current_user_id, target_date, incoming_today, now())
  on conflict (user_id, focus_date) do update
    set seconds = greatest(coalesce(public.lili_focus_daily.seconds, 0), excluded.seconds),
        updated_at = now();

  return jsonb_build_object(
    'focus_today_date', target_date,
    'focus_today_seconds', merged_today,
    'focus_lifetime_seconds', merged_lifetime,
    'focus_week_start_date', target_week,
    'focus_week_seconds', merged_week,
    'outfit_key', merged_outfit
  );
end;
$$;

revoke execute on function public.lili_sync_personal_state(date, integer, bigint, text, boolean, date, integer)
  from public, anon;
grant execute on function public.lili_sync_personal_state(date, integer, bigint, text, boolean, date, integer)
  to authenticated, service_role;

create or replace function public.lili_sync_focus_history(p_history jsonb default '[]'::jsonb)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  current_user_id uuid := (select auth.uid());
  today date := (now() at time zone 'Asia/Shanghai')::date;
  item jsonb;
  item_date date;
  item_seconds integer;
begin
  if current_user_id is null then
    raise exception '需要登录后才能同步专注历史';
  end if;
  if jsonb_typeof(coalesce(p_history, '[]'::jsonb)) <> 'array' then
    raise exception '专注历史格式无效';
  end if;

  for item in select value from jsonb_array_elements(coalesce(p_history, '[]'::jsonb)) loop
    begin
      item_date := (item->>'focus_date')::date;
      item_seconds := greatest(0, least(86400, coalesce((item->>'seconds')::integer, 0)));
    exception when others then
      item_date := null;
    end;
    if item_date is not null and item_date between today - 400 and today then
      insert into public.lili_focus_daily (user_id, focus_date, seconds, updated_at)
      values (current_user_id, item_date, item_seconds, now())
      on conflict (user_id, focus_date) do update
        set seconds = greatest(coalesce(public.lili_focus_daily.seconds, 0), excluded.seconds),
            updated_at = now();
    end if;
  end loop;

  return jsonb_build_object(
    'focus_date', today,
    'days', coalesce((
      select jsonb_agg(
        jsonb_build_object('focus_date', d.focus_date, 'seconds', d.seconds)
        order by d.focus_date
      )
      from public.lili_focus_daily d
      where d.user_id = current_user_id
        and d.focus_date between today - 7 and today
    ), '[]'::jsonb)
  );
end;
$$;

revoke execute on function public.lili_sync_focus_history(jsonb) from public, anon;
grant execute on function public.lili_sync_focus_history(jsonb) to authenticated, service_role;
