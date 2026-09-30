-- Account-owned plan settings and append-only discipline history.
-- These tables are isolated from heartbeat, presence, and work-state payloads.

create table if not exists public.lili_discipline_settings (
  user_id uuid primary key references auth.users(id) on delete cascade,
  settings jsonb not null default '{}'::jsonb,
  client_updated_at timestamptz not null default '-infinity'::timestamptz,
  updated_at timestamptz not null default now()
);

create table if not exists public.lili_discipline_events (
  user_id uuid not null references auth.users(id) on delete cascade,
  id uuid not null,
  dedupe_key text not null default '',
  event_type text not null,
  event_date date not null,
  occurred_at timestamptz not null,
  metadata jsonb not null default '{}'::jsonb,
  requires_explanation boolean not null default false,
  explanation jsonb,
  updated_at timestamptz not null default now(),
  primary key (user_id, id)
);

create unique index if not exists lili_discipline_events_dedupe_idx
  on public.lili_discipline_events(user_id, dedupe_key)
  where dedupe_key <> '';
create index if not exists lili_discipline_events_recent_idx
  on public.lili_discipline_events(user_id, occurred_at desc);

-- Supervisor consent is a separate, explicit relationship. Access is only
-- created by the recipient accepting a request and is checked on every read.
create table if not exists public.lili_discipline_supervisor_requests (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  supervisor_id uuid not null references auth.users(id) on delete cascade,
  status text not null default 'pending'
    check (status in ('pending', 'accepted', 'rejected', 'revoked')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(owner_id, supervisor_id),
  check (owner_id <> supervisor_id)
);

create table if not exists public.lili_discipline_supervisor_access (
  owner_id uuid primary key references auth.users(id) on delete cascade,
  supervisor_id uuid not null references auth.users(id) on delete cascade,
  request_id uuid not null unique references public.lili_discipline_supervisor_requests(id) on delete cascade,
  granted_at timestamptz not null default now(),
  check (owner_id <> supervisor_id)
);

create table if not exists public.lili_discipline_report_reads (
  owner_id uuid not null references auth.users(id) on delete cascade,
  supervisor_id uuid not null references auth.users(id) on delete cascade,
  report_date date not null,
  read_at timestamptz not null default now(),
  primary key(owner_id, supervisor_id, report_date),
  check (owner_id <> supervisor_id)
);

alter table public.lili_discipline_settings enable row level security;
alter table public.lili_discipline_events enable row level security;
alter table public.lili_discipline_supervisor_requests enable row level security;
alter table public.lili_discipline_supervisor_access enable row level security;
alter table public.lili_discipline_report_reads enable row level security;
revoke all on public.lili_discipline_settings, public.lili_discipline_events,
  public.lili_discipline_supervisor_requests, public.lili_discipline_supervisor_access,
  public.lili_discipline_report_reads
  from public, anon, authenticated;

-- The RPC synchronizes an account's settings and idempotently appends events.
-- A stale device cannot overwrite newer settings; event dedupe keys fence
-- repeated scheduled rules across multiple signed-in computers.
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
  return jsonb_build_object(
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

create or replace function public.lili_request_discipline_supervisor(p_supervisor_id uuid)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare me uuid := (select auth.uid()); request_row public.lili_discipline_supervisor_requests;
begin
  if me is null then raise exception '请先登录'; end if;
  if p_supervisor_id is null or p_supervisor_id = me
    or not public.lili_are_buddies(me, p_supervisor_id) then
    raise exception '只能邀请已确认的搭子担任训导主任';
  end if;
  if exists(select 1 from public.lili_discipline_supervisor_access a where a.owner_id = me) then
    raise exception '请先撤销当前训导主任授权';
  end if;
  insert into public.lili_discipline_supervisor_requests(owner_id, supervisor_id)
  values (me, p_supervisor_id)
  on conflict (owner_id, supervisor_id) do update set
    status = 'pending', created_at = now(), updated_at = now()
  returning * into request_row;
  return jsonb_build_object('request_id', request_row.id, 'status', request_row.status,
    'message', '授权申请已发送；对方同意后才能查看纪律摘要。');
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
    'message', case when p_accept then '已授权；对方现在可以查看你的纪律摘要。' else '已拒绝授权申请。' end);
end;
$$;

create or replace function public.lili_revoke_discipline_supervisor()
returns jsonb language plpgsql security definer set search_path = '' as $$
declare me uuid := (select auth.uid());
begin
  if me is null then raise exception '请先登录'; end if;
  update public.lili_discipline_supervisor_requests r set status = 'revoked', updated_at = now()
  where r.owner_id = me and r.status = 'accepted';
  delete from public.lili_discipline_supervisor_access a where a.owner_id = me;
  return jsonb_build_object('message', '训导主任授权已撤销。');
end;
$$;

create or replace function public.lili_discipline_supervisor_snapshot()
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare me uuid := (select auth.uid());
begin
  if me is null then raise exception '请先登录'; end if;
  return jsonb_build_object(
    'owned', (select jsonb_build_object('supervisor_id', a.supervisor_id,
      'supervisor_nickname', public.lili_owner_nickname(a.supervisor_id), 'granted_at', a.granted_at)
      from public.lili_discipline_supervisor_access a
      where a.owner_id = me and public.lili_are_buddies(me, a.supervisor_id)),
    'incoming', coalesce((select jsonb_agg(jsonb_build_object(
      'request_id', r.id, 'owner_id', r.owner_id,
      'owner_nickname', public.lili_owner_nickname(r.owner_id), 'created_at', r.created_at)
      order by r.created_at)
      from public.lili_discipline_supervisor_requests r
      where r.supervisor_id = me and r.status = 'pending'
        and public.lili_are_buddies(me, r.owner_id)), '[]'::jsonb),
    'supervising', coalesce((select jsonb_agg(jsonb_build_object(
      'owner_id', a.owner_id, 'owner_nickname', public.lili_owner_nickname(a.owner_id),
      'granted_at', a.granted_at)
      order by public.lili_owner_nickname(a.owner_id))
      from public.lili_discipline_supervisor_access a
      where a.supervisor_id = me and public.lili_are_buddies(me, a.owner_id)), '[]'::jsonb),
    'outgoing_status', (select case r.status
      when 'pending' then '授权申请等待对方回应。'
      when 'rejected' then '对方拒绝了授权申请。'
      when 'revoked' then '授权已撤销。'
      else null end
      from public.lili_discipline_supervisor_requests r
      where r.owner_id = me order by r.updated_at desc limit 1)
  );
end;
$$;

create or replace function public.lili_discipline_supervisor_report(p_owner_id uuid)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare me uuid := (select auth.uid());
begin
  if me is null then raise exception '请先登录'; end if;
  if p_owner_id is null or not exists(select 1 from public.lili_discipline_supervisor_access a
    where a.owner_id = p_owner_id and a.supervisor_id = me)
    or not public.lili_are_buddies(me, p_owner_id) then
    raise exception '你没有查看这位用户纪律摘要的授权';
  end if;
  return jsonb_build_object('reports', coalesce((
    select jsonb_agg(jsonb_build_object('event_date', e.event_date,
      -- Supervisors receive only bounded, allow-listed summary fields. The
      -- owner controls stored JSON, so never return the original metadata.
      'metadata', jsonb_build_object(
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
      where a.owner_id = p_owner_id and a.supervisor_id = me)
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

revoke execute on function public.lili_discipline_sync(jsonb,timestamptz,jsonb) from public, anon;
grant execute on function public.lili_discipline_sync(jsonb,timestamptz,jsonb) to authenticated;
revoke execute on function public.lili_request_discipline_supervisor(uuid) from public, anon;
revoke execute on function public.lili_respond_discipline_supervisor(uuid,boolean) from public, anon;
revoke execute on function public.lili_revoke_discipline_supervisor() from public, anon;
revoke execute on function public.lili_discipline_supervisor_snapshot() from public, anon;
revoke execute on function public.lili_discipline_supervisor_report(uuid) from public, anon;
revoke execute on function public.lili_mark_discipline_report_read(uuid,date) from public, anon;
grant execute on function public.lili_request_discipline_supervisor(uuid) to authenticated;
grant execute on function public.lili_respond_discipline_supervisor(uuid,boolean) to authenticated;
grant execute on function public.lili_revoke_discipline_supervisor() to authenticated;
grant execute on function public.lili_discipline_supervisor_snapshot() to authenticated;
grant execute on function public.lili_discipline_supervisor_report(uuid) to authenticated;
grant execute on function public.lili_mark_discipline_report_read(uuid,date) to authenticated;
