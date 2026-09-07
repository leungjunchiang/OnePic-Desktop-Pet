-- Freeze the duration contract used by every client and every social surface.
--
-- lili_focus_segments is the immutable/sealed FocusSession fact ledger.
-- lili_focus_device_presence is the per-device live projection.  Live time
-- is never materialized by periodically changing end_at on a canonical fact.
-- All effective totals are the interval union of those two sources.

create or replace function public.lili_reject_open_focus_segment()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.start_at is null
     or new.end_at is null
     or new.end_at <= new.start_at then
    -- Returning NULL from a BEFORE trigger ignores the attempted open fact.
    -- This keeps older RPC callers harmless while the current client uses the
    -- delta endpoint and never uploads an open segment in the first place.
    return null;
  end if;
  return new;
end;
$$;

drop trigger if exists lili_focus_segments_sealed_only
  on public.lili_focus_segments;
create trigger lili_focus_segments_sealed_only
before insert or update of start_at, end_at
on public.lili_focus_segments
for each row
execute function public.lili_reject_open_focus_segment();

comment on function public.lili_reject_open_focus_segment() is
  'Canonical FocusSession facts are sealed rows only; open writes are ignored and live duration belongs in lili_focus_device_presence.';

revoke execute on function public.lili_reject_open_focus_segment() from public, anon, authenticated;

create or replace function public.lili_focus_union_seconds(
  p_user_id uuid,
  p_start_at timestamptz,
  p_end_at timestamptz
)
returns integer
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
    select unnest(range_agg(tstzrange(source.start_at, source.end_at, '[)'))) as r
    from (
      select
        greatest(s.start_at, p_start_at) as start_at,
        least(s.end_at, p_end_at) as end_at
      from public.lili_focus_segments s
      where s.user_id = p_user_id
        and s.end_at is not null
        and s.start_at < p_end_at
        and s.end_at > p_start_at
        and public.lili_focus_segment_is_valid(s.start_at, s.end_at, now())

      union all

      select
        greatest(d.session_started_at, p_start_at) as start_at,
        least(now(), d.last_seen + interval '2 minutes', p_end_at) as end_at
      from public.lili_focus_device_presence d
      where d.user_id = p_user_id
        and d.working
        and d.session_active
        and d.session_id is not null
        and d.session_started_at is not null
        and d.last_seen > now() - interval '2 minutes'
        and d.session_started_at < p_end_at
        and now() > p_start_at
        and d.session_started_at <= now() + interval '2 minutes'
        and extract(epoch from now() - d.session_started_at) between 0 and 86400
    ) source
    where source.start_at < source.end_at
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

  select exists(
    select 1 from public.lili_focus_segments s
    where s.user_id = p_user_id
      and s.end_at is not null
      and s.start_at < today_end
      and s.end_at > ((today - 400)::timestamp at time zone 'Asia/Shanghai')
      and public.lili_focus_segment_is_valid(s.start_at, s.end_at, now())
  ) or exists(
    select 1 from public.lili_focus_device_presence d
    where d.user_id = p_user_id
      and d.working and d.session_active
      and d.session_id is not null and d.session_started_at is not null
      and d.last_seen > now() - interval '2 minutes'
      and d.session_started_at <= now() + interval '2 minutes'
      and extract(epoch from now() - d.session_started_at) between 0 and 86400
  ) into raw_source_active;

  select exists(
    select 1 from public.lili_focus_segments s
    where s.user_id = p_user_id
      and s.end_at is not null
      and s.start_at < week_end_at and s.end_at > week_start_at
      and public.lili_focus_segment_is_valid(s.start_at, s.end_at, now())
  ) or exists(
    select 1 from public.lili_focus_device_presence d
    where d.user_id = p_user_id
      and d.working and d.session_active
      and d.session_id is not null and d.session_started_at is not null
      and d.last_seen > now() - interval '2 minutes'
      and d.session_started_at < week_end_at
      and now() > week_start_at
      and d.session_started_at <= now() + interval '2 minutes'
      and extract(epoch from now() - d.session_started_at) between 0 and 86400
  ) into raw_week_evidence;

  raw_today := public.lili_focus_union_seconds(p_user_id, today_start, today_end);
  raw_week := public.lili_focus_union_seconds(p_user_id, week_start_at, week_end_at);

  return jsonb_build_object(
    'today_seconds', greatest(0, least(86400, raw_today)),
    'week_seconds', greatest(0, least(604800, raw_week)),
    'source', case when raw_source_active or raw_week_evidence then 'focus_segments' else 'none' end,
    'raw_source_active', raw_source_active,
    'raw_week_evidence', raw_week_evidence
  );
end;
$$;

revoke execute on function public.lili_effective_focus_stats(uuid)
  from public, anon, authenticated;

comment on function public.lili_effective_focus_stats(uuid) is
  'Effective account focus = sealed segments plus fresh per-device live projections, interval-unioned without overlap.';

-- Keep the friend-facing contract deliberately aggregate-only.  Device,
-- session and start timestamps never leave this security-definer RPC.
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
      'week_seconds', week_seconds
    )
    order by week_seconds desc, nickname
  ), '[]'::jsonb)
  from rows;
$$;

revoke execute on function public.lili_focus_weekly_leaderboard(text)
  from public, anon;
grant execute on function public.lili_focus_weekly_leaderboard(text) to authenticated;

comment on function public.lili_focus_weekly_leaderboard(text) is
  'Friend-facing aggregate leaderboard; effective week seconds include fresh per-device presence but expose no device-level fields.';
