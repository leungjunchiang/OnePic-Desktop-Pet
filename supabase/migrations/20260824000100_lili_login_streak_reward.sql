-- Account-bound consecutive-login reward. The desktop only sends the
-- authenticated RPC; the calendar date and streak transition are calculated
-- on the server in Asia/Shanghai so local clocks cannot reset or inflate it.

create table if not exists public.lili_login_streaks (
  user_id uuid not null references auth.users(id) on delete cascade,
  last_login_date date,
  streak_days integer not null default 0,
  reward_unlocked boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (user_id),
  constraint lili_login_streaks_days_check check (streak_days between 0 and 3)
);

alter table public.lili_login_streaks enable row level security;

drop policy if exists "lili_login_streaks_owner_select" on public.lili_login_streaks;
create policy "lili_login_streaks_owner_select"
  on public.lili_login_streaks for select
  to authenticated
  using ((select auth.uid()) = user_id);

revoke all on table public.lili_login_streaks from anon, authenticated;

create or replace function public.lili_record_login()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  current_user_id uuid := (select auth.uid());
  today date := (now() at time zone 'Asia/Shanghai')::date;
  previous_date date;
  current_days integer;
  current_reward boolean;
  already_today boolean := false;
  newly_unlocked boolean := false;
begin
  if current_user_id is null then
    raise exception '需要登录后才能记录登录';
  end if;

  select s.last_login_date, s.streak_days, s.reward_unlocked
    into previous_date, current_days, current_reward
    from public.lili_login_streaks s
   where s.user_id = current_user_id
   for update;

  if not found then
    current_days := 1;
    current_reward := false;
  elsif previous_date = today then
    already_today := true;
  elsif previous_date = today - 1 then
    current_days := least(3, greatest(0, current_days) + 1);
  else
    current_days := 1;
  end if;

  if not already_today then
    if current_days >= 3 and not current_reward then
      newly_unlocked := true;
    end if;

    insert into public.lili_login_streaks (
      user_id, last_login_date, streak_days, reward_unlocked, updated_at
    )
    values (
      current_user_id, today, current_days, current_reward or current_days >= 3, now()
    )
    on conflict (user_id) do update
      set last_login_date = excluded.last_login_date,
          streak_days = excluded.streak_days,
          reward_unlocked = public.lili_login_streaks.reward_unlocked or excluded.reward_unlocked,
          updated_at = now();
  end if;

  return jsonb_build_object(
    'login_date', today,
    'streak_days', greatest(0, coalesce(current_days, 0)),
    'reward_unlocked', coalesce(current_reward or current_days >= 3, false),
    'newly_unlocked', newly_unlocked
  );
end;
$$;

revoke all on function public.lili_record_login() from public, anon, authenticated;
grant execute on function public.lili_record_login() to authenticated;

