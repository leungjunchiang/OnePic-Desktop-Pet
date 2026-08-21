-- 收支记录只保留最近 31 天；余额和长期生活图鉴仍由客户端独立维护。
-- 每次写入时清理当前账号的旧记录，避免换电脑后云端旧账又重新出现。

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
  v_occurred_on date := coalesce(p_occurred_on, current_date);
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
  -- A delayed offline event older than the retention window is not stored.
  if v_occurred_on < current_date - 31 then
    return;
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
    v_occurred_on, v_direction,
    left(trim(p_source_key), 160), '{}'::jsonb
  )
  on conflict do nothing;

  delete from public.lili_economy_events
  where user_id = auth.uid()
    and occurred_on < current_date - 31;
end;
$$;

revoke execute on function public.lili_record_economy_event(text,text,integer,text,text,date) from public, anon;
grant execute on function public.lili_record_economy_event(text,text,integer,text,text,date) to authenticated;

delete from public.lili_economy_events
where occurred_on < current_date - 31;
