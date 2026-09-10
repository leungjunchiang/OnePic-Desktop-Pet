-- 六毛经济系统 v2：保留旧账本与余额，补充可审计字段并按本月创收排名。
-- 这里不保存皮肤、娃衣或本地库存；桌面生活库存仍由客户端本地账本维护。

alter table public.lili_economy_events
  add column if not exists direction text,
  add column if not exists source text,
  add column if not exists metadata jsonb not null default '{}'::jsonb;

update public.lili_economy_events
set direction = case
  when amount > 0 then 'income'
  when amount < 0 then 'expense'
  else 'event'
end
where direction is null;

update public.lili_economy_events
set source = source_key
where source is null;

alter table public.lili_economy_events
  alter column direction set default 'event',
  alter column direction set not null,
  drop constraint if exists lili_economy_events_direction_check,
  add constraint lili_economy_events_direction_check
    check (direction in ('income', 'expense', 'event')),
  drop constraint if exists lili_economy_events_category_check,
  add constraint lili_economy_events_category_check
    check (category in (
      'salary', 'focus_wage', 'early_bird', 'early_bird_bonus',
      'performance', 'windfall', 'achievement_income',
      'social_reward', 'special_reward', 'reward', 'spend', 'expense',
      'gift_sent', 'gift_received', 'item_use', 'other'
    ));

create index if not exists lili_economy_user_category_date_idx
  on public.lili_economy_events(user_id, category, occurred_on desc);

create or replace function public.lili_record_economy_event(
  p_event_id text,
  p_category text,
  p_amount integer,
  p_label text,
  p_source_key text,
  p_occurred_on date
) returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_direction text;
begin
  if auth.uid() is null then
    raise exception '请先登录';
  end if;
  if p_category not in (
    'salary', 'focus_wage', 'early_bird', 'early_bird_bonus',
    'performance', 'windfall', 'achievement_income',
    'social_reward', 'special_reward', 'reward', 'spend', 'expense',
    'gift_sent', 'gift_received', 'item_use', 'other'
  ) then
    raise exception '无效的财富类别';
  end if;
  v_direction := case
    when p_amount > 0 then 'income'
    when p_amount < 0 then 'expense'
    else 'event'
  end;
  insert into public.lili_economy_events(
    event_id, user_id, category, amount, label, source_key,
    occurred_on, direction, source, metadata
  )
  values (
    left(trim(p_event_id), 80), auth.uid(), p_category, p_amount,
    left(trim(p_label), 120), left(trim(p_source_key), 160),
    coalesce(p_occurred_on, current_date), v_direction,
    left(trim(p_source_key), 160), '{}'::jsonb
  )
  on conflict do nothing;
end;
$$;

revoke execute on function public.lili_record_economy_event(text,text,integer,text,text,date) from public, anon;
grant execute on function public.lili_record_economy_event(text,text,integer,text,text,date) to authenticated;

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
    select p.user_id, p.nickname, p.wealth_leaderboard_enabled
    from public.lili_profiles p
    where p.user_id = auth.uid()
      or (
        p.wealth_leaderboard_enabled
        and public.lili_are_buddies(auth.uid(), p.user_id)
      )
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
        where e.occurred_on >= b.start_date
          and e.amount < 0
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
