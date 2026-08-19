-- 成果见证收口：提交者手动指定两名搭子，固定奖励 200 吉他拨片。
-- 替补只允许一轮；所有经济结算由服务端幂等完成。

alter table public.lili_achievement_claims
  add column if not exists replacement_round integer not null default 0;

alter table public.lili_achievement_claims
  drop constraint if exists lili_achievement_claims_amount_check,
  add constraint lili_achievement_claims_amount_check check (amount between 1 and 200),
  drop constraint if exists lili_achievement_claims_status_check,
  drop constraint if exists lili_achievement_claims_status_v2,
  add constraint lili_achievement_claims_status_check check (
    status in ('draft', 'pending', 'need_replacement', 'approved', 'settled', 'rejected', 'expired', 'monthly_limit', 'deleted')
  );

create table if not exists public.lili_achievement_witness_invites (
  achievement_id uuid not null references public.lili_achievement_claims(id) on delete cascade,
  witness_id uuid not null references auth.users(id) on delete cascade,
  replacement_round integer not null default 0 check (replacement_round in (0, 1)),
  slot integer not null check (slot between 1 and 2),
  status text not null default 'pending' check (status in ('pending', 'accepted', 'rejected', 'expired')),
  invited_at timestamptz not null default now(),
  responded_at timestamptz,
  primary key (achievement_id, witness_id),
  unique (achievement_id, replacement_round, slot)
);

alter table public.lili_achievement_witness_invites enable row level security;

drop policy if exists lili_achievement_invites_owner_read on public.lili_achievement_witness_invites;
create policy lili_achievement_invites_owner_read on public.lili_achievement_witness_invites
  for select to authenticated using (
    exists (
      select 1 from public.lili_achievement_claims c
      where c.id = achievement_id and c.owner_id = auth.uid()
    )
  );

drop policy if exists lili_achievement_invites_witness_read on public.lili_achievement_witness_invites;
create policy lili_achievement_invites_witness_read on public.lili_achievement_witness_invites
  for select to authenticated using (witness_id = auth.uid());

create index if not exists lili_achievement_invites_witness_pending_idx
  on public.lili_achievement_witness_invites(witness_id, status, invited_at desc);

drop function if exists public.lili_submit_achievement(text, text, integer, text);

create or replace function public.lili_submit_achievement(
  p_kind text,
  p_name text,
  p_amount integer,
  p_note text,
  p_witness_ids uuid[]
) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_month date := date_trunc('month', current_date)::date;
  v_id uuid;
  v_index integer;
  v_witness uuid;
begin
  if auth.uid() is null then raise exception '请先登录'; end if;
  if trim(coalesce(p_name, '')) = '' then raise exception '成果名称不能为空'; end if;
  if coalesce(array_length(p_witness_ids, 1), 0) <> 2 then
    raise exception '成果见证必须指定两名不同搭子';
  end if;
  if p_witness_ids[1] = p_witness_ids[2] or auth.uid() = any(p_witness_ids) then
    raise exception '见证人必须是两名不同的搭子，不能选择自己';
  end if;
  if (
    select count(*) from public.lili_achievement_claims c
    where c.owner_id = auth.uid() and c.month_key = v_month and c.status <> 'deleted'
  ) >= 4 then
    raise exception '本月最多发起4次成果见证申请';
  end if;
  if exists (
    select 1 from public.lili_achievement_claims c
    where c.owner_id = auth.uid()
      and c.month_key = v_month
      and lower(c.kind) = lower(left(trim(p_kind), 30))
      and lower(c.name) = lower(left(trim(p_name), 90))
      and c.status in ('pending', 'need_replacement', 'settled', 'approved')
  ) then raise exception '同月同一成果不能重复提交'; end if;
  foreach v_witness in array p_witness_ids loop
    if not public.lili_are_buddies(auth.uid(), v_witness) then
      raise exception '只能邀请已经建立搭子关系的人见证';
    end if;
  end loop;
  insert into public.lili_achievement_claims(owner_id, kind, name, amount, note, month_key, status, replacement_round)
  values (auth.uid(), left(trim(p_kind), 30), left(trim(p_name), 90), 200,
          left(coalesce(p_note, ''), 160), v_month, 'pending', 0)
  returning id into v_id;
  for v_index in 1..2 loop
    insert into public.lili_achievement_witness_invites(achievement_id, witness_id, slot)
    values (v_id, p_witness_ids[v_index], v_index);
  end loop;
  return jsonb_build_object(
    'id', v_id, 'status', 'pending', 'required_witnesses', 2,
    'reward', 200, 'replacement_round', 0
  );
end;
$$;

-- Old clients cannot silently submit an unaddressed claim after this migration.
create or replace function public.lili_submit_achievement(
  p_kind text, p_name text, p_amount integer, p_note text default ''
) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
begin
  raise exception '请先选择两名搭子作为成果见证人';
end;
$$;

create or replace function public.lili_respond_achievement_witness(
  p_achievement_id uuid,
  p_accept boolean default false
) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_claim public.lili_achievement_claims%rowtype;
  v_invite public.lili_achievement_witness_invites%rowtype;
  v_accepted integer;
  v_event_id text;
begin
  if auth.uid() is null then raise exception '请先登录'; end if;
  select * into v_claim from public.lili_achievement_claims c
    where c.id = p_achievement_id for update;
  if not found then raise exception '成果不存在'; end if;
  select * into v_invite from public.lili_achievement_witness_invites i
    where i.achievement_id = p_achievement_id and i.witness_id = auth.uid()
    order by i.replacement_round desc, i.invited_at desc
    limit 1 for update;
  if not found then raise exception '你不在这项成果的受邀见证人中'; end if;
  if v_invite.status <> 'pending' then
    return jsonb_build_object('status', v_invite.status, 'achievement_id', p_achievement_id);
  end if;
  update public.lili_achievement_witness_invites
    set status = case when coalesce(p_accept, false) then 'accepted' else 'rejected' end,
        responded_at = now()
    where achievement_id = v_invite.achievement_id and witness_id = v_invite.witness_id;
  if not coalesce(p_accept, false) then
    if v_claim.replacement_round = 0 then
      update public.lili_achievement_claims set status = 'need_replacement' where id = v_claim.id;
    else
      update public.lili_achievement_claims set status = 'rejected' where id = v_claim.id;
    end if;
    return jsonb_build_object('status', 'rejected', 'achievement_id', p_achievement_id);
  end if;
  select count(*) into v_accepted
    from public.lili_achievement_witness_invites i
    where i.achievement_id = v_claim.id and i.status = 'accepted';
  if v_accepted < 2 then
    return jsonb_build_object('status', 'pending', 'witness_count', v_accepted, 'achievement_id', p_achievement_id);
  end if;
  if (
    select count(*) from public.lili_achievement_claims c
    where c.owner_id = v_claim.owner_id and c.month_key = v_claim.month_key
      and c.status in ('approved', 'settled')
  ) >= 3 then
    update public.lili_achievement_claims set status = 'monthly_limit' where id = v_claim.id;
    return jsonb_build_object('status', 'monthly_limit', 'achievement_id', p_achievement_id);
  end if;
  v_event_id := 'achievement-' || replace(v_claim.id::text, '-', '');
  insert into public.lili_economy_events(
    event_id, user_id, category, amount, label, source_key, occurred_on, direction, source, metadata
  ) values (
    v_event_id, v_claim.owner_id, 'windfall', 200,
    left(v_claim.kind || '：' || v_claim.name, 120),
    'achievement:witnessed:' || v_claim.id::text, current_date, 'income', 'achievement_witness',
    jsonb_build_object(
      'achievement_id', v_claim.id, 'achievement_witnessed', true,
      'fixed_reward', 200,
      'witness_ids', (select jsonb_agg(i.witness_id) from public.lili_achievement_witness_invites i
                      where i.achievement_id = v_claim.id and i.status = 'accepted')
    )
  ) on conflict do nothing;
  update public.lili_achievement_claims
    set amount = 200, status = 'approved', settled_at = coalesce(settled_at, now()), settled_event_id = v_event_id
    where id = v_claim.id;
  return jsonb_build_object('status', 'approved', 'reward', 200, 'event_id', v_event_id, 'achievement_id', p_achievement_id);
end;
$$;

create or replace function public.lili_confirm_achievement(
  p_achievement_id uuid, p_accept boolean default true
) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
begin
  return public.lili_respond_achievement_witness(p_achievement_id, p_accept);
end;
$$;

create or replace function public.lili_replace_achievement_witnesses(
  p_achievement_id uuid,
  p_witness_ids uuid[]
) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_claim public.lili_achievement_claims%rowtype;
  v_accepted integer;
  v_needed integer;
  v_index integer;
  v_witness uuid;
begin
  if auth.uid() is null then raise exception '请先登录'; end if;
  select * into v_claim from public.lili_achievement_claims c
    where c.id = p_achievement_id and c.owner_id = auth.uid() for update;
  if not found then raise exception '成果不存在或无权修改'; end if;
  if v_claim.replacement_round <> 0 or v_claim.status <> 'need_replacement' then
    raise exception '这项成果已经使用过替补机会';
  end if;
  select count(*) into v_accepted from public.lili_achievement_witness_invites
    where achievement_id = v_claim.id and status = 'accepted';
  v_needed := 2 - v_accepted;
  if coalesce(array_length(p_witness_ids, 1), 0) <> v_needed then
    raise exception '请按缺少的见证人数选择搭子';
  end if;
  foreach v_witness in array p_witness_ids loop
    if auth.uid() = v_witness
      or exists (select 1 from public.lili_achievement_witness_invites i where i.achievement_id = v_claim.id and i.witness_id = v_witness)
      or not public.lili_are_buddies(auth.uid(), v_witness) then
      raise exception '替补见证人无效，不能重复邀请原见证人';
    end if;
  end loop;
  update public.lili_achievement_witness_invites
    set status = 'expired'
    where achievement_id = v_claim.id and status = 'pending';
  for v_index in 1..v_needed loop
    insert into public.lili_achievement_witness_invites(achievement_id, witness_id, replacement_round, slot)
    values (v_claim.id, p_witness_ids[v_index], 1, v_accepted + v_index);
  end loop;
  update public.lili_achievement_claims set replacement_round = 1, status = 'pending' where id = v_claim.id;
  return jsonb_build_object('status', 'pending', 'replacement_round', 1, 'needed', v_needed, 'achievement_id', v_claim.id);
end;
$$;

create or replace function public.lili_achievement_witness_inbox() returns jsonb
language sql stable security definer set search_path = '' as $$
  select coalesce(jsonb_agg(jsonb_build_object(
    'achievement_id', c.id,
    'owner_id', c.owner_id,
    'owner_nickname', public.lili_owner_nickname(c.owner_id),
    'kind', c.kind,
    'name', c.name,
    'note', c.note,
    'amount', 200,
    'replacement_round', c.replacement_round,
    'created_at', c.created_at
  ) order by c.created_at), '[]'::jsonb)
  from public.lili_achievement_claims c
  join public.lili_achievement_witness_invites i on i.achievement_id = c.id
  where i.witness_id = auth.uid() and i.status = 'pending'
    and c.status in ('pending', 'need_replacement');
$$;

revoke all on function public.lili_submit_achievement(text,text,integer,text,uuid[]) from public, anon;
grant execute on function public.lili_submit_achievement(text,text,integer,text,uuid[]) to authenticated;
revoke all on function public.lili_submit_achievement(text,text,integer,text) from public, anon;
grant execute on function public.lili_submit_achievement(text,text,integer,text) to authenticated;
revoke all on function public.lili_respond_achievement_witness(uuid,boolean) from public, anon;
grant execute on function public.lili_respond_achievement_witness(uuid,boolean) to authenticated;
revoke all on function public.lili_confirm_achievement(uuid,boolean) from public, anon;
grant execute on function public.lili_confirm_achievement(uuid,boolean) to authenticated;
revoke all on function public.lili_replace_achievement_witnesses(uuid,uuid[]) from public, anon;
grant execute on function public.lili_replace_achievement_witnesses(uuid,uuid[]) to authenticated;
revoke all on function public.lili_achievement_witness_inbox() from public, anon;
grant execute on function public.lili_achievement_witness_inbox() to authenticated;
