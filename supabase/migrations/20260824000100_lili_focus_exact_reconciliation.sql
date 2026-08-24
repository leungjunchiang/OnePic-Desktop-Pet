-- Corrected clients send account-scoped Beijing totals calculated from their
-- detailed local FocusSession ledger.  The old greatest() merge made a bad
-- snapshot impossible to repair: a stale 53-hour week could only increase.
-- These assignments are intentionally limited to the supplied current day
-- and week, while lifetime remains monotonic.

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
          then greatest(0, least(86400, coalesce(p_today_seconds, 0)))
        else p.focus_today_seconds
      end,
      focus_today_date = greatest(p.focus_today_date, target_date),
      focus_lifetime_seconds = greatest(p.focus_lifetime_seconds, greatest(0, coalesce(p_lifetime_seconds, 0))),
      focus_week_seconds = case
        when target_week > p.focus_week_start_date
          then greatest(0, least(604800, coalesce(p_week_seconds, 0)))
        when target_week = p.focus_week_start_date
          then greatest(0, least(604800, coalesce(p_week_seconds, 0)))
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

  insert into public.lili_focus_daily (user_id, focus_date, seconds, updated_at)
  values (
    (select auth.uid()),
    target_date,
    greatest(0, least(86400, coalesce(p_today_seconds, 0))),
    now()
  )
  on conflict (user_id, focus_date) do update
    set seconds = excluded.seconds,
        updated_at = now();

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

create or replace function public.lili_sync_focus_history(
  p_history jsonb default '[]'::jsonb
) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  current_user_id uuid := (select auth.uid());
  today date := (now() at time zone 'Asia/Shanghai')::date;
  item jsonb;
  item_date date;
  item_seconds integer;
begin
  if current_user_id is null then
    raise exception '需要登录后才能同步专注历史';
  end if;
  if jsonb_typeof(coalesce(p_history, '[]'::jsonb)) <> 'array' then
    raise exception '专注历史格式无效';
  end if;

  for item in select value from jsonb_array_elements(coalesce(p_history, '[]'::jsonb)) loop
    begin
      item_date := (item->>'focus_date')::date;
      item_seconds := greatest(0, least(86400, coalesce((item->>'seconds')::integer, 0)));
    exception when others then
      item_date := null;
    end;
    if item_date is not null and item_date between today - 400 and today then
      insert into public.lili_focus_daily (user_id, focus_date, seconds, updated_at)
      values (current_user_id, item_date, item_seconds, now())
      on conflict (user_id, focus_date) do update
        set seconds = excluded.seconds,
            updated_at = now();
    end if;
  end loop;

  return jsonb_build_object(
    'focus_date', today,
    'days', coalesce((
      select jsonb_agg(
        jsonb_build_object('focus_date', d.focus_date, 'seconds', d.seconds)
        order by d.focus_date
      )
      from public.lili_focus_daily d
      where d.user_id = current_user_id
        and d.focus_date between today - 7 and today
    ), '[]'::jsonb)
  );
end;
$$;

revoke execute on function public.lili_sync_focus_history(jsonb) from public, anon;
grant execute on function public.lili_sync_focus_history(jsonb) to authenticated;

