-- Ordinary one-to-one interactions reuse visit facts. Room events retain their
-- NOT NULL room_id and coaching retains its independent authorization RPCs.
alter table public.lili_visit_events drop constraint if exists lili_visit_events_kind_check;
alter table public.lili_visit_events add constraint lili_visit_events_kind_check check(kind in (
  'visit','cheer','water','rest','food_coffee','food_milk_tea','food_tea','food_cake','food_cake_share',
  'tease','praise','knock','poke','start','return','flower'
));
create index if not exists lili_direct_operation_idx on public.lili_visit_events
  (sender_id, (payload->>'operation_key')) where payload->>'scope'='direct';

-- The fourth REQUIRED argument avoids PostgREST overload ambiguity. Existing
-- three-argument callers, and the existing relay route, remain compatible.
create or replace function public.lili_send_interaction(
  p_target uuid, p_kind text, p_room_id uuid, p_payload jsonb
) returns uuid language plpgsql security definer set search_path='' as $$
declare me uuid:=(select auth.uid()); k text:=lower(trim(p_kind));
  data jsonb:=coalesce(p_payload,'{}'); reply text; operation text; result uuid; old record;
begin
  if me is null then raise exception '请先登录'; end if;
  if p_target is null or p_target=me then raise exception '互动对象不正确'; end if;
  if jsonb_typeof(data)<>'object' or octet_length(data::text)>2000 then raise exception '互动内容格式不正确'; end if;
  reply:=nullif(data->>'reply_to_event_id','');
  if p_room_id is not null then
    if reply is not null then raise exception '回应既有互动请使用直接互动'; end if;
    return public.lili_record_room_event(p_room_id,k,p_target,coalesce(data->>'message',''));
  end if;
  if k not in ('visit','cheer','tease','praise','knock','poke','start','return','flower',
               'food_coffee','food_milk_tea','food_tea') then raise exception '不支持这种直接互动'; end if;
  if not public.lili_are_buddies(me,p_target) then raise exception '搭子关系不存在，无法送出互动'; end if;
  if not exists(select 1 from public.lili_profiles where user_id=p_target and allow_visits
    and buddy_interaction_mode<>'do_not_disturb') then raise exception '对方暂时不接受搭子互动'; end if;
  -- Validate original ownership and sender INSIDE this write RPC, never by a
  -- client lookup or current shared-room membership. No coaching reply bypass.
  if reply is not null and (length(reply)>160 or not exists(
    select 1 from public.lili_interaction_feed f where f.event_id=reply
    and f.receiver_id=me and f.sender_id=p_target
    and f.event_type in ('visit','cheer','tease','praise','knock','poke','start','return','flower',
                        'food_coffee','food_milk_tea','food_tea','food_cake','food_cake_share')
  )) then raise exception '原互动不存在或无权回应'; end if;
  operation:=nullif(data->>'operation_key','');
  if operation is not null then
    if operation !~ '^[a-zA-Z0-9_-]{1,80}$' then raise exception '互动请求编号不正确'; end if;
    perform pg_advisory_xact_lock(hashtextextended('lili-direct:'||me::text||':'||operation,0));
    select id,receiver_id,kind,payload into old from public.lili_visit_events
      where sender_id=me and payload->>'scope'='direct' and payload->>'operation_key'=operation limit 1;
    if found then
      if old.receiver_id<>p_target or old.kind<>k or (old.payload->>'reply_to_event_id') is distinct from reply then
        raise exception '互动请求编号已用于另一操作'; end if;
      return old.id;
    end if;
  end if;
  -- Preserve the existing daytime privacy rule for playful teasing.
  if k='tease' and to_char(now() at time zone 'Asia/Shanghai','HH24:MI') not between '08:00' and '22:30' then
    raise exception '现在是嘲讽时间之外，给对方留点私人休息时间'; end if;
  data:=data||jsonb_build_object('scope','direct');
  if k like 'food_%' then
    return public.lili_send_food_interaction(p_target,k,data);
  end if;
  insert into public.lili_visit_events(sender_id,receiver_id,kind,status,payload)
    values(me,p_target,k,case when k='visit' then 'pending' else 'accepted' end,data) returning id into result;
  return result;
end $$;
revoke all on function public.lili_send_interaction(uuid,text,uuid,jsonb) from public,anon,authenticated;
grant execute on function public.lili_send_interaction(uuid,text,uuid,jsonb) to authenticated;

create or replace function public.lili_send_interaction(p_target uuid,p_kind text,p_room_id uuid default null)
returns uuid language sql security invoker set search_path='' as $$
  select public.lili_send_interaction(p_target,p_kind,p_room_id,'{}'::jsonb);
$$;
revoke all on function public.lili_send_interaction(uuid,text,uuid) from public,anon,authenticated;
grant execute on function public.lili_send_interaction(uuid,text,uuid) to authenticated;
notify pgrst,'reload schema';
