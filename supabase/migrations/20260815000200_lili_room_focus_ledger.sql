-- Replace the old room total accumulator with an idempotent focus-session ledger.
-- The previous trigger added elapsed time whenever a presence row was updated,
-- which could turn stale client timestamps and repeated room switches into
-- hundreds of hours. The ledger records each room focus session once.
create table if not exists public.lili_room_focus_sessions (
  id uuid primary key default gen_random_uuid(),
  room_id uuid not null references public.lili_study_rooms(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  started_at timestamptz not null,
  ended_at timestamptz,
  duration_seconds bigint not null default 0 check (duration_seconds >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (ended_at is null or ended_at >= started_at)
);

alter table public.lili_room_focus_sessions enable row level security;
drop policy if exists lili_room_focus_sessions_read on public.lili_room_focus_sessions;
create policy lili_room_focus_sessions_read on public.lili_room_focus_sessions
for select to authenticated using (
  exists (
    select 1 from public.lili_room_members m
    where m.room_id = public.lili_room_focus_sessions.room_id
      and m.user_id = (select auth.uid())
  )
);

create index if not exists lili_room_focus_sessions_room_idx
  on public.lili_room_focus_sessions(room_id, started_at);
create unique index if not exists lili_room_focus_sessions_one_active_user
  on public.lili_room_focus_sessions(user_id)
  where ended_at is null;

-- The server owns freshness and the start of a room focus session. A global
-- local session outside a room is left untouched.
create or replace function public.lili_touch_presence_server_timestamp()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  new.last_seen := now();
  new.updated_at := now();
  if new.room_id is not null and new.working then
    if tg_op = 'INSERT'
       or not old.working
       or old.room_id is distinct from new.room_id
       or not exists (
         select 1 from public.lili_room_focus_sessions s
         where s.user_id = new.user_id and s.ended_at is null
       ) then
      new.session_started_at := now();
    else
      new.session_started_at := old.session_started_at;
    end if;
  elsif new.room_id is not null and not new.working then
    new.session_started_at := null;
  end if;
  return new;
end;
$$;

-- Keep room events, but stop writing the unreliable legacy accumulator.
create or replace function public.lili_log_presence_event() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  if tg_op = 'INSERT' and new.room_id is not null then
    insert into public.lili_room_events(room_id, actor_id, kind)
    values(new.room_id, new.user_id, 'join');
  elsif tg_op = 'UPDATE' then
    if old.room_id is distinct from new.room_id then
      if old.room_id is not null then
        insert into public.lili_room_events(room_id, actor_id, kind)
        values(old.room_id, new.user_id, 'leave');
      end if;
      if new.room_id is not null then
        insert into public.lili_room_events(room_id, actor_id, kind)
        values(new.room_id, new.user_id, 'join');
      end if;
    elsif new.room_id is not null and old.working is distinct from new.working then
      insert into public.lili_room_events(room_id, actor_id, kind)
      values(new.room_id, new.user_id, case when new.working then 'focus_start' else 'focus_pause' end);
    end if;
  end if;
  return new;
end;
$$;

create or replace function public.lili_sync_room_focus_session() returns trigger
language plpgsql security definer set search_path = '' as $$
declare
  now_at timestamptz := now();
begin
  if tg_op = 'UPDATE'
     and old.working
     and old.room_id is not null
     and (
       not new.working
       or new.room_id is distinct from old.room_id
       or new.session_started_at is distinct from old.session_started_at
     ) then
    update public.lili_room_focus_sessions s
    set ended_at = now_at,
        duration_seconds = greatest(0, floor(extract(epoch from (now_at - s.started_at)))::bigint),
        updated_at = now_at
    where s.user_id = old.user_id and s.ended_at is null;
  end if;

  if new.working and new.room_id is not null then
    insert into public.lili_room_focus_sessions(room_id, user_id, started_at, created_at, updated_at)
    select new.room_id, new.user_id, coalesce(new.session_started_at, now_at), now_at, now_at
    where not exists (
      select 1 from public.lili_room_focus_sessions s
      where s.user_id = new.user_id and s.ended_at is null
    );
  end if;
  return new;
end;
$$;

drop trigger if exists lili_presence_focus_session on public.lili_focus_presence;
create trigger lili_presence_focus_session
after insert or update of room_id, working, session_started_at on public.lili_focus_presence
for each row execute function public.lili_sync_room_focus_session();

-- Existing room totals were already contaminated by the old accumulator. The
-- new ledger starts a clean, room-scoped total from this deployment onward.
update public.lili_room_focus_totals
set cumulative_seconds = 0, updated_at = now();

-- If someone is currently focusing in a room, begin a clean server-timed
-- session for them. This also prevents an old client timestamp from leaking
-- into the new room total.
update public.lili_focus_presence
set session_started_at = now()
where room_id is not null and working;

create or replace function public.lili_room_focus_seconds(p_room_id uuid)
returns bigint
language plpgsql stable security definer set search_path = '' as $$
declare total bigint;
begin
  if not exists (
    select 1 from public.lili_room_members m
    where m.room_id = p_room_id and m.user_id = (select auth.uid())
  ) then
    raise exception 'room_membership_required';
  end if;
  select coalesce(sum(
    case when s.ended_at is null
      then greatest(0, floor(extract(epoch from (now() - s.started_at)))::bigint)
      else s.duration_seconds
    end
  ), 0)::bigint
  into total
  from public.lili_room_focus_sessions s
  where s.room_id = p_room_id;
  return total;
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
    raise exception 'room_membership_required';
  end if;
  select jsonb_build_object(
    'room_schedule', coalesce((select jsonb_build_object('start_at', s.start_at, 'end_at', s.end_at, 'enabled', s.enabled)
      from public.lili_room_schedules s where s.room_id = p_room_id), '{}'::jsonb),
    'room_challenge', coalesce((select jsonb_build_object('title', c.title, 'target_seconds', c.target_seconds,
      'target_rounds', c.target_rounds, 'completed_rounds',
      (select count(*) from public.lili_room_events e where e.room_id = c.room_id and e.kind = 'focus_finish' and e.created_at >= c.created_at),
      'completed_seconds', public.lili_room_focus_seconds(c.room_id))
      from public.lili_room_challenges c where c.room_id = p_room_id), '{}'::jsonb)
  ) into result;
  return result;
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
    raise exception 'room_membership_required';
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
      'shared_focus_seconds', public.lili_room_focus_seconds(r.id)
    ),
    'room_goal', coalesce((select jsonb_build_object(
      'title', g.title, 'target_seconds', g.target_seconds, 'due_at', g.due_at,
      'completed_seconds', public.lili_room_focus_seconds(r.id)
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

revoke execute on function public.lili_touch_presence_server_timestamp() from public, anon, authenticated;
revoke execute on function public.lili_log_presence_event() from public, anon, authenticated;
revoke execute on function public.lili_sync_room_focus_session() from public, anon, authenticated;
revoke execute on function public.lili_room_focus_seconds(uuid) from public, anon;
revoke execute on function public.lili_room_room_rituals(uuid) from public, anon;
revoke execute on function public.lili_room_dashboard(uuid) from public, anon;
grant execute on function public.lili_room_focus_seconds(uuid) to authenticated;
grant execute on function public.lili_room_room_rituals(uuid) to authenticated;
grant execute on function public.lili_room_dashboard(uuid) to authenticated;
