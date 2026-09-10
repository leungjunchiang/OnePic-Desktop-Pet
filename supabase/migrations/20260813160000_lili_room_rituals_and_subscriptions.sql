-- Room rituals, shared challenges, quick phrases and optional buddy alerts.
-- All tables remain room/member scoped; private focus analytics stays local.

create table if not exists public.lili_room_schedules (
  room_id uuid primary key references public.lili_study_rooms(id) on delete cascade,
  start_at text not null default '21:00' check (start_at ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'),
  end_at text not null default '23:00' check (end_at ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'),
  enabled boolean not null default true,
  created_by uuid not null references auth.users(id) on delete cascade,
  updated_at timestamptz not null default now()
);

create table if not exists public.lili_room_challenges (
  room_id uuid primary key references public.lili_study_rooms(id) on delete cascade,
  title text not null default '一起完成' check (char_length(title) between 1 and 80),
  target_seconds integer not null default 14400 check (target_seconds between 60 and 604800),
  target_rounds integer not null default 3 check (target_rounds between 1 and 30),
  created_by uuid not null references auth.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.lili_buddy_subscriptions (
  subscriber_id uuid not null references auth.users(id) on delete cascade,
  buddy_id uuid not null references auth.users(id) on delete cascade,
  on_focus_start boolean not null default true,
  on_focus_end boolean not null default true,
  muted boolean not null default false,
  updated_at timestamptz not null default now(),
  primary key (subscriber_id, buddy_id),
  check (subscriber_id <> buddy_id)
);

alter table public.lili_room_schedules enable row level security;
alter table public.lili_room_challenges enable row level security;
alter table public.lili_buddy_subscriptions enable row level security;

drop policy if exists lili_room_schedules_read on public.lili_room_schedules;
create policy lili_room_schedules_read on public.lili_room_schedules
for select to authenticated using (
  exists (select 1 from public.lili_room_members m
    where m.room_id = public.lili_room_schedules.room_id and m.user_id = (select auth.uid()))
);
drop policy if exists lili_room_challenges_read on public.lili_room_challenges;
create policy lili_room_challenges_read on public.lili_room_challenges
for select to authenticated using (
  exists (select 1 from public.lili_room_members m
    where m.room_id = public.lili_room_challenges.room_id and m.user_id = (select auth.uid()))
);
drop policy if exists lili_buddy_subscriptions_owner on public.lili_buddy_subscriptions;
create policy lili_buddy_subscriptions_owner on public.lili_buddy_subscriptions
for select to authenticated using (subscriber_id = (select auth.uid()));

grant select on public.lili_room_schedules, public.lili_room_challenges to authenticated;
grant select, insert, update, delete on public.lili_buddy_subscriptions to authenticated;

alter table public.lili_room_events drop constraint if exists lili_room_events_kind_check;
alter table public.lili_room_events add constraint lili_room_events_kind_check check (
  kind in ('join','leave','focus_start','focus_pause','focus_finish','poke','cheer','drink',
    'phrase','goal_set','challenge_set','challenge_complete','schedule_set','schedule_start','schedule_end')
);

create or replace function public.lili_record_room_event(
  p_room_id uuid,
  p_kind text,
  p_target_id uuid default null,
  p_message text default ''
) returns uuid
language plpgsql security definer set search_path = '' as $$
declare result uuid;
begin
  if not exists (select 1 from public.lili_room_members m
    where m.room_id = p_room_id and m.user_id = (select auth.uid())) then
    raise exception '你不在这个自习室里';
  end if;
  if p_kind not in ('focus_finish','poke','cheer','drink','phrase','goal_set','challenge_complete','schedule_start','schedule_end') then
    raise exception '不支持的房间动态';
  end if;
  if p_kind in ('poke','cheer','drink','phrase') and (p_target_id is null or not exists(
    select 1 from public.lili_room_members m
    where m.room_id = p_room_id and m.user_id = p_target_id
  )) then
    raise exception '互动对象不在当前房间';
  end if;
  if exists (select 1 from public.lili_room_events e
    where e.room_id = p_room_id and e.actor_id = (select auth.uid()) and e.kind = p_kind
      and e.target_id is not distinct from p_target_id
      and e.created_at > now() - interval '15 seconds') then
    raise exception '互动太频繁，请稍后再试';
  end if;
  insert into public.lili_room_events(room_id, actor_id, target_id, kind, message)
  values (p_room_id, (select auth.uid()), p_target_id, p_kind, left(coalesce(p_message, ''), 240))
  returning id into result;
  return result;
end;
$$;

create or replace function public.lili_set_room_schedule(
  room_id uuid, start_at text, end_at text, enabled boolean default true
) returns void language plpgsql security definer set search_path = '' as $$
begin
  if not exists (select 1 from public.lili_room_members m
    where m.room_id = lili_set_room_schedule.room_id and m.user_id = (select auth.uid())) then
    raise exception '你不在这个自习室里';
  end if;
  if start_at !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$' or end_at !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$' then
    raise exception '时间必须是 HH:MM';
  end if;
  insert into public.lili_room_schedules(room_id, start_at, end_at, enabled, created_by, updated_at)
  values (lili_set_room_schedule.room_id, start_at, end_at, enabled, (select auth.uid()), now())
  on conflict (room_id) do update set start_at = excluded.start_at, end_at = excluded.end_at,
    enabled = excluded.enabled, created_by = excluded.created_by, updated_at = now();
  perform public.lili_record_room_event(lili_set_room_schedule.room_id, 'goal_set', null,
    left('一起开工 ' || start_at || ' · 一起收工 ' || end_at, 240));
end;
$$;

create or replace function public.lili_set_room_challenge(
  room_id uuid, title text, target_seconds integer, target_rounds integer
) returns void language plpgsql security definer set search_path = '' as $$
begin
  if not exists (select 1 from public.lili_room_members m
    where m.room_id = lili_set_room_challenge.room_id and m.user_id = (select auth.uid())) then
    raise exception '你不在这个自习室里';
  end if;
  insert into public.lili_room_challenges(room_id, title, target_seconds, target_rounds, created_by, created_at, updated_at)
  values (lili_set_room_challenge.room_id, left(trim(title), 80), greatest(60, least(target_seconds, 604800)),
    greatest(1, least(target_rounds, 30)), (select auth.uid()), now(), now())
  on conflict (room_id) do update set title = excluded.title, target_seconds = excluded.target_seconds,
    target_rounds = excluded.target_rounds, created_by = excluded.created_by, created_at = now(), updated_at = now();
  perform public.lili_record_room_event(lili_set_room_challenge.room_id, 'goal_set', null,
    left('共同挑战：' || trim(title), 240));
end;
$$;

create or replace function public.lili_set_buddy_subscription(
  buddy_id uuid, on_focus_start boolean, on_focus_end boolean, muted boolean default false
) returns void language plpgsql security definer set search_path = '' as $$
begin
  if buddy_id = (select auth.uid()) then raise exception '不能订阅自己'; end if;
  if not exists (select 1 from public.lili_buddy_links b
    where ((b.requester_id = (select auth.uid()) and b.addressee_id = lili_set_buddy_subscription.buddy_id)
      or (b.addressee_id = (select auth.uid()) and b.requester_id = lili_set_buddy_subscription.buddy_id))
      and b.status = 'accepted') then
    raise exception '只能订阅已确认的搭子';
  end if;
  insert into public.lili_buddy_subscriptions(subscriber_id, buddy_id, on_focus_start, on_focus_end, muted, updated_at)
  values ((select auth.uid()), lili_set_buddy_subscription.buddy_id, on_focus_start, on_focus_end, muted, now())
  on conflict (subscriber_id, buddy_id) do update set on_focus_start = excluded.on_focus_start,
    on_focus_end = excluded.on_focus_end, muted = excluded.muted, updated_at = now();
end;
$$;

create or replace function public.lili_room_room_rituals(room_id uuid) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare result jsonb;
begin
  if not exists (select 1 from public.lili_room_members m
    where m.room_id = lili_room_room_rituals.room_id and m.user_id = (select auth.uid())) then
    raise exception '你不在这个自习室里';
  end if;
  select jsonb_build_object(
    'room_schedule', coalesce((select jsonb_build_object('start_at', s.start_at, 'end_at', s.end_at, 'enabled', s.enabled)
      from public.lili_room_schedules s where s.room_id = lili_room_room_rituals.room_id), '{}'::jsonb),
    'room_challenge', coalesce((select jsonb_build_object('title', c.title, 'target_seconds', c.target_seconds,
      'target_rounds', c.target_rounds, 'completed_rounds',
      (select count(*) from public.lili_room_events e where e.room_id = c.room_id and e.kind = 'focus_finish' and e.created_at >= c.created_at),
      'completed_seconds', coalesce((select t.cumulative_seconds from public.lili_room_focus_totals t where t.room_id = c.room_id), 0))
      from public.lili_room_challenges c where c.room_id = lili_room_room_rituals.room_id), '{}'::jsonb)
  ) into result;
  return result;
end;
$$;

revoke execute on function public.lili_record_room_event(uuid, text, uuid, text) from public, anon;
revoke execute on function public.lili_set_room_schedule(uuid, text, text, boolean) from public, anon;
revoke execute on function public.lili_set_room_challenge(uuid, text, integer, integer) from public, anon;
revoke execute on function public.lili_set_buddy_subscription(uuid, boolean, boolean, boolean) from public, anon;
revoke execute on function public.lili_room_room_rituals(uuid) from public, anon;
grant execute on function public.lili_record_room_event(uuid, text, uuid, text) to authenticated;
grant execute on function public.lili_set_room_schedule(uuid, text, text, boolean) to authenticated;
grant execute on function public.lili_set_room_challenge(uuid, text, integer, integer) to authenticated;
grant execute on function public.lili_set_buddy_subscription(uuid, boolean, boolean, boolean) to authenticated;
grant execute on function public.lili_room_room_rituals(uuid) to authenticated;
