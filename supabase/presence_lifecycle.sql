-- Presence belongs to the application process; activity belongs to focus.
-- Extend the existing per-device ledger, preserving sequence and proof guards.
-- Activity is the user's timer state, not the keyboard-input proof used for rewards.
alter table public.lili_focus_device_presence add column if not exists presence_state text not null default 'online' check (presence_state in ('online','offline'));
alter table public.lili_focus_device_presence add column if not exists activity_state text check (activity_state in ('focus','rest','idle'));
alter table public.lili_focus_presence add column if not exists presence_state text not null default 'online' check (presence_state in ('online','offline'));
alter table public.lili_focus_presence add column if not exists activity_state text check (activity_state in ('focus','rest','idle'));

create or replace function public.lili_device_lifecycle_fields() returns trigger
language plpgsql security definer set search_path='' as $$
declare state text:=nullif(current_setting('lili.presence_state',true),'');
  activity text:=nullif(current_setting('lili.activity_state',true),'');
begin
  new.presence_state:=coalesce(state,'online');
  new.activity_state:=case when new.presence_state='offline' then 'idle'
    when activity='focus' or (new.working and new.session_active) then 'focus'
    when activity='rest' or activity is null then 'rest' else 'idle' end;
  return new;
end $$;
drop trigger if exists lili_device_lifecycle_fields on public.lili_focus_device_presence;
create trigger lili_device_lifecycle_fields before insert or update of working,session_active,presence_sequence
on public.lili_focus_device_presence for each row execute function public.lili_device_lifecycle_fields();
revoke all on function public.lili_device_lifecycle_fields() from public,anon,authenticated;

create or replace function public.lili_account_lifecycle_fields() returns trigger
language plpgsql security definer set search_path='' as $$
declare n integer; active integer; resting integer;
begin
  select count(*),count(*) filter(where activity_state='focus'),count(*) filter(where activity_state='rest')
  into n,active,resting from public.lili_focus_device_presence
  where user_id=new.user_id and presence_state='online' and last_seen>now()-interval '2 minutes';
  new.presence_state:=case when n>0 then 'online' else 'offline' end;
  new.activity_state:=case when active>0 then 'focus' when resting>0 then 'rest' else 'idle' end;
  return new;
end $$;
drop trigger if exists lili_account_lifecycle_fields on public.lili_focus_presence;
create trigger lili_account_lifecycle_fields before insert or update of working,session_active,presence_sequence
on public.lili_focus_presence for each row execute function public.lili_account_lifecycle_fields();
revoke all on function public.lili_account_lifecycle_fields() from public,anon,authenticated;

create or replace function public.lili_upsert_focus_presence_core(
  p_working boolean,
  p_session_active boolean,
  p_session_id text,
  p_session_started_at timestamptz,
  p_device_id text,
  p_sequence bigint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  me uuid := (select auth.uid());
  clean_active boolean;
  clean_session_id text;
  clean_started_at timestamptz;
  clean_device_id text := left(
    coalesce(nullif(btrim(coalesce(p_device_id, '')), ''), 'legacy-rpc'),
    120
  );
  incoming_sequence bigint := greatest(1, coalesce(p_sequence, 0));
  accepted boolean := false;
  device_row public.lili_focus_device_presence;
  account_before public.lili_focus_presence;
  account_row public.lili_focus_presence;
  representative public.lili_focus_device_presence;
  latest_device public.lili_focus_device_presence;
  active_device_count integer := 0;
  working_device_count integer := 0;
  account_sequence bigint := 1;
  account_session_id text;
  account_session_started_at timestamptz;
  account_device_id text;
begin
  if me is null then
    raise exception 'authentication required';
  end if;

  clean_active := coalesce(p_working, false)
    and coalesce(p_session_active, false)
    and nullif(btrim(coalesce(p_session_id, '')), '') is not null
    and p_session_started_at is not null;
  clean_session_id := case
    when clean_active then left(btrim(p_session_id), 160)
    else null
  end;
  clean_started_at := case when clean_active then p_session_started_at else null end;

  -- Serialize the aggregate for one account while retaining an independent
  -- monotonic sequence fence for each installation.
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(me::text, 0)
  );

  insert into public.lili_focus_device_presence(
    user_id,
    device_id,
    working,
    session_active,
    session_id,
    session_started_at,
    presence_sequence,
    last_seen,
    updated_at
  ) values (
    me,
    clean_device_id,
    clean_active,
    clean_active,
    clean_session_id,
    clean_started_at,
    incoming_sequence,
    now(),
    now()
  )
  on conflict (user_id, device_id) do update set
    working = excluded.working,
    session_active = excluded.session_active,
    session_id = excluded.session_id,
    session_started_at = case
      when public.lili_focus_device_presence.session_active
       and excluded.session_active
       and public.lili_focus_device_presence.session_id = excluded.session_id
       and public.lili_focus_device_presence.session_started_at is not null
        then public.lili_focus_device_presence.session_started_at
      else excluded.session_started_at
    end,
    presence_sequence = excluded.presence_sequence,
    last_seen = now(),
    updated_at = now()
  where excluded.presence_sequence > public.lili_focus_device_presence.presence_sequence
  returning * into device_row;

  accepted := found;
  if not accepted then
    select * into device_row
    from public.lili_focus_device_presence d
    where d.user_id = me and d.device_id = clean_device_id;
    select * into account_before
    from public.lili_focus_presence f
    where f.user_id = me;
    return jsonb_build_object(
      'accepted', false,
      'user_id', me,
      'device_id', clean_device_id,
      'device_online', device_row.presence_state = 'online' and device_row.last_seen > now() - interval '2 minutes',
      'device_working', coalesce(device_row.working, false),
      'account_online', coalesce(account_before.presence_state = 'online' and account_before.last_seen > now() - interval '2 minutes', false),
      'account_working', coalesce(account_before.working, false),
      'working', coalesce(account_before.working, false),
      'session_active', coalesce(account_before.session_active, false),
      'session_id', account_before.session_id,
      'session_started_at', account_before.session_started_at,
      'sequence', coalesce(device_row.presence_sequence, 0),
      'account_sequence', coalesce(account_before.presence_sequence, 0),
      'server_timestamp', now()
    );
  end if;

  delete from public.lili_focus_device_presence d
  where d.user_id = me
    and d.last_seen <= now() - interval '7 days';

  select
    count(*)::integer,
    count(*) filter (where d.working and d.session_active)::integer
  into active_device_count, working_device_count
  from public.lili_focus_device_presence d
  where d.user_id = me
    and d.presence_state = 'online'
    and d.last_seen > now() - interval '2 minutes';

  select * into representative
  from public.lili_focus_device_presence d
  where d.user_id = me
    and d.presence_state = 'online'
    and d.last_seen > now() - interval '2 minutes'
    and d.working
    and d.session_active
  order by d.session_started_at, d.device_id
  limit 1;

  select * into latest_device
  from public.lili_focus_device_presence d
  where d.user_id = me
    and d.presence_state = 'online'
    and d.last_seen > now() - interval '2 minutes'
  order by d.last_seen desc, d.device_id
  limit 1;

  select * into account_before
  from public.lili_focus_presence f
  where f.user_id = me
  for update;

  account_sequence := greatest(
    incoming_sequence,
    coalesce(account_before.presence_sequence, 0) + 1
  );
  if working_device_count > 0 then
    -- Keep one account-level live episode continuous while devices overlap.
    -- Switching the representative device must not split room presence or
    -- create a second account session.
    if account_before.user_id is not null
       and account_before.working
       and account_before.session_active
       and account_before.last_seen > now() - interval '2 minutes' then
      account_session_id := account_before.session_id;
      account_session_started_at := account_before.session_started_at;
      account_device_id := account_before.device_id;
    else
      account_session_id := representative.session_id;
      account_session_started_at := representative.session_started_at;
      account_device_id := representative.device_id;
    end if;
  else
    account_session_id := null;
    account_session_started_at := null;
    account_device_id := coalesce(latest_device.device_id, clean_device_id);
  end if;

  insert into public.lili_focus_presence(
    user_id,
    working,
    session_active,
    session_id,
    session_started_at,
    device_id,
    device_claim,
    presence_sequence,
    work_state,
    pause_reason,
    last_seen,
    updated_at
  ) values (
    me,
    working_device_count > 0,
    working_device_count > 0,
    account_session_id,
    account_session_started_at,
    account_device_id,
    false,
    account_sequence,
    case when working_device_count > 0 then 'working' else 'idle' end,
    null,
    now(),
    now()
  )
  on conflict (user_id) do update set
    working = excluded.working,
    session_active = excluded.session_active,
    session_id = excluded.session_id,
    session_started_at = excluded.session_started_at,
    device_id = excluded.device_id,
    device_claim = false,
    presence_sequence = excluded.presence_sequence,
    work_state = excluded.work_state,
    pause_reason = null,
    last_seen = now(),
    updated_at = now()
  returning * into account_row;

  return jsonb_build_object(
    'accepted', true,
    'user_id', me,
    'device_id', clean_device_id,
    'device_online', device_row.presence_state = 'online',
    'device_working', device_row.working,
    'device_session_active', device_row.session_active,
    'device_session_id', device_row.session_id,
    'account_online', active_device_count > 0,
    'account_working', account_row.working,
    'active_device_count', active_device_count,
    'working_device_count', working_device_count,
    -- Existing clients treat these fields as the account compatibility tuple.
    'working', account_row.working,
    'session_active', account_row.session_active,
    'session_id', account_row.session_id,
    'session_started_at', account_row.session_started_at,
    'last_seen_at', account_row.last_seen,
    -- Sequence remains device-scoped so a restarted client can adopt/retry.
    'sequence', device_row.presence_sequence,
    'account_sequence', account_row.presence_sequence,
    'server_timestamp', now()
  );
end;
$$;


revoke all on function public.lili_upsert_focus_presence_core(boolean,boolean,text,timestamptz,text,bigint) from public,anon,authenticated;

create or replace function public.lili_presence_heartbeat(
  p_working boolean,p_session_active boolean,p_session_id text,p_session_started_at timestamptz,
  p_device_id text,p_sequence bigint,p_input_idle_seconds integer,p_presence_state text,p_activity_state text
) returns jsonb language plpgsql security definer set search_path='' as $$
declare result jsonb;
begin
  if auth.uid() is null then raise exception 'authentication required'; end if;
  if p_presence_state is null or p_presence_state not in ('online','offline')
     or p_activity_state is null or p_activity_state not in ('focus','rest','idle')
     or nullif(btrim(p_device_id),'') is null then raise exception 'invalid presence state'; end if;
  perform set_config('lili.presence_state',p_presence_state,true);
  perform set_config('lili.activity_state',case
    when p_activity_state='focus' and not (coalesce(p_working,false) and coalesce(p_session_active,false)
      and nullif(btrim(p_session_id),'') is not null and p_session_started_at is not null) then 'rest'
    else p_activity_state end,true);
  result:=public.lili_upsert_focus_presence_v2(
    p_presence_state='online' and p_activity_state='focus' and coalesce(p_working,false),
    p_presence_state='online' and p_activity_state='focus' and coalesce(p_session_active,false),
    p_session_id,p_session_started_at,p_device_id,p_sequence,p_input_idle_seconds);
  perform set_config('lili.presence_state','',true);
  perform set_config('lili.activity_state','',true);
  return result;
end $$;
revoke all on function public.lili_presence_heartbeat(boolean,boolean,text,timestamptz,text,bigint,integer,text,text) from public,anon;
grant execute on function public.lili_presence_heartbeat(boolean,boolean,text,timestamptz,text,bigint,integer,text,text) to authenticated;

-- One batch aggregate for only the identities already authorized by the dashboard.
-- No arbitrary client user ids, no sender/profile/config downloads, no N+1 HTTP.
create or replace function public.lili_presence_display_rows(p_rows jsonb) returns jsonb
language sql stable security definer set search_path='' as $$
with items as (
  select e,ord,(e->>'user_id')::uuid uid from jsonb_array_elements(coalesce(p_rows,'[]')) with ordinality a(e,ord)
), live as (
  select d.user_id,max(d.last_seen) last_seen,
    count(*) filter(where d.presence_state='online' and d.last_seen>now()-interval '2 minutes') n,
    count(*) filter(where d.presence_state='online' and d.last_seen>now()-interval '2 minutes' and
      coalesce(d.activity_state,case when d.working and d.session_active then 'focus' else 'rest' end)='focus') focused,
    count(*) filter(where d.presence_state='online' and d.last_seen>now()-interval '2 minutes' and
      coalesce(d.activity_state,case when d.working then 'focus' else 'rest' end)='rest') resting
  from public.lili_focus_device_presence d
  where d.user_id in (select uid from items) group by d.user_id
), states as (
  select i.*,l.last_seen,
    (coalesce(l.n,0)>0 and (i.uid=auth.uid() or p.visibility='friends')) is_online,
    case when coalesce(l.focused,0)>0 then 'focus' when coalesce(l.resting,0)>0 then 'rest' else 'idle' end activity,
    (i.uid=auth.uid() or p.visibility='friends') visible
  from items i left join live l on l.user_id=i.uid left join public.lili_profiles p on p.user_id=i.uid
)
select coalesce(jsonb_agg(e || jsonb_build_object(
  'presence_state',case when is_online then 'online' else 'offline' end,
  'activity_state',case when is_online then activity else 'idle' end,
  'online',is_online,'working',is_online and activity='focus',
  'session_active',is_online and activity='focus',
  'status',case when not is_online then 'offline' when activity='idle' then 'online' else activity end,
  'last_seen_at',case when visible then last_seen else null end
) order by ord),'[]') from states;
$$;
revoke all on function public.lili_presence_display_rows(jsonb) from public,anon,authenticated;

create or replace function public.lili_presence_display_dashboard(p_data jsonb) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare result jsonb:=p_data; field text; sub jsonb;
begin
  foreach field in array array['buddies','room_people','active_visits'] loop
    if jsonb_typeof(result->field)='array' then
      result:=jsonb_set(result,array[field],public.lili_presence_display_rows(result->field));
    end if;
  end loop;
  if jsonb_typeof(result->'me_presence')='object' then
    sub:=public.lili_presence_display_rows(jsonb_build_array((result->'me_presence') || jsonb_build_object('user_id',auth.uid())));
    result:=jsonb_set(result,'{me_presence}',sub->0);
  end if;
  if jsonb_typeof(result->'current_room')='object' then
    foreach field in array array['room_people','active_visits'] loop
      if jsonb_typeof(result->'current_room'->field)='array' then
        result:=jsonb_set(result,array['current_room',field],public.lili_presence_display_rows(result->'current_room'->field));
      end if;
    end loop;
  end if;
  return result;
end $$;
revoke all on function public.lili_presence_display_dashboard(jsonb) from public,anon,authenticated;

do $$ begin
  if to_regprocedure('public.lili_dashboard_lifecycle_base()') is null then
    alter function public.lili_dashboard() rename to lili_dashboard_lifecycle_base;
  elsif position('lili_presence_display_dashboard' in pg_get_functiondef('public.lili_dashboard()'::regprocedure))=0 then
    -- A replayed plan migration refreshed its wrapper: capture that new base.
    execute replace(pg_get_functiondef('public.lili_dashboard()'::regprocedure),
      'public.lili_dashboard()', 'public.lili_dashboard_lifecycle_base()');
  end if;
  if to_regprocedure('public.lili_room_dashboard_lifecycle_base(uuid)') is null then
    alter function public.lili_room_dashboard(uuid) rename to lili_room_dashboard_lifecycle_base;
  end if;
end $$;
revoke all on function public.lili_dashboard_lifecycle_base() from public,anon,authenticated;
revoke all on function public.lili_room_dashboard_lifecycle_base(uuid) from public,anon,authenticated;
create or replace function public.lili_dashboard() returns jsonb language plpgsql security definer set search_path='' as $$
begin
  if auth.uid() is null then raise exception 'authentication required'; end if;
  return public.lili_delivery_dashboard(public.lili_presence_display_dashboard(public.lili_dashboard_lifecycle_base()));
end $$;
create or replace function public.lili_room_dashboard(p_room_id uuid) returns jsonb language plpgsql security definer set search_path='' as $$
begin
  if auth.uid() is null then raise exception 'authentication required'; end if;
  return public.lili_delivery_dashboard(public.lili_presence_display_dashboard(public.lili_room_dashboard_lifecycle_base(p_room_id)));
end $$;
revoke all on function public.lili_dashboard() from public,anon;
revoke all on function public.lili_room_dashboard(uuid) from public,anon;
grant execute on function public.lili_dashboard() to authenticated;
grant execute on function public.lili_room_dashboard(uuid) to authenticated;
notify pgrst,'reload schema';
