-- Lili v0.11 搭子自习室。所有表均启用 RLS；不保存任务、聊天、窗口标题或私人素材。
create extension if not exists pgcrypto;

create table public.lili_profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  nickname text not null default '六毛搭子' check (char_length(nickname) between 1 and 24),
  invite_code text not null unique check (invite_code ~ '^[A-Z0-9]{8}$'),
  outfit_key text not null default '' check (char_length(outfit_key) <= 60),
  visibility text not null default 'friends' check (visibility in ('friends','hidden')),
  show_exact_time boolean not null default true,
  allow_visits boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.lili_buddy_links (
  id uuid primary key default gen_random_uuid(),
  requester_id uuid not null references auth.users(id) on delete cascade,
  addressee_id uuid not null references auth.users(id) on delete cascade,
  status text not null default 'pending' check (status in ('pending','accepted','declined')),
  created_at timestamptz not null default now(),
  responded_at timestamptz,
  check (requester_id <> addressee_id)
);
create unique index lili_buddy_pair_unique on public.lili_buddy_links
  (least(requester_id, addressee_id), greatest(requester_id, addressee_id));

create table public.lili_study_rooms (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  name text not null check (char_length(name) between 1 and 30),
  invite_code text not null unique check (invite_code ~ '^[A-Z0-9]{8}$'),
  created_at timestamptz not null default now()
);

create table public.lili_room_members (
  room_id uuid not null references public.lili_study_rooms(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  joined_at timestamptz not null default now(),
  primary key (room_id, user_id)
);

create table public.lili_focus_presence (
  user_id uuid primary key references auth.users(id) on delete cascade,
  working boolean not null default false,
  session_started_at timestamptz,
  focus_date date not null default current_date,
  today_seconds integer not null default 0 check (today_seconds between 0 and 86400),
  outfit_key text not null default '' check (char_length(outfit_key) <= 60),
  room_id uuid references public.lili_study_rooms(id) on delete set null,
  last_seen timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.lili_visit_events (
  id uuid primary key default gen_random_uuid(),
  sender_id uuid not null references auth.users(id) on delete cascade,
  receiver_id uuid not null references auth.users(id) on delete cascade,
  kind text not null default 'visit' check (kind in ('visit','cheer','water','rest')),
  status text not null default 'pending' check (status in ('pending','accepted','declined','expired')),
  created_at timestamptz not null default now(),
  expires_at timestamptz not null default (now() + interval '10 minutes'),
  responded_at timestamptz,
  check (sender_id <> receiver_id)
);

create or replace function public.lili_invite_code() returns text
language sql volatile set search_path = '' as $$
  select upper(substr(encode(extensions.gen_random_bytes(6), 'hex'), 1, 8));
$$;

create or replace function public.lili_new_profile() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  insert into public.lili_profiles(user_id, nickname, invite_code)
  values (new.id, coalesce(nullif(left(new.raw_user_meta_data ->> 'nickname', 24), ''), '六毛搭子'), public.lili_invite_code());
  return new;
end;
$$;
create trigger lili_auth_user_created after insert on auth.users
for each row execute procedure public.lili_new_profile();

create or replace function public.lili_are_buddies(a uuid, b uuid) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists(select 1 from public.lili_buddy_links
    where status='accepted' and ((requester_id=a and addressee_id=b) or (requester_id=b and addressee_id=a)));
$$;
create or replace function public.lili_share_room(a uuid, b uuid) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists(select 1 from public.lili_room_members x join public.lili_room_members y using(room_id)
    where x.user_id=a and y.user_id=b);
$$;

alter table public.lili_profiles enable row level security;
alter table public.lili_buddy_links enable row level security;
alter table public.lili_study_rooms enable row level security;
alter table public.lili_room_members enable row level security;
alter table public.lili_focus_presence enable row level security;
alter table public.lili_visit_events enable row level security;

create policy lili_profiles_read on public.lili_profiles for select to authenticated using (
  user_id=auth.uid() or (visibility='friends' and (public.lili_are_buddies(auth.uid(),user_id) or public.lili_share_room(auth.uid(),user_id))));
create policy lili_profiles_update on public.lili_profiles for update to authenticated using (user_id=auth.uid()) with check (user_id=auth.uid());
create policy lili_links_read on public.lili_buddy_links for select to authenticated using (auth.uid() in (requester_id,addressee_id));
create policy lili_rooms_read on public.lili_study_rooms for select to authenticated using (owner_id=auth.uid() or exists(select 1 from public.lili_room_members where room_id=id and user_id=auth.uid()));
create policy lili_rooms_insert on public.lili_study_rooms for insert to authenticated with check (owner_id=auth.uid());
create policy lili_members_read on public.lili_room_members for select to authenticated using (public.lili_share_room(auth.uid(),user_id));
create policy lili_presence_read on public.lili_focus_presence for select to authenticated using (
  user_id=auth.uid() or public.lili_are_buddies(auth.uid(),user_id) or public.lili_share_room(auth.uid(),user_id));
create policy lili_presence_insert on public.lili_focus_presence for insert to authenticated with check (user_id=auth.uid());
create policy lili_presence_update on public.lili_focus_presence for update to authenticated using (user_id=auth.uid()) with check (user_id=auth.uid());
create policy lili_visits_read on public.lili_visit_events for select to authenticated using (auth.uid() in (sender_id,receiver_id));

create or replace function public.lili_add_buddy_by_code(code text) returns uuid
language plpgsql security definer set search_path = '' as $$
declare target uuid; result uuid;
begin
  select user_id into target from public.lili_profiles where invite_code=upper(trim(code));
  if target is null then raise exception '没有找到这个搭子码'; end if;
  if target=auth.uid() then raise exception '不能添加自己'; end if;
  insert into public.lili_buddy_links(requester_id,addressee_id) values(auth.uid(),target)
  returning id into result;
  return result;
end;
$$;

create or replace function public.lili_respond_buddy(request_id uuid, accept boolean) returns void
language plpgsql security definer set search_path = '' as $$
begin
  update public.lili_buddy_links set status=case when accept then 'accepted' else 'declined' end, responded_at=now()
  where id=request_id and addressee_id=auth.uid() and status='pending';
  if not found then raise exception '搭子申请不存在或无权处理'; end if;
end;
$$;

create or replace function public.lili_create_room(room_name text) returns uuid
language plpgsql security definer set search_path = '' as $$
declare room uuid;
begin
  insert into public.lili_study_rooms(owner_id,name,invite_code)
  values(auth.uid(),left(trim(room_name),30),public.lili_invite_code()) returning id into room;
  insert into public.lili_room_members(room_id,user_id) values(room,auth.uid());
  return room;
end;
$$;

create or replace function public.lili_join_room(code text) returns uuid
language plpgsql security definer set search_path = '' as $$
declare room uuid;
begin
  select id into room from public.lili_study_rooms where invite_code=upper(trim(code));
  if room is null then raise exception '没有找到这个自习室'; end if;
  insert into public.lili_room_members(room_id,user_id) values(room,auth.uid()) on conflict do nothing;
  return room;
end;
$$;

create or replace function public.lili_send_visit(target uuid, visit_kind text default 'visit') returns uuid
language plpgsql security definer set search_path = '' as $$
declare result uuid;
begin
  if not public.lili_are_buddies(auth.uid(),target) then raise exception '只有已接受的搭子可以串门'; end if;
  if not exists(select 1 from public.lili_profiles where user_id=target and allow_visits) then raise exception '对方暂时不接受串门'; end if;
  insert into public.lili_visit_events(sender_id,receiver_id,kind)
  values(auth.uid(),target,case when visit_kind in ('visit','cheer','water','rest') then visit_kind else 'visit' end)
  returning id into result;
  return result;
end;
$$;

create or replace function public.lili_respond_visit(event_id uuid, accept boolean) returns void
language plpgsql security definer set search_path = '' as $$
begin
  update public.lili_visit_events set status=case when accept then 'accepted' else 'declined' end, responded_at=now()
  where id=event_id and receiver_id=auth.uid() and status='pending' and expires_at>now();
  if not found then raise exception '串门邀请已过期或无权处理'; end if;
end;
$$;

create or replace function public.lili_dashboard() returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object(
    'me',(select to_jsonb(p) from public.lili_profiles p where p.user_id=auth.uid()),
    'requests',coalesce((select jsonb_agg(jsonb_build_object('id',l.id,'nickname',p.nickname,'created_at',l.created_at))
      from public.lili_buddy_links l join public.lili_profiles p on p.user_id=l.requester_id
      where l.addressee_id=auth.uid() and l.status='pending'),'[]'::jsonb),
    'buddies',coalesce((select jsonb_agg(jsonb_build_object('user_id',p.user_id,'nickname',p.nickname,'outfit_key',p.outfit_key,
      'working',coalesce(f.working,false),'session_started_at',f.session_started_at,'today_seconds',case when p.show_exact_time then coalesce(f.today_seconds,0) else null end,
      'online',coalesce(f.last_seen>now()-interval '2 minutes',false)))
      from public.lili_profiles p left join public.lili_focus_presence f on f.user_id=p.user_id
      where public.lili_are_buddies(auth.uid(),p.user_id) and p.visibility='friends'),'[]'::jsonb),
    'rooms',coalesce((select jsonb_agg(jsonb_build_object('id',r.id,'name',r.name,'invite_code',r.invite_code,
      'members',(select count(*) from public.lili_room_members m2 where m2.room_id=r.id)))
      from public.lili_study_rooms r join public.lili_room_members m on m.room_id=r.id where m.user_id=auth.uid()),'[]'::jsonb),
    'visits',coalesce((select jsonb_agg(jsonb_build_object('id',v.id,'sender_id',v.sender_id,'nickname',p.nickname,'kind',v.kind,'created_at',v.created_at))
      from public.lili_visit_events v join public.lili_profiles p on p.user_id=v.sender_id
      where v.receiver_id=auth.uid() and v.status='pending' and v.expires_at>now()),'[]'::jsonb)
  );
$$;

grant execute on function public.lili_add_buddy_by_code(text) to authenticated;
grant execute on function public.lili_respond_buddy(uuid,boolean) to authenticated;
grant execute on function public.lili_create_room(text) to authenticated;
grant execute on function public.lili_join_room(text) to authenticated;
grant execute on function public.lili_send_visit(uuid,text) to authenticated;
grant execute on function public.lili_respond_visit(uuid,boolean) to authenticated;
grant execute on function public.lili_dashboard() to authenticated;
