-- Freeze the last trusted daily aggregates that existed before the complete
-- sealed-segment upload contract.  The app cannot write this table.  It is a
-- one-time migration floor, not another mutable counter.

create table if not exists public.lili_focus_legacy_daily_floor (
  user_id uuid not null references auth.users(id) on delete cascade,
  focus_date date not null,
  seconds integer not null check (seconds between 0 and 86400),
  captured_at timestamptz not null default now(),
  primary key (user_id, focus_date)
);

create table if not exists public.lili_focus_legacy_floor_snapshots (
  snapshot_key text primary key,
  captured_at timestamptz not null default now()
);

comment on table public.lili_focus_legacy_daily_floor is
  'Frozen pre-interval daily focus totals. Effective totals use max(canonical raw union, this floor) per Beijing day.';
comment on table public.lili_focus_legacy_floor_snapshots is
  'One-time capture sentinels that prevent later deployments from moving a frozen legacy floor.';

alter table public.lili_focus_legacy_daily_floor enable row level security;
alter table public.lili_focus_legacy_floor_snapshots enable row level security;

revoke all on table public.lili_focus_legacy_daily_floor
  from public, anon, authenticated;
revoke all on table public.lili_focus_legacy_floor_snapshots
  from public, anon, authenticated;
grant select on table public.lili_focus_legacy_daily_floor to service_role;
grant select on table public.lili_focus_legacy_floor_snapshots to service_role;

do $snapshot$
declare
  captured boolean := false;
begin
  insert into public.lili_focus_legacy_floor_snapshots (snapshot_key)
  values ('pre_interval_daily_v1')
  on conflict (snapshot_key) do nothing
  returning true into captured;

  if coalesce(captured, false) then
    insert into public.lili_focus_legacy_daily_floor (
      user_id,
      focus_date,
      seconds,
      captured_at
    )
    select
      d.user_id,
      d.focus_date,
      greatest(0, least(86400, coalesce(d.seconds, 0))),
      now()
    from public.lili_focus_daily d
    where d.focus_date <= (now() at time zone 'Asia/Shanghai')::date
      and coalesce(d.seconds, 0) > 0
    on conflict (user_id, focus_date) do nothing;
  end if;
end;
$snapshot$;

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
  legacy_floor_day integer;
  raw_today integer := 0;
  raw_week integer := 0;
  legacy_floor_week integer := 0;
  effective_today integer := 0;
  effective_week integer := 0;
  legacy_floor_used boolean := false;
begin
  if p_user_id is null then
    return jsonb_build_object(
      'today_seconds', 0,
      'week_seconds', 0,
      'source', 'none',
      'raw_source_active', false,
      'raw_week_evidence', false,
      'legacy_floor_used', false
    );
  end if;

  raw_today := public.lili_focus_union_seconds(p_user_id, today_start, today_end);
  raw_week := public.lili_focus_union_seconds(p_user_id, week_start_at, week_end_at);

  day_cursor := week_start;
  while day_cursor <= today loop
    raw_day := public.lili_focus_union_seconds(
      p_user_id,
      (day_cursor::timestamp at time zone 'Asia/Shanghai'),
      ((day_cursor + 1)::timestamp at time zone 'Asia/Shanghai')
    );
    select greatest(0, least(86400, coalesce(f.seconds, 0)))
      into legacy_floor_day
      from public.lili_focus_legacy_daily_floor f
      where f.user_id = p_user_id
        and f.focus_date = day_cursor;
    legacy_floor_day := coalesce(legacy_floor_day, 0);

    legacy_floor_week := legacy_floor_week + legacy_floor_day;
    effective_week := effective_week + greatest(raw_day, legacy_floor_day);
    if legacy_floor_day > raw_day then
      legacy_floor_used := true;
    end if;
    if day_cursor = today then
      effective_today := greatest(raw_day, legacy_floor_day);
    end if;
    day_cursor := day_cursor + 1;
  end loop;

  return jsonb_build_object(
    'today_seconds', greatest(0, least(86400, effective_today)),
    'week_seconds', greatest(0, least(604800, effective_week)),
    'source', case
      when legacy_floor_used and raw_week > 0
        then 'canonical_interval_union_legacy_floor'
      when legacy_floor_used then 'legacy_floor'
      when raw_week > 0 or raw_today > 0 then 'canonical_interval_union'
      else 'none'
    end,
    'raw_source_active', raw_today > 0,
    'raw_week_evidence', raw_week > 0,
    'legacy_floor_used', legacy_floor_used,
    'canonical_raw_today_seconds', greatest(0, least(86400, raw_today)),
    'canonical_raw_week_seconds', greatest(0, least(604800, raw_week)),
    'legacy_floor_week_seconds', greatest(0, least(604800, legacy_floor_week))
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

create or replace function public.lili_mark_canonical_focus_totals(p_people jsonb)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  item jsonb;
  result jsonb := '[]'::jsonb;
begin
  if jsonb_typeof(coalesce(p_people, '[]'::jsonb)) <> 'array' then
    return result;
  end if;
  for item in
    select value from jsonb_array_elements(coalesce(p_people, '[]'::jsonb))
  loop
    result := result || jsonb_build_array(
      item || jsonb_build_object(
        'focus_totals_source', 'canonical_interval_union_legacy_floor'
      )
    );
  end loop;
  return result;
end;
$$;

revoke execute on function public.lili_mark_canonical_focus_totals(jsonb)
  from public, anon, authenticated;

comment on function public.lili_effective_focus_stats(uuid) is
  'Per Beijing day: max(sealed segments plus fresh live presence interval union, frozen pre-interval daily floor). Never sums raw and floor.';

-- Fail the deployment instead of silently publishing a function that can
-- reduce a frozen account below either evidence source.
do $verify$
declare
  week_start date := date_trunc('week', now() at time zone 'Asia/Shanghai')::date;
  today date := (now() at time zone 'Asia/Shanghai')::date;
  week_start_at timestamptz := (week_start::timestamp at time zone 'Asia/Shanghai');
  week_end_at timestamptz := ((week_start + 7)::timestamp at time zone 'Asia/Shanghai');
begin
  if exists (
    select 1
    from (
      select f.user_id, sum(f.seconds)::integer as floor_week
      from public.lili_focus_legacy_daily_floor f
      where f.focus_date between week_start and today
      group by f.user_id
    ) floors
    where public.lili_effective_focus_week_seconds(floors.user_id) < floors.floor_week
  ) then
    raise exception 'effective focus week total fell below frozen legacy floor';
  end if;

  if exists (
    select 1
    from public.lili_profiles p
    where public.lili_effective_focus_week_seconds(p.user_id)
      < public.lili_focus_union_seconds(p.user_id, week_start_at, week_end_at)
  ) then
    raise exception 'effective focus week total fell below canonical raw union';
  end if;
end;
$verify$;
