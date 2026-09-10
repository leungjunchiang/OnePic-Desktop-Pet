-- A live focus tuple is only a short-lived projection.  It must be backed by
-- recent local keyboard/mouse activity from the current desktop build, not
-- merely by a process that is still sending heartbeats after login.
--
-- The previous RPC is intentionally retained as a compatibility wrapper that
-- reports the device online but resting.  New clients call the v2 RPC with a
-- coarse input-idle value; no keystrokes, pointer positions, or durations are
-- collected or stored.

create table if not exists public.lili_focus_session_quarantines (
  user_id uuid not null references auth.users(id) on delete cascade,
  session_id text not null check (char_length(btrim(session_id)) between 1 and 160),
  reason text not null default 'untrusted_live_session' check (char_length(reason) between 1 and 120),
  quarantined_at timestamptz not null default now(),
  primary key (user_id, session_id)
);

alter table public.lili_focus_session_quarantines enable row level security;
revoke all on table public.lili_focus_session_quarantines from public, anon, authenticated;

comment on table public.lili_focus_session_quarantines is
  'Administrative exclusion list for a confirmed bad live session. Rows never create or alter FocusSegments; canonical projections simply ignore a quarantined session.';

-- Preserve the audited per-device implementation as a private core, then put
-- activity-proof policy at the RPC boundary.  This keeps all sequence and
-- account projection behaviour identical for trusted current clients.
do $$
begin
  if to_regprocedure(
    'public.lili_upsert_focus_presence_core(boolean,boolean,text,timestamp with time zone,text,bigint)'
  ) is null
  and to_regprocedure(
    'public.lili_upsert_focus_presence(boolean,boolean,text,timestamp with time zone,text,bigint)'
  ) is not null then
    alter function public.lili_upsert_focus_presence(
      boolean, boolean, text, timestamptz, text, bigint
    ) rename to lili_upsert_focus_presence_core;
  end if;
end;
$$;

revoke execute on function public.lili_upsert_focus_presence_core(
  boolean, boolean, text, timestamptz, text, bigint
) from public, anon, authenticated;

create or replace function public.lili_upsert_focus_presence_v2(
  p_working boolean,
  p_session_active boolean,
  p_session_id text,
  p_session_started_at timestamptz,
  p_device_id text,
  p_sequence bigint,
  p_input_idle_seconds integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  me uuid := (select auth.uid());
  requested_session_id text := nullif(left(btrim(coalesce(p_session_id, '')), 160), '');
  input_is_recent boolean := coalesce(p_input_idle_seconds between 0 and 600, false);
  session_is_quarantined boolean := false;
  allow_live boolean := false;
  result jsonb;
begin
  if me is null then
    raise exception 'authentication required';
  end if;

  if requested_session_id is not null then
    select exists(
      select 1
      from public.lili_focus_session_quarantines q
      where q.user_id = me
        and q.session_id = requested_session_id
    ) into session_is_quarantined;
  end if;

  allow_live := coalesce(p_working, false)
    and coalesce(p_session_active, false)
    and requested_session_id is not null
    and p_session_started_at is not null
    and input_is_recent
    and not session_is_quarantined;

  result := public.lili_upsert_focus_presence_core(
    allow_live,
    allow_live,
    case when allow_live then requested_session_id else null end,
    case when allow_live then p_session_started_at else null end,
    p_device_id,
    p_sequence
  );

  return result || jsonb_build_object(
    'activity_proven', input_is_recent,
    'live_allowed', allow_live,
    'activity_guard', case
      when session_is_quarantined then 'session_quarantined'
      when coalesce(p_working, false) and coalesce(p_session_active, false) and not input_is_recent
        then 'recent_input_required'
      else 'ok'
    end
  );
end;
$$;

revoke execute on function public.lili_upsert_focus_presence_v2(
  boolean, boolean, text, timestamptz, text, bigint, integer
) from public, anon;
grant execute on function public.lili_upsert_focus_presence_v2(
  boolean, boolean, text, timestamptz, text, bigint, integer
) to authenticated;

comment on function public.lili_upsert_focus_presence_v2(
  boolean, boolean, text, timestamptz, text, bigint, integer
) is
  'Current per-device liveness RPC. A running focus projection requires a fresh coarse input-idle proof (0-600 seconds); it stores no input events or durations.';

-- Old desktop builds do not send activity proof.  Keep their device online so
-- social availability remains correct, but never let a login-only heartbeat
-- create or prolong an unsealed live focus interval.
create or replace function public.lili_upsert_focus_presence(
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
  result jsonb;
begin
  result := public.lili_upsert_focus_presence_core(
    false,
    false,
    null,
    null,
    p_device_id,
    p_sequence
  );
  return result || jsonb_build_object(
    'activity_proven', false,
    'live_allowed', false,
    'activity_guard', 'client_update_required'
  );
end;
$$;

revoke execute on function public.lili_upsert_focus_presence(
  boolean, boolean, text, timestamptz, text, bigint
) from public, anon;
grant execute on function public.lili_upsert_focus_presence(
  boolean, boolean, text, timestamptz, text, bigint
) to authenticated;

comment on function public.lili_upsert_focus_presence(
  boolean, boolean, text, timestamptz, text, bigint
) is
  'Compatibility heartbeat for older clients. It preserves online/rest state but cannot create live focus without v2 activity proof.';

-- Quarantine is deliberately projection-only: a bad live session may still
-- arrive as an old client’s sealed upload, but it can never be counted.  This
-- acknowledges the client upload normally and avoids a retry storm.
create or replace function public.lili_focus_union_seconds(
  p_user_id uuid,
  p_start_at timestamptz,
  p_end_at timestamptz
)
returns integer
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(
    sum(extract(epoch from upper(r) - lower(r)))::integer,
    0
  )
  from (
    select pg_catalog.unnest(
      pg_catalog.range_agg(
        pg_catalog.tstzrange(source.start_at, source.end_at, '[)')
      )
    ) as r
    from (
      select
        greatest(s.start_at, p_start_at) as start_at,
        least(s.end_at, p_end_at) as end_at
      from public.lili_focus_segments s
      where s.user_id = p_user_id
        and s.end_at is not null
        and s.start_at < p_end_at
        and s.end_at > p_start_at
        and public.lili_focus_segment_is_valid(s.start_at, s.end_at, now())
        and not exists (
          select 1
          from public.lili_focus_session_quarantines q
          where q.user_id = s.user_id
            and q.session_id = s.session_id
        )

      union all

      select
        greatest(d.session_started_at, p_start_at) as start_at,
        least(now(), d.last_seen + interval '2 minutes', p_end_at) as end_at
      from public.lili_focus_device_presence d
      where d.user_id = p_user_id
        and d.working
        and d.session_active
        and d.session_id is not null
        and d.session_started_at is not null
        and d.last_seen > now() - interval '2 minutes'
        and d.session_started_at < p_end_at
        and now() > p_start_at
        and d.session_started_at <= now() + interval '2 minutes'
        and extract(epoch from now() - d.session_started_at) between 0 and 86400
        and not exists (
          select 1
          from public.lili_focus_session_quarantines q
          where q.user_id = d.user_id
            and q.session_id = d.session_id
        )
    ) source
    where source.start_at < source.end_at
  ) merged;
$$;

revoke execute on function public.lili_focus_union_seconds(uuid, timestamptz, timestamptz)
  from public, anon;
grant execute on function public.lili_focus_union_seconds(uuid, timestamptz, timestamptz)
  to authenticated, service_role;

comment on function public.lili_focus_union_seconds(uuid, timestamptz, timestamptz)
  is 'Canonical account focus interval union. Confirmed bad live session ids are excluded before range union; this does not create, alter, or add historical durations.';
