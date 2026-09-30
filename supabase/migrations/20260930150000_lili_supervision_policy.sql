-- Owner eligibility is independent of presence, local rule settings and sessions.
-- Default off; a revision prevents a stale device from overwriting consent.
create table if not exists public.lili_supervision_policy (
  owner_id uuid primary key references auth.users(id) on delete cascade,
  enabled boolean not null default false,
  scope text not null default 'selected' check(scope in ('all','selected','invited')),
  selected_ids uuid[] not null default '{}',
  invited_ids uuid[] not null default '{}',
  officer_scope text not null default 'selected' check(officer_scope in ('all','selected')),
  officer_ids uuid[] not null default '{}',
  remind boolean not null default true,
  view_plan boolean not null default true,
  view_progress boolean not null default true,
  view_lateness boolean not null default true,
  view_rest boolean not null default true,
  view_reports boolean not null default true,
  revision bigint not null default 0,
  updated_at timestamptz not null default now()
);
create table if not exists public.lili_supervision_sessions (
  owner_id uuid not null references auth.users(id) on delete cascade,
  supervisor_id uuid not null references auth.users(id) on delete cascade,
  mode text not null check(mode in ('normal','officer')),
  active boolean not null default true,
  updated_at timestamptz not null default now(),
  primary key(owner_id,supervisor_id), check(owner_id <> supervisor_id)
);
create index if not exists lili_supervision_sessions_supervisor_idx
  on public.lili_supervision_sessions(supervisor_id,owner_id) where active;
create table if not exists public.lili_supervision_nudges (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  supervisor_id uuid not null references auth.users(id) on delete cascade,
  kind text not null check(kind in ('start','rest','finish')),
  created_at timestamptz not null default now()
);
create index if not exists lili_supervision_nudges_owner_idx on public.lili_supervision_nudges(owner_id,created_at desc);
alter table public.lili_supervision_policy enable row level security;
alter table public.lili_supervision_sessions enable row level security;
alter table public.lili_supervision_nudges enable row level security;
revoke all on public.lili_supervision_policy, public.lili_supervision_sessions, public.lili_supervision_nudges from public,anon,authenticated;

-- Keep the exact former recipient and field scopes. Never migrate to "all".
insert into public.lili_supervision_policy(owner_id,enabled,scope,selected_ids,officer_ids,view_plan,view_reports,view_lateness)
select a.owner_id,true,'selected',array[a.supervisor_id],
  case when s.settings->>'mode'='officer' then array[a.supervisor_id] else '{}'::uuid[] end,
  a.view_plan,a.view_reports,a.view_lateness
from public.lili_discipline_supervisor_access a left join public.lili_discipline_settings s on s.user_id=a.owner_id
on conflict(owner_id) do nothing;
insert into public.lili_supervision_sessions(owner_id,supervisor_id,mode,active)
select a.owner_id,a.supervisor_id,case when s.settings->>'mode'='officer' then 'officer' else 'normal' end,
  coalesce(s.settings->>'mode' in ('normal','officer'),false)
from public.lili_discipline_supervisor_access a left join public.lili_discipline_settings s on s.user_id=a.owner_id
on conflict(owner_id,supervisor_id) do nothing;

-- Private helper: caller cannot query arbitrary users' policy/allow-lists.
create or replace function public.lili_supervision_permission(p_owner uuid,p_supervisor uuid)
returns jsonb language plpgsql stable security definer set search_path='' as $$
declare p public.lili_supervision_policy; eligible boolean;
begin
  select * into p from public.lili_supervision_policy where owner_id=p_owner;
  eligible := coalesce(p.enabled and public.lili_are_buddies(p_owner,p_supervisor) and
    (p.scope='all' or (p.scope='selected' and p_supervisor=any(p.selected_ids)) or
      (p.scope='invited' and p_supervisor=any(p.invited_ids))),false);
  return jsonb_build_object('enabled',coalesce(p.enabled,false),'eligible',eligible,
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
    'supervisors',coalesce((select jsonb_agg(jsonb_build_object('supervisor_id',s.supervisor_id,'mode',s.mode))
      from public.lili_supervision_sessions s where s.owner_id=me and s.active
      and (public.lili_supervision_permission(me,s.supervisor_id)->>'eligible')::boolean),'[]'::jsonb));
end;
$$;

create or replace function public.lili_set_supervision_policy(p_policy jsonb,p_expected_revision bigint)
returns jsonb language plpgsql security definer set search_path='' as $$
declare me uuid:=(select auth.uid()); current_revision bigint; selected uuid[]; officers uuid[];
begin
  if me is null then raise exception '请先登录'; end if;
  if jsonb_typeof(p_policy)<>'object' or octet_length(p_policy::text)>32768 or p_expected_revision is null
    or coalesce(p_policy->>'scope','') not in ('all','selected','invited')
    or coalesce(p_policy->>'officer_scope','') not in ('all','selected') then raise exception '训导范围无效'; end if;
  insert into public.lili_supervision_policy(owner_id) values(me) on conflict do nothing;
  select revision into current_revision from public.lili_supervision_policy where owner_id=me for update;
  if current_revision<>p_expected_revision then raise exception '授权已在另一台电脑修改，请刷新后重试'; end if;
  select coalesce(array_agg(distinct value::uuid),'{}'::uuid[]) into selected
    from jsonb_array_elements_text(coalesce(p_policy->'selected_ids','[]'));
  select coalesce(array_agg(distinct value::uuid),'{}'::uuid[]) into officers
    from jsonb_array_elements_text(coalesce(p_policy->'officer_ids','[]'));
  if cardinality(selected)>200 or cardinality(officers)>200 or exists(
    select 1 from unnest(selected||officers) u where not public.lili_are_buddies(me,u)) then raise exception '只能指定已确认的搭子'; end if;
  update public.lili_supervision_policy set enabled=coalesce((p_policy->>'enabled')::boolean,false),
    scope=p_policy->>'scope',selected_ids=selected,officer_scope=p_policy->>'officer_scope',officer_ids=officers,
    remind=coalesce((p_policy->>'remind')::boolean,false),view_plan=coalesce((p_policy->>'view_plan')::boolean,false),
    view_progress=coalesce((p_policy->>'view_progress')::boolean,false),view_lateness=coalesce((p_policy->>'view_lateness')::boolean,false),
    view_rest=coalesce((p_policy->>'view_rest')::boolean,false),view_reports=coalesce((p_policy->>'view_reports')::boolean,false),
    revision=revision+1,updated_at=now() where owner_id=me;
  return public.lili_supervision_snapshot();
end;
$$;

create or replace function public.lili_invite_supervisor(p_buddy_id uuid,p_enabled boolean)
returns jsonb language plpgsql security definer set search_path='' as $$
declare me uuid:=(select auth.uid());
begin
  if me is null then raise exception '请先登录'; end if;
  if p_enabled is null or not public.lili_are_buddies(me,p_buddy_id) then raise exception '只能邀请已确认的搭子'; end if;
  perform 1 from public.lili_supervision_policy where owner_id=me and enabled for update;
  if not found then raise exception '请先开启允许搭子训导我'; end if;
  update public.lili_supervision_policy set invited_ids=case when p_enabled then
    array(select distinct unnest(invited_ids||array[p_buddy_id])) else array_remove(invited_ids,p_buddy_id) end,
    revision=revision+1,updated_at=now() where owner_id=me;
  return public.lili_supervision_snapshot();
end;
$$;

create or replace function public.lili_start_supervision(p_owner_id uuid,p_mode text)
returns jsonb language plpgsql security definer set search_path='' as $$
declare me uuid:=(select auth.uid()); permission jsonb;
begin
  if me is null then raise exception '请先登录'; end if;
  -- Serialize eligibility with changes to the owner's policy.
  perform 1 from public.lili_supervision_policy where owner_id=p_owner_id for update;
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

create or replace function public.lili_supervision_nudge(p_owner_id uuid,p_kind text)
returns jsonb language plpgsql security definer set search_path='' as $$
declare me uuid:=(select auth.uid());
begin
  if me is null then raise exception '请先登录'; end if;
  perform 1 from public.lili_supervision_policy where owner_id=p_owner_id for share;
  if not (public.lili_supervision_permission(p_owner_id,me)->>'remind')::boolean
    or p_kind is null or p_kind not in ('start','rest','finish') then raise exception 'TA 未开放搭子提醒'; end if;
  perform pg_advisory_xact_lock(hashtext(p_owner_id::text||me::text));
  if exists(select 1 from public.lili_supervision_nudges where owner_id=p_owner_id and supervisor_id=me
    and created_at>now()-interval '1 minute') then raise exception '刚刚提醒过，稍等一分钟'; end if;
  insert into public.lili_supervision_nudges(owner_id,supervisor_id,kind) values(p_owner_id,me,p_kind);
  return jsonb_build_object('message','轻提醒已发送');
end;
$$;

-- Old clients cannot reopen consent via their one-to-one approval workflow.
create or replace function public.lili_request_discipline_supervisor(p_supervisor_id uuid)
returns jsonb language plpgsql security definer set search_path='' as $$
begin return public.lili_invite_supervisor(p_supervisor_id,true); end; $$;
create or replace function public.lili_respond_discipline_supervisor(p_request_id uuid,p_accept boolean)
returns jsonb language plpgsql security definer set search_path='' as $$
begin raise exception '请升级六毛，在搭子自习室直接选择普通监督或军官监督'; end; $$;
create or replace function public.lili_revoke_discipline_supervisor()
returns jsonb language plpgsql security definer set search_path='' as $$
declare me uuid:=(select auth.uid());
begin
  if me is null then raise exception '请先登录'; end if;
  update public.lili_supervision_policy set enabled=false,revision=revision+1,updated_at=now() where owner_id=me;
  return jsonb_build_object('message','搭子训导已关闭，历史记录保留');
end; $$;
create or replace function public.lili_set_discipline_permissions(p_supervisor_id uuid,p_view_plan boolean,p_view_reports boolean,p_view_lateness boolean)
returns jsonb language plpgsql security definer set search_path='' as $$
begin raise exception '请升级六毛，在训导主任页统一设置允许查看的信息'; end; $$;
create or replace function public.lili_discipline_supervisor_snapshot()
returns jsonb language plpgsql stable security definer set search_path='' as $$
begin return public.lili_supervision_snapshot(); end; $$;

-- Generated whitelist-preserving RPCs; independently checked with real synthetic fixtures.
create or replace function public.lili_buddy_study_overview(p_buddy_id uuid)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare
  me uuid := (select auth.uid());
  own_permission jsonb;
  peer_permission jsonb;
  raw_plan jsonb;
  safe_plan jsonb;
  daily_targets jsonb := '{}'::jsonb;
  daily_starts jsonb := '{}'::jsonb;
  daily_finishes jsonb := '{}'::jsonb;
  day_key text;
  fallback_start text;
  fallback_finish text;
begin
  if me is null then raise exception '请先登录'; end if;
  if p_buddy_id is null or not public.lili_are_buddies(me, p_buddy_id) then raise exception '只能查看已确认搭子的自习室'; end if;
  own_permission := public.lili_supervision_permission(me,p_buddy_id);
  peer_permission := public.lili_supervision_permission(p_buddy_id,me);
  if (peer_permission->>'view_plan')::boolean then
    select s.settings into raw_plan from public.lili_discipline_settings s where s.user_id = p_buddy_id;
    if raw_plan is not null then
      fallback_start := case when raw_plan->>'start_time' ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$' then raw_plan->>'start_time' else '09:00' end;
      fallback_finish := case when raw_plan->>'finish_time' ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$' then raw_plan->>'finish_time' else '18:00' end;
      foreach day_key in array array['mon','tue','wed','thu','fri','sat','sun'] loop
        daily_targets := daily_targets || jsonb_build_object(day_key,
          case when raw_plan->'daily_target_minutes'->>day_key ~ '^[0-9]{1,4}$'
            then least(1440, (raw_plan->'daily_target_minutes'->>day_key)::integer)
            else case when day_key in ('sat','sun') then 0 else 360 end end);
        daily_starts := daily_starts || jsonb_build_object(day_key,
          case when raw_plan->'daily_start_times'->>day_key ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
            then raw_plan->'daily_start_times'->>day_key else fallback_start end);
        daily_finishes := daily_finishes || jsonb_build_object(day_key,
          case when raw_plan->'daily_finish_times'->>day_key ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
            then raw_plan->'daily_finish_times'->>day_key else fallback_finish end);
      end loop;
      safe_plan := jsonb_build_object('daily_target_minutes', daily_targets,
        'daily_start_times', daily_starts, 'daily_finish_times', daily_finishes,
        'weekly_target_minutes', case when raw_plan->>'weekly_target_minutes' ~ '^[0-9]{1,5}$'
          then least(10080,(raw_plan->>'weekly_target_minutes')::integer) else 1800 end);
    end if;
  end if;
  return jsonb_build_object('own_permission',own_permission,'peer_permission',peer_permission,
    'supervising_access',case when (peer_permission->>'eligible')::boolean then peer_permission else null end,
    'peer_plan',safe_plan,'can_read_reports',(peer_permission->>'view_reports')::boolean,
    'active_mode',(select case when s.mode='officer' and (peer_permission->>'officer')::boolean then 'officer' else 'normal' end
      from public.lili_supervision_sessions s where s.owner_id=p_buddy_id and s.supervisor_id=me and s.active
        and (peer_permission->>'eligible')::boolean),
    'own_scope',(select p.scope from public.lili_supervision_policy p where p.owner_id=me),
    'invited_by_me',exists(select 1 from public.lili_supervision_policy p where p.owner_id=me and p_buddy_id=any(p.invited_ids)));
end;
$$;
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
        'actual_start', case when e.metadata->>'actual_start' ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
          then e.metadata->>'actual_start' else null end,
        'actual_finish', case when e.metadata->>'actual_finish' ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
          then e.metadata->>'actual_finish' else null end,
        'planned_finish', case when e.metadata->>'planned_finish' ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
          then e.metadata->>'planned_finish' else null end,
        'mode', case when e.metadata->>'mode' in ('normal', 'officer')
          then e.metadata->>'mode' else 'off' end,
        'lateness_minutes', case when e.metadata->>'lateness_minutes' ~ '^[0-9]{1,5}$'
          then (e.metadata->>'lateness_minutes')::integer else 0 end,
        'today_seconds', case when e.metadata->>'today_seconds' ~ '^[0-9]{1,9}$'
          then (e.metadata->>'today_seconds')::integer else 0 end,
        'daily_target_seconds', case when e.metadata->>'daily_target_seconds' ~ '^[0-9]{1,9}$'
          then (e.metadata->>'daily_target_seconds')::integer else 0 end,
        'daily_gap_seconds', case when e.metadata->>'daily_gap_seconds' ~ '^[0-9]{1,9}$'
          then (e.metadata->>'daily_gap_seconds')::integer else 0 end,
        'long_break_count', case when e.metadata->>'long_break_count' ~ '^[0-9]{1,5}$'
          then (e.metadata->>'long_break_count')::integer else 0 end,
        'break_overtime_seconds', case when e.metadata->>'break_overtime_seconds' ~ '^[0-9]{1,9}$'
          then (e.metadata->>'break_overtime_seconds')::integer else 0 end,
        'weekly_target_seconds', case when e.metadata->>'weekly_target_seconds' ~ '^[0-9]{1,9}$'
          then (e.metadata->>'weekly_target_seconds')::integer else 0 end,
        'week_seconds', case when e.metadata->>'week_seconds' ~ '^[0-9]{1,9}$'
          then (e.metadata->>'week_seconds')::integer else 0 end,
        'weekly_remaining_seconds', case when e.metadata->>'weekly_remaining_seconds' ~ '^[0-9]{1,9}$'
          then (e.metadata->>'weekly_remaining_seconds')::integer else 0 end,
        'unexplained_count', case when e.metadata->>'unexplained_count' ~ '^[0-9]{1,5}$'
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
create or replace function public.lili_mark_discipline_report_read(
  p_owner_id uuid, p_report_date date
) returns jsonb language plpgsql security definer set search_path = '' as $$
declare me uuid := (select auth.uid()); read_stamp timestamptz;
begin
  if me is null then raise exception '请先登录'; end if;
  if p_owner_id is null or p_report_date is null
    or not (public.lili_supervision_permission(p_owner_id,me)->>'view_reports')::boolean
    or not public.lili_are_buddies(me, p_owner_id)
    or not exists(select 1 from public.lili_discipline_events e
      where e.user_id = p_owner_id and e.event_type = 'daily_report' and e.event_date = p_report_date) then
    raise exception '纪律摘要不存在或授权已撤销';
  end if;
  insert into public.lili_discipline_report_reads(owner_id, supervisor_id, report_date)
  values (p_owner_id, me, p_report_date)
  on conflict (owner_id, supervisor_id, report_date) do update set read_at = now()
  returning read_at into read_stamp;
  return jsonb_build_object('read_at', read_stamp);
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

  select s.settings, s.client_updated_at into remote_settings, remote_updated_at
  from public.lili_discipline_settings s where s.user_id = me;
  supervision := public.lili_supervision_snapshot();
  return jsonb_build_object(
    'supervision', supervision,
    'nudges',coalesce((select jsonb_agg(jsonb_build_object('id',n.id,'supervisor_id',n.supervisor_id,'kind',n.kind,'created_at',n.created_at))
      from public.lili_supervision_nudges n where n.owner_id=me and n.created_at>now()-interval '3 minutes'
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
revoke execute on function public.lili_supervision_permission(uuid,uuid) from public,anon,authenticated;
revoke execute on function public.lili_supervision_snapshot() from public,anon,authenticated;
grant execute on function public.lili_supervision_snapshot() to authenticated;
revoke execute on function public.lili_set_supervision_policy(jsonb,bigint) from public,anon,authenticated;
grant execute on function public.lili_set_supervision_policy(jsonb,bigint) to authenticated;
revoke execute on function public.lili_invite_supervisor(uuid,boolean) from public,anon,authenticated;
grant execute on function public.lili_invite_supervisor(uuid,boolean) to authenticated;
revoke execute on function public.lili_start_supervision(uuid,text) from public,anon,authenticated;
grant execute on function public.lili_start_supervision(uuid,text) to authenticated;
revoke execute on function public.lili_supervision_nudge(uuid,text) from public,anon,authenticated;
grant execute on function public.lili_supervision_nudge(uuid,text) to authenticated;
revoke execute on function public.lili_buddy_study_overview(uuid) from public,anon,authenticated;
grant execute on function public.lili_buddy_study_overview(uuid) to authenticated;
revoke execute on function public.lili_discipline_supervisor_report(uuid) from public,anon,authenticated;
grant execute on function public.lili_discipline_supervisor_report(uuid) to authenticated;
revoke execute on function public.lili_mark_discipline_report_read(uuid,date) from public,anon,authenticated;
grant execute on function public.lili_mark_discipline_report_read(uuid,date) to authenticated;
revoke execute on function public.lili_discipline_sync(jsonb,timestamptz,jsonb) from public,anon,authenticated;
grant execute on function public.lili_discipline_sync(jsonb,timestamptz,jsonb) to authenticated;
revoke execute on function public.lili_discipline_supervisor_snapshot() from public,anon,authenticated;
grant execute on function public.lili_discipline_supervisor_snapshot() to authenticated;
revoke execute on function public.lili_request_discipline_supervisor(uuid) from public,anon,authenticated;
grant execute on function public.lili_request_discipline_supervisor(uuid) to authenticated;
revoke execute on function public.lili_respond_discipline_supervisor(uuid,boolean) from public,anon,authenticated;
grant execute on function public.lili_respond_discipline_supervisor(uuid,boolean) to authenticated;
revoke execute on function public.lili_revoke_discipline_supervisor() from public,anon,authenticated;
grant execute on function public.lili_revoke_discipline_supervisor() to authenticated;
revoke execute on function public.lili_set_discipline_permissions(uuid,boolean,boolean,boolean) from public,anon,authenticated;
grant execute on function public.lili_set_discipline_permissions(uuid,boolean,boolean,boolean) to authenticated;
