-- Prefer the raw interval facts for the weekly leaderboard.  The daily table
-- remains a compatibility fallback for accounts that have not upgraded yet.

create or replace function public.lili_focus_weekly_leaderboard(p_period text default 'week') returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  with bounds as (
    select
      date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date as week_start,
      (date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date at time zone 'Asia/Shanghai') as week_start_at,
      ((date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date + 7) at time zone 'Asia/Shanghai') as week_end_at
  ),
  scored as (
    select
      p.user_id,
      b.week_start,
      case
        when coalesce(seg.segment_count, 0) > 0
          then greatest(0, least(604800, coalesce(seg.week_seconds, 0)))
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
        count(*)::integer as segment_count,
        coalesce(sum(extract(epoch from upper(r) - lower(r)))::integer, 0) as week_seconds
      from (
        select unnest(range_agg(
          tstzrange(
            greatest(s.start_at, b.week_start_at),
            least(coalesce(s.end_at, now()), b.week_end_at),
            '[)'
          )
        )) as r
        from public.lili_focus_segments s
        where s.user_id = p.user_id
          and s.start_at < b.week_end_at
          and coalesce(s.end_at, now()) > b.week_start_at
          and (s.end_at is null or s.end_at >= s.start_at)
      ) merged
    ) seg on true
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

