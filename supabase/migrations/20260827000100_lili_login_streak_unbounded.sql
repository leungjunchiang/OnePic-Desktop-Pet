-- Keep the real consecutive-login count after the three-day reward is
-- unlocked.  Older deployments capped the stored value at 3, which made the
-- server disagree with the login-day number shown by newer clients.

alter table public.lili_login_streaks
  drop constraint if exists lili_login_streaks_days_check;

alter table public.lili_login_streaks
  add constraint lili_login_streaks_days_check check (streak_days >= 0);

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
    current_days := greatest(0, current_days) + 1;
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
      current_user_id, today, current_days,
      current_reward or current_days >= 3, now()
    )
    on conflict (user_id) do update
      set last_login_date = excluded.last_login_date,
          streak_days = excluded.streak_days,
          reward_unlocked = public.lili_login_streaks.reward_unlocked
            or excluded.reward_unlocked,
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
