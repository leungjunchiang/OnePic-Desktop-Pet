-- Keep the server's presence record aligned with the same durable focus state
-- that the desktop timer and study room display.  ``working`` remains for
-- backwards compatibility, while the extra fields make a paused session
-- distinguishable from a stale/legacy payload and are useful for diagnostics.

alter table public.lili_focus_presence
  add column if not exists session_active boolean not null default false,
  add column if not exists work_state text not null default 'idle',
  add column if not exists pause_reason text;

alter table public.lili_focus_presence
  drop constraint if exists lili_focus_presence_work_state_check;
alter table public.lili_focus_presence
  add constraint lili_focus_presence_work_state_check
  check (work_state in ('idle', 'working', 'paused_manual', 'paused_idle', 'paused_lock', 'paused_sleep', 'paused_video'));

create index if not exists lili_focus_presence_live_work_idx
  on public.lili_focus_presence(user_id, last_seen desc)
  where working = true and work_state = 'working';

-- A transition must be visible to the taunt ledger even when only the
-- explicit state fields changed (mixed-version clients still use ``working``).
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

drop trigger if exists lili_mark_taunt_started_on_presence on public.lili_focus_presence;
create trigger lili_mark_taunt_started_on_presence
  after insert or update of working, session_active, work_state, pause_reason, last_seen
  on public.lili_focus_presence
  for each row execute function public.lili_mark_taunt_started();

-- The short heartbeat cadence in the desktop client is the authoritative
-- freshness boundary. Keep the server guard conservative enough that a fresh
-- working user cannot be taunted through a stale card or a second device.
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
    where f.user_id = p_target
      and f.working
      and f.work_state = 'working'
      and f.session_active
      and f.last_seen > now() - interval '45 seconds'
  ) then
    raise exception '对方正在工作，请改为送上加油';
  end if;
  -- Old clients do not send the new state fields yet; a fresh legacy
  -- ``working`` heartbeat remains protected by the same server-side guard.
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

revoke execute on function public.lili_send_taunt(uuid) from public, anon;
grant execute on function public.lili_send_taunt(uuid) to authenticated;
