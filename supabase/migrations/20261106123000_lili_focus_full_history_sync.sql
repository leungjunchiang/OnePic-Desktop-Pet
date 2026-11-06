-- Keep the existing owner-scoped sync APIs and their bounded cursor pages.
-- Sealed history may be uploaded and read at any past date. p_days = 0
-- explicitly requests all history and is echoed for client version checking.

begin;

create or replace function public.lili_sync_focus_segments_delta_all_history(
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
  sync_watermark timestamptz := clock_timestamp();
  cursor_updated_at timestamptz := null;
  cursor_segment_id text := '';
  cursor_json jsonb;
  page jsonb := '[]'::jsonb;
  page_count integer := 0;
  last_updated_at timestamptz := null;
  last_segment_id text := '';
  has_more boolean := false;
  item jsonb;
  segment_key text;
  segment_session text;
  segment_start timestamptz;
  segment_end timestamptz;
  segment_device text;
  segment_completed boolean;
  segment_quality smallint;
  segment_task text;
  segment_interruptions smallint;
begin
  if current_user_id is null then
    raise exception 'authentication required for focus segment sync';
  end if;
  if jsonb_typeof(coalesce(p_segments, '[]'::jsonb)) <> 'array' then
    raise exception 'invalid focus segment payload';
  end if;

  if nullif(btrim(coalesce(p_since, '')), '') is not null then
    begin
      cursor_json := p_since::jsonb;
      if jsonb_typeof(cursor_json) = 'object'
         and nullif(cursor_json->>'updated_at', '') is not null then
        cursor_updated_at := (cursor_json->>'updated_at')::timestamptz;
        cursor_segment_id := left(coalesce(cursor_json->>'segment_id', ''), 160);
      else
        -- Accept one old timestamp cursor during rolling client upgrades.
        cursor_updated_at := p_since::timestamptz;
        cursor_segment_id := '';
      end if;
    exception when others then
      raise exception 'invalid focus segment cursor';
    end;
  end if;

  for item in select value from jsonb_array_elements(coalesce(p_segments, '[]'::jsonb)) loop
    begin
      segment_key := left(btrim(coalesce(item->>'segment_id', '')), 160);
      segment_session := left(btrim(coalesce(item->>'session_id', '')), 160);
      segment_start := public.lili_parse_client_focus_timestamp(item->>'start_at');
      segment_end := public.lili_parse_client_focus_timestamp(nullif(item->>'end_at', ''));
      segment_device := left(btrim(coalesce(item->>'device_id', '')), 120);
      segment_completed := coalesce((item->>'completed')::boolean, false);
      segment_quality := greatest(0, least(100, coalesce((item->>'quality')::smallint, 0)));
      segment_task := left(coalesce(item->>'task', ''), 120);
      segment_interruptions := greatest(0, coalesce((item->>'interruptions')::smallint, 0));
    exception when others then
      segment_key := '';
      segment_start := null;
      segment_end := null;
    end;
    if segment_key <> ''
       and segment_start is not null
       and segment_end is not null
       and segment_end > segment_start
       and (segment_start at time zone 'Asia/Shanghai')::date <= today then
      insert into public.lili_focus_segments (
        user_id, segment_id, session_id, start_at, end_at, device_id,
        completed, quality, task, interruptions, updated_at
      ) values (
        current_user_id, segment_key, coalesce(segment_session, ''), segment_start,
        segment_end, coalesce(segment_device, ''), segment_completed,
        segment_quality, coalesce(segment_task, ''), segment_interruptions, now()
      )
      on conflict (user_id, segment_id) do update set
        session_id = excluded.session_id,
        start_at = case
          when public.lili_focus_segments.time_corrected_at is not null
            then public.lili_focus_segments.start_at
          else excluded.start_at
        end,
        -- The ordinary path is equivalent to `end_at = excluded.end_at`;
        -- keep corrected canonical timestamps protected from client retries.
        end_at = case
          when public.lili_focus_segments.time_corrected_at is not null
            then public.lili_focus_segments.end_at
          else excluded.end_at
        end,
        device_id = excluded.device_id,
        completed = excluded.completed,
        quality = excluded.quality,
        task = excluded.task,
        interruptions = excluded.interruptions,
        updated_at = now()
      where public.lili_focus_segments.session_id is distinct from excluded.session_id
         or public.lili_focus_segments.device_id is distinct from excluded.device_id
         or public.lili_focus_segments.completed is distinct from excluded.completed
         or public.lili_focus_segments.quality is distinct from excluded.quality
         or public.lili_focus_segments.task is distinct from excluded.task
         or public.lili_focus_segments.interruptions is distinct from excluded.interruptions
         or (
           public.lili_focus_segments.time_corrected_at is null
           and (
             public.lili_focus_segments.start_at is distinct from excluded.start_at
             or public.lili_focus_segments.end_at is distinct from excluded.end_at
           )
         );
    end if;
  end loop;

  with selected as (
    select s.*
    from public.lili_focus_segments s
    where s.user_id = current_user_id
      and s.end_at is not null
      and s.end_at > s.start_at
      and (s.start_at at time zone 'Asia/Shanghai')::date <= today
      and (cursor_updated_at is null
        or s.updated_at > cursor_updated_at
        or (s.updated_at = cursor_updated_at and s.segment_id > cursor_segment_id))
      and s.updated_at <= sync_watermark
    order by s.updated_at, s.segment_id
    limit 500
  )
  select
    coalesce(jsonb_agg(jsonb_build_object(
      'segment_id', selected.segment_id,
      'session_id', selected.session_id,
      'start_at', selected.start_at,
      'end_at', selected.end_at,
      'device_id', selected.device_id,
      'completed', selected.completed,
      'quality', selected.quality,
      'task', selected.task,
      'interruptions', selected.interruptions,
      'time_corrected_at', selected.time_corrected_at,
      'time_correction_reason', selected.time_correction_reason,
      'updated_at', selected.updated_at
    ) order by selected.updated_at, selected.segment_id), '[]'::jsonb),
    count(*)::integer
  into page, page_count
  from selected;

  if page_count > 0 then
    select selected.updated_at, selected.segment_id
      into last_updated_at, last_segment_id
    from public.lili_focus_segments selected
    where selected.user_id = current_user_id
      and selected.end_at is not null
      and selected.end_at > selected.start_at
      and (selected.start_at at time zone 'Asia/Shanghai')::date <= today
      and (cursor_updated_at is null
        or selected.updated_at > cursor_updated_at
        or (selected.updated_at = cursor_updated_at and selected.segment_id > cursor_segment_id))
      and selected.updated_at <= sync_watermark
    order by selected.updated_at, selected.segment_id
    limit 1 offset 499;
    -- If the page has fewer than 500 rows, fetch its actual last row.
    if last_updated_at is null then
      select selected.updated_at, selected.segment_id
        into last_updated_at, last_segment_id
      from public.lili_focus_segments selected
      where selected.user_id = current_user_id
        and selected.end_at is not null
        and selected.end_at > selected.start_at
        and (selected.start_at at time zone 'Asia/Shanghai')::date <= today
        and (cursor_updated_at is null
          or selected.updated_at > cursor_updated_at
          or (selected.updated_at = cursor_updated_at and selected.segment_id > cursor_segment_id))
        and selected.updated_at <= sync_watermark
      order by selected.updated_at desc, selected.segment_id desc
      limit 1;
    end if;
    select exists(
      select 1
      from public.lili_focus_segments later
      where later.user_id = current_user_id
        and later.end_at is not null
        and later.end_at > later.start_at
        and (later.start_at at time zone 'Asia/Shanghai')::date <= today
        and later.updated_at <= sync_watermark
        and (
          later.updated_at > last_updated_at
          or (later.updated_at = last_updated_at and later.segment_id > last_segment_id)
        )
    ) into has_more;
  else
    last_updated_at := sync_watermark;
    last_segment_id := '';
  end if;

  return jsonb_build_object(
    'segments', page,
    'full_sync', (p_since is null),
    'next_cursor', jsonb_build_object(
      'updated_at', last_updated_at,
      'segment_id', coalesce(last_segment_id, '')
    )::text,
    'has_more', coalesce(has_more, false)
  );
end;
$$;

revoke execute on function public.lili_sync_focus_segments_delta_all_history(jsonb, text)
  from public, anon;
grant execute on function public.lili_sync_focus_segments_delta_all_history(jsonb, text)
  to authenticated;

comment on function public.lili_sync_focus_segments_delta_all_history(jsonb, text) is
  'Authenticated sealed FocusSegment bootstrap/delta sync using a stable (updated_at, segment_id) cursor; repeated unchanged uploads do not refresh updated_at.';

create or replace function public.lili_sync_focus_segments_delta_v2(
  p_segments jsonb,
  p_since text,
  p_all_history boolean
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
  if p_all_history is distinct from true then
    raise exception 'all-history sync must be requested explicitly' using errcode = '22023';
  end if;
  if current_user_id is null then
    raise exception 'authentication required for focus segment sync';
  end if;
  if jsonb_typeof(coalesce(p_segments, '[]'::jsonb)) <> 'array' then
    raise exception 'invalid focus segment payload' using errcode = '22023';
  end if;

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
    perform coalesce((item->>'completed')::boolean, false);
    perform greatest(0, least(100, coalesce((item->>'quality')::smallint, 0)));
    perform greatest(0, coalesce((item->>'interruptions')::smallint, 0));

    if segment_key = ''
       or segment_start is null
       or segment_end is null
       or segment_end <= segment_start
       or (segment_start at time zone 'Asia/Shanghai')::date
          > today then
      raise exception 'invalid sealed focus segment' using errcode = '22023';
    end if;
    if segment_key = any(seen_ids) then
      raise exception 'duplicate focus segment id in upload' using errcode = '22023';
    end if;
    seen_ids := array_append(seen_ids, segment_key);
  end loop;

  result := public.lili_sync_focus_segments_delta_all_history(p_segments, p_since);

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

  -- An empty delta is an acknowledgement of "nothing currently visible",
  -- not a new position in the ordered stream. Keep the prior cursor so a row
  -- committed during the request cannot be skipped permanently.
  if jsonb_array_length(coalesce(result->'segments', '[]'::jsonb)) = 0
     and nullif(btrim(coalesce(p_since, '')), '') is not null then
    result := jsonb_set(result, '{next_cursor}', to_jsonb(p_since), true);
  end if;

  return result || jsonb_build_object(
    'requested_count', jsonb_array_length(coalesce(p_segments, '[]'::jsonb)),
    'accepted_count', jsonb_array_length(accepted_ids),
    'accepted_segment_ids', accepted_ids
  );
end;
$$;

revoke execute on function public.lili_sync_focus_segments_delta_v2(jsonb, text, boolean)
  from public, anon;
grant execute on function public.lili_sync_focus_segments_delta_v2(jsonb, text, boolean)
  to authenticated;

comment on function public.lili_sync_focus_segments_delta_v2(jsonb, text, boolean) is
  'Strict atomic sealed FocusSegment sync with explicit upload acknowledgements and retry-safe empty-delta cursors.';

create or replace function public.lili_focus_recent_segments_v1(
  p_cursor text default null,
  p_days integer default 60,
  p_limit integer default 500
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  current_user_id uuid := (select auth.uid());
  bounded_days integer := case when p_days = 0 then 0
    else greatest(1, least(coalesce(p_days, 60), 90)) end;
  bounded_limit integer := greatest(1, least(coalesce(p_limit, 500), 500));
  window_start timestamptz;
  window_end timestamptz;
  cursor_value jsonb;
  cursor_start_at timestamptz;
  cursor_segment_id text;
  page_rows jsonb := '[]'::jsonb;
  page_count integer := 0;
  last_start_at timestamptz;
  last_segment_id text;
  has_more boolean := false;
begin
  if current_user_id is null then
    raise exception 'authentication required for recent focus reconciliation';
  end if;

  if bounded_days > 0 then
    window_start := (
      ((now() at time zone 'Asia/Shanghai')::date - (bounded_days - 1))
      ::timestamp at time zone 'Asia/Shanghai'
    );
  end if;
  window_end := (
    (((now() at time zone 'Asia/Shanghai')::date + 1)::timestamp)
    at time zone 'Asia/Shanghai'
  );

  if nullif(btrim(coalesce(p_cursor, '')), '') is not null then
    begin
      cursor_value := p_cursor::jsonb;
      cursor_start_at := (cursor_value ->> 'start_at')::timestamptz;
      cursor_segment_id := nullif(btrim(cursor_value ->> 'segment_id'), '');
    exception when others then
      raise exception 'invalid recent focus reconciliation cursor' using errcode = '22023';
    end;
    if cursor_start_at is null or cursor_segment_id is null then
      raise exception 'invalid recent focus reconciliation cursor' using errcode = '22023';
    end if;
  end if;

  with page as (
    select
      s.segment_id,
      s.session_id,
      s.start_at,
      s.end_at,
      s.device_id,
      s.completed,
      s.quality,
      s.task,
      s.interruptions,
      s.updated_at
    from public.lili_focus_segments s
    where s.user_id = current_user_id
      and s.end_at is not null
      and s.end_at > s.start_at
      and (window_start is null or s.end_at > window_start)
      and s.start_at < window_end
      and (
        cursor_start_at is null
        or s.start_at > cursor_start_at
        or (s.start_at = cursor_start_at and s.segment_id > cursor_segment_id)
      )
    order by s.start_at, s.segment_id
    limit bounded_limit
  )
  select
    coalesce(jsonb_agg(
      jsonb_build_object(
        'segment_id', segment_id,
        'session_id', session_id,
        'start_at', start_at,
        'end_at', end_at,
        'device_id', device_id,
        'completed', completed,
        'quality', quality,
        'task', task,
        'interruptions', interruptions,
        'updated_at', updated_at
      ) order by start_at, segment_id
    ), '[]'::jsonb),
    count(*),
    max(start_at),
    (array_agg(segment_id order by start_at desc, segment_id desc))[1]
  into page_rows, page_count, last_start_at, last_segment_id
  from page;

  if page_count > 0 then
    select exists (
      select 1
      from public.lili_focus_segments s
      where s.user_id = current_user_id
        and s.end_at is not null
        and s.end_at > s.start_at
        and (window_start is null or s.end_at > window_start)
        and s.start_at < window_end
        and (
          s.start_at > last_start_at
          or (s.start_at = last_start_at and s.segment_id > last_segment_id)
        )
    ) into has_more;
  end if;

  return jsonb_build_object(
    'segments', page_rows,
    'has_more', has_more,
    'next_cursor', case
      when page_count > 0 then jsonb_build_object(
        'start_at', last_start_at,
        'segment_id', last_segment_id
      )::text
      else null
    end,
    'window_days', bounded_days,
    'window_start', window_start
  );
end;
$$;

revoke execute on function public.lili_focus_recent_segments_v1(text, integer, integer)
  from public, anon;
grant execute on function public.lili_focus_recent_segments_v1(text, integer, integer)
  to authenticated;

comment on function public.lili_focus_recent_segments_v1(text, integer, integer) is
  'Read-only paged reconciliation of one authenticated account''s sealed FocusSegments, with all dates when p_days = 0; does not write facts or delta cursor state.';

commit;
