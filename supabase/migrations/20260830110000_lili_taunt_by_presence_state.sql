-- Make the reaction action state-based instead of time-of-day based.
--
-- A buddy who is working receives encouragement. A buddy who is resting or
-- offline can be taunted only from 08:00 through 22:30 Beijing time. Outside
-- that window the client may explain the privacy rule, but this function
-- rejects before quota/cooldown checks or any insert.

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
  local_minutes integer := extract(hour from (now() at time zone 'Asia/Shanghai'))::integer * 60
    + extract(minute from (now() at time zone 'Asia/Shanghai'))::integer;
  day_start timestamptz := date_trunc('day', now() at time zone 'Asia/Shanghai') at time zone 'Asia/Shanghai';
begin
  if me is null then raise exception '请先登录'; end if;
  if p_target is null or p_target = me then raise exception '不能嘲讽自己'; end if;
  if not public.lili_are_buddies(me, p_target) then raise exception '只能嘲讽已确认的搭子'; end if;

  -- Privacy time is not another kind of reaction.  Run this before the
  -- presence/quota queries so an after-hours click sends no signal and does
  -- not consume the daily allowance.
  if local_minutes not between 480 and 1350 then
    raise exception '现在是嘲讽时间之外，给对方留点私人休息时间';
  end if;

  -- A fresh working presence is the only state that must receive
  -- encouragement. Offline, resting, and stale presence are tauntable during
  -- the permitted window.
  if exists (
    select 1 from public.lili_focus_presence f
    where f.user_id = p_target
      and f.working
      and f.work_state = 'working'
      and f.session_active
      and f.last_seen > now() - interval '45 seconds'
  ) then
    raise exception '对方正在工作，请改为送上加油';
  end if;
  if exists (
    select 1 from public.lili_focus_presence f
    where f.user_id = p_target
      and f.working
      and f.last_seen > now() - interval '45 seconds'
      and coalesce(f.work_state, 'idle') = 'idle'
  ) then
    raise exception '对方正在工作，请改为送上加油';
  end if;

  if exists (
    select 1 from public.lili_buddy_taunts t
    where t.sender_id = me and t.receiver_id = p_target
      and t.created_at >= day_start
      and t.created_at > now() - interval '1 hour'
  ) then
    raise exception '同一位搭子两次嘲讽至少间隔 1 小时';
  end if;
  if (
    select count(*)
    from public.lili_buddy_taunts t
    where t.sender_id = me
      and t.receiver_id = p_target
      and t.created_at >= day_start
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
  return jsonb_build_object(
    'id', taunt_id,
    'receiver_id', p_target,
    'active', true,
    'kind', 'taunt',
    'message', taunt_message
  );
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
  return jsonb_build_object('id', encouragement_id, 'receiver_id', p_target, 'active', true, 'kind', 'encouragement', 'message', encouragement_message);
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

revoke execute on function public.lili_send_taunt(uuid) from public, anon;
revoke execute on function public.lili_send_encouragement(uuid) from public, anon;
revoke execute on function public.lili_encouragement_state() from public, anon;
grant execute on function public.lili_send_taunt(uuid) to authenticated;
grant execute on function public.lili_send_encouragement(uuid) to authenticated;
grant execute on function public.lili_encouragement_state() to authenticated;

