-- Raw focus facts are the cross-device source of truth.  Daily/profile
-- totals remain compatibility caches for older clients, but new clients sync
-- immutable-ish intervals and rebuild every projection locally.

create table if not exists public.lili_focus_segments (
  user_id uuid not null references auth.users(id) on delete cascade,
  segment_id text not null,
  session_id text not null default '',
  start_at timestamptz not null,
  end_at timestamptz,
  device_id text not null default '',
  completed boolean not null default false,
  quality smallint not null default 0,
  task text not null default '',
  interruptions smallint not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (user_id, segment_id),
  constraint lili_focus_segments_time_check check (end_at is null or end_at >= start_at),
  constraint lili_focus_segments_quality_check check (quality between 0 and 100),
  constraint lili_focus_segments_interruptions_check check (interruptions >= 0)
);

create index if not exists lili_focus_segments_user_start_idx
  on public.lili_focus_segments (user_id, start_at desc);

alter table public.lili_focus_segments enable row level security;

drop policy if exists "lili_focus_segments_owner_select" on public.lili_focus_segments;
create policy "lili_focus_segments_owner_select"
  on public.lili_focus_segments for select
  to authenticated
  using ((select auth.uid()) = user_id);

drop policy if exists "lili_focus_segments_owner_insert" on public.lili_focus_segments;
create policy "lili_focus_segments_owner_insert"
  on public.lili_focus_segments for insert
  to authenticated
  with check ((select auth.uid()) = user_id);

drop policy if exists "lili_focus_segments_owner_update" on public.lili_focus_segments;
create policy "lili_focus_segments_owner_update"
  on public.lili_focus_segments for update
  to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

revoke all on table public.lili_focus_segments from anon, authenticated;

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
    raise exception '需要登录后才能同步专注区间';
  end if;
  if jsonb_typeof(coalesce(p_segments, '[]'::jsonb)) <> 'array' then
    raise exception '专注区间格式无效';
  end if;

  for item in select value from jsonb_array_elements(coalesce(p_segments, '[]'::jsonb)) loop
    begin
      segment_key := left(btrim(coalesce(item->>'segment_id', '')), 160);
      segment_session := left(btrim(coalesce(item->>'session_id', '')), 160);
      segment_start := (item->>'start_at')::timestamptz;
      segment_end := nullif(item->>'end_at', '')::timestamptz;
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
        start_at = excluded.start_at,
        end_at = excluded.end_at,
        device_id = excluded.device_id,
        completed = excluded.completed,
        quality = excluded.quality,
        task = excluded.task,
        interruptions = excluded.interruptions,
        updated_at = now();
    end if;
  end loop;

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
          'interruptions', s.interruptions
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

