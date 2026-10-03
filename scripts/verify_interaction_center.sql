-- Real PostgreSQL, synthetic users only; all fixtures roll back.
begin;
set local time zone 'America/Los_Angeles';
do $$
declare owner uuid:=gen_random_uuid(); sender uuid:=gen_random_uuid(); outsider uuid:=gen_random_uuid();
  d date; denied boolean; result jsonb; key text; n integer; today date:=(now() at time zone 'Asia/Shanghai')::date;
begin
  insert into auth.users(id,raw_user_meta_data) values(owner,'{}'),(sender,'{}'),(outsider,'{}');
  insert into public.lili_discipline_settings(user_id,settings,client_updated_at)
    values(owner,'{"plan_version":2,"workdays":["mon","tue","wed","thu","fri","sat","sun"]}',now());
  -- Eight DISTINCT September days; duplicated button presses do not count twice.
  for n in 1..8 loop
    d:=date '2026-09-01'+n-1;
    insert into public.lili_discipline_events(user_id,id,event_type,event_date,occurred_at)
      values(owner,gen_random_uuid(),'rest_day',d,(d::text||'T12:00:00+08:00')::timestamptz);
  end loop;
  insert into public.lili_discipline_events(user_id,id,event_type,event_date,occurred_at)
    values(owner,gen_random_uuid(),'cancel_rest_day','2026-09-01','2026-09-01T12:01:00+08:00');
  if public.lili_is_rest_day(owner,'2026-09-01') then raise exception 'Cancel did not clear active exemption'; end if;
  insert into public.lili_discipline_events(user_id,id,event_type,event_date,occurred_at)
    values(owner,gen_random_uuid(),'rest_day','2026-09-01','2026-09-01T12:02:00+08:00');
  if not public.lili_is_rest_day(owner,'2026-09-01') then raise exception 'Re-enable same consumed date failed'; end if;
  denied:=false;
  begin
    insert into public.lili_discipline_events(user_id,id,event_type,event_date,occurred_at)
      values(owner,gen_random_uuid(),'rest_day','2026-09-09','2026-09-09T12:00:00+08:00');
  exception when others then denied:=sqlerrm like '%额度%'; end;
  if not denied then raise exception 'Ninth distinct day / old device bypassed limit'; end if;
  select count(distinct event_date) into n from public.lili_discipline_events where user_id=owner and event_type='rest_day';
  if n<>8 then raise exception 'Cancellation refunded quota'; end if;
  perform set_config('request.jwt.claim.sub',owner::text,true);
  result:=public.lili_set_rest_day();
  if (result->'rest_day_quota'->>'limit')::int<>8 then raise exception 'Quota missing from mutation response'; end if;
  perform public.lili_cancel_rest_day();
  if public.lili_is_rest_day(owner,today) then raise exception 'Server cancellation failed'; end if;
  result:=public.lili_set_rest_day();
  if not public.lili_is_rest_day(owner,today) then raise exception 'Server re-enable failed'; end if;
  -- First hydration is silent/read; unseen action requirement remains independent.
  insert into public.lili_visit_events(sender_id,receiver_id,kind) values(sender,owner,'visit');
  result:=public.lili_interaction_inbox();
  if (result->'interaction_events'->0->>'unread')::boolean then raise exception 'Initial history marked unread'; end if;
  update public.lili_interaction_inbox_state set baseline_at=now()-interval '1 day' where user_id=owner;
  result:=public.lili_interaction_inbox();
  key:=result->'interaction_events'->0->>'event_id';
  if not (result->'interaction_events'->0->>'unread')::boolean then raise exception 'New event missing unread'; end if;
  result:=public.lili_interaction_inbox(jsonb_build_array(key));
  if (result->'interaction_events'->0->>'unread')::boolean then raise exception 'Cross-device read receipt missing'; end if;
  if not (result->'interaction_events'->0->>'requires_action')::boolean then raise exception 'Reading consumed invitation'; end if;
  result:=public.lili_mark_interactions_read(jsonb_build_array(key));
  if result ? 'interaction_events' or result->'read_event_ids'<>jsonb_build_array(key) then
    raise exception 'Read receipt re-downloaded history / failed'; end if;
  -- Beijing natural calendar range, despite the connection's Los Angeles timezone.
  insert into public.lili_visit_events(sender_id,receiver_id,kind,status,created_at)
    values(sender,owner,'food_tea','accepted',((today-6)::timestamp at time zone 'Asia/Shanghai')),
          (sender,owner,'food_tea','accepted',((today-6)::timestamp at time zone 'Asia/Shanghai')-interval '1 second');
  result:=public.lili_interaction_inbox();
  if jsonb_array_length(result->'interaction_events')<>2 then
    raise exception 'Seven Beijing dates range boundary failed'; end if;
  for key in select jsonb_object_keys(result->'interaction_events'->0) loop
    if key not in ('event_id','sender_id','event_type','created_at','source_id','source','requires_action','payload','nickname','unread','received_silent','handled_at') then
      raise exception 'Unexpected heavy inbox field: %',key; end if;
  end loop;
  if jsonb_typeof(result->'interaction_events'->0->'received_silent')<>'boolean'
     or jsonb_typeof(result->'interaction_events'->0->'handled_at') not in ('null','string') then
    raise exception 'Delivery markers are not bounded scalar fields'; end if;
  key:=result->'interaction_events'->0->>'event_id';
  perform set_config('request.jwt.claim.sub',outsider::text,true);
  result:=public.lili_interaction_inbox(jsonb_build_array(key));
  if jsonb_array_length(result->'interaction_events')<>0 or exists(select 1 from public.lili_interaction_reads where user_id=outsider) then
    raise exception 'Inbox / receipt owner isolation failed'; end if;
  result:=public.lili_mark_interactions_read(jsonb_build_array(key));
  if jsonb_array_length(result->'read_event_ids')<>0 then raise exception 'Outsider read receipt bypass'; end if;
  if has_function_privilege('anon','public.lili_mark_interactions_read(jsonb)','EXECUTE') then raise exception 'Read receipt exposed'; end if;
  if has_table_privilege('authenticated','public.lili_interaction_reads','INSERT')
     or has_table_privilege('authenticated','public.lili_interaction_feed','SELECT')
     or has_function_privilege('anon','public.lili_interaction_inbox(jsonb)','EXECUTE') then
    raise exception 'Private tables/helper became public'; end if;
  if has_function_privilege('authenticated','public.lili_rest_day_quota_guard()','EXECUTE') then raise exception 'Quota guard exposed'; end if;
end $$;
rollback;
