-- 成果见证：成果先进入待见证，2 名不同搭子确认后才形成经济收入。
-- 这张表不保存论文文件或隐私材料，只保存用户主动提交的简短成果记录。

create table if not exists public.lili_achievement_claims (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  kind text not null check (char_length(kind) between 1 and 30),
  name text not null check (char_length(name) between 1 and 90),
  amount integer not null check (amount between 1 and 100),
  note text not null default '' check (char_length(note) <= 160),
  month_key date not null default date_trunc('month', current_date)::date,
  status text not null default 'pending' check (status in ('pending', 'settled', 'monthly_limit', 'deleted')),
  created_at timestamptz not null default now(),
  settled_at timestamptz,
  settled_event_id text
);

create unique index if not exists lili_achievement_owner_name_month_unique
  on public.lili_achievement_claims(owner_id, lower(kind), lower(name), month_key)
  where status in ('pending', 'settled');

create table if not exists public.lili_achievement_witnesses (
  achievement_id uuid not null references public.lili_achievement_claims(id) on delete cascade,
  witness_id uuid not null references auth.users(id) on delete cascade,
  confirmed_at timestamptz not null default now(),
  primary key (achievement_id, witness_id)
);

alter table public.lili_achievement_claims enable row level security;
alter table public.lili_achievement_witnesses enable row level security;

drop policy if exists lili_achievement_claims_owner_read on public.lili_achievement_claims;
create policy lili_achievement_claims_owner_read on public.lili_achievement_claims
  for select to authenticated using (owner_id = auth.uid());

drop policy if exists lili_achievement_claims_buddy_read on public.lili_achievement_claims;
create policy lili_achievement_claims_buddy_read on public.lili_achievement_claims
  for select to authenticated using (
    public.lili_are_buddies(auth.uid(), owner_id)
    and status = 'pending'
  );

drop policy if exists lili_achievement_witnesses_owner_read on public.lili_achievement_witnesses;
create policy lili_achievement_witnesses_owner_read on public.lili_achievement_witnesses
  for select to authenticated using (
    exists (
      select 1 from public.lili_achievement_claims c
      where c.id = achievement_id and c.owner_id = auth.uid()
    )
  );

drop policy if exists lili_achievement_witnesses_buddy_read on public.lili_achievement_witnesses;
create policy lili_achievement_witnesses_buddy_read on public.lili_achievement_witnesses
  for select to authenticated using (witness_id = auth.uid());

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
  if p_amount < 1 or p_amount > 100 then raise exception '成果奖励必须在1到100个吉他拨片之间'; end if;
  if exists (
    select 1 from public.lili_achievement_claims c
    where c.owner_id = auth.uid()
      and c.month_key = v_month
      and lower(c.kind) = lower(left(trim(p_kind), 30))
      and lower(c.name) = lower(left(trim(p_name), 90))
      and c.status in ('pending', 'settled')
  ) then raise exception '同月同一成果不能重复提交'; end if;
  insert into public.lili_achievement_claims(owner_id, kind, name, amount, note, month_key)
  values (auth.uid(), left(trim(p_kind), 30), left(trim(p_name), 90), p_amount, left(coalesce(p_note, ''), 160), v_month)
  returning id into v_id;
  return jsonb_build_object('id', v_id, 'status', 'pending', 'required_witnesses', 2);
end;
$$;

create or replace function public.lili_confirm_achievement(
  p_achievement_id uuid,
  p_accept boolean default true
) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_claim public.lili_achievement_claims%rowtype;
  v_count integer;
  v_event_id text;
  v_source_key text;
begin
  if auth.uid() is null then raise exception '请先登录'; end if;
  select * into v_claim from public.lili_achievement_claims where id = p_achievement_id for update;
  if not found then raise exception '成果不存在'; end if;
  if v_claim.owner_id = auth.uid() then raise exception '不能为自己的成果作见证'; end if;
  if not public.lili_are_buddies(auth.uid(), v_claim.owner_id) then raise exception '只有搭子可以作成果见证'; end if;
  if v_claim.status <> 'pending' then return jsonb_build_object('status', v_claim.status); end if;
  if not coalesce(p_accept, true) then return jsonb_build_object('status', 'declined'); end if;
  if (select count(*) from public.lili_achievement_claims c
      where c.owner_id = v_claim.owner_id and c.month_key = v_claim.month_key and c.status = 'settled') >= 3 then
    update public.lili_achievement_claims set status = 'monthly_limit' where id = v_claim.id;
    return jsonb_build_object('status', 'monthly_limit');
  end if;
  insert into public.lili_achievement_witnesses(achievement_id, witness_id)
  values (v_claim.id, auth.uid()) on conflict do nothing;
  v_count := (select count(*) from public.lili_achievement_witnesses where achievement_id = v_claim.id);
  if v_count < 2 then return jsonb_build_object('status', 'pending', 'witness_count', v_count); end if;
  v_event_id := 'achievement-' || replace(v_claim.id::text, '-', '');
  v_source_key := 'achievement:witnessed:' || v_claim.id::text;
  insert into public.lili_economy_events(
    event_id, user_id, category, amount, label, source_key, occurred_on, direction, source, metadata
  ) values (
    v_event_id, v_claim.owner_id, 'windfall', v_claim.amount,
    left(v_claim.kind || '：' || v_claim.name, 120), v_source_key, current_date,
    'income', 'achievement_witness', jsonb_build_object(
      'achievement_id', v_claim.id, 'achievement_witnessed', true,
      'witness_ids', (select jsonb_agg(witness_id) from public.lili_achievement_witnesses where achievement_id = v_claim.id)
    )
  ) on conflict do nothing;
  update public.lili_achievement_claims
    set status = 'settled', settled_at = now(), settled_event_id = v_event_id
    where id = v_claim.id;
  return jsonb_build_object('status', 'settled', 'witness_count', v_count, 'event_id', v_event_id);
end;
$$;

revoke all on function public.lili_submit_achievement(text, text, integer, text) from public, anon;
grant execute on function public.lili_submit_achievement(text, text, integer, text) to authenticated;
revoke all on function public.lili_confirm_achievement(uuid, boolean) from public, anon;
grant execute on function public.lili_confirm_achievement(uuid, boolean) to authenticated;
