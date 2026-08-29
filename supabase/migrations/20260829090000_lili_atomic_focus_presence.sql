-- Keep the live presence row internally consistent and update it in one
-- authenticated transaction.  The desktop heartbeat is a transport signal;
-- it must never leave a combination such as session_active=true,
-- working=false, session_started_at=NULL behind after a partial request.

create or replace function public.lili_touch_presence_server_timestamp()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  new.last_seen := now();
  new.updated_at := now();

  -- Keep a client-provided start time for the current live episode.  Older
  -- deployments replaced it with ``now()`` whenever the room ledger was not
  -- present, which made a peer's elapsed time jump backwards on reconnect.
  if coalesce(new.working, false)
     and coalesce(new.session_active, false)
     and new.session_started_at is not null then
    if new.session_started_at > now() + interval '2 minutes' then
      new.session_started_at := now();
    end if;
  else
    new.session_started_at := null;
  end if;
  return new;
end;
$$;

drop trigger if exists lili_presence_server_timestamp on public.lili_focus_presence;
create trigger lili_presence_server_timestamp
before insert or update on public.lili_focus_presence
for each row execute function public.lili_touch_presence_server_timestamp();

create or replace function public.lili_upsert_focus_presence(
  p_working boolean,
  p_session_active boolean,
  p_work_state text,
  p_pause_reason text,
  p_session_started_at timestamptz,
  p_focus_date date,
  p_today_seconds integer,
  p_outfit_key text,
  p_room_id uuid,
  p_quick_status text,
  p_quick_status_expires_at timestamptz,
  p_device_id text,
  p_device_claim boolean
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  me uuid := (select auth.uid());
  clean_working boolean;
  clean_active boolean;
  clean_started timestamptz;
  clean_room uuid;
  clean_date date := ((now() at time zone 'Asia/Shanghai')::date);
  clean_today integer;
  clean_state text;
  clean_pause text;
  result_row public.lili_focus_presence;
begin
  if me is null then
    raise exception '请先登录';
  end if;

  -- A live row is valid only when it has the complete live-session tuple.
  -- This also makes malformed/old clients fail closed instead of publishing
  -- a working presence that cannot be used for elapsed-time projection.
  clean_working := coalesce(p_working, false) and p_session_started_at is not null;
  clean_active := clean_working and coalesce(p_session_active, false);
  clean_started := case when clean_active then p_session_started_at else null end;

  clean_state := case
    when clean_active then 'working'
    when not clean_working and coalesce(p_work_state, '') in
      ('paused_manual', 'paused_idle', 'paused_lock', 'paused_sleep', 'paused_video')
      then coalesce(p_work_state, 'paused_manual')
    else 'idle'
  end;
  clean_pause := case when clean_state = 'idle' then null else left(btrim(coalesce(p_pause_reason, '')), 32) end;

  -- Never let an unverified room UUID be written by a security-definer RPC.
  clean_room := case
    when p_room_id is not null and exists (
      select 1 from public.lili_room_members m
      where m.room_id = p_room_id and m.user_id = me
    ) then p_room_id
    else null
  end;

  clean_today := case
    when p_focus_date = clean_date
      then greatest(0, least(86400, coalesce(p_today_seconds, 0)))
    else 0
  end;

  insert into public.lili_focus_presence(
    user_id, working, session_started_at, focus_date, today_seconds,
    outfit_key, room_id, last_seen, updated_at, quick_status,
    quick_status_expires_at, session_active, work_state, pause_reason,
    device_id, device_claim
  ) values (
    me, clean_working, clean_started, clean_date, clean_today,
    left(coalesce(p_outfit_key, ''), 60), clean_room, now(), now(),
    left(coalesce(p_quick_status, ''), 40), p_quick_status_expires_at,
    clean_active, clean_state, clean_pause,
    left(coalesce(p_device_id, ''), 120), coalesce(p_device_claim, false)
  )
  on conflict (user_id) do update set
    working = excluded.working,
    session_started_at = excluded.session_started_at,
    focus_date = excluded.focus_date,
    today_seconds = excluded.today_seconds,
    outfit_key = excluded.outfit_key,
    room_id = excluded.room_id,
    last_seen = excluded.last_seen,
    updated_at = excluded.updated_at,
    quick_status = excluded.quick_status,
    quick_status_expires_at = excluded.quick_status_expires_at,
    session_active = excluded.session_active,
    work_state = excluded.work_state,
    pause_reason = excluded.pause_reason,
    device_id = excluded.device_id,
    device_claim = excluded.device_claim
  returning * into result_row;

  return jsonb_build_object(
    'user_id', result_row.user_id,
    'working', result_row.working,
    'session_active', result_row.session_active,
    'session_started_at', result_row.session_started_at,
    'last_seen', result_row.last_seen,
    'server_timestamp', now()
  );
end;
$$;

revoke execute on function public.lili_upsert_focus_presence(
  boolean, boolean, text, text, timestamptz, date, integer, text, uuid,
  text, timestamptz, text, boolean
) from public, anon;
grant execute on function public.lili_upsert_focus_presence(
  boolean, boolean, text, text, timestamptz, date, integer, text, uuid,
  text, timestamptz, text, boolean
) to authenticated;


