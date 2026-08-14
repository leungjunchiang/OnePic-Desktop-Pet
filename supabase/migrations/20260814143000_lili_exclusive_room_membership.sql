-- Make room membership exclusive: one account can belong to only one room.
-- Room-scoped reads use membership as their privacy boundary; profile
-- visibility is intentionally not applied inside a room the user joined.

-- Repair legacy duplicate memberships before adding the invariant.  Keep a
-- currently active presence room when possible; otherwise keep the newest
-- membership.  This does not delete rooms or room history.
delete from public.lili_room_members m
using (
  select m.room_id, m.user_id,
    row_number() over (
      partition by user_id
      order by (
        fp.room_id is not null
        and fp.room_id = m.room_id
        and fp.last_seen > now() - interval '2 minutes'
      ) desc, m.joined_at desc, m.room_id
    ) as membership_rank
  from public.lili_room_members m
  left join public.lili_focus_presence fp on fp.user_id = m.user_id
) duplicate_memberships
where m.room_id = duplicate_memberships.room_id
  and m.user_id = duplicate_memberships.user_id
  and duplicate_memberships.membership_rank > 1;

create unique index if not exists lili_room_members_one_room_per_user
  on public.lili_room_members(user_id);

create or replace function public.lili_create_room(room_name text)
returns uuid
language plpgsql security definer set search_path = '' as $$
declare
  new_room uuid;
begin
  -- Joining/creating a room is an explicit room switch.
  update public.lili_focus_presence fp
  set room_id = null,
      working = false,
      session_started_at = null,
      quick_status = null,
      quick_status_expires_at = null,
      updated_at = now(),
      last_seen = now()
  where fp.user_id = (select auth.uid());

  delete from public.lili_room_members m
  where m.user_id = (select auth.uid());

  insert into public.lili_study_rooms(owner_id, name, invite_code)
  values ((select auth.uid()), left(trim(room_name), 30), public.lili_invite_code())
  returning id into new_room;

  insert into public.lili_room_members(room_id, user_id)
  values (new_room, (select auth.uid()));
  return new_room;
end;
$$;

create or replace function public.lili_join_room(code text)
returns uuid
language plpgsql security definer set search_path = '' as $$
declare
  target_room uuid;
begin
  select r.id into target_room
  from public.lili_study_rooms r
  where r.invite_code = upper(trim(code));

  if target_room is null then
    raise exception '没有找到这个自习室';
  end if;

  update public.lili_focus_presence fp
  set room_id = null,
      working = false,
      session_started_at = null,
      quick_status = null,
      quick_status_expires_at = null,
      updated_at = now(),
      last_seen = now()
  where fp.user_id = (select auth.uid());

  delete from public.lili_room_members m
  where m.user_id = (select auth.uid());

  insert into public.lili_room_members(room_id, user_id)
  values (target_room, (select auth.uid()));
  return target_room;
end;
$$;

create or replace function public.lili_room_dashboard(p_room_id uuid)
returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare result jsonb;
begin
  if not exists (
    select 1 from public.lili_room_members m
    where m.room_id = p_room_id and m.user_id = (select auth.uid())
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
      'shared_focus_seconds', coalesce((select t.cumulative_seconds from public.lili_room_focus_totals t where t.room_id = r.id), 0) + coalesce((
        select sum(greatest(0, floor(extract(epoch from (now() - f.session_started_at)))::bigint))
        from public.lili_room_members m join public.lili_focus_presence f on f.user_id = m.user_id
        where m.room_id = r.id and f.working and f.session_started_at is not null and f.last_seen > now() - interval '2 minutes'
      ), 0)
    ),
    'room_goal', coalesce((select jsonb_build_object(
      'title', g.title, 'target_seconds', g.target_seconds, 'due_at', g.due_at,
      'completed_seconds', coalesce((select t.cumulative_seconds from public.lili_room_focus_totals t where t.room_id = r.id), 0) + coalesce((
        select sum(greatest(0, floor(extract(epoch from (now() - f.session_started_at)))::bigint))
        from public.lili_room_members m join public.lili_focus_presence f on f.user_id = m.user_id
        where m.room_id = r.id and f.working and f.session_started_at is not null and f.last_seen > now() - interval '2 minutes'
      ), 0)
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

revoke execute on function public.lili_create_room(text) from public, anon;
revoke execute on function public.lili_join_room(text) from public, anon;
revoke execute on function public.lili_room_dashboard(uuid) from public, anon;
grant execute on function public.lili_create_room(text) to authenticated;
grant execute on function public.lili_join_room(text) to authenticated;
grant execute on function public.lili_room_dashboard(uuid) to authenticated;
