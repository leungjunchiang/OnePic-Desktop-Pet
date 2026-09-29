-- Durable buddy reminder subscriptions and explicit work transitions.
-- Heartbeat/presence never writes these tables or creates reminder events.

create table if not exists public.lili_work_status (
  user_id uuid primary key references auth.users(id) on delete cascade,
  work_status text not null default 'off_work'
    check (work_status in ('focused', 'resting', 'off_work')),
  session_id text not null default '',
  updated_at timestamptz not null default '-infinity'::timestamptz
);

create table if not exists public.lili_work_events (
  id uuid primary key,
  actor_id uuid not null references auth.users(id) on delete cascade,
  event_type text not null check (event_type in ('start_work', 'finish_work')),
  session_id text not null,
  occurred_at timestamptz not null,
  created_at timestamptz not null default now(),
  notify_eligible boolean not null default true,
  unique (actor_id, event_type, session_id)
);

create index if not exists lili_work_events_actor_time_idx
  on public.lili_work_events(actor_id, event_type, occurred_at desc);
create index if not exists lili_work_events_recent_idx
  on public.lili_work_events(created_at desc) where notify_eligible;

alter table public.lili_work_status enable row level security;
alter table public.lili_work_events enable row level security;
revoke all on public.lili_work_status, public.lili_work_events from public, anon, authenticated;

-- Each new desktop action changes one subscription field. A stale device
-- cannot replay its old value for the other event type or the mute flag.
create or replace function public.lili_set_buddy_reminder(
  p_buddy_id uuid, p_event_type text, p_enabled boolean
) returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  me uuid := (select auth.uid());
  row_value public.lili_buddy_subscriptions;
begin
  if me is null then raise exception '请先登录'; end if;
  if p_buddy_id is null or p_buddy_id = me or p_event_type is null or
     p_event_type not in ('start_work', 'finish_work') or p_enabled is null then
    raise exception '无效的提醒订阅';
  end if;
  if not public.lili_are_buddies(me, p_buddy_id) then
    raise exception '只能订阅已确认的搭子';
  end if;
  insert into public.lili_buddy_subscriptions(
    subscriber_id, buddy_id, on_focus_start, on_focus_end, muted, updated_at
  ) values (
    me, p_buddy_id, p_event_type = 'start_work' and p_enabled,
    p_event_type = 'finish_work' and p_enabled, false, now()
  )
  on conflict (subscriber_id, buddy_id) do update set
    on_focus_start = case when p_event_type = 'start_work'
      then p_enabled else public.lili_buddy_subscriptions.on_focus_start end,
    on_focus_end = case when p_event_type = 'finish_work'
      then p_enabled else public.lili_buddy_subscriptions.on_focus_end end,
    updated_at = now()
  returning * into row_value;
  return jsonb_build_object('buddy_id', row_value.buddy_id,
    'on_focus_start', row_value.on_focus_start,
    'on_focus_end', row_value.on_focus_end, 'muted', row_value.muted,
    'updated_at', row_value.updated_at);
end;
$$;

create or replace function public.lili_set_buddy_reminder_mute(
  p_buddy_id uuid, p_muted boolean
) returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  me uuid := (select auth.uid());
  row_value public.lili_buddy_subscriptions;
begin
  if me is null then raise exception '请先登录'; end if;
  if p_buddy_id is null or p_buddy_id = me or p_muted is null or
     not public.lili_are_buddies(me, p_buddy_id) then
    raise exception '无效的搭子免打扰设置';
  end if;
  insert into public.lili_buddy_subscriptions(
    subscriber_id, buddy_id, on_focus_start, on_focus_end, muted, updated_at
  ) values (me, p_buddy_id, false, false, p_muted, now())
  on conflict (subscriber_id, buddy_id) do update set
    muted = p_muted, updated_at = now()
  returning * into row_value;
  return jsonb_build_object('buddy_id', row_value.buddy_id,
    'on_focus_start', row_value.on_focus_start,
    'on_focus_end', row_value.on_focus_end, 'muted', row_value.muted,
    'updated_at', row_value.updated_at);
end;
$$;

-- Mixed-version clients still call the old whole-row RPC. Its inputs cannot
-- distinguish an intentional unsubscribe from a stale false snapshot, so it
-- may enable subscriptions but never clear an existing one or change mute.
-- Upgrade to the field-specific RPCs above to cancel or change mute. This
-- protects new settings while an older computer remains signed in.
create or replace function public.lili_set_buddy_subscription(
  p_buddy_id uuid, p_on_focus_start boolean, p_on_focus_end boolean,
  p_muted boolean default false
) returns void language plpgsql security definer set search_path = '' as $$
declare me uuid := (select auth.uid());
begin
  if me is null then raise exception '请先登录'; end if;
  if p_buddy_id is null or p_buddy_id = me or
     not public.lili_are_buddies(me, p_buddy_id) then
    raise exception '只能订阅已确认的搭子';
  end if;
  insert into public.lili_buddy_subscriptions(
    subscriber_id, buddy_id, on_focus_start, on_focus_end, muted, updated_at
  ) values (me, p_buddy_id, coalesce(p_on_focus_start, false),
    coalesce(p_on_focus_end, false), false, now())
  on conflict (subscriber_id, buddy_id) do update set
    on_focus_start = public.lili_buddy_subscriptions.on_focus_start or excluded.on_focus_start,
    on_focus_end = public.lili_buddy_subscriptions.on_focus_end or excluded.on_focus_end,
    muted = public.lili_buddy_subscriptions.muted,
    updated_at = now();
end;
$$;

-- Only explicit local FocusSession actions call this RPC. Replayed requests
-- are idempotent, and older out-of-order events cannot rewind work status.
create or replace function public.lili_publish_work_transition(
  p_event_id uuid, p_work_status text, p_session_id text,
  p_occurred_at timestamptz, p_silent boolean default false
) returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  me uuid := (select auth.uid());
  old_status text;
  old_session_id text;
  old_updated_at timestamptz;
  event_time timestamptz;
  new_event_type text;
  eligible boolean;
begin
  if me is null then raise exception '请先登录'; end if;
  if p_event_id is null or p_work_status is null
     or p_work_status not in ('focused', 'resting', 'off_work')
     or p_session_id is null or length(p_session_id) not between 1 and 80
     or p_occurred_at is null then
    raise exception '无效的工作状态事件';
  end if;
  event_time := least(p_occurred_at, now());
  insert into public.lili_work_status(user_id) values (me)
    on conflict (user_id) do nothing;
  select s.work_status, s.session_id, s.updated_at
    into old_status, old_session_id, old_updated_at
    from public.lili_work_status s where s.user_id = me for update;
  if event_time <= old_updated_at then
    return jsonb_build_object('accepted', true, 'stale', true,
      'work_status', old_status);
  end if;
  if p_work_status = 'focused' and (
    old_status in ('resting', 'off_work')
    or (old_status = 'focused' and old_session_id <> p_session_id)
  ) then
    new_event_type := 'start_work';
  elsif p_work_status = 'off_work' and old_status in ('focused', 'resting') then
    new_event_type := 'finish_work';
  end if;
  update public.lili_work_status set work_status = p_work_status,
    session_id = p_session_id, updated_at = event_time where user_id = me;
  if new_event_type is not null then
    select not exists (
      select 1 from public.lili_work_events e
      where e.actor_id = me and e.event_type = new_event_type
        and e.notify_eligible
        and e.occurred_at > event_time - interval '5 minutes'
        and e.occurred_at <= event_time
    ) into eligible;
    insert into public.lili_work_events(
      id, actor_id, event_type, session_id, occurred_at, notify_eligible
    ) values (p_event_id, me, new_event_type, p_session_id,
      event_time, eligible and not coalesce(p_silent, false))
    on conflict do nothing;
  end if;
  return jsonb_build_object('accepted', true, 'stale', false,
    'work_status', p_work_status, 'event_type', new_event_type);
end;
$$;

-- A single read supplies authoritative per-event flags and recent confirmed
-- work events. Muting filters presentation, never changes enabled flags.
create or replace function public.lili_buddy_reminder_snapshot()
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare
  me uuid := (select auth.uid());
  subscriptions jsonb;
  events jsonb;
begin
  if me is null then raise exception '请先登录'; end if;
  select coalesce(jsonb_agg(jsonb_build_object(
    'buddy_id', s.buddy_id, 'on_focus_start', s.on_focus_start,
    'on_focus_end', s.on_focus_end, 'muted', s.muted,
    'updated_at', s.updated_at
  )), '[]'::jsonb) into subscriptions
  from public.lili_buddy_subscriptions s
  where s.subscriber_id = me and public.lili_are_buddies(me, s.buddy_id);

  select coalesce(jsonb_agg(jsonb_build_object(
    'id', e.id, 'target_user_id', e.actor_id,
    'event_type', e.event_type, 'occurred_at', e.occurred_at,
    'nickname', public.lili_owner_nickname(e.actor_id)
  ) order by e.occurred_at), '[]'::jsonb) into events
  from (
    select event_row.* from public.lili_work_events event_row
    join public.lili_buddy_subscriptions s
      on s.subscriber_id = me and s.buddy_id = event_row.actor_id
    where event_row.notify_eligible
      and event_row.created_at > now() - interval '3 minutes'
      and event_row.created_at >= s.updated_at
      and not s.muted
      and ((event_row.event_type = 'start_work' and s.on_focus_start)
        or (event_row.event_type = 'finish_work' and s.on_focus_end))
      and public.lili_are_buddies(me, event_row.actor_id)
    order by event_row.occurred_at desc limit 30
  ) e;
  return jsonb_build_object('subscriptions', subscriptions, 'events', events,
    'server_timestamp', now());
end;
$$;

revoke execute on function public.lili_set_buddy_reminder(uuid,text,boolean) from public, anon;
revoke execute on function public.lili_set_buddy_reminder_mute(uuid,boolean) from public, anon;
revoke execute on function public.lili_set_buddy_subscription(uuid,boolean,boolean,boolean) from public, anon;
revoke execute on function public.lili_publish_work_transition(uuid,text,text,timestamptz,boolean) from public, anon;
revoke execute on function public.lili_buddy_reminder_snapshot() from public, anon;
grant execute on function public.lili_set_buddy_reminder(uuid,text,boolean) to authenticated;
grant execute on function public.lili_set_buddy_reminder_mute(uuid,boolean) to authenticated;
grant execute on function public.lili_set_buddy_subscription(uuid,boolean,boolean,boolean) to authenticated;
grant execute on function public.lili_publish_work_transition(uuid,text,text,timestamptz,boolean) to authenticated;
grant execute on function public.lili_buddy_reminder_snapshot() to authenticated;
