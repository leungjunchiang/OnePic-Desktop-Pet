-- v305: applied in the existing atomic discipline migration bundle.
-- No client table writes; every transition rechecks consent and expected revision.
create table if not exists public.lili_coaching_cases (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  supervisor_id uuid not null references auth.users(id) on delete cascade,
  source_event_id uuid not null,
  source_rule_key text not null default '',
  kind text not null check(kind in ('late_start','long_break','focus_shortfall','weekly_shortfall','early_finish')),
  event_date date not null,
  title text not null, detail text not null default '',
  state text not null default 'pending' check(state in ('pending','acknowledged','explained','active','completed','forgiven','rejected')),
  required_seconds integer not null default 0 check(required_seconds between 0 and 86400),
  accepted_at timestamptz, closed_at timestamptz,
  explanation text not null default '' check(length(explanation)<=500),
  review_note text not null default '' check(length(review_note)<=300),
  schedule text not null default 'now' check(schedule in ('now','week','tomorrow')),
  paused boolean not null default false,
  history jsonb not null default '[]' check(jsonb_typeof(history)='array'),
  revision bigint not null default 0,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  unique(owner_id,source_event_id),
  foreign key(owner_id,source_event_id) references public.lili_discipline_events(user_id,id) on delete cascade
);
alter table public.lili_coaching_cases add column if not exists source_rule_key text not null default '';
create index if not exists lili_coaching_owner_revision_idx on public.lili_coaching_cases(owner_id,revision);
create index if not exists lili_coaching_supervisor_idx on public.lili_coaching_cases(supervisor_id,owner_id,event_date desc);
alter table public.lili_coaching_cases enable row level security;
revoke all on public.lili_coaching_cases from public,anon,authenticated;

create or replace function public.lili_coaching_revision_guard()
returns trigger language plpgsql security definer set search_path='' as $$
begin
  perform pg_advisory_xact_lock(hashtextextended('lili-discipline:'||new.owner_id::text,0));
  new.revision:=nextval('public.lili_discipline_revision_seq');
  new.updated_at:=clock_timestamp();
  return new;
end; $$;
revoke execute on function public.lili_coaching_revision_guard() from public,anon,authenticated;
drop trigger if exists lili_coaching_revision_guard on public.lili_coaching_cases;
create trigger lili_coaching_revision_guard before insert or update on public.lili_coaching_cases
  for each row execute function public.lili_coaching_revision_guard();

-- Private helper: formal discipline needs active strict mode plus each field opt-in.
create or replace function public.lili_can_handle_case(p_owner uuid,p_supervisor uuid,p_kind text)
returns boolean language sql stable security definer set search_path='' as $$
  select coalesce((p->>'officer')::boolean and (p->>'remind')::boolean and (p->>'view_reports')::boolean
    and case when p_kind='late_start' then (p->>'view_lateness')::boolean and (p->>'view_plan')::boolean
             when p_kind='long_break' then (p->>'view_rest')::boolean
             else (p->>'view_progress')::boolean and (p->>'view_plan')::boolean end
    and exists(select 1 from public.lili_supervision_sessions s where s.owner_id=p_owner
      and s.supervisor_id=p_supervisor and s.active and s.mode='officer'),false)
  from (select public.lili_supervision_permission(p_owner,p_supervisor) as p) q;
$$;
revoke execute on function public.lili_can_handle_case(uuid,uuid,text) from public,anon,authenticated;

-- One union of canonical FocusSession intervals, never a heartbeat or daily counter.
create or replace function public.lili_coaching_focus_seconds(p_owner uuid,p_start timestamptz)
returns integer language sql stable security definer set search_path='' as $$
  with clipped as (
    select greatest(start_at,p_start) as lo,least(end_at,now()) as hi from public.lili_focus_segments
    where user_id=p_owner and end_at>p_start and start_at<now() and end_at>=start_at
      and end_at-start_at<=interval '24 hours' and end_at<=now()+interval '2 minutes'
  ), prior as (
    select lo,hi,max(hi) over(order by lo,hi rows between unbounded preceding and 1 preceding) as previous_hi
    from clipped where hi>lo
  ), groups as (
    select lo,hi,sum(case when previous_hi is null or lo>previous_hi then 1 else 0 end)
      over(order by lo,hi) as grp from prior
  ), islands as (select min(lo) as lo,max(hi) as hi from groups group by grp)
  select coalesce(floor(sum(extract(epoch from hi-lo)))::int,0) from islands;
$$;
revoke execute on function public.lili_coaching_focus_seconds(uuid,timestamptz) from public,anon,authenticated;

create or replace function public.lili_coaching_case_action(
  p_owner_id uuid,p_action text,p_action_id uuid,p_case_id uuid default null,
  p_source_event_id uuid default null,p_expected_revision bigint default 0,
  p_minutes integer default 0,p_text text default '')
returns jsonb language plpgsql security definer set search_path='' as $$
declare me uuid:=(select auth.uid()); c public.lili_coaching_cases; e public.lili_discipline_events;
  before_state text; wanted int; title text; detail text; allowed boolean; actual text; planned text; late int;
begin
  if me is null or p_owner_id is null or p_action_id is null then raise exception '请先登录'; end if;
  if p_action is null or p_minutes is null or p_minutes<0 or p_minutes>1440 then raise exception '动作或补时无效'; end if;
  -- Same lock order as policy mutations: policy -> discipline owner -> case row.
  perform 1 from public.lili_supervision_policy where owner_id=p_owner_id for share;
  perform pg_advisory_xact_lock(hashtextextended('lili-discipline:'||p_owner_id::text,0));
  if p_case_id is not null then
    select * into c from public.lili_coaching_cases where owner_id=p_owner_id and id=p_case_id for update;
    if not found then raise exception '事项不存在'; end if;
  else
    select * into e from public.lili_discipline_events where user_id=p_owner_id and id=p_source_event_id;
    if not found or e.event_type not in ('late_start','long_break','focus_shortfall','weekly_shortfall','early_finish')
      then raise exception '只能处理真实纪律结果'; end if;
    if not public.lili_can_handle_case(p_owner_id,me,e.event_type) then raise exception 'TA 未开放此严格训导权限'; end if;
    select * into c from public.lili_coaching_cases where owner_id=p_owner_id and source_event_id=e.id for update;
    if not found then
      if p_action not in ('request_explanation','request_makeup','forgive','week_makeup','tomorrow_makeup')
        then raise exception '不能用此动作创建事项'; end if;
      wanted:=0;
      if e.event_type='late_start' then
        actual:=public.lili_actual_work_start_clock(p_owner_id,e.event_date);
        planned:=substring(e.metadata->>'planned_start' from 12 for 5);
        if planned !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$' or planned is null or actual is null then raise exception '缺少真实开工或历史计划'; end if;
        late:=greatest(0,extract(epoch from actual::time-planned::time)::int/60);
        if late=0 then raise exception '真实开工已更正，不存在迟到'; end if;
        title:='迟到 '||late||' 分钟'; detail:='计划 '||planned||' · 实际 '||actual;
      elsif e.event_type='long_break' then
        wanted:=greatest(0,coalesce((e.metadata->>'overtime_seconds')::int,0));
        title:='长休超时 '||ceil(wanted/60.0)::int||' 分钟'; detail:='普通短休不记录纪律。';
        if wanted=0 then raise exception '没有已结算的长休超时'; end if;
      else
        wanted:=greatest(0,coalesce((e.metadata->>'gap_seconds')::int,(e.metadata->>'remaining_seconds')::int,0));
        if e.event_type='early_finish' then wanted:=greatest(wanted,coalesce((e.metadata->>'minutes_early')::int,0)*60); end if;
        if wanted=0 then raise exception '没有已结算的目标缺口'; end if;
        title:=case when e.event_type='early_finish' then '下班异常' else '目标缺口 '||ceil(wanted/60.0)::int||' 分钟' end;
        detail:='已结算的计划执行结果；周目标保持不变。';
      end if;
      if public.lili_is_rest_day(p_owner_id,e.event_date) or public.lili_is_rest_day(p_owner_id,(now() at time zone 'Asia/Shanghai')::date)
        then raise exception '免战期间暂停训导'; end if;
      insert into public.lili_coaching_cases(owner_id,supervisor_id,source_event_id,source_rule_key,kind,event_date,title,detail)
        values(p_owner_id,me,e.id,e.dedupe_key,e.event_type,e.event_date,title,detail) returning * into c;
    end if;
  end if;
  allowed:=public.lili_can_handle_case(p_owner_id,c.supervisor_id,c.kind)
    and not public.lili_is_rest_day(p_owner_id,(now() at time zone 'Asia/Shanghai')::date);
  if me<>p_owner_id and me<>c.supervisor_id then raise exception '此事项由另一位搭子处理'; end if;
  if me<>p_owner_id and not allowed then raise exception 'TA 未开放此严格训导权限'; end if;
  -- Idempotent retries still require authorization. No stale device can undo a decision.
  if exists(select 1 from jsonb_array_elements(c.history) h where h->>'action_id'=p_action_id::text) then
    return jsonb_build_object('case',to_jsonb(c),'message','此操作已处理'); end if;
  if not allowed then raise exception '训导已暂停，历史记录保留'; end if;
  if c.revision<>p_expected_revision and not (p_case_id is null and p_expected_revision=0 and c.history='[]')
    then raise exception '事项已在另一台电脑更新，请刷新后重试'; end if;
  if c.state in ('completed','forgiven') then raise exception '事项已经结案'; end if;
  if jsonb_array_length(c.history)>=100 and p_action<>'forgive' then raise exception '事项处理次数已达上限，可联系搭子放过'; end if;
  before_state:=c.state;
  if me=p_owner_id then
    if p_action='explain' and c.state in ('pending','rejected') then
      if length(btrim(coalesce(p_text,''))) not between 1 and 500 then raise exception '请写一两句话说明'; end if;
      c.explanation:=btrim(p_text); c.state:='explained';
    elsif p_action='accept' and c.state='pending' and c.required_seconds>0 then
      c.state:='active'; c.accepted_at:=now();
      if c.schedule='tomorrow' then
        c.accepted_at:=((now() at time zone 'Asia/Shanghai')::date+1)::timestamp at time zone 'Asia/Shanghai';
      end if;
    elsif p_action='complete' and c.state='active' then
      if public.lili_coaching_focus_seconds(p_owner_id,c.accepted_at)<c.required_seconds then
        return jsonb_build_object('case',to_jsonb(c),'completion_pending',true,'message','等待专注事实同步后确认'); end if;
      c.state:='completed'; c.closed_at:=now();
    else raise exception '此动作不属于用户当前可操作范围'; end if;
  else
    if p_action='forgive' then c.state:='forgiven'; c.closed_at:=now();
    elsif p_action='request_explanation' and c.state in ('pending','rejected','acknowledged') then
      c.state:='pending'; c.required_seconds:=0;
    elsif p_action in ('request_makeup','week_makeup','tomorrow_makeup') and c.state in ('pending','rejected','acknowledged') then
      if p_minutes<1 then raise exception '补时至少一分钟'; end if;
      c.required_seconds:=p_minutes*60; c.state:='pending'; c.accepted_at:=null;
      c.schedule:=case p_action when 'week_makeup' then 'week' when 'tomorrow_makeup' then 'tomorrow' else 'now' end;
    elsif p_action='approve' and c.state='explained' then c.state:='completed'; c.closed_at:=now();
    elsif p_action='approve_makeup' and c.state='explained' then
      if p_minutes<1 then raise exception '補时至少一分钟'; end if;
      c.required_seconds:=p_minutes*60; c.state:='active'; c.accepted_at:=now();
    elsif p_action='reject' and c.state='explained' then
      if length(btrim(coalesce(p_text,''))) not between 1 and 300 then raise exception '请说明需要补充什么'; end if;
      c.state:='rejected'; c.review_note:=btrim(p_text);
    else raise exception '此动作不属于监督者当前可操作范围'; end if;
    if p_action<>'reject' then c.review_note:=left(btrim(coalesce(p_text,'')),300); end if;
  end if;
  c.history:=c.history||jsonb_build_array(jsonb_build_object('action_id',p_action_id,'action',p_action,
    'actor_id',me,'from',before_state,'to',c.state,'at',now(),'minutes',p_minutes,'text',left(coalesce(p_text,''),500)));
  update public.lili_coaching_cases set state=c.state,required_seconds=c.required_seconds,
    accepted_at=c.accepted_at,closed_at=c.closed_at,explanation=c.explanation,review_note=c.review_note,
    schedule=c.schedule,history=c.history,paused=false where id=c.id returning * into c;
  -- Existing ledger explanation UI must not bypass bilateral review or keep a closed case pending.
  if c.state in ('completed','forgiven') then
    update public.lili_discipline_events set requires_explanation=false where user_id=p_owner_id and id=c.source_event_id;
  end if;
  return jsonb_build_object('case',to_jsonb(c),'message',case c.state when 'explained' then '说明已提交'
    when 'active' then '补时已开始' when 'completed' then '已结案' when 'forgiven' then '已放过' when 'rejected' then '说明已退回' else '已发送要求' end);
end; $$;
revoke execute on function public.lili_coaching_case_action(uuid,text,uuid,uuid,uuid,bigint,integer,text) from public,anon,authenticated;
grant execute on function public.lili_coaching_case_action(uuid,text,uuid,uuid,uuid,bigint,integer,text) to authenticated;

-- Explicit target page reads; source result summaries only, never full FocusSessions.
create or replace function public.lili_coaching_overview(p_owner uuid)
returns jsonb language plpgsql stable security definer set search_path='' as $$
declare me uuid:=(select auth.uid()); rows jsonb; candidates jsonb;
begin
  if me is null or not public.lili_are_buddies(me,p_owner) then raise exception '只能查看搭子'; end if;
  select coalesce(jsonb_agg(to_jsonb(q)),'[]') into rows from (
    select c.* from public.lili_coaching_cases c where c.owner_id=p_owner and c.supervisor_id=me
      and public.lili_can_handle_case(p_owner,me,c.kind) order by c.updated_at desc limit 50) q;
  select coalesce(jsonb_agg(jsonb_build_object('id',e.id,'kind',e.event_type,'event_date',e.event_date,
    'title',case e.event_type when 'late_start' then '迟到 '||coalesce(e.metadata->>'minutes_late','0')||' 分钟'
      when 'long_break' then '长休超时' when 'early_finish' then '下班异常' else '已结算目标缺口' end,
    'suggested_minutes',case when e.event_type='long_break' then greatest(1,ceil(coalesce((e.metadata->>'overtime_seconds')::numeric,0)/60)::int)
      else 30 end)),'[]') into candidates from (
    select d.* from public.lili_discipline_events d where d.user_id=p_owner
      and d.event_date>=(now() at time zone 'Asia/Shanghai')::date-7
      and d.event_type in ('late_start','long_break','focus_shortfall','weekly_shortfall','early_finish')
      and public.lili_can_handle_case(p_owner,me,d.event_type)
      and not public.lili_is_rest_day(p_owner,d.event_date)
      and not exists(select 1 from public.lili_coaching_cases c where c.owner_id=p_owner and c.source_event_id=d.id)
      and (case when d.event_type='late_start' then coalesce((d.metadata->>'minutes_late')::int,0)>0
                when d.event_type='long_break' then coalesce((d.metadata->>'overtime_seconds')::int,0)>0
                else coalesce((d.metadata->>'gap_seconds')::int,(d.metadata->>'remaining_seconds')::int,(d.metadata->>'minutes_early')::int,0)>0 end)
    order by d.occurred_at desc limit 30) e;
  return jsonb_build_object('coaching_cases',rows,'coaching_candidates',candidates,
    'unhandled_case_count',(select count(*) from public.lili_coaching_cases c where c.owner_id=p_owner
      and c.state not in ('completed','forgiven') and public.lili_can_handle_case(p_owner,me,c.kind)));
end; $$;
revoke execute on function public.lili_coaching_overview(uuid) from public,anon,authenticated;

-- Reuse the existing discipline worker / 60s runtime poll; separate bounded cursor.
create or replace function public.lili_coaching_sync_delta(
  p_settings jsonb default null,p_client_updated_at timestamptz default null,p_events jsonb default '[]',
  p_after_revision bigint default 0,p_include_config boolean default false,p_after_case_revision bigint default 0)
returns jsonb language plpgsql security definer set search_path='' as $$
declare me uuid:=(select auth.uid()); result jsonb; rows jsonb; next_cursor bigint; more boolean;
  e public.lili_discipline_events; supervisor uuid; actual text; plan jsonb; late integer;
begin
  if me is null then raise exception '请先登录'; end if;
  perform 1 from public.lili_supervision_policy where owner_id=me for share;
  result:=public.lili_discipline_sync_delta(p_settings,p_client_updated_at,p_events,p_after_revision,p_include_config);
  -- A real first start can arrive before the client has hydrated its remote mode.
  -- Derive one final lateness from the same original session/start facts, not login/presence.
  for e in select * from public.lili_discipline_events d where d.user_id=me and d.event_type='start_work'
    and d.event_date=(now() at time zone 'Asia/Shanghai')::date and result->'acknowledged_ids' ? d.id::text loop
    actual:=public.lili_actual_work_start_clock(me,e.event_date); plan:=public.lili_week_plan(me);
    if actual is not null and not public.lili_is_rest_day(me,e.event_date)
      and plan->'workdays' ? (array['mon','tue','wed','thu','fri','sat','sun'])[extract(isodow from e.event_date)::int] then
      late:=greatest(0,extract(epoch from actual::time-(plan->>'start_time')::time)::int/60);
      if late>=30 and exists(select 1 from public.lili_supervision_sessions s where s.owner_id=me
          and public.lili_can_handle_case(me,s.supervisor_id,'late_start')) then
        insert into public.lili_discipline_events(user_id,id,dedupe_key,event_type,event_date,occurred_at,metadata,requires_explanation)
          values(me,gen_random_uuid(),e.event_date||':late_start','late_start',e.event_date,e.occurred_at,
            jsonb_build_object('rule_key',e.event_date||':late_start','minutes_late',late,
              'planned_start',e.event_date::text||'T'||(plan->>'start_time')||':00+08:00',
              'actual_start',actual,'actual_start_source','first_real_start_after_0600'),true)
          on conflict(user_id,dedupe_key) where dedupe_key<>'' do nothing;
        if found then result:=jsonb_set(result,'{has_more}','true'); end if;
      end if;
    end if;
  end loop;
  -- Automatically open current strict issues only on newly acknowledged facts.
  -- Historical hydration is never converted into a new pending case.
  for e in select * from public.lili_discipline_events d where d.user_id=me and d.requires_explanation
    and d.event_date=(now() at time zone 'Asia/Shanghai')::date
    and (result->'acknowledged_ids' ? d.id::text or (d.updated_at>=statement_timestamp() and d.metadata->>'actual_start_source'='first_real_start_after_0600'))
    and d.event_type in ('late_start','long_break','focus_shortfall','weekly_shortfall','early_finish')
    and not exists(select 1 from public.lili_coaching_cases c where c.owner_id=me and c.source_event_id=d.id) loop
    select s.supervisor_id into supervisor from public.lili_supervision_sessions s
      where s.owner_id=me and public.lili_can_handle_case(me,s.supervisor_id,e.event_type)
      order by s.updated_at limit 1;
    if supervisor is not null and not public.lili_is_rest_day(me,e.event_date) then
      insert into public.lili_coaching_cases(owner_id,supervisor_id,source_event_id,source_rule_key,kind,event_date,title,detail,history)
        values(me,supervisor,e.id,e.dedupe_key,e.event_type,e.event_date,
          case e.event_type when 'late_start' then '迟到 '||coalesce(e.metadata->>'minutes_late','0')||' 分钟'
            when 'long_break' then '长休超时' when 'early_finish' then '下班异常' else '已结算目标缺口' end,
          coalesce(e.metadata->>'detail','需要说明这次计划偏差。'),
          jsonb_build_array(jsonb_build_object('action','auto_requested','actor_id',supervisor,'at',now(),'to','pending')))
        on conflict(owner_id,source_event_id) do nothing;
    end if;
  end loop;
  -- Earlier-device work and later final settlements can resolve the underlying result.
  -- An explanation flag alone cannot cancel a case; only the corrected numeric result can.
  update public.lili_coaching_cases c set state='forgiven',closed_at=now(),review_note='原始专注事实已更正，此事项解除',
    history=c.history||jsonb_build_array(jsonb_build_object('action','fact_corrected','from',c.state,'to','forgiven','at',now()))
    from public.lili_discipline_events d where d.user_id=me and d.id=c.source_event_id and c.owner_id=me
      and c.state not in ('completed','forgiven') and
      ((c.kind='late_start' and d.metadata->>'minutes_late'='0')
       or (c.kind='focus_shortfall' and d.metadata->>'gap_seconds'='0')
       or (c.kind='weekly_shortfall' and d.metadata->>'remaining_seconds'='0'));
  update public.lili_coaching_cases c set paused=not(public.lili_can_handle_case(me,c.supervisor_id,c.kind)
      and not public.lili_is_rest_day(me,(now() at time zone 'Asia/Shanghai')::date))
    where c.owner_id=me and c.state not in ('completed','forgiven') and c.paused is distinct from
      not(public.lili_can_handle_case(me,c.supervisor_id,c.kind)
      and not public.lili_is_rest_day(me,(now() at time zone 'Asia/Shanghai')::date));
  select coalesce(jsonb_agg(to_jsonb(q) order by q.revision),'[]'),coalesce(max(q.revision),p_after_case_revision)
    into rows,next_cursor from (select * from public.lili_coaching_cases where owner_id=me
      and revision>greatest(p_after_case_revision,0) order by revision limit 100) q;
  select exists(select 1 from public.lili_coaching_cases where owner_id=me and revision>next_cursor) into more;
  return result||jsonb_build_object('coaching_cases',rows,'next_case_revision',next_cursor,'has_more_cases',more);
end; $$;
revoke execute on function public.lili_coaching_sync_delta(jsonb,timestamptz,jsonb,bigint,boolean,bigint) from public,anon,authenticated;
grant execute on function public.lili_coaching_sync_delta(jsonb,timestamptz,jsonb,bigint,boolean,bigint) to authenticated;
