-- Make every server-side focus total use the same account-level interval union.
--
-- lili_focus_device_presence is the canonical liveness source for the
-- multi-device heartbeat: one row per (user_id, device_id).  The older
-- lili_focus_presence row is retained as a compatibility fallback for mixed
-- version clients.  Both sources are unioned, so the compatibility row can
-- never double-count a device interval.
--
-- Closed lili_focus_segments remain the immutable FocusSession facts.  Live
-- rows are read-only projections and are clipped by server freshness; this
-- migration never writes or changes a FocusSession row.

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
      -- Only closed segments are durable duration facts. An open row is
      -- never allowed to keep counting after a crashed/stopped client.
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

      -- The multi-device source has one liveness row per device.  A device
      -- is usable only while its heartbeat is fresh and its session tuple is
      -- complete.  The server timestamp bounds the open interval; the
      -- two-minute lease prevents a crashed client from counting forever.
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

      union all

      -- Compatibility for a desktop that has not yet adopted the
      -- per-device heartbeat write.  In current deployments this normally
      -- duplicates the representative device row and is removed by the
      -- interval union above.
      select
        greatest(f.session_started_at, p_start_at) as start_at,
        least(now(), f.last_seen + interval '2 minutes', p_end_at) as end_at
      from public.lili_focus_presence f
      where f.user_id = p_user_id
        and f.working
        and f.session_active
        and f.session_id is not null
        and f.session_started_at is not null
        and f.last_seen > now() - interval '2 minutes'
        and f.session_started_at < p_end_at
        and now() > p_start_at
        and f.session_started_at <= now() + interval '2 minutes'
        and extract(epoch from now() - f.session_started_at) between 0 and 86400
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

  -- Evidence follows the same validity rules as the union. Check the
  -- per-device table first; the legacy row is only a compatibility fallback.
  select exists(
    select 1
    from public.lili_focus_segments s
    where s.user_id = p_user_id
      and s.end_at is not null
      and s.start_at < today_end
      and s.end_at > ((today - 400)::timestamp at time zone 'Asia/Shanghai')
      and public.lili_focus_segment_is_valid(s.start_at, s.end_at, now())
  ) or exists(
    select 1
    from public.lili_focus_device_presence d
    where d.user_id = p_user_id
      and d.working
      and d.session_active
      and d.session_id is not null
      and d.session_started_at is not null
      and d.last_seen > now() - interval '2 minutes'
      and d.session_started_at <= now() + interval '2 minutes'
      and extract(epoch from now() - d.session_started_at) between 0 and 86400
  ) or exists(
    select 1
    from public.lili_focus_presence f
    where f.user_id = p_user_id
      and f.working
      and f.session_active
      and f.session_id is not null
      and f.session_started_at is not null
      and f.last_seen > now() - interval '2 minutes'
      and f.session_started_at <= now() + interval '2 minutes'
      and extract(epoch from now() - f.session_started_at) between 0 and 86400
  ) into raw_source_active;

  select exists(
    select 1
    from public.lili_focus_segments s
    where s.user_id = p_user_id
      and s.end_at is not null
      and s.start_at < week_end_at
      and s.end_at > week_start_at
      and public.lili_focus_segment_is_valid(s.start_at, s.end_at, now())
  ) or exists(
    select 1
    from public.lili_focus_device_presence d
    where d.user_id = p_user_id
      and d.working
      and d.session_active
      and d.session_id is not null
      and d.session_started_at is not null
      and d.last_seen > now() - interval '2 minutes'
      and d.session_started_at < week_end_at
      and now() > week_start_at
      and d.session_started_at <= now() + interval '2 minutes'
      and extract(epoch from now() - d.session_started_at) between 0 and 86400
  ) or exists(
    select 1
    from public.lili_focus_presence f
    where f.user_id = p_user_id
      and f.working
      and f.session_active
      and f.session_id is not null
      and f.session_started_at is not null
      and f.last_seen > now() - interval '2 minutes'
      and f.session_started_at < week_end_at
      and now() > week_start_at
      and f.session_started_at <= now() + interval '2 minutes'
      and extract(epoch from now() - f.session_started_at) between 0 and 86400
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

comment on function public.lili_focus_union_seconds(uuid, timestamptz, timestamptz) is
  'Account-level FocusSession interval union: immutable closed segments plus fresh per-device live projections; overlapping devices count once.';
