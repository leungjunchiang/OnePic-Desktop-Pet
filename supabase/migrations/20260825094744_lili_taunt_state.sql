-- Persistent playful punishment for a buddy who is offline or not working.
-- The receiver remains taunted until the first fresh working heartbeat, then
-- for twenty minutes.  This is deliberately separate from room events:
-- room events are short-lived activity, while this state must survive a
-- second computer and ordinary dashboard refreshes.

create table if not exists public.lili_buddy_taunts (
  id uuid primary key default gen_random_uuid(),
  sender_id uuid not null references auth.users(id) on delete cascade,
  receiver_id uuid not null references auth.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  started_working_at timestamptz,
  constraint lili_buddy_taunts_not_self check (sender_id <> receiver_id)
);

create index if not exists lili_buddy_taunts_receiver_active_idx
  on public.lili_buddy_taunts(receiver_id, created_at desc);
create index if not exists lili_buddy_taunts_sender_idx
  on public.lili_buddy_taunts(sender_id);

alter table public.lili_buddy_taunts enable row level security;

drop policy if exists lili_buddy_taunts_participant_select on public.lili_buddy_taunts;
create policy lili_buddy_taunts_participant_select
  on public.lili_buddy_taunts
  for select to authenticated
  using ((select auth.uid()) in (sender_id, receiver_id));

create or replace function public.lili_mark_taunt_started()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if coalesce(new.working, false) then
    update public.lili_buddy_taunts t
    set started_working_at = coalesce(t.started_working_at, now())
    where t.receiver_id = new.user_id
      and t.started_working_at is null;
  end if;
  return new;
end;
$$;

drop trigger if exists lili_mark_taunt_started_on_presence on public.lili_focus_presence;
create trigger lili_mark_taunt_started_on_presence
  after insert or update of working, last_seen on public.lili_focus_presence
  for each row execute function public.lili_mark_taunt_started();

create or replace function public.lili_send_taunt(p_target uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  me uuid := (select auth.uid());
  taunt_id uuid;
begin
  if me is null then
    raise exception '请先登录';
  end if;
  if p_target is null or p_target = me then
    raise exception '不能嘲讽自己';
  end if;
  if not public.lili_are_buddies(me, p_target) then
    raise exception '只能嘲讽已确认的搭子';
  end if;
  if exists (
    select 1 from public.lili_focus_presence f
    where f.user_id = p_target
      and f.working
      and f.last_seen > now() - interval '2 minutes'
  ) then
    raise exception '对方正在工作，暂时不能嘲讽';
  end if;
  if exists (
    select 1 from public.lili_buddy_taunts t
    where t.receiver_id = p_target
      and (t.started_working_at is null or t.started_working_at + interval '20 minutes' > now())
  ) then
    raise exception '对方已经处于嘲讽状态';
  end if;

  insert into public.lili_buddy_taunts(sender_id, receiver_id)
  values (me, p_target)
  returning id into taunt_id;

  return jsonb_build_object('id', taunt_id, 'receiver_id', p_target, 'active', true);
end;
$$;

create or replace function public.lili_taunt_state()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  me uuid := (select auth.uid());
  result jsonb;
begin
  if me is null then
    raise exception '请先登录';
  end if;

  -- A client can have missed the trigger while it was offline.  Reconcile
  -- the first currently-working heartbeat before returning the state.
  update public.lili_buddy_taunts t
  set started_working_at = coalesce(t.started_working_at, now())
  where t.receiver_id = me
    and t.started_working_at is null
    and exists (
      select 1 from public.lili_focus_presence f
      where f.user_id = me
        and f.working
        and f.last_seen > now() - interval '2 minutes'
    );

  select jsonb_build_object(
    'active', true,
    'id', t.id,
    'sender_id', t.sender_id,
    'sender_nickname', public.lili_owner_nickname(t.sender_id),
    'created_at', t.created_at,
    'started_working_at', t.started_working_at,
    'punishment_until', t.started_working_at + interval '20 minutes'
  )
  into result
  from public.lili_buddy_taunts t
  where t.receiver_id = me
    and (t.started_working_at is null or t.started_working_at + interval '20 minutes' > now())
  order by t.created_at desc
  limit 1;

  return coalesce(result, jsonb_build_object('active', false));
end;
$$;

revoke execute on function public.lili_mark_taunt_started() from public, anon, authenticated;
revoke execute on function public.lili_send_taunt(uuid) from public, anon;
revoke execute on function public.lili_taunt_state() from public, anon;
grant execute on function public.lili_send_taunt(uuid) to authenticated;
grant execute on function public.lili_taunt_state() to authenticated;
