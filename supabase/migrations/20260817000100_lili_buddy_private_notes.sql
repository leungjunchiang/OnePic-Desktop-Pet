-- Private buddy labels belong to the viewer, not to the public profile.
-- They are intentionally separate from rooms, presence and realtime events.

create table if not exists public.lili_buddy_private_notes (
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  buddy_user_id uuid not null references auth.users(id) on delete cascade,
  private_note_name text not null
    check (char_length(btrim(private_note_name)) between 1 and 40),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (owner_user_id, buddy_user_id),
  check (owner_user_id <> buddy_user_id)
);

alter table public.lili_buddy_private_notes enable row level security;

drop policy if exists lili_buddy_private_notes_select on public.lili_buddy_private_notes;
drop policy if exists lili_buddy_private_notes_insert on public.lili_buddy_private_notes;
drop policy if exists lili_buddy_private_notes_update on public.lili_buddy_private_notes;
drop policy if exists lili_buddy_private_notes_delete on public.lili_buddy_private_notes;

create policy lili_buddy_private_notes_select
  on public.lili_buddy_private_notes
  for select to authenticated
  using ((select auth.uid()) is not null and (select auth.uid()) = owner_user_id);

create policy lili_buddy_private_notes_insert
  on public.lili_buddy_private_notes
  for insert to authenticated
  with check ((select auth.uid()) is not null and (select auth.uid()) = owner_user_id);

create policy lili_buddy_private_notes_update
  on public.lili_buddy_private_notes
  for update to authenticated
  using ((select auth.uid()) is not null and (select auth.uid()) = owner_user_id)
  with check ((select auth.uid()) is not null and (select auth.uid()) = owner_user_id);

create policy lili_buddy_private_notes_delete
  on public.lili_buddy_private_notes
  for delete to authenticated
  using ((select auth.uid()) is not null and (select auth.uid()) = owner_user_id);

grant select, insert, update, delete
  on table public.lili_buddy_private_notes to authenticated;
revoke all on table public.lili_buddy_private_notes from anon;

create or replace function public.lili_buddy_private_notes() returns jsonb
language sql stable set search_path = '' as $$
  select coalesce(
    jsonb_object_agg(n.buddy_user_id::text, n.private_note_name order by n.updated_at desc),
    '{}'::jsonb
  )
  from public.lili_buddy_private_notes n
  where n.owner_user_id = (select auth.uid());
$$;

create or replace function public.lili_set_buddy_private_note(
  p_buddy_id uuid,
  p_private_note_name text default ''
) returns void
language plpgsql set search_path = '' as $$
declare
  clean_name text := nullif(left(btrim(coalesce(p_private_note_name, '')), 40), '');
begin
  if (select auth.uid()) is null then
    raise exception '请先登录搭子自习室';
  end if;
  if p_buddy_id is null or p_buddy_id = (select auth.uid()) then
    raise exception '私人备注只能设置给搭子';
  end if;
  if not exists (
    select 1
    from public.lili_buddy_links b
    where b.status = 'accepted'
      and ((b.requester_id = (select auth.uid()) and b.addressee_id = p_buddy_id)
        or (b.addressee_id = (select auth.uid()) and b.requester_id = p_buddy_id))
  ) then
    raise exception '只能给已确认的搭子设置私人备注';
  end if;

  if clean_name is null then
    delete from public.lili_buddy_private_notes
    where owner_user_id = (select auth.uid()) and buddy_user_id = p_buddy_id;
    return;
  end if;

  insert into public.lili_buddy_private_notes(owner_user_id, buddy_user_id, private_note_name)
  values ((select auth.uid()), p_buddy_id, clean_name)
  on conflict (owner_user_id, buddy_user_id) do update
    set private_note_name = excluded.private_note_name,
        updated_at = now();
end;
$$;

revoke execute on function public.lili_buddy_private_notes() from public, anon;
revoke execute on function public.lili_set_buddy_private_note(uuid, text) from public, anon;
grant execute on function public.lili_buddy_private_notes() to authenticated;
grant execute on function public.lili_set_buddy_private_note(uuid, text) to authenticated;

