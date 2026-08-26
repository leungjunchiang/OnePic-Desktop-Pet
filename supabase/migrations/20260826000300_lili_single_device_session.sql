-- One account may actively use one desktop at a time.
--
-- The desktop sends a random, machine-scoped device_id with each authenticated
-- presence heartbeat.  The first heartbeat from a process may set
-- device_claim=true and replace the previous lease.  Once the claim is made,
-- heartbeats from the previous machine fail with the stable
-- ``device_session_revoked`` marker; the client clears its local session on
-- the next sync cycle.  This keeps the policy server-authoritative and avoids
-- trusting hostnames or a client clock.

create table if not exists public.lili_device_sessions (
  user_id uuid primary key references auth.users(id) on delete cascade,
  device_id text not null check (char_length(device_id) between 32 and 64),
  claimed_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now()
);

alter table public.lili_device_sessions enable row level security;

drop policy if exists lili_device_sessions_owner_select on public.lili_device_sessions;
create policy lili_device_sessions_owner_select
  on public.lili_device_sessions for select
  to authenticated
  using ((select auth.uid()) = user_id);

-- Clients never need direct DML on the lease table.  The trigger below is the
-- only writer and runs with the function owner so a forged PostgREST payload
-- cannot update another account's lease.
revoke all on table public.lili_device_sessions from anon, authenticated;

alter table public.lili_focus_presence
  add column if not exists device_id text not null default '',
  add column if not exists device_claim boolean not null default false;

create or replace function public.lili_guard_device_session()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  current_user_id uuid := (select auth.uid());
  current_device text;
begin
  if current_user_id is null or new.user_id <> current_user_id then
    raise exception '需要登录后才能同步设备状态';
  end if;

  -- Empty ids are accepted for old clients only while this account has no
  -- claimed lease yet.  Once a new client has claimed the account, an old
  -- client cannot keep refreshing presence without a device id.
  if btrim(coalesce(new.device_id, '')) = '' then
    if exists (
      select 1 from public.lili_device_sessions s
      where s.user_id = new.user_id
    ) then
      raise exception 'device_session_revoked' using errcode = 'P0001';
    end if;
    return new;
  end if;

  select s.device_id into current_device
  from public.lili_device_sessions s
  where s.user_id = new.user_id
  for update;

  if current_device is null then
    insert into public.lili_device_sessions(user_id, device_id, claimed_at, last_seen_at)
    values (new.user_id, left(btrim(new.device_id), 64), now(), now())
    on conflict (user_id) do update
      set device_id = excluded.device_id,
          claimed_at = now(),
          last_seen_at = now();
  elsif current_device = left(btrim(new.device_id), 64) then
    update public.lili_device_sessions
    set last_seen_at = now()
    where user_id = new.user_id;
  elsif coalesce(new.device_claim, false) then
    update public.lili_device_sessions
    set device_id = left(btrim(new.device_id), 64),
        claimed_at = now(),
        last_seen_at = now()
    where user_id = new.user_id;
  else
    raise exception 'device_session_revoked' using errcode = 'P0001';
  end if;

  return new;
end;
$$;

drop trigger if exists lili_guard_device_session on public.lili_focus_presence;
create trigger lili_guard_device_session
before insert or update on public.lili_focus_presence
for each row execute function public.lili_guard_device_session();

revoke all on function public.lili_guard_device_session() from public, anon, authenticated;
