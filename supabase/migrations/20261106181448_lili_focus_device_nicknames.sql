-- Store optional labels separately from the immutable focus intervals.
begin;

create table if not exists public.lili_focus_device_nicknames (
  user_id uuid not null references auth.users(id) on delete cascade,
  device_id text not null check (char_length(device_id) between 1 and 120
    and device_id = btrim(device_id) and device_id !~ '[[:cntrl:]]'),
  nickname text not null check (char_length(nickname) between 1 and 32
    and nickname = btrim(nickname) and nickname !~ '[[:cntrl:]]'),
  primary key (user_id, device_id)
);

alter table public.lili_focus_device_nicknames enable row level security;
revoke all on table public.lili_focus_device_nicknames from public, anon, authenticated;
grant select, insert, update, delete on table public.lili_focus_device_nicknames to authenticated;

drop policy if exists lili_focus_device_nicknames_owner on public.lili_focus_device_nicknames;
create policy lili_focus_device_nicknames_owner on public.lili_focus_device_nicknames
  for all to authenticated
  using (user_id = (select auth.uid()))
  with check (user_id = (select auth.uid()));

create or replace function public.lili_focus_device_nicknames(
  p_account_id uuid,
  p_device_id text default null,
  p_nickname text default null
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  caller uuid := (select auth.uid());
  clean_device text := btrim(p_device_id);
  clean_nickname text := btrim(p_nickname);
  nicknames jsonb;
begin
  -- Bind the queued request to its original account even after a session switch.
  if caller is null or caller is distinct from p_account_id then
    raise exception 'Device nickname account mismatch' using errcode = '42501';
  end if;

  if p_nickname is not null then
    if clean_device is null or char_length(clean_device) not between 1 and 120
      or clean_device ~ '[[:cntrl:]]' or char_length(clean_nickname) > 32
      or clean_nickname ~ '[[:cntrl:]]' then
      raise exception 'Invalid device nickname' using errcode = '22023';
    end if;

    if clean_nickname = '' then
      delete from public.lili_focus_device_nicknames
        where user_id = caller and device_id = clean_device;
    else
      insert into public.lili_focus_device_nicknames (user_id, device_id, nickname)
        values (caller, clean_device, clean_nickname)
        on conflict (user_id, device_id) do update set nickname = excluded.nickname
        where lili_focus_device_nicknames.nickname is distinct from excluded.nickname;
    end if;
  end if;

  select coalesce(jsonb_object_agg(d.device_id, d.nickname), '{}'::jsonb)
    into nicknames from public.lili_focus_device_nicknames d where d.user_id = caller;
  return jsonb_build_object('account_id', caller, 'nicknames', nicknames);
end;
$$;

revoke all on function public.lili_focus_device_nicknames(uuid, text, text) from public, anon, authenticated;
grant execute on function public.lili_focus_device_nicknames(uuid, text, text) to authenticated;
notify pgrst, 'reload schema';
commit;
