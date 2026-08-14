-- Split the immutable pet identity from the user-owned social nickname and
-- make room RPC argument names unambiguous.  Existing nickname values are
-- preserved; the legacy default is rendered as the neutral owner “搭子”.

alter table public.lili_profiles
  add column if not exists owner_nickname text;

update public.lili_profiles
set owner_nickname = nullif(left(trim(nickname), 24), '')
where owner_nickname is null
  and nickname is not null
  and nickname <> '六毛搭子';

create or replace function public.lili_owner_nickname(p_user_id uuid)
returns text
language sql stable security definer set search_path = '' as $$
  select coalesce(
    nullif(nullif(left(trim(p.owner_nickname), 24), '六毛'), '六毛搭子'),
    nullif(left(trim(p.nickname), 24), '六毛搭子'),
    '搭子'
  )
  from public.lili_profiles p
  where p.user_id = p_user_id;
$$;

create or replace function public.lili_new_profile() returns trigger
language plpgsql security definer set search_path = '' as $$
declare owner text;
begin
  owner := nullif(left(trim(new.raw_user_meta_data ->> 'nickname'), 24), '');
  insert into public.lili_profiles(user_id, nickname, owner_nickname, invite_code)
  values (new.id, coalesce(owner, '搭子'), owner, public.lili_invite_code());
  return new;
end;
$$;

alter table public.lili_focus_presence
  add column if not exists quick_status text,
  add column if not exists quick_status_expires_at timestamptz;

alter table public.lili_focus_presence
  drop constraint if exists lili_focus_presence_quick_status_check;
alter table public.lili_focus_presence
  add constraint lili_focus_presence_quick_status_check
  check (quick_status is null or char_length(quick_status) <= 40);

-- All room RPCs are recreated with p_ arguments.  The client sends these
-- names explicitly, so Postgres can never confuse an input parameter with a
-- table column in a WHERE or ON CONFLICT clause.
drop function if exists public.lili_send_interaction(uuid, text, uuid);
drop function if exists public.lili_set_room_goal(uuid, text, integer, timestamptz);
drop function if exists public.lili_leave_room(uuid);
drop function if exists public.lili_room_dashboard(uuid);
drop function if exists public.lili_set_room_schedule(uuid, text, text, boolean);
drop function if exists public.lili_set_room_challenge(uuid, text, integer, integer);
drop function if exists public.lili_set_buddy_subscription(uuid, boolean, boolean, boolean);
drop function if exists public.lili_room_room_rituals(uuid);

create or replace function public.lili_send_interaction(
  p_target uuid,
  p_kind text,
  p_room_id uuid default null
) returns uuid
language plpgsql security definer set search_path = '' as $$
begin
  if p_room_id is null then
    raise exception '请先加入一个自习室';
  end if;
  return public.lili_record_room_event(p_room_id, p_kind, p_target, '');
end;
$$;

create or replace function public.lili_set_room_goal(
  p_room_id uuid,
  p_title text,
  p_target_seconds integer,
  p_due_at timestamptz default null
) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not exists (
    select 1 from public.lili_room_members m
    where m.room_id = p_room_id and m.user_id = (select auth.uid())
  ) then
    raise exception '你不在这个自习室里';
  end if;
  insert into public.lili_room_goals(room_id, title, target_seconds, due_at, created_by, updated_at)
  values (p_room_id, left(trim(p_title), 80), greatest(60, least(p_target_seconds, 604800)), p_due_at, (select auth.uid()), now())
  on conflict on constraint lili_room_goals_pkey do update
    set title = excluded.title,
        target_seconds = excluded.target_seconds,
        due_at = excluded.due_at,
        created_by = excluded.created_by,
        updated_at = now();
  perform public.lili_record_room_event(p_room_id, 'goal_set', null, left(trim(p_title), 80));
end;
$$;

create or replace function public.lili_leave_room(p_room_id uuid) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not exists (
    select 1 from public.lili_room_members m
    where m.room_id = p_room_id and m.user_id = (select auth.uid())
  ) then
    raise exception '你不在这个自习室里';
  end if;
  update public.lili_focus_presence fp
  set room_id = null, updated_at = now(), last_seen = now(),
      quick_status = null, quick_status_expires_at = null
  where fp.user_id = (select auth.uid()) and fp.room_id = p_room_id;
  delete from public.lili_room_members m
  where m.room_id = p_room_id and m.user_id = (select auth.uid());
end;
$$;

create or replace function public.lili_set_room_schedule(
  p_room_id uuid, p_start_at text, p_end_at text, p_enabled boolean default true
) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not exists (
    select 1 from public.lili_room_members m
    where m.room_id = p_room_id and m.user_id = (select auth.uid())
  ) then
    raise exception '你不在这个自习室里';
  end if;
  if p_start_at !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
     or p_end_at !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$' then
    raise exception '时间必须是 HH:MM';
  end if;
  insert into public.lili_room_schedules(room_id, start_at, end_at, enabled, created_by, updated_at)
  values (p_room_id, p_start_at, p_end_at, p_enabled, (select auth.uid()), now())
  on conflict on constraint lili_room_schedules_pkey do update
    set start_at = excluded.start_at, end_at = excluded.end_at,
        enabled = excluded.enabled, created_by = excluded.created_by, updated_at = now();
  perform public.lili_record_room_event(p_room_id, 'goal_set', null,
    left('一起开工 ' || p_start_at || ' · 一起收工 ' || p_end_at, 240));
end;
$$;

create or replace function public.lili_set_room_challenge(
  p_room_id uuid, p_title text, p_target_seconds integer, p_target_rounds integer
) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not exists (
    select 1 from public.lili_room_members m
    where m.room_id = p_room_id and m.user_id = (select auth.uid())
  ) then
    raise exception '你不在这个自习室里';
  end if;
  insert into public.lili_room_challenges(room_id, title, target_seconds, target_rounds, created_by, created_at, updated_at)
  values (p_room_id, left(trim(p_title), 80), greatest(60, least(p_target_seconds, 604800)),
    greatest(1, least(p_target_rounds, 30)), (select auth.uid()), now(), now())
  on conflict on constraint lili_room_challenges_pkey do update
    set title = excluded.title, target_seconds = excluded.target_seconds,
        target_rounds = excluded.target_rounds, created_by = excluded.created_by,
        created_at = now(), updated_at = now();
  perform public.lili_record_room_event(p_room_id, 'goal_set', null,
    left('共同挑战：' || trim(p_title), 240));
end;
$$;

create or replace function public.lili_set_buddy_subscription(
  p_buddy_id uuid, p_on_focus_start boolean, p_on_focus_end boolean, p_muted boolean default false
) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if p_buddy_id = (select auth.uid()) then raise exception '不能订阅自己'; end if;
  if not exists (
    select 1 from public.lili_buddy_links b
    where ((b.requester_id = (select auth.uid()) and b.addressee_id = p_buddy_id)
       or (b.addressee_id = (select auth.uid()) and b.requester_id = p_buddy_id))
      and b.status = 'accepted'
  ) then
    raise exception '只能订阅已确认的搭子';
  end if;
  insert into public.lili_buddy_subscriptions(subscriber_id, buddy_id, on_focus_start, on_focus_end, muted, updated_at)
  values ((select auth.uid()), p_buddy_id, p_on_focus_start, p_on_focus_end, p_muted, now())
  on conflict on constraint lili_buddy_subscriptions_pkey do update
    set on_focus_start = excluded.on_focus_start, on_focus_end = excluded.on_focus_end,
        muted = excluded.muted, updated_at = now();
end;
$$;

create or replace function public.lili_room_room_rituals(p_room_id uuid) returns jsonb
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
    'room_schedule', coalesce((select jsonb_build_object('start_at', s.start_at, 'end_at', s.end_at, 'enabled', s.enabled)
      from public.lili_room_schedules s where s.room_id = p_room_id), '{}'::jsonb),
    'room_challenge', coalesce((select jsonb_build_object('title', c.title, 'target_seconds', c.target_seconds,
      'target_rounds', c.target_rounds, 'completed_rounds',
      (select count(*) from public.lili_room_events e where e.room_id = c.room_id and e.kind = 'focus_finish' and e.created_at >= c.created_at),
      'completed_seconds', coalesce((select t.cumulative_seconds from public.lili_room_focus_totals t where t.room_id = c.room_id), 0))
      from public.lili_room_challenges c where c.room_id = p_room_id), '{}'::jsonb)
  ) into result;
  return result;
end;
$$;

create or replace function public.lili_room_dashboard(p_room_id uuid) returns jsonb
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
      where m.room_id = r.id and p.visibility = 'friends'
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
      'id', e.id, 'kind', e.kind, 'message', e.message, 'created_at', e.created_at,
      'nickname', coalesce(public.lili_owner_nickname(e.actor_id), '搭子'),
      'target_nickname', public.lili_owner_nickname(e.target_id)
    ) order by e.created_at desc) from public.lili_room_events e where e.room_id = r.id), '[]'::jsonb)
  ) into result
  from public.lili_study_rooms r
  where r.id = p_room_id;
  return jsonb_build_object('current_room', result);
end;
$$;

revoke execute on function public.lili_owner_nickname(uuid) from public, anon;
grant execute on function public.lili_owner_nickname(uuid) to authenticated;
revoke execute on function public.lili_send_interaction(uuid, text, uuid) from public, anon;
revoke execute on function public.lili_set_room_goal(uuid, text, integer, timestamptz) from public, anon;
revoke execute on function public.lili_leave_room(uuid) from public, anon;
revoke execute on function public.lili_room_dashboard(uuid) from public, anon;
revoke execute on function public.lili_set_room_schedule(uuid, text, text, boolean) from public, anon;
revoke execute on function public.lili_set_room_challenge(uuid, text, integer, integer) from public, anon;
revoke execute on function public.lili_set_buddy_subscription(uuid, boolean, boolean, boolean) from public, anon;
revoke execute on function public.lili_room_room_rituals(uuid) from public, anon;
grant execute on function public.lili_send_interaction(uuid, text, uuid) to authenticated;
grant execute on function public.lili_set_room_goal(uuid, text, integer, timestamptz) to authenticated;
grant execute on function public.lili_leave_room(uuid) to authenticated;
grant execute on function public.lili_room_dashboard(uuid) to authenticated;
grant execute on function public.lili_set_room_schedule(uuid, text, text, boolean) to authenticated;
grant execute on function public.lili_set_room_challenge(uuid, text, integer, integer) to authenticated;
grant execute on function public.lili_set_buddy_subscription(uuid, boolean, boolean, boolean) to authenticated;
grant execute on function public.lili_room_room_rituals(uuid) to authenticated;
