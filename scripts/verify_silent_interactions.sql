-- Synthetic users only. Everything, including read/handling effects, rolls back.
begin;
do $$
declare a uuid:=gen_random_uuid(); b uuid:=gen_random_uuid(); c uuid:=gen_random_uuid();
  t uuid; e uuid; v uuid; normal uuid; late uuid; result jsonb; denied boolean;
begin
  insert into auth.users(id,raw_user_meta_data) values(a,'{}'),(b,'{}'),(c,'{}');
  insert into public.lili_buddy_links(requester_id,addressee_id,status) values(a,b,'accepted');
  update public.lili_profiles set allow_visits=false where user_id=a;
  if not exists(select 1 from public.lili_profiles where user_id=a and allow_visits and buddy_interaction_mode='do_not_disturb') then raise exception 'Legacy reception toggle still rejects messages'; end if;
  perform set_config('request.jwt.claim.sub',b::text,true);
  v:=public.lili_send_interaction(a,'cheer',null,'{}');
  if not exists(select 1 from public.lili_interaction_delivery where receiver_id=a and event_id='visit:'||v and received_silent and handled_at is null) then raise exception 'DND delivery missing'; end if;
  insert into public.lili_buddy_taunts(sender_id,receiver_id) values(b,a) returning id into t;
  insert into public.lili_buddy_encouragements(sender_id,receiver_id) values(b,a) returning id into e;
  if exists(select 1 from public.lili_buddy_taunts where id=t and released_at is null)
    or exists(select 1 from public.lili_buddy_encouragements where id=e and ended_at is null) then raise exception 'Silent effect activated on receipt'; end if;
  perform set_config('request.jwt.claim.sub',a::text,true);
  perform public.lili_mark_interactions_read(jsonb_build_array('taunt:'||t));
  if exists(select 1 from public.lili_interaction_delivery where event_id='taunt:'||t and handled_at is not null) then raise exception 'Read equals handled'; end if;
  result:=public.lili_interaction_inbox();
  if not exists(select 1 from jsonb_array_elements(result->'interaction_events') x where x->>'event_id'='taunt:'||t and (x->>'received_silent')::boolean and x->>'handled_at' is null) then raise exception 'Inbox lost silent delivery state'; end if;
  result:=public.lili_handle_silent_interaction('taunt:'||t);
  if not (result->>'first_handled')::boolean or not (result->'taunt_state'->>'active')::boolean then raise exception 'First handling did not activate taunt'; end if;
  update public.lili_buddy_taunts set worked_seconds=99 where id=t;
  result:=public.lili_handle_silent_interaction('taunt:'||t);
  if (result->>'first_handled')::boolean or (select worked_seconds from public.lili_buddy_taunts where id=t)<>99 then raise exception 'Second device replayed/reset effect'; end if;
  -- Turning DND off does not lose the captured delivery condition.
  update public.lili_profiles set buddy_interaction_mode='welcome' where user_id=a;
  perform public.lili_presence_heartbeat(true,true,'silent-test-focus',now(),'silent-test-device',1,0,'online','focus');
  result:=public.lili_handle_silent_interaction('cheer:'||e);
  if not (result->>'first_handled')::boolean or not (result->'encouragement_state'->>'active')::boolean then raise exception 'Deferred cheer not activated'; end if;
  result:=public.lili_handle_silent_interaction('visit:'||v);
  if not (result->>'first_handled')::boolean then raise exception 'Direct cheer was not processed'; end if;
  result:=public.lili_interaction_inbox();
  if (select count(*) from jsonb_array_elements(result->'interaction_events') x where x->>'event_type'='cheer')<>2 then raise exception 'Activated child became duplicate inbox interaction'; end if;
  insert into public.lili_visit_events(sender_id,receiver_id,kind,status) values(b,a,'cheer','accepted') returning id into normal;
  update public.lili_profiles set buddy_interaction_mode='do_not_disturb' where user_id=a;
  result:=public.lili_handle_silent_interaction('visit:'||normal);
  if (result->>'first_handled')::boolean then raise exception 'Normal delivery replayed after enabling DND'; end if;
  -- Visit acceptance/decline are actual handling; merely opening the details is not.
  insert into public.lili_visit_events(sender_id,receiver_id,kind,status) values(b,a,'food_tea','pending') returning id into late;
  perform public.lili_respond_visit(late,true);
  result:=public.lili_handle_silent_interaction('visit:'||late);
  if (result->>'first_handled')::boolean then raise exception 'Already accepted food replayed'; end if;
  perform set_config('request.jwt.claim.sub',c::text,true);
  denied:=false;
  begin perform public.lili_handle_silent_interaction('visit:'||v); exception when others then denied:=true; end;
  if not denied then raise exception 'Another user claimed receiver effect'; end if;
  perform set_config('request.jwt.claim.sub','',true);
  denied:=false;
  begin perform public.lili_handle_silent_interaction('visit:'||v); exception when others then denied:=true; end;
  if not denied or has_function_privilege('anon','public.lili_handle_silent_interaction(text)','execute')
    or has_table_privilege('authenticated','public.lili_interaction_delivery','select') then raise exception 'Delivery privacy leaked'; end if;
end $$;
rollback;
