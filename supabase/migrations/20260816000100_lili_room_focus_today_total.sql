-- Expose two room-scoped focus metrics without introducing a second data store:
--   * today_shared_focus_seconds: ledger time overlapping today's Beijing day
--   * cumulative_shared_focus_seconds: the full room ledger total
--
-- The existing shared_focus_seconds key remains as a cumulative compatibility
-- alias for older clients. Both values are derived from the same
-- lili_room_focus_sessions ledger.

create or replace function public.lili_room_focus_seconds_today(p_room_id uuid)
returns bigint
language plpgsql stable security definer set search_path = '' as $$
declare
  day_start timestamptz;
  day_end timestamptz;
  total bigint;
begin
  if not exists (
    select 1 from public.lili_room_members m
    where m.room_id = p_room_id and m.user_id = (select auth.uid())
  ) then
    raise exception 'room_membership_required';
  end if;

  -- The product day is Beijing time even when Postgres/session timezone is UTC.
  day_start := date_trunc('day', now() at time zone 'Asia/Shanghai') at time zone 'Asia/Shanghai';
  day_end := day_start + interval '1 day';

  select coalesce(sum(
    greatest(
      0,
      floor(extract(epoch from (
        least(coalesce(s.ended_at, now()), day_end)
        - greatest(s.started_at, day_start)
      )))::bigint
    )
  ), 0)::bigint
  into total
  from public.lili_room_focus_sessions s
  where s.room_id = p_room_id
    and s.started_at < day_end
    and coalesce(s.ended_at, now()) > day_start;

  return total;
end;
$$;

create or replace function public.lili_room_dashboard(p_room_id uuid)
returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  result jsonb;
  today_total bigint;
  cumulative_total bigint;
begin
  if not exists (
    select 1 from public.lili_room_members m
    where m.room_id = p_room_id and m.user_id = (select auth.uid())
  ) then
    raise exception 'room_membership_required';
  end if;

  -- Evaluate the ledger once per metric per dashboard instead of repeating
  -- the same room-wide aggregation for the summary and room goal.
  today_total := public.lili_room_focus_seconds_today(p_room_id);
  cumulative_total := public.lili_room_focus_seconds(p_room_id);

  select jsonb_build_object(
    'id', r.id,
    'name', r.name,
    'invite_code', r.invite_code,
    'room_people', coalesce((
      select jsonb_agg(jsonb_build_object(
        'user_id', p.user_id,
        'nickname', public.lili_owner_nickname(p.user_id),
        'owner_nickname', public.lili_owner_nickname(p.user_id),
        'outfit_key', coalesce(f.outfit_key, p.outfit_key),
        'working', case when f.working and f.last_seen > now() - interval '2 minutes' then true else false end,
        'status', case
          when f.last_seen is null or f.last_seen <= now() - interval '2 minutes' then 'offline'
          when f.working then 'focus' else 'rest' end,
        'session_started_at', f.session_started_at,
        'session_seconds', case when f.working and f.session_started_at is not null
          then greatest(0, floor(extract(epoch from (now() - f.session_started_at)))::int) else 0 end,
        'today_seconds', case when p.show_exact_time then coalesce(f.today_seconds, 0) else null end,
        'quick_status', case when f.quick_status_expires_at is null or f.quick_status_expires_at > now() then f.quick_status else null end,
        'quick_status_expires_at', case when f.quick_status_expires_at is null or f.quick_status_expires_at > now() then f.quick_status_expires_at else null end,
        'online', coalesce(f.last_seen > now() - interval '2 minutes', false),
        'is_self', p.user_id = (select auth.uid())
      ) order by public.lili_owner_nickname(p.user_id))
      from public.lili_room_members m
      join public.lili_profiles p on p.user_id = m.user_id
      left join public.lili_focus_presence f on f.user_id = p.user_id
      where m.room_id = r.id
    ), '[]'::jsonb),
    'room_summary', jsonb_build_object(
      'member_count', (select count(*) from public.lili_room_members m where m.room_id = r.id),
      'focus_count', (select count(*) from public.lili_room_members m join public.lili_focus_presence f on f.user_id = m.user_id
        where m.room_id = r.id and f.working and f.last_seen > now() - interval '2 minutes'),
      'today_shared_focus_seconds', today_total,
      'cumulative_shared_focus_seconds', cumulative_total,
      'shared_focus_seconds', cumulative_total
    ),
    'room_goal', coalesce((select jsonb_build_object(
      'title', g.title, 'target_seconds', g.target_seconds, 'due_at', g.due_at,
      'completed_seconds', cumulative_total
    ) from public.lili_room_goals g where g.room_id = r.id), '{}'::jsonb),
    'room_activity', coalesce((select jsonb_agg(jsonb_build_object(
      'id', e.id,
      'actor_id', e.actor_id,
      'target_id', e.target_id,
      'kind', e.kind,
      'message', e.message,
      'created_at', e.created_at,
      'owner_nickname', public.lili_owner_nickname(e.actor_id),
      'target_owner_nickname', public.lili_owner_nickname(e.target_id),
      'nickname', coalesce(public.lili_owner_nickname(e.actor_id), '搭子'),
      'target_nickname', public.lili_owner_nickname(e.target_id)
    ) order by e.created_at desc) from public.lili_room_events e where e.room_id = r.id), '[]'::jsonb)
  ) into result
  from public.lili_study_rooms r
  where r.id = p_room_id;

  return jsonb_build_object('current_room', result);
end;
$$;

revoke execute on function public.lili_room_focus_seconds_today(uuid) from public, anon;
revoke execute on function public.lili_room_dashboard(uuid) from public, anon;
grant execute on function public.lili_room_focus_seconds_today(uuid) to authenticated;
grant execute on function public.lili_room_dashboard(uuid) to authenticated;
