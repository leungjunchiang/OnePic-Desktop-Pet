-- Make sealed FocusSegment upload acknowledgement explicit and atomic.
--
-- The v1 delta RPC historically swallowed every per-item exception.  A
-- missing EXECUTE grant on lili_parse_client_focus_timestamp therefore made
-- the RPC return success while inserting zero rows.  Clients then persisted
-- SHA fingerprints for facts the server had never accepted.  Keep v1 for old
-- clients, but repair its helper privilege and put a strict v2 wrapper in
-- front of it for new clients.  Any malformed/unaccepted row raises, rolling
-- back the complete RPC transaction and leaving the client batch retryable.

revoke all on function public.lili_parse_client_focus_timestamp(text)
  from public, anon;
grant execute on function public.lili_parse_client_focus_timestamp(text)
  to authenticated;

create or replace function public.lili_sync_focus_segments_delta_v2(
  p_segments jsonb default '[]'::jsonb,
  p_since text default null
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  current_user_id uuid := (select auth.uid());
  today date := (now() at time zone 'Asia/Shanghai')::date;
  item jsonb;
  segment_key text;
  segment_start timestamptz;
  segment_end timestamptz;
  seen_ids text[] := array[]::text[];
  accepted_ids jsonb := '[]'::jsonb;
  result jsonb;
begin
  if current_user_id is null then
    raise exception 'authentication required for focus segment sync';
  end if;
  if jsonb_typeof(coalesce(p_segments, '[]'::jsonb)) <> 'array' then
    raise exception 'invalid focus segment payload' using errcode = '22023';
  end if;

  -- Validate the complete upload before invoking the legacy transport body.
  -- Do not catch these errors: one invalid row must roll back the whole batch.
  for item in
    select value
    from jsonb_array_elements(coalesce(p_segments, '[]'::jsonb))
  loop
    if jsonb_typeof(item) <> 'object' then
      raise exception 'invalid focus segment item' using errcode = '22023';
    end if;
    segment_key := left(btrim(coalesce(item->>'segment_id', '')), 160);
    segment_start := public.lili_parse_client_focus_timestamp(item->>'start_at');
    segment_end := public.lili_parse_client_focus_timestamp(nullif(item->>'end_at', ''));

    -- Force the same scalar casts used by v1 so malformed values cannot be
    -- silently ignored by its broad exception handler.
    perform coalesce((item->>'completed')::boolean, false);
    perform greatest(0, least(100, coalesce((item->>'quality')::smallint, 0)));
    perform greatest(0, coalesce((item->>'interruptions')::smallint, 0));

    if segment_key = ''
       or segment_start is null
       or segment_end is null
       or segment_end <= segment_start
       or (segment_start at time zone 'Asia/Shanghai')::date
          not between today - 400 and today then
      raise exception 'invalid sealed focus segment' using errcode = '22023';
    end if;
    if segment_key = any(seen_ids) then
      raise exception 'duplicate focus segment id in upload' using errcode = '22023';
    end if;
    seen_ids := array_append(seen_ids, segment_key);
  end loop;

  -- This call and every verification below run in this function's transaction.
  -- Raising after it therefore also rolls back inserts performed by v1.
  result := public.lili_sync_focus_segments_delta(p_segments, p_since);

  foreach segment_key in array seen_ids
  loop
    if not exists (
      select 1
      from public.lili_focus_segments s
      where s.user_id = current_user_id
        and s.segment_id = segment_key
        and s.end_at is not null
        and s.end_at > s.start_at
    ) then
      raise exception 'focus segment upload was not accepted'
        using errcode = 'P0001';
    end if;
    accepted_ids := accepted_ids || jsonb_build_array(segment_key);
  end loop;

  return result || jsonb_build_object(
    'requested_count', jsonb_array_length(coalesce(p_segments, '[]'::jsonb)),
    'accepted_count', jsonb_array_length(accepted_ids),
    'accepted_segment_ids', accepted_ids
  );
end;
$$;

revoke execute on function public.lili_sync_focus_segments_delta_v2(jsonb, text)
  from public, anon;
grant execute on function public.lili_sync_focus_segments_delta_v2(jsonb, text)
  to authenticated;

comment on function public.lili_sync_focus_segments_delta_v2(jsonb, text) is
  'Strict atomic sealed FocusSegment bootstrap/delta sync. Returns explicit accepted ids; malformed or unaccepted uploads roll back and cannot advance a client cursor.';
