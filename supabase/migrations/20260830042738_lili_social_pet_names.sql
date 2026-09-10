-- Give each account an optional social-facing 六毛 name.
--
-- The desktop renderer applies the viewer's private note first.  This
-- migration supplies the second-level, owner-chosen name in every dashboard
-- and room JSON object that already contains an authorized user identity.  A
-- nullable column keeps old accounts on the existing "搭子家的六毛"
-- fallback.

alter table public.lili_profiles
  add column if not exists pet_name text;

alter table public.lili_profiles
  drop constraint if exists lili_profiles_pet_name_check;

alter table public.lili_profiles
  add constraint lili_profiles_pet_name_check
  check (pet_name is null or char_length(btrim(pet_name)) between 1 and 24);

create or replace function public.lili_attach_social_pet_names(payload jsonb)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  item jsonb;
  key text;
  result jsonb;
  raw_user_id text;
  person_id uuid;
  owner_pet_name text;
begin
  if payload is null then
    return null;
  end if;

  if jsonb_typeof(payload) = 'array' then
    result := '[]'::jsonb;
    for item in select value from jsonb_array_elements(payload) loop
      result := result || jsonb_build_array(public.lili_attach_social_pet_names(item));
    end loop;
    return result;
  end if;

  if jsonb_typeof(payload) <> 'object' then
    return payload;
  end if;

  result := '{}'::jsonb;
  for key in select jsonb_object_keys(payload) loop
    result := result || jsonb_build_object(
      key,
      public.lili_attach_social_pet_names(payload -> key)
    );
  end loop;

  raw_user_id := nullif(coalesce(
    payload ->> 'user_id',
    payload ->> 'peer_id',
    payload ->> 'buddy_user_id',
    payload ->> 'buddy_id',
    payload ->> 'owner_id',
    payload ->> 'actor_id',
    payload ->> 'sender_id',
    payload ->> 'requester_id',
    payload ->> 'target_id',
    payload ->> 'receiver_id'
  ), '');
  if raw_user_id is not null then
    begin
      person_id := raw_user_id::uuid;
    exception when invalid_text_representation then
      person_id := null;
    end;
  end if;

  if person_id is not null then
    select nullif(btrim(p.pet_name), '')
      into owner_pet_name
      from public.lili_profiles p
      where p.user_id = person_id;
    if owner_pet_name is not null then
      result := jsonb_set(result, '{pet_name}', to_jsonb(owner_pet_name), true);
    end if;
  end if;
  return result;
end;
$$;

-- This helper is an implementation detail of the authenticated wrappers, not
-- a public RPC.  Keep it unavailable to clients even though the wrappers are
-- security-definer functions.
revoke execute on function public.lili_attach_social_pet_names(jsonb)
  from public, anon, authenticated;

do $$
begin
  if to_regprocedure('public.lili_dashboard()') is not null
     and to_regprocedure('public.lili_dashboard_social_pet_names_base()') is null then
    execute 'alter function public.lili_dashboard() rename to lili_dashboard_social_pet_names_base';
  end if;
  if to_regprocedure('public.lili_room_dashboard(uuid)') is not null
     and to_regprocedure('public.lili_room_dashboard_social_pet_names_base(uuid)') is null then
    execute 'alter function public.lili_room_dashboard(uuid) rename to lili_room_dashboard_social_pet_names_base';
  end if;
end;
$$;

create or replace function public.lili_dashboard()
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select public.lili_attach_social_pet_names(
    public.lili_dashboard_social_pet_names_base()
  );
$$;

revoke execute on function public.lili_dashboard_social_pet_names_base()
  from public, anon, authenticated;
revoke execute on function public.lili_dashboard() from public, anon;
grant execute on function public.lili_dashboard() to authenticated;

create or replace function public.lili_room_dashboard(p_room_id uuid)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select public.lili_attach_social_pet_names(
    public.lili_room_dashboard_social_pet_names_base(p_room_id)
  );
$$;

revoke execute on function public.lili_room_dashboard_social_pet_names_base(uuid)
  from public, anon, authenticated;
revoke execute on function public.lili_room_dashboard(uuid) from public, anon;
grant execute on function public.lili_room_dashboard(uuid) to authenticated;
