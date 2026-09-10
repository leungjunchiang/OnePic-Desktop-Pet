-- 六毛经济账本与荒野王国榜单使用同一套“创收”口径。
-- 本地 EconomyLedger 的 INCOME_CATEGORIES 是唯一产品口径；礼物、消费和
-- 仅记录生活事件的类别不会被榜单误算为本月创收。

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
          and e.category in (
            'salary', 'focus_wage', 'performance', 'windfall',
            'achievement_income', 'social_reward', 'special_reward', 'reward'
          )
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
