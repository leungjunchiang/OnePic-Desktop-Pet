-- Preserve old daily totals as explicit compatibility evidence.
--
-- Sealed FocusSegments remain the only source for an interval/timeline.  Old
-- clients still send cumulative daily counters, so those counters are stored
-- in a separate ledger and can affect effective calendar totals without ever
-- being turned into synthetic FocusSegments.

create table if not exists public.lili_focus_legacy_compatibility_ledger (
  user_id uuid not null references auth.users(id) on delete cascade,
  focus_date date not null,
  legacy_seconds integer not null check (legacy_seconds between 0 and 86400),
  legacy_source text not null default 'legacy_sync',
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  cutover_at timestamptz,
  cutover_legacy_seconds integer not null default 0
    check (cutover_legacy_seconds between 0 and 86400),
  primary key (user_id, focus_date)
);

create index if not exists lili_focus_legacy_ledger_user_date_idx
  on public.lili_focus_legacy_compatibility_ledger (user_id, focus_date desc);

create table if not exists public.lili_focus_legacy_compatibility_meta (
  snapshot_key text primary key,
  cutover_at timestamptz not null default now()
);

alter table public.lili_focus_legacy_compatibility_ledger enable row level security;
alter table public.lili_focus_legacy_compatibility_meta enable row level security;
revoke all on table public.lili_focus_legacy_compatibility_ledger
  from public, anon, authenticated;
revoke all on table public.lili_focus_legacy_compatibility_meta
  from public, anon, authenticated;
grant select on table public.lili_focus_legacy_compatibility_ledger to service_role;
grant select on table public.lili_focus_legacy_compatibility_meta to service_role;

comment on table public.lili_focus_legacy_compatibility_ledger is
  'Legacy daily aggregate evidence. It contributes to effective calendar totals but never creates or alters a FocusSegment.';
comment on column public.lili_focus_legacy_compatibility_ledger.cutover_legacy_seconds is
  'Legacy cumulative baseline at the interval-ledger cutover; canonical time after cutover is added separately.';

do $meta$
begin
  insert into public.lili_focus_legacy_compatibility_meta (snapshot_key)
  values ('legacy_daily_ledger_v1')
  on conflict (snapshot_key) do nothing;
end;
$meta$;

-- One-time migration of every date that already had a daily aggregate.  The
-- frozen floor is included as well so this migration is safe to apply after
-- the previous floor migration, and the profile row covers a day that was
-- reported before its daily-history row was flushed.
do $backfill$
begin
  insert into public.lili_focus_legacy_compatibility_ledger (
    user_id,
    focus_date,
    legacy_seconds,
    legacy_source,
    first_seen_at,
    last_seen_at,
    cutover_at,
    cutover_legacy_seconds
  )
  with source_rows as (
    select d.user_id, d.focus_date, greatest(0, least(86400, coalesce(d.seconds, 0))) as seconds
    from public.lili_focus_daily d
    where d.focus_date <= (now() at time zone 'Asia/Shanghai')::date

    union all

    select f.user_id, f.focus_date, greatest(0, least(86400, coalesce(f.seconds, 0))) as seconds
    from public.lili_focus_legacy_daily_floor f
    where f.focus_date <= (now() at time zone 'Asia/Shanghai')::date

    union all

    select p.user_id, p.focus_today_date,
           greatest(0, least(86400, coalesce(p.focus_today_seconds, 0))) as seconds
    from public.lili_profiles p
    where p.focus_today_date <= (now() at time zone 'Asia/Shanghai')::date
  ),
  collapsed as (
    select user_id, focus_date, max(seconds)::integer as seconds
    from source_rows
    where focus_date is not null and seconds > 0
    group by user_id, focus_date
  ),
  meta as (
    select cutover_at
    from public.lili_focus_legacy_compatibility_meta
    where snapshot_key = 'legacy_daily_ledger_v1'
  )
  select user_id, focus_date, seconds, 'legacy_daily_migration', now(), now(),
         (select cutover_at from meta), seconds
  from collapsed
  on conflict (user_id, focus_date) do update
    set legacy_seconds = greatest(
          public.lili_focus_legacy_compatibility_ledger.legacy_seconds,
          excluded.legacy_seconds
        ),
        legacy_source = case
          when excluded.legacy_seconds >= public.lili_focus_legacy_compatibility_ledger.legacy_seconds
            then excluded.legacy_source
          else public.lili_focus_legacy_compatibility_ledger.legacy_source
        end,
        last_seen_at = now(),
        cutover_at = coalesce(
          public.lili_focus_legacy_compatibility_ledger.cutover_at,
          excluded.cutover_at
        ),
        cutover_legacy_seconds = greatest(
          public.lili_focus_legacy_compatibility_ledger.cutover_legacy_seconds,
          excluded.cutover_legacy_seconds
        );
end;
$backfill$;

create or replace function public.lili_record_legacy_focus_day(
  p_user_id uuid,
  p_focus_date date,
  p_seconds integer,
  p_source text default 'legacy_sync'
)
returns boolean
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  today date := (now() at time zone 'Asia/Shanghai')::date;
  target_seconds integer := greatest(0, least(86400, coalesce(p_seconds, 0)));
  snapshot_cutover timestamptz;
  raw_after_cutover integer := 0;
  baseline integer;
begin
  if p_user_id is null or p_focus_date is null or p_focus_date > today then
    return false;
  end if;

  select m.cutover_at
    into snapshot_cutover
    from public.lili_focus_legacy_compatibility_meta m
   where m.snapshot_key = 'legacy_daily_ledger_v1';

  if snapshot_cutover is not null then
    raw_after_cutover := public.lili_focus_union_seconds(
      p_user_id,
      greatest(
        p_focus_date::timestamp at time zone 'Asia/Shanghai',
        snapshot_cutover
      ),
      (p_focus_date + 1)::timestamp at time zone 'Asia/Shanghai'
    );
  end if;

  -- Old clients send cumulative daily time.  Preserve the portion that is
  -- not explained by post-cutover canonical intervals as the compatibility
  -- baseline, while keeping the full observed aggregate for audit/display.
  baseline := greatest(0, least(86400, target_seconds - raw_after_cutover));

  insert into public.lili_focus_legacy_compatibility_ledger (
    user_id,
    focus_date,
    legacy_seconds,
    legacy_source,
    cutover_at,
    cutover_legacy_seconds
  ) values (
    p_user_id,
    p_focus_date,
    target_seconds,
    left(coalesce(nullif(btrim(p_source), ''), 'legacy_sync'), 80),
    snapshot_cutover,
    baseline
  )
  on conflict (user_id, focus_date) do update
    set legacy_seconds = greatest(
          public.lili_focus_legacy_compatibility_ledger.legacy_seconds,
          excluded.legacy_seconds
        ),
        legacy_source = case
          when excluded.legacy_seconds >= public.lili_focus_legacy_compatibility_ledger.legacy_seconds
            then excluded.legacy_source
          else public.lili_focus_legacy_compatibility_ledger.legacy_source
        end,
        last_seen_at = now(),
        cutover_at = coalesce(
          public.lili_focus_legacy_compatibility_ledger.cutover_at,
          excluded.cutover_at
        ),
        cutover_legacy_seconds = greatest(
          public.lili_focus_legacy_compatibility_ledger.cutover_legacy_seconds,
          excluded.cutover_legacy_seconds
        );

  return true;
end;
$$;

revoke execute on function public.lili_record_legacy_focus_day(uuid, date, integer, text)
  from public, anon, authenticated;
grant execute on function public.lili_record_legacy_focus_day(uuid, date, integer, text)
  to service_role;

create or replace function public.lili_effective_focus_day_seconds(
  p_user_id uuid,
  p_focus_date date
)
returns integer
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  day_start timestamptz := p_focus_date::timestamp at time zone 'Asia/Shanghai';
  day_end timestamptz := (p_focus_date + 1)::timestamp at time zone 'Asia/Shanghai';
  canonical_seconds integer := 0;
  frozen_seconds integer := 0;
  legacy_seconds integer := 0;
  cutover_at timestamptz;
  cutover_seconds integer := 0;
  canonical_after_cutover integer := 0;
begin
  if p_user_id is null or p_focus_date is null then
    return 0;
  end if;

  canonical_seconds := public.lili_focus_union_seconds(p_user_id, day_start, day_end);

  select greatest(0, least(86400, coalesce(f.seconds, 0)))
    into frozen_seconds
    from public.lili_focus_legacy_daily_floor f
   where f.user_id = p_user_id and f.focus_date = p_focus_date;
  frozen_seconds := coalesce(frozen_seconds, 0);

  select greatest(0, least(86400, coalesce(l.legacy_seconds, 0))),
         l.cutover_at,
         greatest(0, least(86400, coalesce(l.cutover_legacy_seconds, 0)))
    into legacy_seconds, cutover_at, cutover_seconds
    from public.lili_focus_legacy_compatibility_ledger l
   where l.user_id = p_user_id and l.focus_date = p_focus_date;
  legacy_seconds := coalesce(legacy_seconds, 0);
  cutover_seconds := coalesce(cutover_seconds, 0);

  if cutover_at is not null and cutover_at < day_end then
    canonical_after_cutover := public.lili_focus_union_seconds(
      p_user_id,
      greatest(day_start, cutover_at),
      day_end
    );
  end if;

  return greatest(
    0,
    least(86400, greatest(
      canonical_seconds,
      frozen_seconds,
      legacy_seconds,
      least(86400, cutover_seconds + canonical_after_cutover)
    ))
  );
end;
$$;

revoke execute on function public.lili_effective_focus_day_seconds(uuid, date)
  from public, anon, authenticated;
grant execute on function public.lili_effective_focus_day_seconds(uuid, date)
  to service_role;

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
  day_cursor date;
  canonical_day integer;
  effective_day integer;
  ledger_day integer;
  frozen_day integer;
  canonical_after_cutover integer;
  cutover_at timestamptz;
  raw_today integer := public.lili_focus_union_seconds(
    p_user_id,
    today::timestamp at time zone 'Asia/Shanghai',
    (today + 1)::timestamp at time zone 'Asia/Shanghai'
  );
  raw_week integer := public.lili_focus_union_seconds(
    p_user_id,
    week_start::timestamp at time zone 'Asia/Shanghai',
    (week_start + 7)::timestamp at time zone 'Asia/Shanghai'
  );
  effective_today integer := 0;
  effective_week integer := 0;
  canonical_after_cutover_week integer := 0;
  legacy_week integer := 0;
  legacy_used boolean := false;
begin
  if p_user_id is null then
    return jsonb_build_object(
      'today_seconds', 0,
      'week_seconds', 0,
      'source', 'none',
      'raw_source_active', false,
      'raw_week_evidence', false,
      'legacy_compatibility_used', false
    );
  end if;

  day_cursor := week_start;
  while day_cursor <= today loop
    canonical_day := public.lili_focus_union_seconds(
      p_user_id,
      day_cursor::timestamp at time zone 'Asia/Shanghai',
      (day_cursor + 1)::timestamp at time zone 'Asia/Shanghai'
    );
    effective_day := public.lili_effective_focus_day_seconds(p_user_id, day_cursor);
    effective_week := effective_week + effective_day;
    if effective_day > canonical_day then
      legacy_used := true;
    end if;
    if day_cursor = today then
      effective_today := effective_day;
    end if;

    select greatest(0, least(86400, coalesce(l.legacy_seconds, 0))),
           l.cutover_at
      into ledger_day, cutover_at
      from public.lili_focus_legacy_compatibility_ledger l
     where l.user_id = p_user_id and l.focus_date = day_cursor;
    ledger_day := coalesce(ledger_day, 0);
    legacy_week := legacy_week + ledger_day;

    if cutover_at is not null
       and cutover_at < ((day_cursor + 1)::timestamp at time zone 'Asia/Shanghai') then
      canonical_after_cutover := public.lili_focus_union_seconds(
        p_user_id,
        greatest(day_cursor::timestamp at time zone 'Asia/Shanghai', cutover_at),
        (day_cursor + 1)::timestamp at time zone 'Asia/Shanghai'
      );
      canonical_after_cutover_week := canonical_after_cutover_week + canonical_after_cutover;
    end if;
    day_cursor := day_cursor + 1;
  end loop;

  return jsonb_build_object(
    'today_seconds', greatest(0, least(86400, effective_today)),
    'week_seconds', greatest(0, least(604800, effective_week)),
    'source', case
      when legacy_used and (raw_week > 0 or legacy_week > 0)
        then 'canonical_interval_union_legacy_ledger'
      when legacy_week > 0 then 'legacy_compatibility_ledger'
      when raw_week > 0 or raw_today > 0 then 'canonical_interval_union'
      else 'none'
    end,
    'raw_source_active', raw_today > 0,
    'raw_week_evidence', raw_week > 0,
    'legacy_compatibility_used', legacy_used,
    'canonical_raw_today_seconds', greatest(0, least(86400, raw_today)),
    'canonical_raw_week_seconds', greatest(0, least(604800, raw_week)),
    'legacy_compatibility_week_seconds', greatest(0, least(604800, legacy_week)),
    'canonical_after_cutover_week_seconds', greatest(0, least(604800, canonical_after_cutover_week))
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

create or replace function public.lili_effective_focus_week_seconds(p_user_id uuid)
returns integer
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce((public.lili_effective_focus_stats(p_user_id)->>'week_seconds')::integer, 0);
$$;

revoke execute on function public.lili_effective_focus_today_seconds(uuid)
  from public, anon, authenticated;
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
  target_user_id uuid;
begin
  if jsonb_typeof(coalesce(p_people, '[]'::jsonb)) <> 'array' then
    return result;
  end if;
  for item in select value from jsonb_array_elements(coalesce(p_people, '[]'::jsonb)) loop
    target_user_id := null;
    begin
      target_user_id := nullif(item ->> 'user_id', '')::uuid;
    exception when invalid_text_representation then
      target_user_id := null;
    end;
    if target_user_id is not null then
      if item ? 'today_seconds' and item -> 'today_seconds' <> 'null'::jsonb then
        item := jsonb_set(item, '{today_seconds}',
          to_jsonb(public.lili_effective_focus_today_seconds(target_user_id)), true);
      end if;
      if item ? 'week_seconds' and item -> 'week_seconds' <> 'null'::jsonb then
        item := jsonb_set(item, '{week_seconds}',
          to_jsonb(public.lili_effective_focus_week_seconds(target_user_id)), true);
      end if;
    end if;
    result := result || jsonb_build_array(item || jsonb_build_object(
      'focus_totals_source', 'canonical_interval_union',
      'focus_totals_effective_source', 'canonical_interval_union_legacy_ledger'
    ));
  end loop;
  return result;
end;
$$;

revoke execute on function public.lili_mark_canonical_focus_totals(jsonb)
  from public, anon, authenticated;

create or replace function public.lili_sync_personal_state(
  p_focus_date date default ((now() at time zone 'Asia/Shanghai')::date),
  p_today_seconds integer default 0,
  p_lifetime_seconds bigint default 0,
  p_outfit_key text default null,
  p_outfit_set boolean default false,
  p_week_start date default (date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date),
  p_week_seconds integer default 0
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  current_user_id uuid := (select auth.uid());
  current_day date := ((now() at time zone 'Asia/Shanghai')::date);
  current_week date := (date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date);
  target_date date := coalesce(p_focus_date, current_day);
  stats jsonb;
  merged_lifetime bigint;
  merged_outfit text;
  ledger_updated boolean := false;
begin
  if current_user_id is null then
    raise exception '需要登录';
  end if;

  ledger_updated := public.lili_record_legacy_focus_day(
    current_user_id,
    target_date,
    greatest(0, least(86400, coalesce(p_today_seconds, 0))),
    'legacy_sync_personal_state'
  );

  update public.lili_profiles p
  set focus_lifetime_seconds = greatest(
        p.focus_lifetime_seconds,
        greatest(0, coalesce(p_lifetime_seconds, 0))
      ),
      outfit_key = case
        when coalesce(p_outfit_set, false)
          then left(btrim(coalesce(p_outfit_key, '')), 60)
        else p.outfit_key
      end,
      updated_at = now()
  where p.user_id = current_user_id
  returning p.focus_lifetime_seconds, p.outfit_key
    into merged_lifetime, merged_outfit;

  if not found then
    raise exception '搭子资料不存在';
  end if;

  stats := public.lili_effective_focus_stats(current_user_id);
  return jsonb_build_object(
    'focus_today_date', current_day,
    'focus_today_seconds', coalesce((stats->>'today_seconds')::integer, 0),
    'focus_lifetime_seconds', merged_lifetime,
    'focus_week_start_date', current_week,
    'focus_week_seconds', coalesce((stats->>'week_seconds')::integer, 0),
    'outfit_key', merged_outfit,
    'legacy_ledger_updated', ledger_updated,
    'focus_totals_source', coalesce(stats->>'source', 'none'),
    'focus_totals_effective_source', 'canonical_interval_union_legacy_ledger'
  );
end;
$$;

revoke execute on function public.lili_sync_personal_state(
  date, integer, bigint, text, boolean, date, integer
) from public, anon;
grant execute on function public.lili_sync_personal_state(
  date, integer, bigint, text, boolean, date, integer
) to authenticated, service_role;

create or replace function public.lili_sync_focus_history(p_history jsonb default '[]'::jsonb)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  current_user_id uuid := (select auth.uid());
  today date := (now() at time zone 'Asia/Shanghai')::date;
  item jsonb;
  item_date date;
  item_seconds integer;
  accepted integer := 0;
begin
  if current_user_id is null then
    raise exception '需要登录后才能同步专注历史';
  end if;
  if jsonb_typeof(coalesce(p_history, '[]'::jsonb)) <> 'array' then
    raise exception '专注历史格式无效';
  end if;

  for item in select value from jsonb_array_elements(coalesce(p_history, '[]'::jsonb)) loop
    item_date := null;
    item_seconds := 0;
    begin
      if coalesce(item->>'focus_date', '') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then
        item_date := (item->>'focus_date')::date;
        item_seconds := greatest(0, least(86400, coalesce((item->>'seconds')::integer, 0)));
      end if;
    exception when others then
      item_date := null;
    end;
    if item_date is not null and item_date <= today then
      if public.lili_record_legacy_focus_day(
        current_user_id, item_date, item_seconds, 'legacy_sync_focus_history'
      ) then
        accepted := accepted + 1;
      end if;
    end if;
  end loop;

  return jsonb_build_object(
    'focus_date', today,
    'accepted_legacy_days', accepted,
    'days', coalesce((
      select jsonb_agg(jsonb_build_object(
        'focus_date', generated.day_value::date,
        'seconds', public.lili_effective_focus_day_seconds(current_user_id, generated.day_value::date),
        'effective_seconds', public.lili_effective_focus_day_seconds(current_user_id, generated.day_value::date),
        'canonical_seconds', public.lili_focus_union_seconds(
          current_user_id,
          generated.day_value::date::timestamp at time zone 'Asia/Shanghai',
          (generated.day_value::date + 1)::timestamp at time zone 'Asia/Shanghai'
        ),
        'legacy_seconds', greatest(
          coalesce((select l.legacy_seconds from public.lili_focus_legacy_compatibility_ledger l
                    where l.user_id = current_user_id and l.focus_date = generated.day_value::date), 0),
          coalesce((select f.seconds from public.lili_focus_legacy_daily_floor f
                    where f.user_id = current_user_id and f.focus_date = generated.day_value::date), 0)
        ),
        'time_source', case
          when public.lili_effective_focus_day_seconds(current_user_id, generated.day_value::date)
             > public.lili_focus_union_seconds(
                 current_user_id,
                 generated.day_value::date::timestamp at time zone 'Asia/Shanghai',
                 (generated.day_value::date + 1)::timestamp at time zone 'Asia/Shanghai'
               ) then 'legacy_compatibility_ledger'
          else 'canonical_interval_union'
        end
      ) order by generated.day_value)
      from generate_series(today - 7, today, interval '1 day') generated(day_value)
    ), '[]'::jsonb)
  );
end;
$$;

revoke execute on function public.lili_sync_focus_history(jsonb) from public, anon;
grant execute on function public.lili_sync_focus_history(jsonb) to authenticated, service_role;

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
      and (p.user_id = (select auth.uid())
        or public.lili_are_buddies((select auth.uid()), p.user_id))
  )
  select coalesce(jsonb_agg(
    jsonb_build_object(
      'user_id', user_id,
      'nickname', nickname,
      'week_start', date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date,
      'week_seconds', week_seconds,
      'focus_totals_source', 'canonical_interval_union',
      'focus_totals_effective_source', 'canonical_interval_union_legacy_ledger'
    )
    order by week_seconds desc, nickname
  ), '[]'::jsonb)
  from rows;
$$;

revoke execute on function public.lili_focus_weekly_leaderboard(text) from public, anon;
grant execute on function public.lili_focus_weekly_leaderboard(text) to authenticated, service_role;

create or replace function public.lili_dashboard()
returns jsonb
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
    raise exception 'authentication required';
  end if;
  payload := public.lili_dashboard_multidevice_base_20260830();
  payload := jsonb_set(payload, '{buddies}', public.lili_mark_canonical_focus_totals(
    public.lili_normalize_focus_today_people(public.lili_zero_never_seen_presence(coalesce(payload -> 'buddies', '[]'::jsonb)))
  ), true);
  payload := jsonb_set(payload, '{room_people}', public.lili_mark_canonical_focus_totals(
    public.lili_normalize_focus_today_people(public.lili_zero_never_seen_presence(coalesce(payload -> 'room_people', '[]'::jsonb)))
  ), true);
  payload := jsonb_set(payload, '{active_visits}', public.lili_mark_canonical_focus_totals(
    public.lili_normalize_focus_today_people(public.lili_zero_never_seen_presence(coalesce(payload -> 'active_visits', '[]'::jsonb)))
  ), true);
  if jsonb_typeof(payload -> 'current_room') = 'object' then
    payload := jsonb_set(payload, '{current_room,room_people}', public.lili_mark_canonical_focus_totals(
      public.lili_normalize_focus_today_people(public.lili_zero_never_seen_presence(
        coalesce(payload -> 'current_room' -> 'room_people', '[]'::jsonb)
      ))
    ), true);
  end if;
  if jsonb_typeof(payload -> 'me_presence') = 'object' then
    payload := jsonb_set(payload, '{me_presence,today_seconds}', to_jsonb(public.lili_effective_focus_today_seconds(me_id)), true);
    payload := jsonb_set(payload, '{me_presence,week_seconds}', to_jsonb(public.lili_effective_focus_week_seconds(me_id)), true);
    payload := jsonb_set(payload, '{me_presence,focus_totals_source}', to_jsonb('canonical_interval_union'::text), true);
    payload := jsonb_set(payload, '{me_presence,focus_totals_effective_source}', to_jsonb('canonical_interval_union_legacy_ledger'::text), true);
  end if;
  if jsonb_typeof(payload -> 'me') = 'object' then
    payload := jsonb_set(payload, '{me,focus_today_seconds}', to_jsonb(public.lili_effective_focus_today_seconds(me_id)), true);
    payload := jsonb_set(payload, '{me,focus_week_seconds}', to_jsonb(public.lili_effective_focus_week_seconds(me_id)), true);
    payload := jsonb_set(payload, '{me,focus_totals_source}', to_jsonb('canonical_interval_union'::text), true);
    payload := jsonb_set(payload, '{me,focus_totals_effective_source}', to_jsonb('canonical_interval_union_legacy_ledger'::text), true);
  end if;
  payload := jsonb_set(payload, '{focus_totals_source}', to_jsonb('canonical_interval_union'::text), true);
  payload := jsonb_set(payload, '{focus_totals_effective_source}', to_jsonb('canonical_interval_union_legacy_ledger'::text), true);
  return payload;
end;
$$;

revoke execute on function public.lili_dashboard() from public, anon;
grant execute on function public.lili_dashboard() to authenticated;

create or replace function public.lili_room_dashboard_social_pet_names_base(p_room_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  payload jsonb;
begin
  payload := public.lili_room_dashboard_presence_base_20260828(p_room_id);
  if jsonb_typeof(payload -> 'current_room') = 'object' then
    payload := jsonb_set(payload, '{current_room,room_people}', public.lili_mark_canonical_focus_totals(
      public.lili_normalize_focus_today_people(coalesce(payload -> 'current_room' -> 'room_people', '[]'::jsonb))
    ), true);
  end if;
  payload := jsonb_set(payload, '{focus_totals_source}', to_jsonb('canonical_interval_union'::text), true);
  payload := jsonb_set(payload, '{focus_totals_effective_source}', to_jsonb('canonical_interval_union_legacy_ledger'::text), true);
  return payload;
end;
$$;

revoke execute on function public.lili_room_dashboard_social_pet_names_base(uuid)
  from public, anon, authenticated;

comment on function public.lili_sync_personal_state(
  date, integer, bigint, text, boolean, date, integer
) is 'Compatibility RPC. Old daily scalars are recorded as legacy evidence; effective totals combine that ledger with canonical interval unions without synthesizing intervals.';
comment on function public.lili_sync_focus_history(jsonb) is
  'Compatibility RPC. Daily aggregate input is redirected to the legacy compatibility ledger; returned days expose canonical, legacy, and effective provenance.';
comment on function public.lili_effective_focus_stats(uuid) is
  'Effective account focus = canonical interval union plus legacy daily compatibility evidence, with post-cutover canonical time added once. Legacy evidence never becomes a FocusSegment.';

do $verify$
declare
  personal_definition text;
  history_definition text;
begin
  personal_definition := pg_get_functiondef(
    'public.lili_sync_personal_state(date,integer,bigint,text,boolean,date,integer)'::regprocedure
  );
  history_definition := pg_get_functiondef(
    'public.lili_sync_focus_history(jsonb)'::regprocedure
  );
  if position('insert into public.lili_focus_daily' in lower(personal_definition)) > 0
     or position('insert into public.lili_focus_daily' in lower(history_definition)) > 0
     or position('insert into public.lili_focus_segments' in lower(personal_definition)) > 0
     or position('insert into public.lili_focus_segments' in lower(history_definition)) > 0 then
    raise exception 'legacy compatibility ledger verification failed: aggregate input became canonical interval data';
  end if;
end;
$verify$;
