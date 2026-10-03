-- Only synthetic fixtures; real SQL/RPC semantics, everything rolls back.
begin;
-- REPLAY_PRESENCE_HERE
do $$
declare owner uuid:=gen_random_uuid(); viewer uuid:=gen_random_uuid(); r jsonb; rows jsonb; denied boolean:=false;
begin
  insert into auth.users(id,raw_user_meta_data) values(owner,'{}'),(viewer,'{}');
  perform set_config('request.jwt.claim.sub',owner::text,true);
  r:=public.lili_presence_heartbeat(false,false,null,null,'pc-a',1,null,'online','idle');
  if not (r->>'account_online')::boolean or (r->>'account_working')::boolean then raise exception 'idle is offline'; end if;
  rows:=public.lili_presence_display_rows(jsonb_build_array(jsonb_build_object('user_id',owner)));
  if rows->0->>'status'<>'online' or rows->0->>'activity_state'<>'idle' then raise exception 'idle projection'; end if;
  r:=public.lili_presence_heartbeat(true,true,'real-focus',now(),'pc-a',2,0,'online','focus');
  if not (r->>'account_working')::boolean then raise exception 'focus lost'; end if;
  r:=public.lili_presence_heartbeat(false,false,null,null,'pc-a',3,null,'online','rest');
  rows:=public.lili_presence_display_rows(jsonb_build_array(jsonb_build_object('user_id',owner)));
  if rows->0->>'status'<>'rest' then raise exception 'pause lost'; end if;
  r:=public.lili_presence_heartbeat(false,false,null,null,'pc-a',4,null,'online','idle');
  r:=public.lili_presence_heartbeat(false,false,null,null,'pc-b',1,null,'online','idle');
  r:=public.lili_presence_heartbeat(false,false,null,null,'pc-a',5,null,'offline','idle');
  if not (r->>'account_online')::boolean or (r->>'device_online')::boolean then raise exception 'one-device exit killed live peer'; end if;
  r:=public.lili_presence_heartbeat(false,false,null,null,'pc-b',2,null,'offline','idle');
  if (r->>'account_online')::boolean then raise exception 'explicit exit stayed online'; end if;
  r:=public.lili_presence_heartbeat(false,false,null,null,'pc-b',1,null,'online','idle');
  if (r->>'accepted')::boolean or (r->>'account_online')::boolean then raise exception 'stale sequence resurrected exit'; end if;
  r:=public.lili_presence_heartbeat(true,true,'no-proof',now(),'pc-a',6,null,'online','focus');
  if not (r->>'account_online')::boolean or (r->>'account_working')::boolean then raise exception 'input proof confused with liveness'; end if;
  rows:=public.lili_presence_display_rows(jsonb_build_array(jsonb_build_object('user_id',owner)));
  if rows->0->>'status'<>'focus' then raise exception 'missing input proof hid explicit focus activity'; end if;
  r:=public.lili_presence_heartbeat(true,true,'no-proof',now(),'pc-a',7,900,'online','focus');
  if (r->>'account_working')::boolean then raise exception 'display activity bypassed accounting proof'; end if;
  rows:=public.lili_presence_display_rows(jsonb_build_array(jsonb_build_object('user_id',owner)));
  if rows->0->>'status'<>'focus' then raise exception 'reading without keyboard changed activity'; end if;
  -- Neither timestamp mutation nor TTL expiry creates work or subscription events.
  update public.lili_focus_device_presence set last_seen=now()-interval '121 seconds' where user_id=owner;
  rows:=public.lili_presence_display_rows(jsonb_build_array(jsonb_build_object('user_id',owner)));
  if rows->0->>'status'<>'offline' then raise exception 'crash TTL failed'; end if;
  r:=public.lili_presence_heartbeat(false,false,null,null,'pc-a',8,null,'online','rest');
  if not (r->>'account_online')::boolean then raise exception 'network recovery failed'; end if;
  rows:=public.lili_presence_display_rows(jsonb_build_array(jsonb_build_object('user_id',owner)));
  if rows->0->>'status'<>'rest' then raise exception 'finished session did not return to rest'; end if;
  r:=public.lili_presence_heartbeat(true,false,null,null,'pc-a',9,0,'online','focus');
  rows:=public.lili_presence_display_rows(jsonb_build_array(jsonb_build_object('user_id',owner)));
  if rows->0->>'status'<>'rest' or (r->>'account_working')::boolean then raise exception 'invalid focus tuple accepted'; end if;
  -- Compatibility: an old v2 idle heartbeat still announces application liveness.
  r:=public.lili_upsert_focus_presence_v2(false,false,null,null,'old-client',1,null);
  if not (r->>'account_online')::boolean then raise exception 'legacy compatibility'; end if;
  r:=public.lili_dashboard();
  if r->'me_presence'->>'presence_state'<>'online' then raise exception 'dashboard flags absent'; end if;
  update public.lili_profiles set visibility='hidden' where user_id=owner;
  perform set_config('request.jwt.claim.sub',viewer::text,true);
  rows:=public.lili_presence_display_rows(jsonb_build_array(jsonb_build_object('user_id',owner)));
  if (rows->0->>'online')::boolean or rows->0->>'last_seen_at' is not null then raise exception 'privacy leak'; end if;
  perform set_config('request.jwt.claim.sub','',true);
  begin perform public.lili_presence_heartbeat(false,false,null,null,'anonymous',1,null,'online','idle');
  exception when others then denied:=sqlerrm like '%authentication required%'; end;
  if not denied then raise exception 'anonymous heartbeat allowed'; end if;
  if has_function_privilege('authenticated','public.lili_presence_display_rows(jsonb)','execute')
     or has_function_privilege('anon','public.lili_presence_heartbeat(boolean,boolean,text,timestamptz,text,bigint,integer,text,text)','execute')
  then raise exception 'private RPC permissions'; end if;
end $$;
rollback;
