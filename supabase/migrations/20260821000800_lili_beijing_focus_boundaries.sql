-- Repair already deployed weekly focus functions to use the same Beijing
-- calendar as the desktop client.  The earlier migration used current_date,
-- which is UTC in Supabase and can disagree with a user's 00:00–23:59 day.

update public.lili_profiles
set focus_week_seconds = 0,
    focus_week_start_date = date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date
where focus_week_start_date < date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date;

create or replace function public.lili_sync_personal_state(
  p_focus_date date default (now() at time zone 'Asia/Shanghai')::date,
  p_today_seconds integer default 0,
  p_lifetime_seconds bigint default 0,
  p_outfit_key text default null,
  p_outfit_set boolean default false,
  p_week_start date default date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date,
  p_week_seconds integer default 0
) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  target_date date := coalesce(p_focus_date, (now() at time zone 'Asia/Shanghai')::date);
  target_week date := coalesce(p_week_start, date_trunc('week', target_date)::date);
  merged_today integer;
  merged_lifetime bigint;
  merged_week integer;
  merged_outfit text;
begin
  update public.lili_profiles p
  set focus_today_seconds = case
        when target_date > p.focus_today_date
          then greatest(0, least(86400, coalesce(p_today_seconds, 0)))
        when target_date = p.focus_today_date
          then greatest(p.focus_today_seconds, greatest(0, least(86400, coalesce(p_today_seconds, 0))))
        else p.focus_today_seconds
      end,
      focus_today_date = greatest(p.focus_today_date, target_date),
      focus_lifetime_seconds = greatest(p.focus_lifetime_seconds, greatest(0, coalesce(p_lifetime_seconds, 0))),
      focus_week_seconds = case
        when target_week > p.focus_week_start_date
          then greatest(0, least(604800, coalesce(p_week_seconds, 0)))
        when target_week = p.focus_week_start_date
          then greatest(p.focus_week_seconds, greatest(0, least(604800, coalesce(p_week_seconds, 0))))
        else p.focus_week_seconds
      end,
      focus_week_start_date = greatest(p.focus_week_start_date, target_week),
      outfit_key = case
        when coalesce(p_outfit_set, false) then left(btrim(coalesce(p_outfit_key, '')), 60)
        else p.outfit_key
      end,
      updated_at = now()
  where p.user_id = (select auth.uid())
  returning p.focus_today_seconds, p.focus_lifetime_seconds,
            p.focus_week_seconds, p.outfit_key
    into merged_today, merged_lifetime, merged_week, merged_outfit;

  if not found then
    raise exception '搭子资料不存在';
  end if;

  return jsonb_build_object(
    'focus_today_date', target_date,
    'focus_today_seconds', merged_today,
    'focus_lifetime_seconds', merged_lifetime,
    'focus_week_start_date', target_week,
    'focus_week_seconds', merged_week,
    'outfit_key', merged_outfit
  );
end;
$$;

revoke execute on function public.lili_sync_personal_state(date, integer, bigint, text, boolean, date, integer) from public, anon;
grant execute on function public.lili_sync_personal_state(date, integer, bigint, text, boolean, date, integer) to authenticated;

create or replace function public.lili_focus_weekly_leaderboard(p_period text default 'week') returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(jsonb_agg(
    jsonb_build_object(
      'user_id', p.user_id,
      'nickname', public.lili_owner_nickname(p.user_id),
      'week_start', p.focus_week_start_date,
      'week_seconds', case
        when p.focus_week_start_date = date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date
          then greatest(0, p.focus_week_seconds)
        else 0
      end
    )
    order by
      case when p.focus_week_start_date = date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date
        then greatest(0, p.focus_week_seconds) else 0 end desc,
      public.lili_owner_nickname(p.user_id)
  ), '[]'::jsonb)
  from public.lili_profiles p
  where (p.wealth_leaderboard_enabled or not p.wealth_leaderboard_preference_set)
    and (p.user_id = (select auth.uid()) or public.lili_are_buddies((select auth.uid()), p.user_id));
$$;

revoke execute on function public.lili_focus_weekly_leaderboard(text) from public, anon;
grant execute on function public.lili_focus_weekly_leaderboard(text) to authenticated;
