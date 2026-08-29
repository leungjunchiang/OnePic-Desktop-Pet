-- Compatibility personal-state RPCs must not accept client-computed focus
-- durations as facts.  FocusSession rows are the only duration source;
-- profile/day/week values are derived projections maintained by the raw
-- segment reconciliation path.

create or replace function public.lili_sync_personal_state(
  p_focus_date date default ((now() at time zone 'Asia/Shanghai')::date),
  p_today_seconds integer default 0,
  p_lifetime_seconds bigint default 0,
  p_outfit_key text default null,
  p_outfit_set boolean default false
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  current_user_id uuid := (select auth.uid());
  current_day date := ((now() at time zone 'Asia/Shanghai')::date);
  stats jsonb;
  merged_lifetime bigint;
  merged_outfit text;
begin
  if current_user_id is null then
    raise exception '需要登录';
  end if;

  -- p_focus_date and p_today_seconds are intentionally ignored.  They are
  -- client projections and cannot be allowed to resurrect a stale cache.
  update public.lili_profiles p
  set focus_lifetime_seconds = greatest(
        p.focus_lifetime_seconds,
        greatest(0, coalesce(p_lifetime_seconds, 0))
      ),
      outfit_key = case
        when coalesce(p_outfit_set, false)
          then left(btrim(coalesce(p_outfit_key, '')), 60)
        else p.outfit_key
      end,
      updated_at = now()
  where p.user_id = current_user_id
  returning p.focus_lifetime_seconds, p.outfit_key
    into merged_lifetime, merged_outfit;

  if not found then
    raise exception '搭子资料不存在';
  end if;

  stats := public.lili_effective_focus_stats(current_user_id);
  return jsonb_build_object(
    'focus_today_date', current_day,
    'focus_today_seconds', coalesce((stats->>'today_seconds')::integer, 0),
    'focus_lifetime_seconds', merged_lifetime,
    'outfit_key', merged_outfit
  );
end;
$$;

create or replace function public.lili_sync_personal_state(
  p_focus_date date default ((now() at time zone 'Asia/Shanghai')::date),
  p_today_seconds integer default 0,
  p_lifetime_seconds bigint default 0,
  p_outfit_key text default null,
  p_outfit_set boolean default false,
  p_week_start date default (date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date),
  p_week_seconds integer default 0
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  current_user_id uuid := (select auth.uid());
  current_day date := ((now() at time zone 'Asia/Shanghai')::date);
  current_week date := (date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date);
  stats jsonb;
  merged_lifetime bigint;
  merged_outfit text;
begin
  if current_user_id is null then
    raise exception '需要登录';
  end if;

  -- p_focus_date/p_today_seconds/p_week_start/p_week_seconds are retained
  -- only for wire compatibility with older clients.  None of them is used
  -- to update a metric or a daily cache.
  update public.lili_profiles p
  set focus_lifetime_seconds = greatest(
        p.focus_lifetime_seconds,
        greatest(0, coalesce(p_lifetime_seconds, 0))
      ),
      outfit_key = case
        when coalesce(p_outfit_set, false)
          then left(btrim(coalesce(p_outfit_key, '')), 60)
        else p.outfit_key
      end,
      updated_at = now()
  where p.user_id = current_user_id
  returning p.focus_lifetime_seconds, p.outfit_key
    into merged_lifetime, merged_outfit;

  if not found then
    raise exception '搭子资料不存在';
  end if;

  stats := public.lili_effective_focus_stats(current_user_id);
  return jsonb_build_object(
    'focus_today_date', current_day,
    'focus_today_seconds', coalesce((stats->>'today_seconds')::integer, 0),
    'focus_lifetime_seconds', merged_lifetime,
    'focus_week_start_date', current_week,
    'focus_week_seconds', coalesce((stats->>'week_seconds')::integer, 0),
    'outfit_key', merged_outfit
  );
end;
$$;

revoke execute on function public.lili_sync_personal_state(
  date, integer, bigint, text, boolean
) from public, anon;
grant execute on function public.lili_sync_personal_state(
  date, integer, bigint, text, boolean
) to authenticated;

revoke execute on function public.lili_sync_personal_state(
  date, integer, bigint, text, boolean, date, integer
) from public, anon;
grant execute on function public.lili_sync_personal_state(
  date, integer, bigint, text, boolean, date, integer
) to authenticated;
