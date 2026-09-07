-- Expose a small, account-scoped read projection for the current focus episode
-- on every device.  FocusSession/focus_segments remain the immutable history;
-- this table is only liveness and this RPC is only for the two live display
-- surfaces.

alter table public.lili_focus_device_presence
  add column if not exists last_session_id text,
  add column if not exists last_session_started_at timestamptz,
  add column if not exists last_session_ended_at timestamptz;

create or replace function public.lili_capture_focus_device_projection_stop()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  -- Preserve the just-finished live projection for a short hand-off window.
  -- The following closed FocusSegment will replace it in the interval union;
  -- this bridge prevents the display from dropping while delta sync is in
  -- flight.  No FocusSession or focus_segments row is touched here.
  if tg_op = 'UPDATE'
     and old.working
     and old.session_active
     and old.session_id is not null
     and old.session_started_at is not null
     and not (
       new.working
       and new.session_active
       and new.session_id is not null
       and new.session_started_at is not null
     ) then
    new.last_session_id := old.session_id;
    new.last_session_started_at := old.session_started_at;
    new.last_session_ended_at := now();
  elsif new.working
        and new.session_active
        and new.session_id is not null
        and new.session_started_at is not null then
    -- A new live episode must not inherit an older bridge row.
    new.last_session_id := null;
    new.last_session_started_at := null;
    new.last_session_ended_at := null;
  end if;
  return new;
end;
$$;

drop trigger if exists lili_capture_focus_device_projection_stop
  on public.lili_focus_device_presence;
create trigger lili_capture_focus_device_projection_stop
before insert or update on public.lili_focus_device_presence
for each row execute function public.lili_capture_focus_device_projection_stop();

revoke execute on function public.lili_capture_focus_device_projection_stop()
  from public, anon, authenticated;

create or replace function public.lili_focus_live_projection()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  me_id uuid := (select auth.uid());
  devices jsonb;
begin
  if me_id is null then
    raise exception 'authentication required';
  end if;

  -- The result is deliberately tiny and contains only the caller's devices.
  -- Fresh active rows are open intervals.  Recently stopped rows are a
  -- display-only bridge until the immutable segment delta arrives.
  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'device_id', d.device_id,
        'session_id', coalesce(d.session_id, d.last_session_id),
        'start_at', coalesce(d.session_started_at, d.last_session_started_at),
        'end_at', case
          when d.working and d.session_active then null
          else d.last_session_ended_at
        end,
        'working', d.working and d.session_active,
        'live', d.working and d.session_active,
        'last_seen_at', d.last_seen,
        'presence_sequence', d.presence_sequence
      )
      order by coalesce(d.session_started_at, d.last_session_started_at), d.device_id
    ),
    '[]'::jsonb
  )
  into devices
  from public.lili_focus_device_presence d
  where d.user_id = me_id
    and (
      (
        d.working
        and d.session_active
        and d.session_id is not null
        and d.session_started_at is not null
        and d.last_seen > now() - interval '2 minutes'
      )
      or (
        not d.working
        and not d.session_active
        and d.last_session_id is not null
        and d.last_session_started_at is not null
        and d.last_session_ended_at is not null
        and d.last_session_ended_at > now() - interval '2 minutes'
      )
    );

  return jsonb_build_object(
    'user_id', me_id,
    'devices', devices,
    'server_timestamp', now(),
    'fresh_for_seconds', 120
  );
end;
$$;

revoke execute on function public.lili_focus_live_projection() from public, anon;
grant execute on function public.lili_focus_live_projection() to authenticated;

comment on function public.lili_focus_live_projection() is
  'Returns only the caller account''s fresh per-device live focus projection; does not read or write FocusSession facts.';
