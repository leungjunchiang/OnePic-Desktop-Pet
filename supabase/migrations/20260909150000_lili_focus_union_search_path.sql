-- The canonical interval helper runs as SECURITY DEFINER with an empty
-- search_path.  Qualify PostgreSQL built-ins so the helper remains callable
-- from dashboard/report functions on every role.

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
    ) source
    where source.start_at < source.end_at
  ) merged;
$$;

revoke execute on function public.lili_focus_union_seconds(uuid, timestamptz, timestamptz)
  from public, anon;
grant execute on function public.lili_focus_union_seconds(uuid, timestamptz, timestamptz)
  to authenticated, service_role;

comment on function public.lili_focus_union_seconds(uuid, timestamptz, timestamptz)
  is 'Canonical account focus interval union; built-ins are schema-qualified for the empty SECURITY DEFINER search path.';
