-- 荒野国王富豪榜默认参加，只有用户主动关闭后才退出。
-- 旧版本只有一个 enabled 字段，无法区分“从未设置”与“主动关闭”；
-- 因此不批量覆盖旧值，而是在榜单查询中把 preference_set=false 视为默认参加。

alter table public.lili_profiles
  add column if not exists wealth_leaderboard_preference_set boolean not null default false;

create or replace function public.lili_economy_leaderboard(p_period text default 'month') returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  with bounds as (
    select case
      when lower(coalesce(p_period, 'month')) = 'week' then current_date - 6
      when lower(coalesce(p_period, 'month')) = 'all' then date '1970-01-01'
      else date_trunc('month', current_date)::date
    end as start_date
  ), eligible as (
    select p.user_id, p.nickname
    from public.lili_profiles p
    where (p.wealth_leaderboard_enabled or not p.wealth_leaderboard_preference_set)
      and (p.user_id = auth.uid() or public.lili_are_buddies(auth.uid(), p.user_id))
  ), totals as (
    select
      e.user_id,
      coalesce(sum(e.amount), 0)::integer as net_worth,
      coalesce(sum(e.amount) filter (
        where e.occurred_on >= b.start_date
          and e.amount > 0
          and e.category not in ('gift_received', 'refund')
      ), 0)::integer as period_income,
      coalesce(abs(sum(e.amount) filter (
        where e.occurred_on >= b.start_date and e.amount < 0
      )), 0)::integer as spent,
      coalesce(sum(e.amount) filter (
        where e.occurred_on >= b.start_date
          and e.amount > 0
          and e.category in ('windfall', 'achievement_income')
      ), 0)::integer as windfall
    from public.lili_economy_events e
    cross join bounds b
    group by e.user_id
  )
  select coalesce(jsonb_agg(
    jsonb_build_object(
      'user_id', x.user_id,
      'nickname', x.nickname,
      'net_worth', coalesce(t.net_worth, 0),
      'period_income', coalesce(t.period_income, 0),
      'spent', coalesce(t.spent, 0),
      'windfall', coalesce(t.windfall, 0)
    )
    order by coalesce(t.period_income, 0) desc,
             coalesce(t.windfall, 0) desc,
             x.nickname
  ), '[]'::jsonb)
  from eligible x
  left join totals t on t.user_id = x.user_id;
$$;

revoke execute on function public.lili_economy_leaderboard(text) from public, anon;
grant execute on function public.lili_economy_leaderboard(text) to authenticated;
