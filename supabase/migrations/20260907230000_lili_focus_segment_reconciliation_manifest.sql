-- Low-frequency convergence audit for a device whose composite cursor may
-- already have moved past a sealed row.  The normal delta RPC remains the
-- transport.  This RPC exchanges an id manifest and returns only rows that
-- are present in the account ledger but absent from the requesting device's
-- local manifest; it is not a recurring historical download.

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
  server_manifest jsonb;
  missing_local_segments jsonb;
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

  select coalesce(jsonb_agg(
    jsonb_build_object(
      'segment_id', s.segment_id,
      'updated_at', s.updated_at
    ) order by s.updated_at, s.segment_id
  ), '[]'::jsonb)
    into server_manifest
  from (
    select s.segment_id, s.updated_at
    from public.lili_focus_segments s
    where s.user_id = current_user_id
      and s.end_at is not null
      and s.end_at > s.start_at
    order by s.updated_at, s.segment_id
    limit 500
  ) s;

  -- Return only targeted missing rows.  A device with an empty local ledger
  -- uses the normal bootstrap path and therefore does not receive this audit
  -- response.
  with requested as (
    select distinct btrim(value) as segment_id
    from jsonb_array_elements_text(coalesce(p_segment_ids, '[]'::jsonb)) as ids(value)
  )
  select coalesce(jsonb_agg(
    jsonb_build_object(
      'segment_id', s.segment_id,
      'session_id', s.session_id,
      'start_at', s.start_at,
      'end_at', s.end_at,
      'device_id', s.device_id,
      'completed', s.completed,
      'quality', s.quality,
      'task', s.task,
      'interruptions', s.interruptions,
      'updated_at', s.updated_at
    ) order by s.updated_at, s.segment_id
  ), '[]'::jsonb)
    into missing_local_segments
  from (
    select s.*
    from public.lili_focus_segments s
    where s.user_id = current_user_id
      and s.end_at is not null
      and s.end_at > s.start_at
      and not exists (
        select 1 from requested r where r.segment_id = s.segment_id
      )
    order by s.updated_at, s.segment_id
    limit 500
  ) s;

  return jsonb_build_object(
    'checked_count', requested_count,
    'present_count', present_count,
    'missing_count', requested_count - present_count,
    'missing_segment_ids', missing_ids,
    'server_total_count', server_total_count,
    'server_manifest', server_manifest,
    'server_manifest_complete', server_total_count <= 500,
    'missing_local_segments', missing_local_segments
  );
end;
$$;

revoke execute on function public.lili_focus_segment_integrity_v1(jsonb)
  from public, anon;
grant execute on function public.lili_focus_segment_integrity_v1(jsonb)
  to authenticated;

comment on function public.lili_focus_segment_integrity_v1(jsonb) is
  'Low-frequency account-local FocusSegment convergence audit: id manifest plus targeted rows missing from the caller cache; no recurring full history download.';
