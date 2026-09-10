-- Persistent buddy reactions: a redeemable "caught slacking" taunt and a
-- one-hour encouragement while the receiver is actively working.
--
-- Taunts never add punishment time when several buddies send them. Each
-- active taunt is cleared after 20 continuous working minutes; a pause resets
-- that continuous-work counter. Encouragement ends as soon as the receiver's
-- fresh working presence becomes false.

alter table public.lili_buddy_taunts
  add column if not exists message text not null default '',
  add column if not exists work_started_at timestamptz,
  add column if not exists worked_seconds integer not null default 0,
  add column if not exists released_at timestamptz;

create index if not exists lili_buddy_taunts_receiver_open_idx
  on public.lili_buddy_taunts(receiver_id, created_at desc)
  where released_at is null;

create table if not exists public.lili_buddy_encouragements (
  id uuid primary key default gen_random_uuid(),
  sender_id uuid not null references auth.users(id) on delete cascade,
  receiver_id uuid not null references auth.users(id) on delete cascade,
  message text not null default '',
  created_at timestamptz not null default now(),
  expires_at timestamptz not null default (now() + interval '1 hour'),
  ended_at timestamptz,
  constraint lili_buddy_encouragements_not_self check (sender_id <> receiver_id)
);

create index if not exists lili_buddy_encouragements_receiver_open_idx
  on public.lili_buddy_encouragements(receiver_id, created_at desc)
  where ended_at is null;
create index if not exists lili_buddy_encouragements_sender_idx
  on public.lili_buddy_encouragements(sender_id, receiver_id, created_at desc);

alter table public.lili_buddy_encouragements enable row level security;
drop policy if exists lili_buddy_encouragements_participant_select on public.lili_buddy_encouragements;
create policy lili_buddy_encouragements_participant_select
  on public.lili_buddy_encouragements
  for select to authenticated
  using ((select auth.uid()) in (sender_id, receiver_id));

-- Presence is the source of truth for continuous valid work. A pause resets
-- the counter; a fresh working heartbeat advances it without double counting.
create or replace function public.lili_mark_taunt_started()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if coalesce(new.working, false) then
    update public.lili_buddy_taunts t
    set started_working_at = coalesce(t.started_working_at, now()),
        work_started_at = now(),
        worked_seconds = least(
          1200,
          greatest(0, t.worked_seconds + case
            when t.work_started_at is null then 0
            else floor(extract(epoch from (now() - t.work_started_at)))::integer
          end)
        )
    where t.receiver_id = new.user_id
      and t.released_at is null;
    update public.lili_buddy_taunts t
    set released_at = now(), work_started_at = null
    where t.receiver_id = new.user_id
      and t.released_at is null
      and t.worked_seconds >= 1200;
  else
    update public.lili_buddy_taunts t
    set work_started_at = null, worked_seconds = 0
    where t.receiver_id = new.user_id
      and t.released_at is null;
  end if;
  return new;
end;
$$;

create or replace function public.lili_send_taunt(p_target uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  me uuid := (select auth.uid());
  taunt_id uuid;
  taunt_message text;
  message_index integer := floor(random() * 14)::integer;
begin
  if me is null then raise exception '请先登录'; end if;
  if p_target is null or p_target = me then raise exception '不能嘲讽自己'; end if;
  if not public.lili_are_buddies(me, p_target) then raise exception '只能嘲讽已确认的搭子'; end if;
  if exists (
    select 1 from public.lili_focus_presence f
    where f.user_id = p_target and f.working and f.last_seen > now() - interval '2 minutes'
  ) then
    raise exception '对方正在工作，请改为送上加油';
  end if;
  if exists (
    select 1 from public.lili_buddy_taunts t
    where t.sender_id = me and t.receiver_id = p_target
      and t.created_at > now() - interval '1 hour'
  ) then
    raise exception '同一位搭子两次嘲讽至少间隔 1 小时';
  end if;
  if (
    select count(*)
    from public.lili_buddy_taunts t
    where t.sender_id = me
      and t.receiver_id = p_target
      and t.created_at >= date_trunc('day', now() at time zone 'Asia/Shanghai') at time zone 'Asia/Shanghai'
  ) >= 3 then
    raise exception '同一位搭子每天最多嘲讽 3 次';
  end if;
  taunt_message := case message_index
    when 0 then '怎么，今天准备靠意念完成？'
    when 1 then '工位有人，工作没人。'
    when 2 then '不急，DDL会替你急。'
    when 3 then '任务还在，你倒先下线了。'
    when 4 then '今日研究方法：观察任务自然消失。'
    when 5 then '样本没跑，你先跑了。'
    when 6 then 'Codex都醒了，你还没开工？'
    when 7 then '任务：0%，精神内耗：100%。'
    when 8 then '就这？'
    when 9 then '离开工只差一个开始按钮。'
    when 10 then '恭喜，被搭子当场抓获。'
    when 11 then '有人已经发现你没在干活了。'
    when 12 then '人不在线，债倒是先欠上了。'
    else '没关系，回来记得先还20分钟。'
  end;
  insert into public.lili_buddy_taunts(sender_id, receiver_id, message)
  values (me, p_target, taunt_message)
  returning id into taunt_id;
  return jsonb_build_object('id', taunt_id, 'receiver_id', p_target, 'active', true, 'message', taunt_message);
end;
$$;

create or replace function public.lili_taunt_state()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  me uuid := (select auth.uid());
  result jsonb;
begin
  if me is null then raise exception '请先登录'; end if;
  -- The trigger normally advances this state. This reconciliation covers a
  -- missed trigger after an offline period and makes pause/start immediate.
  if exists (
    select 1 from public.lili_focus_presence f
    where f.user_id = me and f.working and f.last_seen > now() - interval '2 minutes'
  ) then
    update public.lili_buddy_taunts t
    set started_working_at = coalesce(t.started_working_at, now()),
        work_started_at = now(),
        worked_seconds = least(1200, greatest(0, t.worked_seconds + case
          when t.work_started_at is null then 0
          else floor(extract(epoch from (now() - t.work_started_at)))::integer
        end))
    where t.receiver_id = me and t.released_at is null;
    update public.lili_buddy_taunts t
    set released_at = now(), work_started_at = null
    where t.receiver_id = me and t.released_at is null and t.worked_seconds >= 1200;
  else
    update public.lili_buddy_taunts t
    set work_started_at = null, worked_seconds = 0
    where t.receiver_id = me and t.released_at is null;
  end if;
  select jsonb_build_object(
    'active', true,
    'id', t.id,
    'sender_id', t.sender_id,
    'sender_nickname', public.lili_owner_nickname(t.sender_id),
    'created_at', t.created_at,
    'message', coalesce(nullif(t.message, ''), '怎么，今天准备靠意念完成？'),
    'support_count', (select count(*) from public.lili_buddy_taunts active_t where active_t.receiver_id = me and active_t.released_at is null),
    'remaining_work_seconds', greatest(0, 1200 - t.worked_seconds - case when t.work_started_at is null then 0 else floor(extract(epoch from (now() - t.work_started_at)))::integer end)
  ) into result
  from public.lili_buddy_taunts t
  where t.receiver_id = me and t.released_at is null
  order by t.created_at desc limit 1;
  return coalesce(result, jsonb_build_object('active', false));
end;
$$;

create or replace function public.lili_send_encouragement(p_target uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  me uuid := (select auth.uid());
  encouragement_id uuid;
  encouragement_message text;
  message_index integer := floor(random() * 5)::integer;
begin
  if me is null then raise exception '请先登录'; end if;
  if p_target is null or p_target = me then raise exception '不能给自己加油'; end if;
  if not public.lili_are_buddies(me, p_target) then raise exception '只能给已确认的搭子加油'; end if;
  if not exists (
    select 1 from public.lili_focus_presence f
    where f.user_id = p_target and f.working and f.last_seen > now() - interval '2 minutes'
  ) then
    raise exception '对方已经暂停工作，请改为嘲讽';
  end if;
  if exists (
    select 1 from public.lili_buddy_taunts t
    where t.receiver_id = p_target and t.released_at is null
  ) then
    raise exception '对方正在被搭子抓包，等惩罚结束后再加油';
  end if;
  if exists (
    select 1 from public.lili_buddy_encouragements e
    where e.sender_id = me and e.receiver_id = p_target
      and e.created_at > now() - interval '30 minutes'
  ) then
    raise exception '同一位搭子 30 分钟内只能送一次持续加油';
  end if;
  encouragement_message := case message_index
    when 0 then '抓到一个真在干活的。'
    when 1 then '行，今天没摸鱼。'
    when 2 then '继续，别给我机会嘲讽你。'
    when 3 then '已经在干了，那我只能给你加油了。'
    else '这次嘲讽失败，奖励一个加油。'
  end;
  insert into public.lili_buddy_encouragements(sender_id, receiver_id, message)
  values (me, p_target, encouragement_message)
  returning id into encouragement_id;
  return jsonb_build_object('id', encouragement_id, 'receiver_id', p_target, 'active', true, 'message', encouragement_message);
end;
$$;

create or replace function public.lili_encouragement_state()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  me uuid := (select auth.uid());
  result jsonb;
begin
  if me is null then raise exception '请先登录'; end if;
  update public.lili_buddy_encouragements e
  set ended_at = now()
  where e.receiver_id = me and e.ended_at is null
    and (e.expires_at <= now() or not exists (
      select 1 from public.lili_focus_presence f
      where f.user_id = me and f.working and f.last_seen > now() - interval '2 minutes'
    ));
  select jsonb_build_object(
    'active', true,
    'id', e.id,
    'sender_id', e.sender_id,
    'sender_nickname', public.lili_owner_nickname(e.sender_id),
    'message', e.message,
    'created_at', e.created_at,
    'expires_at', e.expires_at,
    'support_count', (select count(*) from public.lili_buddy_encouragements active_e where active_e.receiver_id = me and active_e.ended_at is null and active_e.expires_at > now())
  ) into result
  from public.lili_buddy_encouragements e
  where e.receiver_id = me and e.ended_at is null and e.expires_at > now()
  order by e.created_at desc limit 1;
  return coalesce(result, jsonb_build_object('active', false));
end;
$$;

-- The desktop polls both states together so a social refresh adds one RPC,
-- not two sequential network round trips.
create or replace function public.lili_reaction_state()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
begin
  return jsonb_build_object(
    'taunt', public.lili_taunt_state(),
    'encouragement', public.lili_encouragement_state()
  );
end;
$$;

revoke execute on function public.lili_send_encouragement(uuid) from public, anon;
revoke execute on function public.lili_encouragement_state() from public, anon;
revoke execute on function public.lili_reaction_state() from public, anon;
grant execute on function public.lili_send_encouragement(uuid) to authenticated;
grant execute on function public.lili_encouragement_state() to authenticated;
grant execute on function public.lili_reaction_state() to authenticated;
