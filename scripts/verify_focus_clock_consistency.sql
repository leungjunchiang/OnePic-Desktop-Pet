-- Synthetic account only; no real user's claims, profile or facts are read/modified.
-- Verify persisted intervals survive midnight and totals use Beijing windows.
begin;
set local time zone 'UTC';
do $$
declare owner uuid:=gen_random_uuid();
  day date:=(now() at time zone 'Asia/Shanghai')::date-1;
  begin_at timestamptz;
  result integer;
begin
  insert into auth.users(id,raw_user_meta_data) values(owner,'{}');
  begin_at:=day::timestamp at time zone 'Asia/Shanghai';
  insert into public.lili_focus_segments(user_id,segment_id,session_id,device_id,start_at,end_at) values
    (owner,'full-work','focus-1','mac',begin_at+interval '9 hours',begin_at+interval '15 hours 38 minutes'),
    (owner,'overlap','focus-1','mac',begin_at+interval '10 hours',begin_at+interval '11 hours'),
    (owner,'midnight','focus-2','mac',begin_at+interval '23 hours 58 minutes',begin_at+interval '24 hours 2 minutes');
  result:=public.lili_effective_focus_day_seconds(owner,day);
  if result<>24000 then raise exception 'Yesterday lost facts or double-counted overlap: %',result; end if;
  result:=public.lili_effective_focus_day_seconds(owner,day+1);
  if result<>120 then raise exception 'Midnight leaked previous day: %',result; end if;
  if public.lili_effective_focus_week_seconds(owner) is distinct from
      (public.lili_effective_focus_stats(owner)->>'week_seconds')::integer then
    raise exception 'Leaderboard and dashboard use different week totals';
  end if;
  perform set_config('TimeZone','America/Los_Angeles',true);
  if public.lili_effective_focus_day_seconds(owner,day)<>24000
     or public.lili_effective_focus_day_seconds(owner,day+1)<>120 then
    raise exception 'Server/system timezone changed Beijing totals';
  end if;
end $$;
rollback;
