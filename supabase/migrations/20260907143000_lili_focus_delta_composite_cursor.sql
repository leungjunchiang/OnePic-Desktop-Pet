-- Make focus-segment delta sync boundary-safe.
--
-- The old endpoint used updated_at >= p_since and a bare timestamp cursor.
-- Rows sharing a timestamp could therefore be repeated indefinitely or be
-- skipped when a client advanced past a page boundary.  The cursor is now a
-- JSON string containing (updated_at, segment_id), ordered lexicographically.
-- Canonical rows remain sealed; open duration is still presence projection.

drop function if exists public.lili_sync_focus_segments_delta(jsonb, timestamptz);
drop function if exists public.lili_sync_focus_segments_delta(jsonb, text);

create function public.lili_sync_focus_segments_delta(
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
       and (segment_start at time zone 'Asia/Shanghai')::date between today - 400 and today then
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
      and (s.start_at at time zone 'Asia/Shanghai')::date between today - 400 and today
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
      and (selected.start_at at time zone 'Asia/Shanghai')::date between today - 400 and today
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
        and (selected.start_at at time zone 'Asia/Shanghai')::date between today - 400 and today
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
        and (later.start_at at time zone 'Asia/Shanghai')::date between today - 400 and today
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

revoke execute on function public.lili_sync_focus_segments_delta(jsonb, text)
  from public, anon;
grant execute on function public.lili_sync_focus_segments_delta(jsonb, text)
  to authenticated;

comment on function public.lili_sync_focus_segments_delta(jsonb, text) is
  'Authenticated sealed FocusSegment bootstrap/delta sync using a stable (updated_at, segment_id) cursor; repeated unchanged uploads do not refresh updated_at.';
