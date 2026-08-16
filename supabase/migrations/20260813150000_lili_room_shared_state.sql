-- Shared study-room state: room-scoped members, goals, timeline events and
-- interaction rate limiting.  The desktop client never receives task text,
-- window titles or keyboard/mouse data.

create table if not exists public.lili_room_goals (
  room_id uuid primary key references public.lili_study_rooms(id) on delete cascade,
  title text not null default '一起专注' check (char_length(title) between 1 and 80),
  target_seconds integer not null default 3000 check (target_seconds between 60 and 604800),
  due_at timestamptz,
  created_by uuid not null references auth.users(id) on delete cascade,
  updated_at timestamptz not null default now()
);

create table if not exists public.lili_room_events (
  id uuid primary key default gen_random_uuid(),
  room_id uuid not null references public.lili_study_rooms(id) on delete cascade,
  actor_id uuid not null references auth.users(id) on delete cascade,
  target_id uuid references auth.users(id) on delete set null,
  kind text not null check (kind in ('join','leave','focus_start','focus_pause','focus_finish','poke','cheer','drink','goal_set')),
  message text not null default '' check (char_length(message) <= 240),
  created_at timestamptz not null default now()
);
create index if not exists lili_room_events_room_time_idx
  on public.lili_room_events(room_id, created_at desc);

alter table public.lili_room_goals enable row level security;
alter table public.lili_room_events enable row level security;

drop policy if exists lili_room_goals_read on public.lili_room_goals;
create policy lili_room_goals_read on public.lili_room_goals for select to authenticated using (
  exists(select 1 from public.lili_room_members m where m.room_id=lili_room_goals.room_id and m.user_id=auth.uid())
);
drop policy if exists lili_room_events_read on public.lili_room_events;
create policy lili_room_events_read on public.lili_room_events for select to authenticated using (
  exists(select 1 from public.lili_room_members m where m.room_id=lili_room_events.room_id and m.user_id=auth.uid())
);

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
drop trigger if exists lili_presence_room_event on public.lili_focus_presence;
create trigger lili_presence_room_event
after insert or update of room_id, working on public.lili_focus_presence
for each row execute function public.lili_log_presence_event();
revoke execute on function public.lili_log_presence_event() from public, anon, authenticated;

create or replace function public.lili_record_room_event(
  p_room_id uuid,
  p_kind text,
  p_target_id uuid default null,
  p_message text default ''
) returns uuid
language plpgsql security definer set search_path = '' as $$
declare result uuid;
begin
  if not exists(select 1 from public.lili_room_members m where m.room_id=p_room_id and m.user_id=auth.uid()) then
    raise exception '你不在这个自习室里';
  end if;
  if p_kind not in ('focus_finish','poke','cheer','drink','goal_set') then
    raise exception '不支持的房间动态';
  end if;
  if p_kind in ('poke','cheer','drink') and (p_target_id is null or not exists(
    select 1 from public.lili_room_members m where m.room_id=p_room_id and m.user_id=p_target_id
  )) then
    raise exception '互动对象不在当前房间';
  end if;
  if exists(select 1 from public.lili_room_events e
    where e.room_id=p_room_id and e.actor_id=auth.uid() and e.kind=p_kind
      and e.target_id is not distinct from p_target_id
      and e.created_at > now() - interval '15 seconds') then
    raise exception '互动太频繁，请稍后再试';
  end if;
  insert into public.lili_room_events(room_id, actor_id, target_id, kind, message)
  values(p_room_id, auth.uid(), p_target_id, p_kind, left(coalesce(p_message,''),240))
  returning id into result;
  return result;
end;
$$;

create or replace function public.lili_send_interaction(
  target uuid,
  kind text,
  room_id uuid default null
) returns uuid
language plpgsql security definer set search_path = '' as $$
begin
  if room_id is null then raise exception '请先加入一个自习室'; end if;
  return public.lili_record_room_event(room_id, kind, target, '');
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
  if not exists(select 1 from public.lili_room_members m where m.room_id=room_id and m.user_id=auth.uid()) then
    raise exception '你不在这个自习室里';
  end if;
  insert into public.lili_room_goals(room_id, title, target_seconds, due_at, created_by, updated_at)
  values(room_id, left(trim(title),80), greatest(60,least(target_seconds,604800)), due_at, auth.uid(), now())
  on conflict(room_id) do update set title=excluded.title, target_seconds=excluded.target_seconds,
    due_at=excluded.due_at, created_by=excluded.created_by, updated_at=now();
  perform public.lili_record_room_event(room_id, 'goal_set', null, left(trim(title),80));
end;
$$;

create or replace function public.lili_leave_room(room_id uuid) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not exists(select 1 from public.lili_room_members m where m.room_id=room_id and m.user_id=auth.uid()) then
    raise exception '你不在这个自习室里';
  end if;
  update public.lili_focus_presence set room_id=null, updated_at=now(), last_seen=now()
    where user_id=auth.uid() and lili_focus_presence.room_id=room_id;
  delete from public.lili_room_members m where m.room_id=room_id and m.user_id=auth.uid();
end;
$$;

create or replace function public.lili_room_dashboard(room_id uuid) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare result jsonb;
begin
  if not exists(select 1 from public.lili_room_members m where m.room_id=room_id and m.user_id=auth.uid()) then
    raise exception '你不在这个自习室里';
  end if;
  select jsonb_build_object(
    'id', r.id,
    'name', r.name,
    'invite_code', r.invite_code,
    'room_people', coalesce((select jsonb_agg(jsonb_build_object(
      'user_id',p.user_id,'nickname',p.nickname,'outfit_key',coalesce(f.outfit_key,p.outfit_key),
      'working',case when f.working and f.last_seen>now()-interval '2 minutes' then true else false end,
      'status',case when f.last_seen is null or f.last_seen<=now()-interval '2 minutes' then 'offline' when f.working then 'focus' else 'rest' end,
      'session_started_at',f.session_started_at,
      'session_seconds',case when f.working and f.session_started_at is not null then greatest(0,floor(extract(epoch from (now()-f.session_started_at)))::int) else 0 end,
      'today_seconds',case when p.show_exact_time then coalesce(f.today_seconds,0) else null end,
      'online',coalesce(f.last_seen>now()-interval '2 minutes',false),
      'is_self',p.user_id=auth.uid()
    ) order by p.nickname) from public.lili_room_members m
      join public.lili_profiles p on p.user_id=m.user_id
      left join public.lili_focus_presence f on f.user_id=p.user_id
      where m.room_id=r.id and p.visibility='friends'),'[]'::jsonb),
    'room_summary', jsonb_build_object(
      'member_count',(select count(*) from public.lili_room_members m where m.room_id=r.id),
      'focus_count',(select count(*) from public.lili_room_members m join public.lili_focus_presence f on f.user_id=m.user_id
        where m.room_id=r.id and f.working and f.last_seen>now()-interval '2 minutes'),
      'shared_focus_seconds',coalesce((select sum(greatest(0,floor(extract(epoch from (now()-f.session_started_at)))::int))
        from public.lili_room_members m join public.lili_focus_presence f on f.user_id=m.user_id
        where m.room_id=r.id and f.working and f.session_started_at is not null and f.last_seen>now()-interval '2 minutes'),0)
    ),
    'room_goal', coalesce((select jsonb_build_object('title',g.title,'target_seconds',g.target_seconds,'due_at',g.due_at,
      'completed_seconds',coalesce((select sum(greatest(0,floor(extract(epoch from (now()-f.session_started_at)))::int))
        from public.lili_room_members m join public.lili_focus_presence f on f.user_id=m.user_id
        where m.room_id=r.id and f.working and f.session_started_at is not null and f.last_seen>now()-interval '2 minutes'),0))
      from public.lili_room_goals g where g.room_id=r.id),'{}'::jsonb),
    'room_activity', coalesce((select jsonb_agg(jsonb_build_object(
      'id',e.id,'kind',e.kind,'message',e.message,'created_at',e.created_at,
      'nickname',coalesce(ap.nickname,'搭子'),'target_nickname',tp.nickname
    ) order by e.created_at desc) from public.lili_room_events e
      join public.lili_profiles ap on ap.user_id=e.actor_id
      left join public.lili_profiles tp on tp.user_id=e.target_id
      where e.room_id=r.id),'[]'::jsonb)
  ) into result from public.lili_study_rooms r where r.id=room_id;
  return jsonb_build_object('current_room', result);
end;
$$;

revoke execute on function public.lili_record_room_event(uuid,text,uuid,text) from public, anon;
revoke execute on function public.lili_send_interaction(uuid,text,uuid) from public, anon;
revoke execute on function public.lili_set_room_goal(uuid,text,integer,timestamptz) from public, anon;
revoke execute on function public.lili_leave_room(uuid) from public, anon;
revoke execute on function public.lili_room_dashboard(uuid) from public, anon;
grant execute on function public.lili_record_room_event(uuid,text,uuid,text) to authenticated;
grant execute on function public.lili_send_interaction(uuid,text,uuid) to authenticated;
grant execute on function public.lili_set_room_goal(uuid,text,integer,timestamptz) to authenticated;
grant execute on function public.lili_leave_room(uuid) to authenticated;
grant execute on function public.lili_room_dashboard(uuid) to authenticated;

