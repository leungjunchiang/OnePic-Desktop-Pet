-- Real PostgreSQL RPC tests. Synthetic users only; no personal data retained.
begin;
-- REPLAY_DIRECT_HERE
do $$
declare a uuid:=gen_random_uuid(); b uuid:=gen_random_uuid(); c uuid:=gen_random_uuid();
  origin uuid; sent uuid; again uuid; item text; key text; denied boolean; n integer;
begin
  insert into auth.users(id,raw_user_meta_data) values(a,'{}'),(b,'{}'),(c,'{}');
  insert into public.lili_buddy_links(requester_id,addressee_id,status) values(a,b,'accepted');
  -- No room or room membership fixtures at all.
  insert into public.lili_visit_events(sender_id,receiver_id,kind,status) values(b,a,'cheer','accepted') returning id into origin;
  key:='visit:'||origin;
  perform set_config('request.jwt.claim.sub',a::text,true);
  foreach item in array array['cheer','praise','flower','knock','start','return','visit','food_coffee'] loop
    sent:=public.lili_send_interaction(b,item,null,jsonb_build_object('reply_to_event_id',key,'operation_key',item));
    again:=public.lili_send_interaction(b,item,null,jsonb_build_object('reply_to_event_id',key,'operation_key',item));
    if sent<>again then raise exception 'Network retry duplicated a direct event'; end if;
    if not exists(select 1 from public.lili_visit_events where id=sent and sender_id=a and receiver_id=b
      and kind=item and payload->>'reply_to_event_id'=key and payload->>'scope'='direct') then
      raise exception 'Direct reply lost sender/scope/original event'; end if;
  end loop;
  sent:=public.lili_send_interaction(b,'cheer',null); -- legacy three arguments
  if sent is null then raise exception 'Legacy direct call failed'; end if;
  -- A room-origin reply does not require remaining room membership.
  insert into public.lili_study_rooms(owner_id,name,invite_code) values(b,'direct-test',public.lili_invite_code()) returning id into origin;
  insert into public.lili_room_events(room_id,actor_id,target_id,kind,message)
    values(origin,b,a,'cheer','') returning id into sent;
  perform public.lili_send_interaction(b,'flower',null,jsonb_build_object('reply_to_event_id','room:'||sent));
  -- Current room membership is still required for explicit room operations.
  denied:=false;
  begin perform public.lili_send_interaction(b,'cheer',origin); exception when others then denied:=true; end;
  if not denied then raise exception 'Room membership validation was bypassed'; end if;
  -- Forged target/event and strict coaching may not use this ordinary endpoint.
  denied:=false;
  begin perform public.lili_send_interaction(b,'cheer',null,jsonb_build_object('reply_to_event_id','visit:'||gen_random_uuid()));
    exception when others then denied:=true; end;
  if not denied then raise exception 'Forged original event accepted'; end if;
  denied:=false;
  begin perform public.lili_send_interaction(c,'cheer',null,jsonb_build_object('reply_to_event_id',key));
    exception when others then denied:=true; end;
  if not denied then raise exception 'Non-buddy accepted'; end if;
  insert into public.lili_buddy_links(requester_id,addressee_id,status) values(a,c,'accepted');
  denied:=false;
  begin perform public.lili_send_interaction(c,'cheer',null,jsonb_build_object('reply_to_event_id',key));
    exception when others then denied:=true; end;
  if not denied then raise exception 'Reply was redirected to a different buddy'; end if;
  insert into public.lili_visit_events(sender_id,receiver_id,kind,status) values(b,c,'cheer','accepted') returning id into sent;
  denied:=false;
  begin perform public.lili_send_interaction(b,'cheer',null,jsonb_build_object('reply_to_event_id','visit:'||sent));
    exception when others then denied:=true; end;
  if not denied then raise exception 'Someone else received the original event'; end if;
  denied:=false;
  begin perform public.lili_send_interaction(b,'require_explanation',null,'{}'); exception when others then denied:=true; end;
  if not denied then raise exception 'Coaching authorization bypass'; end if;
  denied:=false;
  begin perform public.lili_send_interaction(b,'flower',null,jsonb_build_object('operation_key','cheer','reply_to_event_id',key));
    exception when others then denied:=true; end;
  if not denied then raise exception 'Operation key rebound to another action'; end if;
  update public.lili_profiles set allow_visits=false where user_id=b;
  denied:=false;
  begin perform public.lili_send_interaction(b,'cheer',null,'{}'); exception when others then denied:=true; end;
  if not denied then raise exception 'Interaction opt-out bypassed'; end if;
  update public.lili_profiles set allow_visits=true,buddy_interaction_mode='do_not_disturb' where user_id=b;
  denied:=false;
  begin perform public.lili_send_interaction(b,'flower',null,'{}'); exception when others then denied:=true; end;
  if not denied then raise exception 'Do-not-disturb bypassed'; end if;
  update public.lili_profiles set buddy_interaction_mode='welcome' where user_id=b;
  if to_char(now() at time zone 'Asia/Shanghai','HH24:MI') between '08:00' and '22:30' then
    perform public.lili_send_interaction(b,'tease',null,jsonb_build_object('reply_to_event_id',key));
  else
    denied:=false;
    begin perform public.lili_send_interaction(b,'tease',null,'{}'); exception when others then denied:=true; end;
    if not denied then raise exception 'Teasing daytime privacy bypassed'; end if;
  end if;
  delete from public.lili_buddy_links where requester_id=a and addressee_id=b;
  denied:=false;
  begin perform public.lili_send_interaction(b,'cheer',null,jsonb_build_object('reply_to_event_id',key));
    exception when others then denied:=true; end;
  if not denied then raise exception 'Removed relationship revived by history'; end if;
  if has_function_privilege('anon','public.lili_send_interaction(uuid,text,uuid,jsonb)','execute') then
    raise exception 'Anonymous execute leaked'; end if;
  if has_table_privilege('authenticated','public.lili_interaction_feed','select') then
    raise exception 'Private feed exposed'; end if;
  perform set_config('request.jwt.claim.sub','',true);
  denied:=false;
  begin perform public.lili_send_interaction(b,'cheer',null,'{}'); exception when others then denied:=true; end;
  if not denied then raise exception 'Missing login accepted'; end if;
end $$;
rollback;
