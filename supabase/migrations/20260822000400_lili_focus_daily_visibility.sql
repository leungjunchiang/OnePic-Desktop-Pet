-- lili_focus_daily is a permanent, one-row-per-account-per-Beijing-day
-- summary. It is intentionally not deleted: lifetime totals, streaks and
-- future reports must not depend on short-lived presence data.
--
-- The sync response is a temporary two-day view. On the 23rd, 21st is no
-- longer returned to the desktop even though its compact daily summary stays
-- available for permanent account statistics.

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

  -- Keep accepting recent local summaries so the permanent daily summary can
  -- recover after a new computer comes online. This table is not presence
  -- data and is deliberately retained for long-term account statistics.
  for item in select value from jsonb_array_elements(coalesce(p_history, '[]'::jsonb)) loop
    item_date := null;
    item_seconds := 0;
    if coalesce(item->>'focus_date', '') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then
      begin
        item_date := (item->>'focus_date')::date;
        item_seconds := greatest(0, least(86400, coalesce((item->>'seconds')::integer, 0)));
      exception when others then
        item_date := null;
      end;
    end if;

    if item_date is not null
       and item_date between today - 400 and today then
      insert into public.lili_focus_daily (user_id, focus_date, seconds, updated_at)
      values (current_user_id, item_date, item_seconds, now())
      on conflict (user_id, focus_date) do update
        set seconds = greatest(public.lili_focus_daily.seconds, excluded.seconds),
            updated_at = now();
    end if;
  end loop;

  -- Only the short-lived comparison window is returned to the client.
  return jsonb_build_object(
    'focus_date', today,
    'retention_days', 2,
    'days', coalesce((
      select jsonb_agg(
        jsonb_build_object('focus_date', d.focus_date, 'seconds', d.seconds)
        order by d.focus_date
      )
      from public.lili_focus_daily d
      where d.user_id = current_user_id
        and d.focus_date between today - 1 and today
    ), '[]'::jsonb)
  );
end;
$$;

revoke execute on function public.lili_sync_focus_history(jsonb) from public, anon;
grant execute on function public.lili_sync_focus_history(jsonb) to authenticated;
