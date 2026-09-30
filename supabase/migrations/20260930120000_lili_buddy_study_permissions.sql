-- Explicit, owner-controlled viewing scopes, separate from presence and plan sync.
-- Existing consent allowed summary plans/lateness; keep that scope on upgrade.
alter table public.lili_discipline_supervisor_access
  add column if not exists view_plan boolean not null default true,
  add column if not exists view_reports boolean not null default true,
  add column if not exists view_lateness boolean not null default true;

create or replace function public.lili_set_discipline_permissions(
  p_supervisor_id uuid, p_view_plan boolean, p_view_reports boolean, p_view_lateness boolean
) returns jsonb language plpgsql security definer set search_path = '' as $$
declare me uuid := (select auth.uid());
begin
  if me is null then raise exception '请先登录'; end if;
  if p_view_plan is null or p_view_reports is null or p_view_lateness is null
    or not public.lili_are_buddies(me, p_supervisor_id) then raise exception '授权范围无效'; end if;
  update public.lili_discipline_supervisor_access a set
    view_plan = p_view_plan, view_reports = p_view_reports, view_lateness = p_view_lateness
  where a.owner_id = me and a.supervisor_id = p_supervisor_id;
  if not found then raise exception '请先完成双方监督授权'; end if;
  return jsonb_build_object('message', '授权范围已保存，其他设备将读取最新范围。');
end;
$$;

create or replace function public.lili_buddy_study_overview(p_buddy_id uuid)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare
  me uuid := (select auth.uid());
  own_access public.lili_discipline_supervisor_access;
  peer_access public.lili_discipline_supervisor_access;
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
  select * into own_access from public.lili_discipline_supervisor_access a where a.owner_id = me and a.supervisor_id = p_buddy_id;
  select * into peer_access from public.lili_discipline_supervisor_access a where a.owner_id = p_buddy_id and a.supervisor_id = me;
  if peer_access.owner_id is not null and peer_access.view_plan then
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
  return jsonb_build_object(
    'has_supervisor', exists(select 1 from public.lili_discipline_supervisor_access a where a.owner_id = me),
    'owned_access', case when own_access.owner_id is null then null else jsonb_build_object(
      'view_plan', own_access.view_plan, 'view_reports', own_access.view_reports, 'view_lateness', own_access.view_lateness) end,
    'supervising_access', case when peer_access.owner_id is null then null else jsonb_build_object(
      'view_plan', peer_access.view_plan, 'view_reports', peer_access.view_reports, 'view_lateness', peer_access.view_lateness) end,
    'incoming_request', (select jsonb_build_object('request_id', r.id) from public.lili_discipline_supervisor_requests r
      where r.owner_id = p_buddy_id and r.supervisor_id = me and r.status = 'pending'),
    'outgoing_status', (select r.status from public.lili_discipline_supervisor_requests r where r.owner_id = me and r.supervisor_id = p_buddy_id),
    'peer_plan', safe_plan,
    'can_read_reports', coalesce(peer_access.view_reports, false)
  );
end;
$$;

create or replace function public.lili_respond_discipline_supervisor(
  p_request_id uuid, p_accept boolean
) returns jsonb language plpgsql security definer set search_path = '' as $$
declare me uuid := (select auth.uid()); request_row public.lili_discipline_supervisor_requests;
begin
  if me is null then raise exception '请先登录'; end if;
  if p_request_id is null or p_accept is null then raise exception '授权申请不存在或已经处理'; end if;
  select * into request_row from public.lili_discipline_supervisor_requests r
    where r.id = p_request_id and r.supervisor_id = me and r.status = 'pending' for update;
  if not found then raise exception '授权申请不存在或已经处理'; end if;
  if not public.lili_are_buddies(me, request_row.owner_id) then
    raise exception '双方必须保持搭子关系才能授权';
  end if;
  if p_accept then
    perform pg_advisory_xact_lock(hashtext(request_row.owner_id::text));
    if exists(select 1 from public.lili_discipline_supervisor_access a
      where a.owner_id = request_row.owner_id and a.supervisor_id <> me) then
      raise exception '对方已经指定了另一位训导主任';
    end if;
    insert into public.lili_discipline_supervisor_access(owner_id, supervisor_id, request_id)
    values (request_row.owner_id, me, request_row.id)
    on conflict (owner_id) do update set supervisor_id = excluded.supervisor_id,
      request_id = excluded.request_id, granted_at = now();
  end if;
  update public.lili_discipline_supervisor_requests set
    status = case when p_accept then 'accepted' else 'rejected' end, updated_at = now()
  where id = request_row.id returning * into request_row;
  return jsonb_build_object('status', request_row.status,
    'message', case when p_accept then '已接受邀请；你现在可以查看对方授权的纪律摘要。' else '已拒绝授权申请。' end);
end;
$$;

create or replace function public.lili_discipline_supervisor_report(p_owner_id uuid)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare me uuid := (select auth.uid());
begin
  if me is null then raise exception '请先登录'; end if;
  if p_owner_id is null or not exists(select 1 from public.lili_discipline_supervisor_access a
    where a.owner_id = p_owner_id and a.supervisor_id = me and a.view_reports)
    or not public.lili_are_buddies(me, p_owner_id) then
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
      ) - case when (select a.view_lateness from public.lili_discipline_supervisor_access a where a.owner_id = p_owner_id and a.supervisor_id = me)
          then array[]::text[] else array['lateness_minutes', 'actual_start'] end
        - case when (select a.view_plan from public.lili_discipline_supervisor_access a where a.owner_id = p_owner_id and a.supervisor_id = me)
          then array[]::text[] else array['planned_start', 'planned_finish', 'daily_target_seconds', 'weekly_target_seconds', 'daily_gap_seconds', 'weekly_remaining_seconds'] end
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
    or not exists(select 1 from public.lili_discipline_supervisor_access a
      where a.owner_id = p_owner_id and a.supervisor_id = me and a.view_reports)
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


revoke execute on function public.lili_set_discipline_permissions(uuid,boolean,boolean,boolean) from public, anon;
revoke execute on function public.lili_buddy_study_overview(uuid) from public, anon;
grant execute on function public.lili_set_discipline_permissions(uuid,boolean,boolean,boolean) to authenticated;
grant execute on function public.lili_buddy_study_overview(uuid) to authenticated;
