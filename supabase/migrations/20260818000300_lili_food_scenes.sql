-- Lili food scenarios and buddy food interaction permissions.
-- Non-destructive: preserves existing profiles, visit events, economy and skin data.
alter table public.lili_profiles
  add column if not exists buddy_interaction_mode text not null default 'focus_priority';

alter table public.lili_profiles
  drop constraint if exists lili_profiles_buddy_interaction_mode_check;
alter table public.lili_profiles
  add constraint lili_profiles_buddy_interaction_mode_check
  check (buddy_interaction_mode in ('welcome', 'focus_priority', 'do_not_disturb'));

alter table public.lili_visit_events
  add column if not exists payload jsonb not null default '{}'::jsonb;

alter table public.lili_visit_events
  drop constraint if exists lili_visit_events_kind_check;
alter table public.lili_visit_events
  add constraint lili_visit_events_kind_check
  check (kind in ('visit', 'cheer', 'water', 'rest', 'food_coffee', 'food_milk_tea', 'food_tea', 'food_cake'));

create or replace function public.lili_set_buddy_interaction_mode(p_mode text) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if p_mode not in ('welcome', 'focus_priority', 'do_not_disturb') then
    raise exception '互动模式不受支持';
  end if;
  update public.lili_profiles
  set buddy_interaction_mode = p_mode,
      updated_at = now()
  where user_id = auth.uid();
  if not found then
    raise exception '搭子资料不存在';
  end if;
end;
$$;

create or replace function public.lili_send_food_interaction(
  p_target uuid,
  p_kind text,
  p_payload jsonb default '{}'::jsonb
) returns uuid
language plpgsql security definer set search_path = '' as $$
declare
  result uuid;
  clean_kind text := lower(trim(coalesce(p_kind, '')));
  clean_payload jsonb := coalesce(p_payload, '{}'::jsonb);
begin
  if not public.lili_are_buddies(auth.uid(), p_target) then
    raise exception '只有已接受的搭子可以发送食物互动';
  end if;
  if not exists (
    select 1 from public.lili_profiles
    where user_id = p_target and allow_visits
  ) then
    raise exception '对方暂时不接受搭子互动';
  end if;
  if clean_kind not in ('food_coffee', 'food_milk_tea', 'food_tea', 'food_cake') then
    raise exception '食物互动类型不受支持';
  end if;
  if jsonb_typeof(clean_payload) <> 'object' then
    raise exception '互动内容格式不正确';
  end if;
  if octet_length(clean_payload::text) > 2000 then
    raise exception '互动内容过长';
  end if;
  insert into public.lili_visit_events(sender_id, receiver_id, kind, payload)
  values (auth.uid(), p_target, clean_kind, clean_payload)
  returning id into result;
  return result;
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
    'visits',coalesce((select jsonb_agg(jsonb_build_object(
      'id',v.id,'sender_id',v.sender_id,'nickname',p.nickname,'kind',v.kind,
      'payload',coalesce(v.payload,'{}'::jsonb),'created_at',v.created_at
    ))
      from public.lili_visit_events v join public.lili_profiles p on p.user_id=v.sender_id
      where v.receiver_id=auth.uid() and v.status='pending' and v.expires_at>now()),'[]'::jsonb)
  );
$$;

grant execute on function public.lili_set_buddy_interaction_mode(text) to authenticated;
grant execute on function public.lili_send_food_interaction(uuid, text, jsonb) to authenticated;
revoke execute on function public.lili_set_buddy_interaction_mode(text) from anon, public;
revoke execute on function public.lili_send_food_interaction(uuid, text, jsonb) from anon, public;
