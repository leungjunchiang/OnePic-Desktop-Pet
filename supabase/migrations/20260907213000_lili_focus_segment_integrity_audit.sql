-- Detect sealed FocusSegments that an old client recorded as acknowledged but
-- that are absent from the authenticated user's canonical cloud ledger.
-- Only a bounded id manifest crosses the network; no historical payload or
-- other user's segment is exposed.

create or replace function public.lili_focus_segment_integrity_v1(
  p_segment_ids jsonb default '[]'::jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  current_user_id uuid := (select auth.uid());
  requested_count integer;
  present_count integer;
  server_total_count integer;
  missing_ids jsonb;
begin
  if current_user_id is null then
    raise exception 'authentication required for focus segment integrity audit';
  end if;
  if jsonb_typeof(coalesce(p_segment_ids, '[]'::jsonb)) <> 'array' then
    raise exception 'invalid focus segment integrity manifest' using errcode = '22023';
  end if;
  if jsonb_array_length(coalesce(p_segment_ids, '[]'::jsonb)) > 500 then
    raise exception 'focus segment integrity manifest is too large' using errcode = '22023';
  end if;
  if exists (
    select 1
    from jsonb_array_elements(coalesce(p_segment_ids, '[]'::jsonb)) as item(value)
    where jsonb_typeof(item.value) <> 'string'
       or length(btrim(item.value #>> '{}')) not between 1 and 160
  ) then
    raise exception 'invalid focus segment integrity id' using errcode = '22023';
  end if;

  with requested as (
    select distinct btrim(value) as segment_id
    from jsonb_array_elements_text(coalesce(p_segment_ids, '[]'::jsonb)) as ids(value)
  )
  select count(*) into requested_count from requested;

  with requested as (
    select distinct btrim(value) as segment_id
    from jsonb_array_elements_text(coalesce(p_segment_ids, '[]'::jsonb)) as ids(value)
  )
  select count(*) into present_count
  from requested r
  join public.lili_focus_segments s
    on s.user_id = current_user_id
   and s.segment_id = r.segment_id
   and s.end_at is not null
   and s.end_at > s.start_at;

  with requested as (
    select distinct btrim(value) as segment_id
    from jsonb_array_elements_text(coalesce(p_segment_ids, '[]'::jsonb)) as ids(value)
  )
  select coalesce(jsonb_agg(r.segment_id order by r.segment_id), '[]'::jsonb)
    into missing_ids
  from requested r
  where not exists (
    select 1
    from public.lili_focus_segments s
    where s.user_id = current_user_id
      and s.segment_id = r.segment_id
      and s.end_at is not null
      and s.end_at > s.start_at
  );

  select count(*) into server_total_count
  from public.lili_focus_segments s
  where s.user_id = current_user_id
    and s.end_at is not null
    and s.end_at > s.start_at;

  return jsonb_build_object(
    'checked_count', requested_count,
    'present_count', present_count,
    'missing_count', requested_count - present_count,
    'missing_segment_ids', missing_ids,
    'server_total_count', server_total_count
  );
end;
$$;

revoke execute on function public.lili_focus_segment_integrity_v1(jsonb)
  from public, anon;
grant execute on function public.lili_focus_segment_integrity_v1(jsonb)
  to authenticated;

comment on function public.lili_focus_segment_integrity_v1(jsonb) is
  'Low-frequency owner-only sealed FocusSegment id integrity audit; returns only ids absent from the caller ledger.';
