-- Reduce focus-segment egress without changing FocusSession facts or totals.
--
-- The legacy RPC remains available for older desktops and keeps its full
-- snapshot response shape.  Its upsert is made idempotent so a repeated
-- upload does not manufacture a new updated_at value.  New desktops use the
-- delta RPC below: the first request receives the current 400-day snapshot,
-- and later requests receive only rows changed after the saved cursor.

create index if not exists lili_focus_segments_user_updated_idx
  on public.lili_focus_segments(user_id, updated_at, segment_id);

create or replace function public.lili_sync_focus_segments(
  p_segments jsonb default '[]'::jsonb
) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  current_user_id uuid := (select auth.uid());
  today date := (now() at time zone 'Asia/Shanghai')::date;
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
       and (segment_end is null or segment_end >= segment_start)
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

  perform public.lili_reconcile_focus_derived_totals(current_user_id);

  return jsonb_build_object(
    'segments', coalesce((
      select jsonb_agg(
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
          'time_corrected_at', s.time_corrected_at,
          'time_correction_reason', s.time_correction_reason
        ) order by s.start_at
      )
      from public.lili_focus_segments s
      where s.user_id = current_user_id
        and (s.start_at at time zone 'Asia/Shanghai')::date between today - 400 and today
    ), '[]'::jsonb)
  );
end;
$$;

revoke execute on function public.lili_sync_focus_segments(jsonb) from public, anon;
grant execute on function public.lili_sync_focus_segments(jsonb) to authenticated;

create or replace function public.lili_sync_focus_segments_delta(
  p_segments jsonb default '[]'::jsonb,
  p_since timestamptz default null
) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  current_user_id uuid := (select auth.uid());
  today date := (now() at time zone 'Asia/Shanghai')::date;
  sync_watermark timestamptz := clock_timestamp();
  changed_rows integer := 0;
  affected_rows integer := 0;
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
       and (segment_end is null or segment_end >= segment_start)
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
      get diagnostics affected_rows = row_count;
      changed_rows := changed_rows + affected_rows;
    end if;
  end loop;

  if changed_rows > 0 then
    perform public.lili_reconcile_focus_derived_totals(current_user_id);
  end if;

  return jsonb_build_object(
    'segments', coalesce((
      select jsonb_agg(
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
          'time_corrected_at', s.time_corrected_at,
          'time_correction_reason', s.time_correction_reason,
          'updated_at', s.updated_at
        ) order by s.updated_at, s.start_at, s.segment_id
      )
      from public.lili_focus_segments s
      where s.user_id = current_user_id
        and (s.start_at at time zone 'Asia/Shanghai')::date between today - 400 and today
        and (p_since is null or s.updated_at >= p_since)
        and s.updated_at <= sync_watermark
    ), '[]'::jsonb),
    'full_sync', (p_since is null),
    'next_cursor', sync_watermark,
    'has_more', false
  );
end;
$$;

revoke execute on function public.lili_sync_focus_segments_delta(jsonb, timestamptz) from public, anon;
grant execute on function public.lili_sync_focus_segments_delta(jsonb, timestamptz) to authenticated;

comment on function public.lili_sync_focus_segments_delta(jsonb, timestamptz) is
  'Authenticated first-snapshot/incremental focus-segment sync. The cursor is transport metadata; FocusSession rows remain immutable facts.';
