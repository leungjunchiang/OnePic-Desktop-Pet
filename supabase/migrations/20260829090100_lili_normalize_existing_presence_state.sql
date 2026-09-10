-- Normalize rows written by older clients before the atomic presence RPC was
-- deployed. The cleanup is intentionally scoped to the authenticated caller;
-- running a data update from a migration has no auth.uid() and is rejected by
-- the device-lease trigger. The atomic heartbeat also repairs the caller's
-- row on its next sync.
create or replace function public.lili_normalize_own_presence_state()
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  me uuid := (select auth.uid());
begin
  if me is null then
    raise exception '请先登录';
  end if;

  update public.lili_focus_presence
  set
    working = false,
    session_active = false,
    session_started_at = null,
    work_state = case
      when coalesce(work_state, '') like 'paused_%' then work_state
      else 'idle'
    end,
    pause_reason = case
      when coalesce(work_state, '') like 'paused_%' then pause_reason
      else null
    end,
    updated_at = now(),
    last_seen = now()
  where user_id = me
    and (
      (coalesce(session_active, false) and (not coalesce(working, false) or session_started_at is null))
      or (coalesce(working, false) and (not coalesce(session_active, false) or session_started_at is null))
      or (not coalesce(working, false) and session_started_at is not null)
    );
end;
$$;

revoke execute on function public.lili_normalize_own_presence_state() from public, anon;
grant execute on function public.lili_normalize_own_presence_state() to authenticated;

comment on table public.lili_focus_presence is
  'Atomic live presence state; active rows require working=true, session_active=true and a non-null session_started_at.';
