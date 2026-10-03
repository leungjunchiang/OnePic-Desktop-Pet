-- Capture DND at delivery. Read receipts and once-only activation are separate.
create table if not exists public.lili_interaction_delivery (
  receiver_id uuid not null references auth.users(id) on delete cascade,
  event_id text not null check(length(event_id)<=160),
  received_silent boolean not null,
  handled_at timestamptz,
  changed_at timestamptz not null default now(),
  parent_event_id text,
  primary key(receiver_id,event_id)
);
alter table public.lili_interaction_delivery enable row level security;
alter table public.lili_interaction_delivery add column if not exists changed_at timestamptz not null default now();
revoke all on public.lili_interaction_delivery from public,anon,authenticated;
drop policy if exists lili_delivery_rpc_only on public.lili_interaction_delivery;
create policy lili_delivery_rpc_only on public.lili_interaction_delivery
for all to anon,authenticated using(false) with check(false);
create index if not exists lili_delivery_changed_idx on public.lili_interaction_delivery(receiver_id,changed_at desc,event_id)
where parent_event_id is null;

create or replace function public.lili_delivery_changed() returns trigger
language plpgsql security definer set search_path='' as $$
begin new.changed_at:=clock_timestamp(); return new; end $$;
revoke all on function public.lili_delivery_changed() from public,anon,authenticated;
drop trigger if exists lili_delivery_changed on public.lili_interaction_delivery;
create trigger lili_delivery_changed before update of handled_at on public.lili_interaction_delivery
for each row execute function public.lili_delivery_changed();

create or replace function public.lili_always_receive_interactions() returns trigger
language plpgsql security definer set search_path='' as $$
begin
  -- Legacy opt-out becomes silent inbox delivery; unrelated profile edits in
  -- new clients omit this field. Presence never changes the preference.
  if not new.allow_visits then new.buddy_interaction_mode:='do_not_disturb'; end if;
  new.allow_visits:=true;
  return new;
end $$;
revoke all on function public.lili_always_receive_interactions() from public,anon,authenticated;
drop trigger if exists lili_always_receive_interactions on public.lili_profiles;
create trigger lili_always_receive_interactions before insert or update of allow_visits
on public.lili_profiles for each row execute function public.lili_always_receive_interactions();
update public.lili_profiles set allow_visits=true,buddy_interaction_mode='do_not_disturb' where not allow_visits;

create or replace function public.lili_capture_interaction_delivery() returns trigger
language plpgsql security definer set search_path='' as $$
declare recipient uuid; key text; silent boolean;
begin
  if tg_table_name='lili_room_events' then
    if new.target_id is null or new.kind not in ('visit','cheer','tease','praise','knock','poke','start','return','flower','water','rest','drink','phrase','food_coffee','food_milk_tea','food_tea','food_cake','food_cake_share') then return new; end if;
    recipient:=new.target_id; key:='room:'||new.id;
  else
    recipient:=new.receiver_id;
    key:=case tg_table_name when 'lili_visit_events' then 'visit:' when 'lili_buddy_taunts' then 'taunt:' else 'cheer:' end||new.id;
  end if;
  -- Receiver-activated child effects are not incoming messages.
  if exists(select 1 from public.lili_interaction_delivery d where d.receiver_id=recipient and d.event_id=key and d.parent_event_id is not null) then return new; end if;
  select buddy_interaction_mode='do_not_disturb' into silent from public.lili_profiles where user_id=recipient;
  insert into public.lili_interaction_delivery(receiver_id,event_id,received_silent)
    values(recipient,key,coalesce(silent,false)) on conflict do nothing;
  if tg_table_name='lili_visit_events' then
    new.payload:=coalesce(new.payload,'{}')||jsonb_build_object('received_silent',coalesce(silent,false));
  end if;
  if silent then
    if tg_table_name='lili_buddy_taunts' then new.released_at:=new.created_at;
    elsif tg_table_name='lili_buddy_encouragements' then new.ended_at:=new.created_at;
    end if;
  end if;
  return new;
end $$;
revoke all on function public.lili_capture_interaction_delivery() from public,anon,authenticated;
drop trigger if exists lili_capture_interaction_delivery on public.lili_visit_events;
create trigger lili_capture_interaction_delivery before insert on public.lili_visit_events
for each row execute function public.lili_capture_interaction_delivery();

create or replace function public.lili_settle_visit_delivery() returns trigger
language plpgsql security definer set search_path='' as $$
begin
  if old.status='pending' and new.status in ('accepted','declined') then
    update public.lili_interaction_delivery set handled_at=coalesce(handled_at,now())
      where receiver_id=new.receiver_id and event_id='visit:'||new.id;
  end if;
  return new;
end $$;
revoke all on function public.lili_settle_visit_delivery() from public,anon,authenticated;
drop trigger if exists lili_settle_visit_delivery on public.lili_visit_events;
create trigger lili_settle_visit_delivery after update of status on public.lili_visit_events
for each row execute function public.lili_settle_visit_delivery();

-- Decorate existing bounded dashboard batches; no extra client round trip.
create or replace function public.lili_delivery_dashboard(p_data jsonb) returns jsonb
language plpgsql security definer set search_path='' as $$
declare me uuid:=(select auth.uid()); field text; prefix text; rows jsonb; revision text;
begin
  if me is null then raise exception '请先登录'; end if;
  foreach field in array array['visits','active_visits','room_activity'] loop
    if jsonb_typeof(p_data->field)='array' then
      prefix:=case when field='room_activity' then 'room:' else 'visit:' end;
      select coalesce(jsonb_agg(x.value||jsonb_build_object('received_silent',coalesce(d.received_silent,false),'handled_at',d.handled_at) order by x.ord),'[]') into rows
      from jsonb_array_elements(p_data->field) with ordinality x(value,ord)
      left join public.lili_interaction_delivery d on d.receiver_id=me and d.event_id=prefix||(x.value->>'id');
      p_data:=jsonb_set(p_data,array[field],rows);
    end if;
  end loop;
  select event_id||'@'||changed_at into revision from public.lili_interaction_delivery
    where receiver_id=me and parent_event_id is null order by changed_at desc,event_id desc limit 1;
  return p_data||jsonb_build_object('interaction_delivery_revision',coalesce(revision,''));
end $$;
revoke all on function public.lili_delivery_dashboard(jsonb) from public,anon,authenticated;
drop trigger if exists lili_capture_interaction_delivery on public.lili_room_events;
create trigger lili_capture_interaction_delivery before insert on public.lili_room_events
for each row execute function public.lili_capture_interaction_delivery();
drop trigger if exists lili_capture_interaction_delivery on public.lili_buddy_taunts;
create trigger lili_capture_interaction_delivery before insert on public.lili_buddy_taunts
for each row execute function public.lili_capture_interaction_delivery();
drop trigger if exists lili_capture_interaction_delivery on public.lili_buddy_encouragements;
create trigger lili_capture_interaction_delivery before insert on public.lili_buddy_encouragements
for each row execute function public.lili_capture_interaction_delivery();

create or replace function public.lili_handle_silent_interaction(p_event_id text) returns jsonb
language plpgsql security definer set search_path='' as $$
declare me uuid:=(select auth.uid()); delivery record; event record; effect_id uuid; result jsonb:='{}';
begin
  if me is null then raise exception '请先登录'; end if;
  select * into delivery from public.lili_interaction_delivery
    where receiver_id=me and event_id=p_event_id for update;
  if not found then raise exception '此消息不是可处理的免打扰互动'; end if;
  select * into event from public.lili_interaction_feed f where f.receiver_id=me and f.event_id=p_event_id;
  if not found or delivery.parent_event_id is not null then raise exception '互动不存在或无权处理'; end if;
  if not delivery.received_silent or delivery.handled_at is not null then
    return jsonb_build_object('event_id',p_event_id,'first_handled',false); end if;
  if not public.lili_are_buddies(me,event.sender_id) then raise exception '搭子关系已结束'; end if;
  update public.lili_interaction_delivery set handled_at=now() where receiver_id=me and event_id=p_event_id;
  if event.event_type in ('tease','cheer') then
    effect_id:=event.source_id;
    if event.source not in ('taunt','cheer') then
      effect_id:=gen_random_uuid();
      insert into public.lili_interaction_delivery(receiver_id,event_id,received_silent,handled_at,parent_event_id)
        values(me,case when event.event_type='tease' then 'taunt:' else 'cheer:' end||effect_id,false,now(),p_event_id);
      if event.event_type='tease' then
        insert into public.lili_buddy_taunts(id,sender_id,receiver_id,message,created_at)
          values(effect_id,event.sender_id,me,'怎么，今天准备靠意念完成？',event.created_at);
      else
        insert into public.lili_buddy_encouragements(id,sender_id,receiver_id,message,created_at)
          values(effect_id,event.sender_id,me,'搭子给你加油，继续专注吧。',event.created_at);
      end if;
    end if;
    if event.event_type='tease' then
      update public.lili_buddy_taunts set released_at=null,worked_seconds=0,work_started_at=null,started_working_at=null where id=effect_id and receiver_id=me;
      result:=jsonb_build_object('taunt_state',public.lili_taunt_state());
    else
      update public.lili_buddy_encouragements set ended_at=null,expires_at=now()+interval '1 hour' where id=effect_id and receiver_id=me;
      -- Persistent work-cheer follows the existing working-presence rule.
      -- An idle receiver still gets a short visual reaction in their client.
      result:=jsonb_build_object('encouragement_state',public.lili_encouragement_state());
    end if;
  end if;
  return result||jsonb_build_object('event_id',p_event_id,'first_handled',true,'handled_at',now(),'event_type',event.event_type);
end $$;
revoke all on function public.lili_handle_silent_interaction(text) from public,anon,authenticated;
grant execute on function public.lili_handle_silent_interaction(text) to authenticated;
notify pgrst,'reload schema';
