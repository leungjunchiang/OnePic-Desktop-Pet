-- Keep legacy Dashboard fields consistent with the multi-device aggregate.
-- The first Phase 2 migration added account_* fields, but older clients still
-- read online/working/status/session_active from me_presence.  Re-project
-- those fields from fresh device rows without touching focus facts.

create or replace function public.lili_dashboard()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  me_id uuid := (select auth.uid());
  payload jsonb;
  active_device_count integer := 0;
  working_device_count integer := 0;
begin
  if me_id is null then
    raise exception 'authentication required';
  end if;

  payload := public.lili_dashboard_multidevice_base_20260830();

  select
    count(*)::integer,
    count(*) filter (where d.working and d.session_active)::integer
  into active_device_count, working_device_count
  from public.lili_focus_device_presence d
  where d.user_id = me_id
    and d.last_seen > now() - interval '2 minutes';

  if jsonb_typeof(payload -> 'me_presence') = 'object' then
    -- New clients use account_*; old clients continue to use these fields.
    payload := jsonb_set(payload, '{me_presence,account_online}', to_jsonb(active_device_count > 0), true);
    payload := jsonb_set(payload, '{me_presence,account_working}', to_jsonb(working_device_count > 0), true);
    payload := jsonb_set(payload, '{me_presence,active_device_count}', to_jsonb(active_device_count), true);
    payload := jsonb_set(payload, '{me_presence,working_device_count}', to_jsonb(working_device_count), true);
    payload := jsonb_set(payload, '{me_presence,online}', to_jsonb(active_device_count > 0), true);
    payload := jsonb_set(payload, '{me_presence,working}', to_jsonb(working_device_count > 0), true);
    payload := jsonb_set(payload, '{me_presence,session_active}', to_jsonb(working_device_count > 0), true);
    payload := jsonb_set(
      payload,
      '{me_presence,status}',
      to_jsonb(case
        when working_device_count > 0 then 'focus'
        when active_device_count > 0 then 'rest'
        else 'offline'
      end),
      true
    );
    if working_device_count = 0 then
      payload := jsonb_set(payload, '{me_presence,session_id}', 'null'::jsonb, true);
      payload := jsonb_set(payload, '{me_presence,session_started_at}', 'null'::jsonb, true);
      payload := jsonb_set(payload, '{me_presence,session_seconds}', '0'::jsonb, true);
    end if;
  end if;

  return payload;
end;
$$;

revoke execute on function public.lili_dashboard() from public, anon;
grant execute on function public.lili_dashboard() to authenticated;
