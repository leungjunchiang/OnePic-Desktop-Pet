-- Make room-visible focus totals use the same canonical FocusSession interval
-- projection as the cards, reports and leaderboard.
--
-- The old room total read lili_room_focus_sessions, a legacy room ledger that
-- is not populated by the current FocusSession sync path.  That made a room
-- containing active work show zero (or a stale total) while the member card
-- showed a non-zero value.  The room total below includes the current member
-- and members who opted into exact-time visibility; hidden members are not
-- reverse-engineered through an aggregate.

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
    select unnest(range_agg(tstzrange(source.start_at, source.end_at, '[)'))) as r
    from (
      select
        greatest(s.start_at, p_start_at) as start_at,
        least(coalesce(s.end_at, now()), p_end_at) as end_at
      from public.lili_focus_segments s
      where s.user_id = p_user_id
        and s.start_at < p_end_at
        and coalesce(s.end_at, now()) > p_start_at
        and public.lili_focus_segment_is_valid(s.start_at, s.end_at, now())

      union all

      -- An active session is a live projection, not a new segment.  Include
      -- it only while its presence heartbeat is fresh, and let range_agg
      -- merge any overlap with already-synced closed segments.
      select
        greatest(f.session_started_at, p_start_at) as start_at,
        least(now(), p_end_at) as end_at
      from public.lili_focus_presence f
      where f.user_id = p_user_id
        and f.working
        and f.session_active
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

  -- A current active session is raw evidence even when its first closed
  -- segment has not reached the server yet.  Without this, presence says
  -- "working" but buddy cards report only the last closed checkpoint.
  select exists(
    select 1
    from public.lili_focus_segments s
    where s.user_id = p_user_id
      and s.start_at < now() + interval '2 minutes'
      and coalesce(s.end_at, now()) > ((today - 400)::timestamp at time zone 'Asia/Shanghai')
  ) or exists(
    select 1
    from public.lili_focus_presence f
    where f.user_id = p_user_id
      and f.working
      and f.session_active
      and f.session_started_at is not null
      and f.last_seen > now() - interval '2 minutes'
      and f.session_started_at <= now() + interval '2 minutes'
      and extract(epoch from now() - f.session_started_at) between 0 and 86400
  ) into raw_source_active;

  select exists(
    select 1
    from public.lili_focus_segments s
    where s.user_id = p_user_id
      and s.start_at < week_end_at
      and coalesce(s.end_at, now()) > week_start_at
  ) or exists(
    select 1
    from public.lili_focus_presence f
    where f.user_id = p_user_id
      and f.working
      and f.session_active
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

create or replace function public.lili_room_focus_seconds_today(p_room_id uuid)
returns bigint
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  total bigint;
begin
  if not exists (
    select 1 from public.lili_room_members m
    where m.room_id = p_room_id and m.user_id = (select auth.uid())
  ) then
    raise exception 'room_membership_required';
  end if;

  -- This is the visible room total: the current user is always included;
  -- other members contribute only when their exact-time preference permits it.
  select coalesce(sum(public.lili_effective_focus_today_seconds(m.user_id)), 0)::bigint
  into total
  from public.lili_room_members m
  left join public.lili_profiles p on p.user_id = m.user_id
  where m.room_id = p_room_id
    and (
      m.user_id = (select auth.uid())
      or coalesce(p.show_exact_time, false)
    );

  return total;
end;
$$;

revoke execute on function public.lili_room_focus_seconds_today(uuid)
  from public, anon;
grant execute on function public.lili_room_focus_seconds_today(uuid) to authenticated;

