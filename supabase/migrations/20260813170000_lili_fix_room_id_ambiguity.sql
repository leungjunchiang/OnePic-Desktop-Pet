-- Qualify room_id function parameters so PostgreSQL never confuses them with
-- room_id columns in room-scoped queries.

create or replace function public.lili_leave_room(room_id uuid) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not exists (
    select 1 from public.lili_room_members m
    where m.room_id = lili_leave_room.room_id and m.user_id = (select auth.uid())
  ) then
    raise exception '你不在这个自习室里';
  end if;
  update public.lili_focus_presence
    set room_id = null, updated_at = now(), last_seen = now()
    where user_id = (select auth.uid())
      and lili_focus_presence.room_id = lili_leave_room.room_id;
  delete from public.lili_room_members m
    where m.room_id = lili_leave_room.room_id
      and m.user_id = (select auth.uid());
end;
$$;

create or replace function public.lili_set_room_goal(
  room_id uuid,
  title text,
  target_seconds integer,
  due_at timestamptz default null
) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not exists (
    select 1 from public.lili_room_members m
    where m.room_id = lili_set_room_goal.room_id and m.user_id = (select auth.uid())
  ) then
    raise exception '你不在这个自习室里';
  end if;
  insert into public.lili_room_goals(room_id, title, target_seconds, due_at, created_by, updated_at)
  values (
    lili_set_room_goal.room_id,
    left(trim(lili_set_room_goal.title), 80),
    greatest(60, least(lili_set_room_goal.target_seconds, 604800)),
    lili_set_room_goal.due_at,
    (select auth.uid()),
    now()
  )
  on conflict (room_id) do update
    set title = excluded.title,
        target_seconds = excluded.target_seconds,
        due_at = excluded.due_at,
        created_by = excluded.created_by,
        updated_at = now();
  perform public.lili_record_room_event(
    lili_set_room_goal.room_id,
    'goal_set',
    null,
    left(trim(lili_set_room_goal.title), 80)
  );
end;
$$;

create or replace function public.lili_room_dashboard(room_id uuid) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare result jsonb;
begin
  if not exists (
    select 1 from public.lili_room_members m
    where m.room_id = lili_room_dashboard.room_id and m.user_id = (select auth.uid())
  ) then
    raise exception '你不在这个自习室里';
  end if;
  select jsonb_build_object(
    'id', r.id,
    'name', r.name,
    'invite_code', r.invite_code,
    'room_people', coalesce((
      select jsonb_agg(jsonb_build_object(
        'user_id', p.user_id,
        'nickname', p.nickname,
        'outfit_key', coalesce(f.outfit_key, p.outfit_key),
        'working', case when f.working and f.last_seen > now() - interval '2 minutes' then true else false end,
        'status', case
          when f.last_seen is null or f.last_seen <= now() - interval '2 minutes' then 'offline'
          when f.working then 'focus'
          else 'rest'
        end,
        'session_started_at', f.session_started_at,
        'session_seconds', case
          when f.working and f.session_started_at is not null
            then greatest(0, floor(extract(epoch from (now() - f.session_started_at)))::int)
          else 0
        end,
        'today_seconds', case when p.show_exact_time then coalesce(f.today_seconds, 0) else null end,
        'online', coalesce(f.last_seen > now() - interval '2 minutes', false),
        'is_self', p.user_id = (select auth.uid())
      ) order by p.nickname)
      from public.lili_room_members m
      join public.lili_profiles p on p.user_id = m.user_id
      left join public.lili_focus_presence f on f.user_id = p.user_id
      where m.room_id = r.id and p.visibility = 'friends'
    ), '[]'::jsonb),
    'room_summary', jsonb_build_object(
      'member_count', (select count(*) from public.lili_room_members m where m.room_id = r.id),
      'focus_count', (
        select count(*)
        from public.lili_room_members m
        join public.lili_focus_presence f on f.user_id = m.user_id
        where m.room_id = r.id
          and f.working
          and f.last_seen > now() - interval '2 minutes'
      ),
      'shared_focus_seconds', coalesce((
        select t.cumulative_seconds
        from public.lili_room_focus_totals t
        where t.room_id = r.id
      ), 0) + coalesce((
        select sum(greatest(0, floor(extract(epoch from (now() - f.session_started_at)))::bigint))
        from public.lili_room_members m
        join public.lili_focus_presence f on f.user_id = m.user_id
        where m.room_id = r.id
          and f.working
          and f.session_started_at is not null
          and f.last_seen > now() - interval '2 minutes'
      ), 0)
    ),
    'room_goal', coalesce((
      select jsonb_build_object(
        'title', g.title,
        'target_seconds', g.target_seconds,
        'due_at', g.due_at,
        'completed_seconds', coalesce((
          select t.cumulative_seconds
          from public.lili_room_focus_totals t
          where t.room_id = r.id
        ), 0) + coalesce((
          select sum(greatest(0, floor(extract(epoch from (now() - f.session_started_at)))::bigint))
          from public.lili_room_members m
          join public.lili_focus_presence f on f.user_id = m.user_id
          where m.room_id = r.id
            and f.working
            and f.session_started_at is not null
            and f.last_seen > now() - interval '2 minutes'
        ), 0)
      )
      from public.lili_room_goals g
      where g.room_id = r.id
    ), '{}'::jsonb),
    'room_activity', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', e.id,
        'kind', e.kind,
        'message', e.message,
        'created_at', e.created_at,
        'nickname', coalesce(ap.nickname, '搭子'),
        'target_nickname', tp.nickname
      ) order by e.created_at desc)
      from public.lili_room_events e
      join public.lili_profiles ap on ap.user_id = e.actor_id
      left join public.lili_profiles tp on tp.user_id = e.target_id
      where e.room_id = r.id
    ), '[]'::jsonb)
  )
  into result
  from public.lili_study_rooms r
  where r.id = lili_room_dashboard.room_id;
  return jsonb_build_object('current_room', result);
end;
$$;

revoke execute on function public.lili_leave_room(uuid) from public, anon;
revoke execute on function public.lili_set_room_goal(uuid, text, integer, timestamptz) from public, anon;
revoke execute on function public.lili_room_dashboard(uuid) from public, anon;
grant execute on function public.lili_leave_room(uuid) to authenticated;
grant execute on function public.lili_set_room_goal(uuid, text, integer, timestamptz) to authenticated;
grant execute on function public.lili_room_dashboard(uuid) to authenticated;
