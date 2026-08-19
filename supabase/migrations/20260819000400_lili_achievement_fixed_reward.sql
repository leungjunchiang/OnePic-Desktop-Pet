-- 成果见证奖励统一为固定 50 个吉他拨片。
-- 旧的 settled 经济记录不回写；新提交不再接受用户自填金额。

create or replace function public.lili_submit_achievement(
  p_kind text,
  p_name text,
  p_amount integer,
  p_note text default ''
) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_month date := date_trunc('month', current_date)::date;
  v_id uuid;
begin
  if auth.uid() is null then raise exception '请先登录'; end if;
  if trim(coalesce(p_name, '')) = '' then raise exception '成果名称不能为空'; end if;
  if exists (
    select 1 from public.lili_achievement_claims c
    where c.owner_id = auth.uid()
      and c.month_key = v_month
      and lower(c.kind) = lower(left(trim(p_kind), 30))
      and lower(c.name) = lower(left(trim(p_name), 90))
      and c.status in ('pending', 'settled')
  ) then raise exception '同月同一成果不能重复提交'; end if;
  insert into public.lili_achievement_claims(owner_id, kind, name, amount, note, month_key)
  values (auth.uid(), left(trim(p_kind), 30), left(trim(p_name), 90), 50, left(coalesce(p_note, ''), 160), v_month)
  returning id into v_id;
  return jsonb_build_object('id', v_id, 'status', 'pending', 'required_witnesses', 2, 'reward', 50);
end;
$$;

revoke all on function public.lili_submit_achievement(text, text, integer, text) from public, anon;
grant execute on function public.lili_submit_achievement(text, text, integer, text) to authenticated;
