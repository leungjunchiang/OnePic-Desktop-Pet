-- Exercise both devices and the old client against synthetic accounts only.
begin;
do $$
declare
  owner_id uuid := gen_random_uuid();
  other_id uuid := gen_random_uuid();
  prior_start timestamptz := now() - interval '800 days';
  payload jsonb;
  result jsonb;
  cursor_after text;
begin
  insert into auth.users(id, raw_user_meta_data)
    values (owner_id, '{}'), (other_id, '{}');
  perform set_config('request.jwt.claim.sub', owner_id::text, true);
  execute 'set local role authenticated';

  payload := jsonb_build_array(
    jsonb_build_object('segment_id', 'history-a', 'session_id', 'session-a',
      'device_id', 'old-desktop', 'start_at', prior_start,
      'end_at', prior_start + interval '30 minutes'),
    jsonb_build_object('segment_id', 'history-b', 'session_id', 'session-b',
      'device_id', 'old-desktop', 'start_at', prior_start + interval '1 hour',
      'end_at', prior_start + interval '2 hours')
  );
  result := public.lili_sync_focus_segments_delta_v2(payload, null, true);
  if (result->>'accepted_count')::int is distinct from 2
      or jsonb_array_length(result->'segments') is distinct from 2 then
    raise exception 'full-history delta did not accept and return both old segments';
  end if;
  cursor_after := result->>'next_cursor';
  result := public.lili_sync_focus_segments_delta_v2(payload, cursor_after, true);
  if (result->>'accepted_count')::int is distinct from 2
      or jsonb_array_length(result->'segments') is distinct from 0
      or result->>'next_cursor' is distinct from cursor_after then
    raise exception 'retry duplicated facts or advanced an empty delta';
  end if;

  result := public.lili_focus_recent_segments_v1(null, 0, 1);
  if result->>'window_days' is distinct from '0'
      or result->>'has_more' is distinct from 'true'
      or result#>>'{segments,0,segment_id}' is distinct from 'history-a' then
    raise exception 'a second device could not read the first full-history page';
  end if;
  result := public.lili_focus_recent_segments_v1(result->>'next_cursor', 0, 1);
  if result->>'has_more' is distinct from 'false'
      or result#>>'{segments,0,segment_id}' is distinct from 'history-b' then
    raise exception 'a second device could not read the final full-history page';
  end if;
  result := public.lili_sync_focus_segments_delta_v2('[]'::jsonb, null);
  if jsonb_array_length(result->'segments') is distinct from 0 then
    raise exception 'older clients received rows outside their supported date window';
  end if;
  result := public.lili_focus_recent_segments_v1(null, 60, 500);
  if jsonb_array_length(result->'segments') is distinct from 0 then
    raise exception 'the legacy recent-window call no longer filters dates';
  end if;

  perform set_config('request.jwt.claim.sub', other_id::text, true);
  result := public.lili_focus_recent_segments_v1(null, 0, 500);
  if jsonb_array_length(result->'segments') is distinct from 0 then
    raise exception 'full-history download leaked another account';
  end if;
  result := public.lili_sync_focus_segments_delta_v2('[]'::jsonb, null, true);
  if jsonb_array_length(result->'segments') is distinct from 0 then
    raise exception 'full-history delta leaked another account';
  end if;
end $$;
rollback;
