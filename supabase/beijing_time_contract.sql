-- v306: the existing actual-start RPC now always uses the Beijing calendar.
-- Legacy device utc_offset_minutes is metadata, never a business timezone.
-- No schema change, facts rewrite, extra RPC or client polling.
create or replace function public.lili_actual_work_start_clock(p_owner_id uuid,p_day date)
returns text language sql stable security definer set search_path='' as $$
  with bounds as (
    select p_day::timestamp at time zone 'Asia/Shanghai' as lo,
      (p_day+1)::timestamp at time zone 'Asia/Shanghai' as hi,
      (p_day::timestamp+interval '6 hours') at time zone 'Asia/Shanghai' as work_lo
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
    select e.occurred_at as started from public.lili_discipline_events e,bounds b
    where e.user_id=p_owner_id and e.event_type='start_work' and e.occurred_at>=b.work_lo and e.occurred_at<b.hi
      and e.metadata->>'actual_start_source'='explicit_focus_start' and e.occurred_at<=now()+interval '2 minutes'
      and coalesce(e.metadata->>'source','') not in ('sleep','lock','display_off','restart_safe_seal','account_switch','restore','heartbeat','startup','reconnect')
      and coalesce(e.metadata->>'restored','false')<>'true'
  ), starts as (select started from facts union all select started from explicit)
  select to_char(min(s.started) at time zone 'Asia/Shanghai','HH24:MI')
    from bounds b left join starts s on s.started>=b.work_lo and s.started<b.hi;
$$;
revoke execute on function public.lili_actual_work_start_clock(uuid,date) from public,anon,authenticated;
