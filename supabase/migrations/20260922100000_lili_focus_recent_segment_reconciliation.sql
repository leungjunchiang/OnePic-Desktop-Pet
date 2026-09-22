-- Bounded, read-only recovery for a client whose historical FocusSegment
-- cache was clipped by an older 500-row local limit.  This deliberately
-- does not alter the ordinary updated_at delta stream or any canonical row.

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
  bounded_days integer := greatest(1, least(coalesce(p_days, 60), 90));
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

  window_start := (
    ((now() at time zone 'Asia/Shanghai')::date - (bounded_days - 1))
    ::timestamp at time zone 'Asia/Shanghai'
  );
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
      and s.end_at > window_start
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
        and s.end_at > window_start
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
  'Read-only paged reconciliation of one authenticated account''s recent sealed FocusSegments; does not write facts or delta cursor state.';
