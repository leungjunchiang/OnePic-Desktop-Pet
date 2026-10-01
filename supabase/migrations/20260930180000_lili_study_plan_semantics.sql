-- v299: one weekly goal; rest days are append-only account events, never presence fields.
-- Pair pauses are owner actions independent of the global opt-in policy.
alter table public.lili_supervision_sessions add column if not exists paused_by_owner boolean not null default false;
alter table public.lili_supervision_nudges drop constraint if exists lili_supervision_nudges_kind_check;
alter table public.lili_supervision_nudges add constraint lili_supervision_nudges_kind_check
  check(kind in ('start','rest','finish','progress','cheer','take_break','return','rest_more','explain','praise','flower','approve_finish','knock','ask'));
create index if not exists lili_rest_day_lookup_idx on public.lili_discipline_events(user_id,event_date) where event_type='rest_day';

create or replace function public.lili_is_rest_day(p_owner uuid,p_day date)
returns boolean language sql stable security definer set search_path='' as $$
  select exists(select 1 from public.lili_discipline_events where user_id=p_owner and event_date=p_day and event_type='rest_day');
$$;

create or replace function public.lili_week_plan(p_owner uuid)
returns jsonb language plpgsql stable security definer set search_path='' as $$
declare raw jsonb; days jsonb:='[]'; key text;
begin
  select settings into raw from public.lili_discipline_settings where user_id=p_owner;
  raw:=coalesce(raw,'{}');
  foreach key in array array['mon','tue','wed','thu','fri','sat','sun'] loop
    if (jsonb_typeof(raw->'workdays')='array' and raw->'workdays' ? key) or
      (jsonb_typeof(raw->'workdays') is distinct from 'array' and
        case when raw->'daily_target_minutes'->>key ~ '^[0-9]{1,4}$' then (raw->'daily_target_minutes'->>key)::int>0 else key not in ('sat','sun') end)
      then days:=days||jsonb_build_array(key); end if;
  end loop;
  return jsonb_build_object('workdays',days,
    'weekly_target_minutes',case when raw->>'weekly_target_minutes' ~ '^[0-9]{1,5}$' then least(10080,(raw->>'weekly_target_minutes')::int) else 1800 end,
    'start_time',case when raw->>'start_time' ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$' then raw->>'start_time' else '09:00' end,
    'planned_finish_enabled',coalesce(raw->>'planned_finish_enabled'='true',false),
    'finish_time',case when raw->>'planned_finish_enabled'='true' and raw->>'finish_time' ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$' then raw->>'finish_time' else null end,
    'late_grace_minutes',case when raw->>'late_grace_minutes' ~ '^[0-9]{1,3}$' then least(240,(raw->>'late_grace_minutes')::int) else 30 end,
    'break_limit_minutes',case when raw->>'break_limit_minutes' ~ '^[0-9]{1,3}$' then greatest(1,least(480,(raw->>'break_limit_minutes')::int)) else 20 end,
    'catchup_strategy',case when raw->>'catchup_strategy' in ('even','frontload','none') then raw->>'catchup_strategy' else 'even' end);
end; $$;

create or replace function public.lili_set_rest_day()
returns jsonb language plpgsql security definer set search_path='' as $$
declare me uuid:=(select auth.uid()); day date:=(now() at time zone 'Asia/Shanghai')::date; plan jsonb;
begin
  if me is null then raise exception '请先登录'; end if;
  plan:=public.lili_week_plan(me);
  if not plan->'workdays' ? (array['mon','tue','wed','thu','fri','sat','sun'])[extract(isodow from day)::int] then
    raise exception '今天本来就是休息日，不需要免战牌'; end if;
  perform pg_advisory_xact_lock(hashtext(me::text));
  insert into public.lili_discipline_events(user_id,id,dedupe_key,event_type,event_date,occurred_at,metadata,requires_explanation)
    values(me,gen_random_uuid(),day::text||':rest_day','rest_day',day,now(),'{}',false)
    on conflict(user_id,dedupe_key) where dedupe_key<>'' do nothing;
  update public.lili_discipline_events set requires_explanation=false where user_id=me and event_date=day;
  return public.lili_discipline_sync();
end; $$;

create or replace function public.lili_pause_peer_supervision(p_supervisor_id uuid,p_paused boolean)
returns jsonb language plpgsql security definer set search_path='' as $$
declare me uuid:=(select auth.uid());
begin
  if me is null then raise exception '请先登录'; end if;
  if p_paused is null or not public.lili_are_buddies(me,p_supervisor_id) then raise exception '只能调整与已确认搭子的关系'; end if;
  perform 1 from public.lili_supervision_policy where owner_id=me for update;
  insert into public.lili_supervision_sessions(owner_id,supervisor_id,mode,active,paused_by_owner)
    values(me,p_supervisor_id,'normal',false,p_paused)
    on conflict(owner_id,supervisor_id) do update set active=false,paused_by_owner=p_paused,updated_at=now();
  update public.lili_supervision_policy set revision=revision+1,updated_at=now() where owner_id=me;
  return jsonb_build_object('message',case when p_paused then '已暂停 TA 对你的监督，其他搭子和历史记录不受影响。' else '已恢复 TA 的监督资格，仍以你的全局范围为准。' end);
end; $$;

create or replace function public.lili_supervision_permission(p_owner uuid,p_supervisor uuid)
returns jsonb language plpgsql stable security definer set search_path='' as $$
declare p public.lili_supervision_policy; eligible boolean; paused boolean;
begin
  select * into p from public.lili_supervision_policy where owner_id=p_owner;
  paused := exists(select 1 from public.lili_supervision_sessions where owner_id=p_owner and supervisor_id=p_supervisor and paused_by_owner);
  eligible := coalesce(not paused and p.enabled and public.lili_are_buddies(p_owner,p_supervisor) and
    (p.scope='all' or (p.scope='selected' and p_supervisor=any(p.selected_ids)) or
      (p.scope='invited' and p_supervisor=any(p.invited_ids))),false);
  return jsonb_build_object('enabled',coalesce(p.enabled,false),'eligible',eligible,'paused',paused,
    'officer',eligible and (p.officer_scope='all' or p_supervisor=any(p.officer_ids)),
    'remind',eligible and p.remind,'view_plan',eligible and p.view_plan,
    'view_progress',eligible and p.view_progress,'view_lateness',eligible and p.view_lateness,
    'view_rest',eligible and p.view_rest,'view_reports',eligible and p.view_reports);
end;
$$;

create or replace function public.lili_supervision_snapshot()
returns jsonb language plpgsql stable security definer set search_path='' as $$
declare me uuid:=(select auth.uid()); p jsonb; effective text;
begin
  if me is null then raise exception '请先登录'; end if;
  select to_jsonb(s)-'owner_id' into p from public.lili_supervision_policy s where owner_id=me;
  select case when bool_or(s.mode='officer' and (public.lili_supervision_permission(me,s.supervisor_id)->>'officer')::boolean)
    then 'officer' when count(*)>0 then 'normal' else 'off' end into effective
  from public.lili_supervision_sessions s where s.owner_id=me and s.active
    and (public.lili_supervision_permission(me,s.supervisor_id)->>'eligible')::boolean;
  return jsonb_build_object('policy',coalesce(p,jsonb_build_object('enabled',false,'scope','selected',
    'selected_ids','[]'::jsonb,'invited_ids','[]'::jsonb,'officer_scope','selected','officer_ids','[]'::jsonb,
    'remind',true,'view_plan',true,'view_progress',true,'view_lateness',true,'view_rest',true,'view_reports',true,'revision',0)),
    'effective_mode',effective,
    'supervising',coalesce((select jsonb_agg(jsonb_build_object('owner_id',s.owner_id,'mode',
      case when s.mode='officer' and (public.lili_supervision_permission(s.owner_id,me)->>'officer')::boolean then 'officer' else 'normal' end))
      from public.lili_supervision_sessions s where s.supervisor_id=me and s.active
      and (public.lili_supervision_permission(s.owner_id,me)->>'eligible')::boolean),'[]'::jsonb),
    'supervisors',coalesce((select jsonb_agg(jsonb_build_object('supervisor_id',s.supervisor_id,'mode',case when s.mode='officer' and (public.lili_supervision_permission(me,s.supervisor_id)->>'officer')::boolean then 'officer' else 'normal' end))
      from public.lili_supervision_sessions s where s.owner_id=me and s.active
      and (public.lili_supervision_permission(me,s.supervisor_id)->>'eligible')::boolean),'[]'::jsonb));
end;
$$;

create or replace function public.lili_start_supervision(p_owner_id uuid,p_mode text)
returns jsonb language plpgsql security definer set search_path='' as $$
declare me uuid:=(select auth.uid()); permission jsonb;
begin
  if me is null then raise exception '请先登录'; end if;
  -- Serialize eligibility with changes to the owner's policy.
  perform 1 from public.lili_supervision_policy where owner_id=p_owner_id for update;
  perform pg_advisory_xact_lock(hashtext(p_owner_id::text));
  if p_mode<>'off' and public.lili_is_rest_day(p_owner_id,(now() at time zone 'Asia/Shanghai')::date) then raise exception 'TA 今天挂了免战牌，暂停训导'; end if;
  permission:=public.lili_supervision_permission(p_owner_id,me);
  if p_mode not in ('normal','officer','off') or p_mode is null then raise exception '监督模式无效'; end if;
  if p_mode='off' then
    update public.lili_supervision_sessions set active=false,updated_at=now() where owner_id=p_owner_id and supervisor_id=me;
  else
    if not (permission->>'eligible')::boolean or (p_mode='officer' and not (permission->>'officer')::boolean)
      then raise exception 'TA 尚未向你开放此监督模式'; end if;
    insert into public.lili_supervision_sessions(owner_id,supervisor_id,mode) values(p_owner_id,me,p_mode)
    on conflict(owner_id,supervisor_id) do update set mode=excluded.mode,active=true,updated_at=now();
  end if;
  update public.lili_supervision_policy set revision=revision+1,updated_at=now() where owner_id=p_owner_id;
  return jsonb_build_object('message','监督模式已更新；TA 的其他设备将在下一次同步时应用。');
end;
$$;

-- No raw plan JSON, identities, app activity or explanations leave this helper.
create or replace function public.lili_buddy_study_overview(p_buddy_id uuid)
returns jsonb language plpgsql stable security definer set search_path='' as $$
declare me uuid:=(select auth.uid()); own jsonb; peer jsonb; plan jsonb; day date:=(now() at time zone 'Asia/Shanghai')::date;
  state text; updated timestamptz; exempt boolean; today integer; week integer; target integer:=0; count_days integer:=0;
  budget integer; base_target integer; actions jsonb:='[]'; progress jsonb; rest_seconds integer; start_stamp timestamp;
  candidate date; key text; started boolean; raw jsonb;
begin
  if me is null then raise exception '请先登录'; end if;
  if not public.lili_are_buddies(me,p_buddy_id) then raise exception '只能查看已确认搭子的详情'; end if;
  own:=public.lili_supervision_permission(me,p_buddy_id);
  peer:=public.lili_supervision_permission(p_buddy_id,me);
  exempt:=public.lili_is_rest_day(p_buddy_id,day);
  -- Plan is needed internally for rules, but returned only after field authorization.
  plan:=public.lili_week_plan(p_buddy_id);
  select work_status,updated_at into state,updated from public.lili_work_status where user_id=p_buddy_id;
  select exists(select 1 from public.lili_work_events where actor_id=p_buddy_id and event_type='start_work'
    and (occurred_at at time zone 'Asia/Shanghai')::date=day) into started;
  if (peer->>'view_progress')::boolean then
    today:=public.lili_effective_focus_today_seconds(p_buddy_id); week:=public.lili_effective_focus_week_seconds(p_buddy_id);
    progress:=jsonb_build_object('today_seconds',today,'week_seconds',week);
    if (peer->>'view_plan')::boolean then
      for offset_day in 0..(7-extract(isodow from day)::int) loop
        candidate:=day+offset_day;
        key:=(array['mon','tue','wed','thu','fri','sat','sun'])[extract(isodow from candidate)::int];
        if plan->'workdays' ? key and not public.lili_is_rest_day(p_buddy_id,candidate) then count_days:=count_days+1; end if;
      end loop;
      budget:=greatest(0,(plan->>'weekly_target_minutes')::int*60-greatest(0,week-today));
      if count_days>0 and not exempt and plan->'workdays' ? (array['mon','tue','wed','thu','fri','sat','sun'])[extract(isodow from day)::int]
        and plan->>'catchup_strategy'<>'none' then
        target:=(budget+count_days-1)/count_days;
        if plan->>'catchup_strategy'='frontload' then
          base_target:=ceil((plan->>'weekly_target_minutes')::numeric*60/greatest(1,jsonb_array_length(plan->'workdays')))::int;
          target:=greatest(target,budget-base_target*(count_days-1)); end if;
      end if;
      progress:=progress||jsonb_build_object('daily_target_seconds',target);
    end if;
  end if;
  if state='resting' and (peer->>'view_rest')::boolean then
    rest_seconds:=greatest(0,extract(epoch from now()-updated)::int); end if;
  if not exempt and (peer->>'remind')::boolean then
    if state='focused' then actions:=actions||'["cheer","take_break","praise"]';
    elsif state='resting' and (peer->>'view_rest')::boolean then
      actions:=actions||'["return","rest_more"]';
      if rest_seconds>(plan->>'break_limit_minutes')::int*60 then actions:=actions||'["rest"]'; end if;
    end if;
    start_stamp:=day+(plan->>'start_time')::time;
    if (peer->>'view_plan')::boolean and (peer->>'view_lateness')::boolean and not started and state is distinct from 'focused'
      and plan->'workdays' ? (array['mon','tue','wed','thu','fri','sat','sun'])[extract(isodow from day)::int]
      and (now() at time zone 'Asia/Shanghai')>start_stamp+make_interval(mins=>(plan->>'late_grace_minutes')::int)
      then actions:=actions||'["start","knock"]'; end if;
    if target>0 and today<target and (now() at time zone 'Asia/Shanghai')>start_stamp+interval '3 hours' then actions:=actions||'["progress"]'; end if;
    if state in ('focused','resting') then actions:=actions||'["finish"]'; end if;
    if target>0 and today>=target then actions:=actions||'["approve_finish","flower","praise"]'; end if;
    if (peer->>'view_reports')::boolean then actions:=actions||'["ask"]'; end if;
    if (peer->>'view_reports')::boolean and exists(select 1 from public.lili_discipline_events e
      where e.user_id=p_buddy_id and e.requires_explanation and e.explanation is null
        and not public.lili_is_rest_day(p_buddy_id,e.event_date)) then actions:=actions||'["explain"]'; end if;
  end if;
  return jsonb_build_object('own_permission',own,'peer_permission',peer,
    'peer_plan',case when (peer->>'view_plan')::boolean then plan- 'late_grace_minutes'-'break_limit_minutes'-'catchup_strategy' else null end,
    'progress',progress,'rest_seconds',rest_seconds,'exempt',exempt,'exemption_date',case when exempt then day else null end,'actions',actions,
    'can_read_reports',(peer->>'view_reports')::boolean,
    'active_mode',(select case when s.mode='officer' and (peer->>'officer')::boolean then 'officer' else 'normal' end
      from public.lili_supervision_sessions s where s.owner_id=p_buddy_id and s.supervisor_id=me and s.active and (peer->>'eligible')::boolean),
    'peer_active_mode',(select case when s.mode='officer' and (own->>'officer')::boolean then 'officer' else 'normal' end
      from public.lili_supervision_sessions s where s.owner_id=me and s.supervisor_id=p_buddy_id and s.active and (own->>'eligible')::boolean))
      ||public.lili_coaching_overview(p_buddy_id);
end; $$;

create or replace function public.lili_supervision_nudge(p_owner_id uuid,p_kind text)
returns jsonb language plpgsql security definer set search_path='' as $$
declare me uuid:=(select auth.uid()); overview jsonb;
begin
  if me is null then raise exception '请先登录'; end if;
  perform 1 from public.lili_supervision_policy where owner_id=p_owner_id for share;
  perform pg_advisory_xact_lock(hashtext(p_owner_id::text));
  overview:=public.lili_buddy_study_overview(p_owner_id);
  if p_kind is null or not overview->'actions' ? p_kind then raise exception 'TA 当前状态不适合这个提醒，或今天免战'; end if;
  if exists(select 1 from public.lili_supervision_nudges where owner_id=p_owner_id and supervisor_id=me and created_at>now()-interval '1 minute')
    then raise exception '刚刚提醒过，稍等一分钟'; end if;
  insert into public.lili_supervision_nudges(owner_id,supervisor_id,kind) values(p_owner_id,me,p_kind);
  return jsonb_build_object('message','轻提醒已发送');
end; $$;

create or replace function public.lili_discipline_supervisor_report(p_owner_id uuid)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare me uuid := (select auth.uid()); permission jsonb;
begin
  if me is null then raise exception '请先登录'; end if;
  permission:=public.lili_supervision_permission(p_owner_id,me);
  if not (permission->>'view_reports')::boolean then
    raise exception '你没有查看这位用户纪律摘要的授权';
  end if;
  return jsonb_build_object('reports', coalesce((
    select jsonb_agg(jsonb_build_object('event_date', e.event_date,
      -- Supervisors receive only bounded, allow-listed summary fields. The
      -- owner controls stored JSON, so never return the original metadata.
      'metadata', (jsonb_build_object(
        'planned_start', case when e.metadata->>'planned_start' ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
          then e.metadata->>'planned_start' else null end,
        'actual_start', public.lili_actual_work_start_clock(p_owner_id,e.event_date),
        'actual_finish', case when e.metadata->>'actual_finish' ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
          then e.metadata->>'actual_finish' else null end,
        'planned_finish', case when e.metadata->>'planned_finish' ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
          then e.metadata->>'planned_finish' else null end,
        'mode', case when e.metadata->>'mode' in ('normal', 'officer')
          then e.metadata->>'mode' else 'off' end,
        'lateness_minutes', case when public.lili_is_rest_day(p_owner_id,e.event_date) then 0 when public.lili_actual_work_start_clock(p_owner_id,e.event_date) is not null and e.metadata->>'planned_start' ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
          then greatest(0,extract(epoch from public.lili_actual_work_start_clock(p_owner_id,e.event_date)::time-(e.metadata->>'planned_start')::time)::int/60) else null end,
        'today_seconds', case when e.metadata->>'today_seconds' ~ '^[0-9]{1,9}$'
          then (e.metadata->>'today_seconds')::integer else 0 end,
        'daily_target_seconds', case when public.lili_is_rest_day(p_owner_id,e.event_date) then 0 when e.metadata->>'daily_target_seconds' ~ '^[0-9]{1,9}$'
          then (e.metadata->>'daily_target_seconds')::integer else 0 end,
        'daily_gap_seconds', case when public.lili_is_rest_day(p_owner_id,e.event_date) then 0 when e.metadata->>'daily_gap_seconds' ~ '^[0-9]{1,9}$'
          then (e.metadata->>'daily_gap_seconds')::integer else 0 end,
        'long_break_count', case when public.lili_is_rest_day(p_owner_id,e.event_date) then 0 when e.metadata->>'long_break_count' ~ '^[0-9]{1,5}$'
          then (e.metadata->>'long_break_count')::integer else 0 end,
        'break_overtime_seconds', case when public.lili_is_rest_day(p_owner_id,e.event_date) then 0 when e.metadata->>'break_overtime_seconds' ~ '^[0-9]{1,9}$'
          then (e.metadata->>'break_overtime_seconds')::integer else 0 end,
        'weekly_target_seconds', case when e.metadata->>'weekly_target_seconds' ~ '^[0-9]{1,9}$'
          then (e.metadata->>'weekly_target_seconds')::integer else 0 end,
        'week_seconds', case when e.metadata->>'week_seconds' ~ '^[0-9]{1,9}$'
          then (e.metadata->>'week_seconds')::integer else 0 end,
        'weekly_remaining_seconds', case when e.metadata->>'weekly_remaining_seconds' ~ '^[0-9]{1,9}$'
          then (e.metadata->>'weekly_remaining_seconds')::integer else 0 end,
        'unexplained_count', case when public.lili_is_rest_day(p_owner_id,e.event_date) then 0 when e.metadata->>'unexplained_count' ~ '^[0-9]{1,5}$'
          then (e.metadata->>'unexplained_count')::integer else 0 end
      ) - case when (permission->>'view_lateness')::boolean
          then array[]::text[] else array['lateness_minutes', 'actual_start', 'actual_finish'] end
        - case when (permission->>'view_plan')::boolean
          then array[]::text[] else array['planned_start', 'planned_finish', 'daily_target_seconds', 'weekly_target_seconds', 'daily_gap_seconds', 'weekly_remaining_seconds'] end
       - case when (permission->>'view_progress')::boolean then array[]::text[] else array['today_seconds','week_seconds','daily_gap_seconds','weekly_remaining_seconds'] end
        - case when (permission->>'view_rest')::boolean then array[]::text[] else array['long_break_count','break_overtime_seconds'] end
      ), 'read_at', rr.read_at)
      order by e.event_date desc)
    from (
      select event_row.* from public.lili_discipline_events event_row
      where event_row.user_id = p_owner_id and event_row.event_type = 'daily_report'
      order by event_row.event_date desc limit 30
    ) e
    left join public.lili_discipline_report_reads rr
      on rr.owner_id = p_owner_id and rr.supervisor_id = me and rr.report_date = e.event_date
  ), '[]'::jsonb));
end;
$$;

create or replace function public.lili_discipline_sync(
  p_settings jsonb default null,
  p_client_updated_at timestamptz default null,
  p_events jsonb default '[]'::jsonb
) returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  me uuid := (select auth.uid());
  incoming jsonb;
  remote_settings jsonb;
  remote_updated_at timestamptz;
  item jsonb;
  event_id uuid;
  event_dedupe text;
  supervision jsonb;
  old_plan jsonb;
begin
  if me is null then raise exception '请先登录'; end if;
  if p_settings is not null and octet_length(p_settings::text) > 16384 then
    raise exception '训导主任设置数据过大';
  end if;
  incoming := case when jsonb_typeof(p_events) = 'array' then p_events else '[]'::jsonb end;
  if jsonb_array_length(incoming) > 2000 then
    raise exception '单次同步事件数量过多';
  end if;

  if p_settings is not null and p_client_updated_at is not null then
    select settings into old_plan from public.lili_discipline_settings where user_id=me;
    -- A legacy client may sync its rule preferences, but cannot restore seven independent goals.
    if old_plan->>'plan_version'='2' and p_settings->>'plan_version' is distinct from '2' then
      p_settings:=p_settings||jsonb_build_object('plan_version',2,'weekly_target_minutes',old_plan->'weekly_target_minutes',
        'workdays',old_plan->'workdays','start_time',old_plan->'start_time','finish_time',old_plan->'finish_time',
        'planned_finish_enabled',old_plan->'planned_finish_enabled','catchup_strategy',old_plan->'catchup_strategy');
    end if;
    insert into public.lili_discipline_settings(user_id, settings, client_updated_at, updated_at)
    values (me, p_settings, p_client_updated_at, now())
    on conflict (user_id) do update set
      settings = excluded.settings,
      client_updated_at = excluded.client_updated_at,
      updated_at = now()
    where excluded.client_updated_at > public.lili_discipline_settings.client_updated_at;
  end if;

  for item in select value from jsonb_array_elements(incoming) loop
    begin
      if octet_length(item::text) > 8192 then continue; end if;
      if item->>'event_type'='rest_day' then
        if (item->>'event_date')::date>(now() at time zone 'Asia/Shanghai')::date then continue; end if;
        if not public.lili_week_plan(me)->'workdays' ? (array['mon','tue','wed','thu','fri','sat','sun'])[extract(isodow from (item->>'event_date')::date)::int] then continue; end if;
        item:=item||jsonb_build_object('requires_explanation',false,'metadata',jsonb_build_object('rule_key',(item->>'event_date')||':rest_day'));
      end if;
      event_id := (item->>'id')::uuid;
      event_dedupe := coalesce(item->'metadata'->>'rule_key', '');
      if event_dedupe <> '' then
        insert into public.lili_discipline_events(
          user_id, id, dedupe_key, event_type, event_date, occurred_at,
          metadata, requires_explanation, explanation
        ) values (
          me, event_id, event_dedupe, left(coalesce(item->>'event_type', 'unknown'), 64),
          (item->>'event_date')::date, (item->>'occurred_at')::timestamptz,
          coalesce(item->'metadata', '{}'::jsonb),
          coalesce((item->>'requires_explanation')::boolean, false),
          nullif(item->'explanation', '""'::jsonb)
        ) on conflict (user_id, dedupe_key) where dedupe_key <> '' do update set
          explanation = coalesce(public.lili_discipline_events.explanation, excluded.explanation),
          updated_at = now();
      else
        insert into public.lili_discipline_events(
          user_id, id, event_type, event_date, occurred_at,
          metadata, requires_explanation, explanation
        ) values (
          me, event_id, left(coalesce(item->>'event_type', 'unknown'), 64),
          (item->>'event_date')::date, (item->>'occurred_at')::timestamptz,
          coalesce(item->'metadata', '{}'::jsonb),
          coalesce((item->>'requires_explanation')::boolean, false),
          nullif(item->'explanation', '""'::jsonb)
        ) on conflict (user_id, id) do update set
          explanation = coalesce(public.lili_discipline_events.explanation, excluded.explanation),
          updated_at = now();
      end if;
    exception when others then
      -- One malformed event must not block settings or other valid events.
      continue;
    end;
  end loop;

  update public.lili_discipline_events e set requires_explanation=false where e.user_id=me and e.requires_explanation and public.lili_is_rest_day(me,e.event_date);
  select s.settings, s.client_updated_at into remote_settings, remote_updated_at
  from public.lili_discipline_settings s where s.user_id = me;
  supervision := public.lili_supervision_snapshot();
  return jsonb_build_object(
    'supervision', supervision,
    'rest_days',coalesce((select jsonb_agg(distinct event_date) from public.lili_discipline_events where user_id=me and event_type='rest_day'),'[]'),
    'nudges',coalesce((select jsonb_agg(jsonb_build_object('id',n.id,'supervisor_id',n.supervisor_id,'kind',n.kind,'created_at',n.created_at))
      from public.lili_supervision_nudges n where n.owner_id=me and n.created_at>now()-interval '3 minutes'
        and not public.lili_is_rest_day(me,(now() at time zone 'Asia/Shanghai')::date)
        and (public.lili_supervision_permission(me,n.supervisor_id)->>'remind')::boolean),'[]'::jsonb),
    'settings', coalesce(remote_settings, '{}'::jsonb),
    'client_updated_at', remote_updated_at,
    'events', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', e.id, 'event_type', e.event_type, 'event_date', e.event_date,
        'occurred_at', e.occurred_at, 'metadata', e.metadata,
        'requires_explanation', e.requires_explanation,
        'explanation', coalesce(e.explanation, 'null'::jsonb)
      ) order by e.occurred_at desc)
      from (
        select event_row.* from public.lili_discipline_events event_row
        where event_row.user_id = me
        order by event_row.occurred_at desc limit 2000
      ) e
    ), '[]'::jsonb)
  );
end;
$$;

-- Wrap the existing canonical statistics projection without adding a second clock/store.
do $$ begin
  if to_regprocedure('public.lili_dashboard_study_base_v299()') is null then
    alter function public.lili_dashboard() rename to lili_dashboard_study_base_v299;
  end if;
end; $$;
create or replace function public.lili_dashboard()
returns jsonb language plpgsql stable security definer set search_path='' as $$
declare payload jsonb; people jsonb; field text; day date:=(now() at time zone 'Asia/Shanghai')::date;
begin
  if (select auth.uid()) is null then raise exception '请先登录'; end if;
  payload:=public.lili_dashboard_study_base_v299();
  foreach field in array array['buddies','room_people','active_visits'] loop
    select coalesce(jsonb_agg(entry.value || jsonb_build_object('rest_day_date',case
      when public.lili_is_rest_day((coalesce(entry.value->>'user_id',entry.value->>'id'))::uuid,day) then day else null end)),'[]')
      into people from jsonb_array_elements(coalesce(payload->field,'[]')) entry(value);
    payload:=jsonb_set(payload,array[field],people,true);
  end loop;
  return payload;
end; $$;

revoke execute on function public.lili_is_rest_day(uuid,date) from public,anon,authenticated;

revoke execute on function public.lili_week_plan(uuid) from public,anon,authenticated;

revoke execute on function public.lili_set_rest_day() from public,anon,authenticated;
grant execute on function public.lili_set_rest_day() to authenticated;

revoke execute on function public.lili_pause_peer_supervision(uuid,boolean) from public,anon,authenticated;
grant execute on function public.lili_pause_peer_supervision(uuid,boolean) to authenticated;

revoke execute on function public.lili_dashboard_study_base_v299() from public,anon,authenticated;

revoke execute on function public.lili_dashboard() from public,anon,authenticated;
grant execute on function public.lili_dashboard() to authenticated;

create or replace function public.lili_respond_discipline_supervisor(p_request_id uuid,p_accept boolean)
returns jsonb language plpgsql security definer set search_path='' as $$
begin raise exception '请升级六毛，在搭子详情直接选择普通训导或严格训导'; end; $$;

-- v302: extend this idempotent deployment bundle; keep the old RPC for old clients.
-- No second focus/statistics table. A commit-ordered, account-serialized event cursor
-- avoids timestamp pagination skipping a late-committing write from another device.
create sequence if not exists public.lili_discipline_revision_seq;
alter table public.lili_discipline_events add column if not exists sync_revision bigint not null default 0;
update public.lili_discipline_events set sync_revision=nextval('public.lili_discipline_revision_seq') where sync_revision=0;
create index if not exists lili_discipline_delta_idx on public.lili_discipline_events(user_id,sync_revision);
revoke all on sequence public.lili_discipline_revision_seq from public,anon,authenticated;

create or replace function public.lili_discipline_revision_guard()
returns trigger language plpgsql security definer set search_path='' as $$
begin
  perform pg_advisory_xact_lock(hashtextextended('lili-discipline:'||new.user_id::text,0));
  if tg_op='UPDATE' and (new.event_type,new.event_date,new.occurred_at,new.metadata,new.requires_explanation,new.explanation)
      is not distinct from (old.event_type,old.event_date,old.occurred_at,old.metadata,old.requires_explanation,old.explanation) then
    return old; -- repeated legacy uploads must not move the cursor or rewrite timestamps
  end if;
  new.sync_revision:=nextval('public.lili_discipline_revision_seq');
  new.updated_at:=clock_timestamp();
  return new;
end; $$;
revoke execute on function public.lili_discipline_revision_guard() from public,anon,authenticated;
drop trigger if exists lili_discipline_revision_guard on public.lili_discipline_events;
create trigger lili_discipline_revision_guard before insert or update on public.lili_discipline_events
for each row execute function public.lili_discipline_revision_guard();

create or replace function public.lili_discipline_runtime_snapshot()
returns jsonb language plpgsql stable security definer set search_path='' as $$
declare me uuid:=(select auth.uid()); enabled boolean; revision bigint; effective text;
begin
  if me is null then raise exception '请先登录'; end if;
  select p.enabled,p.revision into enabled,revision from public.lili_supervision_policy p where p.owner_id=me;
  select case when bool_or(s.mode='officer' and (public.lili_supervision_permission(me,s.supervisor_id)->>'officer')::boolean)
    then 'officer' when count(*)>0 then 'normal' else 'off' end into effective
  from public.lili_supervision_sessions s where s.owner_id=me and s.active
    and (public.lili_supervision_permission(me,s.supervisor_id)->>'eligible')::boolean;
  return jsonb_build_object('policy',jsonb_build_object('enabled',coalesce(enabled,false),'revision',coalesce(revision,0)),
    'effective_mode',effective);
end; $$;
revoke execute on function public.lili_discipline_runtime_snapshot() from public,anon,authenticated;

create or replace function public.lili_discipline_sync_delta(
  p_settings jsonb default null,p_client_updated_at timestamptz default null,
  p_events jsonb default '[]',p_after_revision bigint default 0,p_include_config boolean default false
) returns jsonb language plpgsql security definer set search_path='' as $$
declare
  me uuid:=(select auth.uid()); item jsonb; event_id uuid; dedupe text; day date; kind text;
  old_plan jsonb; remote_plan jsonb; remote_stamp timestamptz; accepted jsonb:='[]'; rows jsonb;
  next_revision bigint:=greatest(coalesce(p_after_revision,0),0); has_more boolean; result jsonb;
  affected_days date[]:=array[]::date[]; actual_clock text;
begin
  if me is null then raise exception '请先登录'; end if;
  if jsonb_typeof(p_events) is distinct from 'array' or jsonb_array_length(p_events)>100
    or octet_length(p_events::text)>819200 then raise exception '纪律事件批次无效'; end if;
  if p_settings is not null and (jsonb_typeof(p_settings)<>'object' or octet_length(p_settings::text)>16384) then
    raise exception '工作计划数据无效'; end if;
  perform pg_advisory_xact_lock(hashtextextended('lili-discipline:'||me::text,0));
  if p_settings is not null and p_client_updated_at is not null then
    select settings into old_plan from public.lili_discipline_settings where user_id=me;
    if old_plan->>'plan_version'='2' and p_settings->>'plan_version' is distinct from '2' then
      p_settings:=p_settings||jsonb_build_object('plan_version',2,'weekly_target_minutes',old_plan->'weekly_target_minutes',
        'workdays',old_plan->'workdays','start_time',old_plan->'start_time','finish_time',old_plan->'finish_time',
        'planned_finish_enabled',old_plan->'planned_finish_enabled','catchup_strategy',old_plan->'catchup_strategy');
    end if;
    insert into public.lili_discipline_settings(user_id,settings,client_updated_at,updated_at)
    values(me,p_settings,p_client_updated_at,now())
    on conflict(user_id) do update set settings=excluded.settings,client_updated_at=excluded.client_updated_at,updated_at=now()
    where excluded.client_updated_at>public.lili_discipline_settings.client_updated_at;
  end if;
  for item in select value from jsonb_array_elements(p_events) loop
    begin
      if jsonb_typeof(item)<>'object' or octet_length(item::text)>8192 then continue; end if;
      event_id:=(item->>'id')::uuid; day:=(item->>'event_date')::date; kind:=item->>'event_type';
      if kind not in ('start_work','late_start','long_break','finish_work','early_finish','focus_shortfall',
                       'weekly_shortfall','daily_report','rest_day','cancel_rest_day') then continue; end if;
      if jsonb_typeof(item->'metadata') is distinct from 'object' then continue; end if;
      if kind='start_work' then
        if coalesce(item->'metadata'->>'source','') in ('sleep','lock','display_off','restart_safe_seal','account_switch','restore','heartbeat','startup','reconnect')
          or coalesce(item->'metadata'->>'restored','false')='true' then continue; end if;
        actual_clock:=case when item->'metadata'->>'actual_start' ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
          then item->'metadata'->>'actual_start' else to_char((item->>'occurred_at')::timestamptz at time zone 'Asia/Shanghai','HH24:MI') end;
        if actual_clock<'06:00' then continue; end if;
      end if;
      if kind in ('start_work','late_start') then affected_days:=array_append(affected_days,day); end if;
      if exists(select 1 from public.lili_discipline_events where user_id=me and id=event_id) then
        update public.lili_discipline_events set
          metadata=case when (kind='start_work' and (item->>'occurred_at')::timestamptz<occurred_at)
            or (kind in ('daily_report','finish_work','early_finish','focus_shortfall','weekly_shortfall') and (item->>'occurred_at')::timestamptz>occurred_at)
            then item->'metadata' else metadata end,
          occurred_at=case when kind='start_work' then least(occurred_at,(item->>'occurred_at')::timestamptz)
            when kind in ('daily_report','finish_work','early_finish','focus_shortfall','weekly_shortfall') then greatest(occurred_at,(item->>'occurred_at')::timestamptz) else occurred_at end,
          requires_explanation=case when kind in ('early_finish','focus_shortfall','weekly_shortfall') and (item->>'occurred_at')::timestamptz>occurred_at then coalesce((item->>'requires_explanation')::boolean,false) else requires_explanation end,
          explanation=coalesce(explanation,nullif(nullif(item->'explanation','""'::jsonb),'null'::jsonb))
          where user_id=me and id=event_id and event_type=kind;
        if not found then continue; end if;
        accepted:=accepted||jsonb_build_array(event_id);
        continue;
      end if;
      if kind='rest_day' then
        if day>(now() at time zone 'Asia/Shanghai')::date or not public.lili_week_plan(me)->'workdays'
          ? (array['mon','tue','wed','thu','fri','sat','sun'])[extract(isodow from day)::int] then continue; end if;
        item:=item||jsonb_build_object('requires_explanation',false,'metadata',jsonb_build_object('rule_key',day||':rest_day'));
      end if;
      -- One actual start per local day; earlier offline devices may correct the first timestamp.
      dedupe:=coalesce(item->'metadata'->>'rule_key','');
      if kind='start_work' then dedupe:=day||':actual_start'; end if;
      if dedupe='' then dedupe:='event:'||event_id; end if;
      insert into public.lili_discipline_events(user_id,id,dedupe_key,event_type,event_date,occurred_at,metadata,requires_explanation,explanation)
      values(me,event_id,dedupe,kind,day,(item->>'occurred_at')::timestamptz,
        coalesce(item->'metadata','{}'),coalesce((item->>'requires_explanation')::boolean,false),
        nullif(nullif(item->'explanation','""'::jsonb),'null'::jsonb))
      on conflict(user_id,dedupe_key) where dedupe_key<>'' do update set
        occurred_at=case when excluded.event_type='start_work' then least(public.lili_discipline_events.occurred_at,excluded.occurred_at)
          when excluded.event_type in ('daily_report','finish_work','early_finish','focus_shortfall','weekly_shortfall','rest_day','cancel_rest_day') then greatest(public.lili_discipline_events.occurred_at,excluded.occurred_at)
          else public.lili_discipline_events.occurred_at end,
        metadata=case when (excluded.event_type='start_work' and excluded.occurred_at<public.lili_discipline_events.occurred_at)
          or (excluded.event_type in ('daily_report','finish_work','early_finish','focus_shortfall','weekly_shortfall','rest_day','cancel_rest_day') and excluded.occurred_at>public.lili_discipline_events.occurred_at)
          then excluded.metadata else public.lili_discipline_events.metadata end,
        requires_explanation=case when excluded.event_type in ('early_finish','focus_shortfall','weekly_shortfall') and excluded.occurred_at>public.lili_discipline_events.occurred_at then excluded.requires_explanation else public.lili_discipline_events.requires_explanation end,
        explanation=coalesce(public.lili_discipline_events.explanation,excluded.explanation),
        updated_at=now();
      accepted:=accepted||jsonb_build_array(event_id);
    exception when invalid_text_representation or invalid_datetime_format or datetime_field_overflow or not_null_violation or unique_violation then
      -- Invalid rows get no ACK and remain durable in the client's queue.
      continue;
    end;
  end loop;
  -- Only changed work days need reconciliation. Idle runtime polls do not scan history.
  for day in select distinct unnest(affected_days) loop
    actual_clock:=public.lili_actual_work_start_clock(me,day);
    if actual_clock is not null then
      update public.lili_discipline_events e set
        metadata=e.metadata||jsonb_build_object('minutes_late',greatest(0,extract(epoch from
          actual_clock::time-substring(e.metadata->>'planned_start' from 12 for 5)::time)::int/60),
          'actual_start',actual_clock,'actual_start_source','first_real_start_after_0600'),
        requires_explanation=e.requires_explanation and greatest(0,extract(epoch from
          actual_clock::time-substring(e.metadata->>'planned_start' from 12 for 5)::time)::int/60)>=30
      where e.user_id=me and e.event_date=day and e.event_type='late_start'
        and substring(e.metadata->>'planned_start' from 12 for 5) ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$';
    end if;
  end loop;
  select coalesce(jsonb_agg(jsonb_build_object('id',e.id,'event_type',e.event_type,'event_date',e.event_date,
      'occurred_at',e.occurred_at,'metadata',e.metadata,'requires_explanation',e.requires_explanation,
      'explanation',e.explanation) order by e.sync_revision),'[]'),coalesce(max(e.sync_revision),next_revision)
    into rows,next_revision from (
      select * from public.lili_discipline_events where user_id=me and sync_revision>greatest(coalesce(p_after_revision,0),0)
      and event_type in ('start_work','late_start','long_break','finish_work','early_finish','focus_shortfall',
                        'weekly_shortfall','daily_report','rest_day','cancel_rest_day')
      order by sync_revision limit 100) e;
  select exists(select 1 from public.lili_discipline_events where user_id=me and sync_revision>next_revision
    and event_type in ('start_work','late_start','long_break','finish_work','early_finish','focus_shortfall',
                      'weekly_shortfall','daily_report','rest_day','cancel_rest_day')) into has_more;
  if not has_more then select greatest(next_revision,coalesce(max(sync_revision),0)) into next_revision
    from public.lili_discipline_events where user_id=me; end if;
  result:=jsonb_build_object('events',rows,'acknowledged_ids',accepted,'next_revision',next_revision,'has_more',has_more,
    'supervision',public.lili_discipline_runtime_snapshot(),
    'nudges',coalesce((select jsonb_agg(jsonb_build_object('id',n.id,'supervisor_id',n.supervisor_id,'kind',n.kind,'created_at',n.created_at))
      from public.lili_supervision_nudges n where n.owner_id=me and n.created_at>now()-interval '3 minutes'
        and not public.lili_is_rest_day(me,(now() at time zone 'Asia/Shanghai')::date)
        and (public.lili_supervision_permission(me,n.supervisor_id)->>'remind')::boolean),'[]'));
  if p_include_config or p_settings is not null then
    select settings,client_updated_at into remote_plan,remote_stamp from public.lili_discipline_settings where user_id=me;
    result:=result||jsonb_build_object('settings',coalesce(remote_plan,'{}'),'client_updated_at',remote_stamp,
      'rest_day_quota',public.lili_rest_day_quota());
  end if;
  return result;
end; $$;
revoke execute on function public.lili_discipline_sync_delta(jsonb,timestamptz,jsonb,bigint,boolean) from public,anon,authenticated;
grant execute on function public.lili_discipline_sync_delta(jsonb,timestamptz,jsonb,bigint,boolean) to authenticated;

-- Notification polling is independent from low-frequency subscription configuration.
create or replace function public.lili_buddy_reminder_events()
returns jsonb language plpgsql stable security definer set search_path='' as $$
declare me uuid:=(select auth.uid()); events jsonb;
begin
  if me is null then raise exception '请先登录'; end if;
  select coalesce(jsonb_agg(jsonb_build_object('id',e.id,'target_user_id',e.actor_id,
    'event_type',e.event_type,'occurred_at',e.occurred_at,'nickname',public.lili_owner_nickname(e.actor_id))
    order by e.occurred_at),'[]') into events from (
    select event_row.* from public.lili_work_events event_row
    join public.lili_buddy_subscriptions s on s.subscriber_id=me and s.buddy_id=event_row.actor_id
    where event_row.notify_eligible and event_row.created_at>now()-interval '3 minutes'
      and event_row.created_at>=s.updated_at and not s.muted
      and ((event_row.event_type='start_work' and s.on_focus_start) or (event_row.event_type='finish_work' and s.on_focus_end))
      and public.lili_are_buddies(me,event_row.actor_id)
    order by event_row.occurred_at desc limit 30) e;
  return jsonb_build_object('events',events,'server_timestamp',now());
end; $$;
revoke execute on function public.lili_buddy_reminder_events() from public,anon,authenticated;
grant execute on function public.lili_buddy_reminder_events() to authenticated;


-- v303: original FocusSession facts are authoritative; no historical backfill writes.
create index if not exists lili_focus_session_start_idx on public.lili_focus_segments(user_id,device_id,session_id,start_at);
create or replace function public.lili_actual_work_start_clock(p_owner_id uuid,p_day date)
returns text language sql stable security definer set search_path='' as $$
  with zone as (
    select coalesce((select (e.metadata->>'utc_offset_minutes')::int
      from public.lili_discipline_events e where e.user_id=p_owner_id and e.event_date=p_day
        and e.event_type='start_work' and e.metadata->>'utc_offset_minutes' ~ '^-?[0-9]{1,3}$'
        and (e.metadata->>'utc_offset_minutes')::int between -840 and 840
      order by e.occurred_at limit 1),480) as minutes
  ), bounds as (
    select minutes,(p_day::timestamp at time zone 'UTC')-make_interval(mins=>minutes) as lo,
      ((p_day+1)::timestamp at time zone 'UTC')-make_interval(mins=>minutes) as hi from zone
  ), seeds as (
    select distinct s.device_id,s.session_id,case when s.session_id='' then s.segment_id else '' end as independent
    from public.lili_focus_segments s,bounds b where s.user_id=p_owner_id and s.start_at>=b.lo and s.start_at<b.hi
      and s.end_at>=s.start_at and s.end_at-s.start_at<=interval '24 hours' and s.end_at<=now()+interval '2 minutes'
  ), facts as (
    select original.started from seeds seed cross join lateral (
      select min(s.start_at) as started from public.lili_focus_segments s where s.user_id=p_owner_id
        and s.device_id=seed.device_id and s.session_id=seed.session_id
        and (seed.independent='' or s.segment_id=seed.independent)
        and s.end_at>=s.start_at and s.end_at-s.start_at<=interval '24 hours' and s.end_at<=now()+interval '2 minutes'
    ) original
  ), explicit as (
    select e.occurred_at as started from public.lili_discipline_events e
    where e.user_id=p_owner_id and e.event_date=p_day and e.event_type='start_work'
      and e.metadata->>'actual_start_source'='explicit_focus_start' and e.occurred_at<=now()+interval '2 minutes'
      and coalesce(e.metadata->>'source','') not in ('sleep','lock','display_off','restart_safe_seal','account_switch','restore','heartbeat','startup','reconnect')
      and coalesce(e.metadata->>'restored','false')<>'true'
  ), starts as (select started from facts union all select started from explicit)
  select to_char((min(s.started) at time zone 'UTC')+make_interval(mins=>b.minutes),'HH24:MI')
    from bounds b left join starts s on s.started>=b.lo+interval '6 hours' and s.started<b.hi group by b.minutes;
$$;
revoke execute on function public.lili_actual_work_start_clock(uuid,date) from public,anon,authenticated;
