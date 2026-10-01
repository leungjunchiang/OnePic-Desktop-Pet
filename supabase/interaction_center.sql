-- v307: quota protects ALL legacy/delta writes. Existing events remain facts;
-- inbox is a bounded projection, with private read receipts only.
create table if not exists public.lili_rest_day_usage (
  user_id uuid not null references auth.users(id) on delete cascade,
  beijing_date date not null,
  primary key(user_id,beijing_date)
);
alter table public.lili_rest_day_usage enable row level security;
revoke all on public.lili_rest_day_usage from public,anon,authenticated;
-- Preserve prior usage (including cancelled days); never delete/rewrite facts.
insert into public.lili_rest_day_usage(user_id,beijing_date)
select distinct user_id,(occurred_at at time zone 'Asia/Shanghai')::date
from public.lili_discipline_events where event_type='rest_day' on conflict do nothing;

create or replace function public.lili_rest_day_quota_guard() returns trigger
language plpgsql security definer set search_path='' as $$
declare used integer;
begin
  if new.event_type <> 'rest_day' then return new; end if;
  new.event_date := (new.occurred_at at time zone 'Asia/Shanghai')::date;
  perform pg_advisory_xact_lock(hashtextextended('lili-discipline:'||new.user_id::text,0));
  if exists(select 1 from public.lili_rest_day_usage u where u.user_id=new.user_id
    and u.beijing_date=new.event_date) then return new; end if;
  if not(public.lili_week_plan(new.user_id)->'workdays' ?
      (array['mon','tue','wed','thu','fri','sat','sun'])[extract(isodow from new.event_date)::int]) then
    raise exception '固定休息日无需免战牌'; end if;
  if new.event_date > (now() at time zone 'Asia/Shanghai')::date then
    raise exception '不能使用未来免战日'; end if;
  select count(*) into used from public.lili_rest_day_usage u
    where u.user_id=new.user_id and date_trunc('month',u.beijing_date::timestamp)=date_trunc('month',new.event_date::timestamp);
  if used>=8 then raise exception '本月免战额度已用完，下月恢复'; end if;
  insert into public.lili_rest_day_usage(user_id,beijing_date) values(new.user_id,new.event_date) on conflict do nothing;
  return new;
end $$;
revoke all on function public.lili_rest_day_quota_guard() from public,anon,authenticated;
drop trigger if exists lili_rest_day_quota on public.lili_discipline_events;
create trigger lili_rest_day_quota before insert or update on public.lili_discipline_events
for each row execute function public.lili_rest_day_quota_guard();

create or replace function public.lili_rest_day_quota() returns jsonb
language plpgsql security definer set search_path='' as $$
declare me uuid:=(select auth.uid()); d date:=(now() at time zone 'Asia/Shanghai')::date; days jsonb;
begin
  if me is null then raise exception '请先登录'; end if;
  select coalesce(jsonb_agg(x.event_date order by x.event_date),'[]') into days from
    (select beijing_date as event_date from public.lili_rest_day_usage where user_id=me
      and date_trunc('month',beijing_date::timestamp)=date_trunc('month',d::timestamp)) x;
  return jsonb_build_object('month',to_char(d,'YYYY-MM'),'used_dates',days,'limit',8);
end $$;
revoke all on function public.lili_rest_day_quota() from public,anon,authenticated;
grant execute on function public.lili_rest_day_quota() to authenticated;

create or replace function public.lili_set_rest_day() returns jsonb
language plpgsql security definer set search_path='' as $$
declare me uuid:=(select auth.uid()); moment timestamptz:=clock_timestamp();
  d date:=(moment at time zone 'Asia/Shanghai')::date; plan jsonb;
begin
  if me is null then raise exception '请先登录'; end if;
  plan:=public.lili_week_plan(me);
  if not(plan->'workdays' ? (array['mon','tue','wed','thu','fri','sat','sun'])[extract(isodow from d)::int]) then
    raise exception '今天是固定休息日，无需使用免战牌'; end if;
  perform pg_advisory_xact_lock(hashtextextended('lili-discipline:'||me::text,0));
  insert into public.lili_discipline_events(user_id,id,dedupe_key,event_type,event_date,occurred_at,metadata)
  values(me,gen_random_uuid(),d::text||':rest_day','rest_day',d,moment,'{}')
  on conflict(user_id,dedupe_key) where dedupe_key<>'' do update set occurred_at=excluded.occurred_at;
  update public.lili_discipline_events set requires_explanation=false where user_id=me and event_date=d;
  return public.lili_discipline_sync()||jsonb_build_object('rest_day_quota',public.lili_rest_day_quota());
end $$;
revoke all on function public.lili_set_rest_day() from public,anon,authenticated;
grant execute on function public.lili_set_rest_day() to authenticated;

create or replace function public.lili_is_rest_day(p_owner uuid,p_day date) returns boolean
language sql stable security definer set search_path='' as $$
  select coalesce((select event_type='rest_day' from public.lili_discipline_events
    where user_id=p_owner and event_date=p_day and event_type in ('rest_day','cancel_rest_day')
    order by occurred_at desc,sync_revision desc limit 1),false);
$$;
revoke all on function public.lili_is_rest_day(uuid,date) from public,anon,authenticated;

create or replace function public.lili_cancel_rest_day() returns jsonb
language plpgsql security definer set search_path='' as $$
declare me uuid:=(select auth.uid()); moment timestamptz:=clock_timestamp(); d date:=(moment at time zone 'Asia/Shanghai')::date;
begin
  if me is null then raise exception '请先登录'; end if;
  perform pg_advisory_xact_lock(hashtextextended('lili-discipline:'||me::text,0));
  if public.lili_is_rest_day(me,d) then
    insert into public.lili_discipline_events(user_id,id,event_type,event_date,occurred_at,metadata)
    values(me,gen_random_uuid(),'cancel_rest_day',d,moment,'{}');
  end if;
  return public.lili_discipline_sync()||jsonb_build_object('rest_day_quota',public.lili_rest_day_quota());
end $$;
revoke all on function public.lili_cancel_rest_day() from public,anon,authenticated;
grant execute on function public.lili_cancel_rest_day() to authenticated;

create table if not exists public.lili_interaction_inbox_state (
  user_id uuid primary key references auth.users(id) on delete cascade,
  baseline_at timestamptz not null default now()
);
create table if not exists public.lili_interaction_reads (
  user_id uuid not null references auth.users(id) on delete cascade,
  event_id text not null check(length(event_id)<=160),
  read_at timestamptz not null default now(),
  primary key(user_id,event_id)
);
alter table public.lili_interaction_inbox_state enable row level security;
alter table public.lili_interaction_reads enable row level security;
revoke all on public.lili_interaction_inbox_state,public.lili_interaction_reads from public,anon,authenticated;

create or replace view public.lili_interaction_feed as
select 'visit:'||v.id as event_id,v.receiver_id,v.sender_id,v.kind as event_type,
  v.created_at,v.id as source_id,'visit'::text as source,
  v.status='pending' and v.expires_at>now() and (v.kind='visit' or v.kind like 'food_%') as requires_action,
  jsonb_build_object('status',v.status) as payload
from public.lili_visit_events v
union all
select 'nudge:'||n.id,n.owner_id,n.supervisor_id,n.kind,n.created_at,n.id,'nudge',false,'{}'::jsonb
from public.lili_supervision_nudges n
union all
select 'case:'||c.id||':'||coalesce(c.history->-1->>'action_id','issued'),
  case when c.history->-1->>'actor_id'=c.owner_id::text then c.supervisor_id else c.owner_id end,
  case when c.history->-1->>'actor_id'=c.owner_id::text then c.owner_id else c.supervisor_id end,
  'coaching_action',coalesce((c.history->-1->>'at')::timestamptz,c.created_at),c.id,'case',
  not c.paused and c.state in ('pending','rejected','explained','acknowledged'),
  jsonb_build_object('state',c.state,'title',c.title,'paused',c.paused)
from public.lili_coaching_cases c
where c.history->-1->>'actor_id' is distinct from c.owner_id::text
  or public.lili_can_handle_case(c.owner_id,c.supervisor_id,c.kind)
union all
select 'room:'||r.id,r.target_id,r.actor_id,r.kind,r.created_at,r.id,'room',false,
  jsonb_build_object('message',r.message)
from public.lili_room_events r where r.target_id is not null and r.kind not in ('focus_start','focus_pause','focus_finish','join','leave','goal_set')
union all
select 'taunt:'||t.id,t.receiver_id,t.sender_id,'tease',t.created_at,t.id,'taunt',false,'{}'::jsonb
from public.lili_buddy_taunts t
union all
select 'cheer:'||e.id,e.receiver_id,e.sender_id,'cheer',e.created_at,e.id,'cheer',false,'{}'::jsonb
from public.lili_buddy_encouragements e
union all
select 'buddy:'||b.id,b.addressee_id,b.requester_id,'buddy_request',b.created_at,b.id,'buddy',b.status='pending',
  jsonb_build_object('status',b.status)
from public.lili_buddy_links b
union all
select 'buddy-outgoing:'||b.id,b.requester_id,b.addressee_id,'buddy_outgoing',b.created_at,b.id,'buddy_outgoing',false,
  jsonb_build_object('status',b.status)
from public.lili_buddy_links b;
revoke all on public.lili_interaction_feed from public,anon,authenticated;

create or replace function public.lili_interaction_inbox(p_read_ids jsonb default '[]') returns jsonb
language plpgsql security definer set search_path='' as $$
declare me uuid:=(select auth.uid()); baseline timestamptz; result jsonb;
begin
  if me is null then raise exception '请先登录'; end if;
  if jsonb_typeof(p_read_ids)<>'array' or jsonb_array_length(p_read_ids)>30 then
    raise exception '一次最多处理30条已读状态'; end if;
  insert into public.lili_interaction_inbox_state(user_id) values(me) on conflict do nothing;
  select baseline_at into baseline from public.lili_interaction_inbox_state where user_id=me;
  insert into public.lili_interaction_reads(user_id,event_id)
    select me,f.event_id from public.lili_interaction_feed f
    where f.receiver_id=me and f.event_id in (select jsonb_array_elements_text(p_read_ids))
    on conflict do nothing;
  -- One UNION plus one profile join; no per-event profile requests, no report/plan payload.
  select coalesce(jsonb_agg(to_jsonb(x) order by x.created_at desc),'[]') into result from (
    select f.event_id,f.sender_id,f.event_type,f.created_at,f.source_id,f.source,f.requires_action,f.payload,
      coalesce(p.nickname,'搭子') as nickname,
      f.source<>'buddy_outgoing' and f.created_at>baseline and r.event_id is null as unread
    from public.lili_interaction_feed f
    left join public.lili_profiles p on p.user_id=f.sender_id
    left join public.lili_interaction_reads r on r.user_id=me and r.event_id=f.event_id
    where f.receiver_id=me and ((f.created_at >= (((now() at time zone 'Asia/Shanghai')::date-6)::timestamp at time zone 'Asia/Shanghai')
      and f.created_at < (((now() at time zone 'Asia/Shanghai')::date+1)::timestamp at time zone 'Asia/Shanghai')) or f.requires_action)
    order by f.created_at desc,f.event_id desc limit 30
  ) x;
  delete from public.lili_interaction_reads where user_id=me and read_at<now()-interval '30 days';
  return jsonb_build_object('interaction_events',result,'server_timestamp',now());
end $$;
revoke all on function public.lili_interaction_inbox(jsonb) from public,anon,authenticated;
grant execute on function public.lili_interaction_inbox(jsonb) to authenticated;

-- 已读只返回小回执，不再为了标记已读重传完整列表；旧 inbox 参数保持兼容。
create or replace function public.lili_mark_interactions_read(p_read_ids jsonb) returns jsonb
language plpgsql security definer set search_path='' as $$
declare me uuid:=(select auth.uid()); ids jsonb;
begin
  if me is null then raise exception '请先登录'; end if;
  if p_read_ids is null or jsonb_typeof(p_read_ids)<>'array' or jsonb_array_length(p_read_ids)>30 then
    raise exception '一次最多处理30条已读状态'; end if;
  select coalesce(jsonb_agg(x.event_id),'[]') into ids from (
    select distinct f.event_id from public.lili_interaction_feed f
    where f.receiver_id=me and f.event_id in (select jsonb_array_elements_text(p_read_ids))
  ) x;
  insert into public.lili_interaction_reads(user_id,event_id)
    select me,jsonb_array_elements_text(ids) on conflict do nothing;
  return jsonb_build_object('read_event_ids',ids,'server_timestamp',now());
end $$;
revoke all on function public.lili_mark_interactions_read(jsonb) from public,anon,authenticated;
grant execute on function public.lili_mark_interactions_read(jsonb) to authenticated;
