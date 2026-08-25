-- The profile week counter is a compatibility snapshot.  It can be stale
-- after a second device reconnects, so the leaderboard must use the canonical
-- one-row-per-user-per-Beijing-day ledger whenever it has data for this week.
-- This prevents a peer's 3-hour week from being displayed as 6 or 8 hours.

create or replace function public.lili_focus_weekly_leaderboard(p_period text default 'week') returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  with bounds as (
    select date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date as week_start
  ),
  scored as (
    select
      p.user_id,
      b.week_start,
      case
        when coalesce(d.day_count, 0) > 0
          then greatest(0, least(604800, coalesce(d.week_seconds, 0)))
        when p.focus_week_start_date = b.week_start
          then greatest(0, least(604800, coalesce(p.focus_week_seconds, 0)))
        else 0
      end as week_seconds
    from public.lili_profiles p
    cross join bounds b
    left join lateral (
      select
        count(*)::integer as day_count,
        coalesce(sum(greatest(0, least(86400, d.seconds))), 0)::integer as week_seconds
      from public.lili_focus_daily d
      where d.user_id = p.user_id
        and d.focus_date >= b.week_start
        and d.focus_date < b.week_start + 7
    ) d on true
    where (p.wealth_leaderboard_enabled or not p.wealth_leaderboard_preference_set)
      and (p.user_id = (select auth.uid()) or public.lili_are_buddies((select auth.uid()), p.user_id))
  )
  select coalesce(jsonb_agg(
    jsonb_build_object(
      'user_id', s.user_id,
      'nickname', public.lili_owner_nickname(s.user_id),
      'week_start', s.week_start,
      'week_seconds', s.week_seconds
    )
    order by s.week_seconds desc, public.lili_owner_nickname(s.user_id)
  ), '[]'::jsonb)
  from scored s;
$$;

revoke execute on function public.lili_focus_weekly_leaderboard(text) from public, anon;
grant execute on function public.lili_focus_weekly_leaderboard(text) to authenticated;
