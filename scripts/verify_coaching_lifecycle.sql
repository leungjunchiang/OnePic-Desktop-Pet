-- Real RPC lifecycle, permissions, CAS, idempotency and union-focus verification.
-- Synthetic accounts and focus facts are always rolled back.
begin;
do $$
declare owner uuid:=gen_random_uuid(); coach uuid:=gen_random_uuid(); stranger uuid:=gen_random_uuid();
  source uuid:=gen_random_uuid(); start_id uuid:=gen_random_uuid(); cid uuid; aid uuid:=gen_random_uuid();
  result jsonb; previous jsonb; rev bigint; denied boolean; day date:=(now() at time zone 'Asia/Shanghai')::date-1;
begin
  insert into auth.users(id,raw_user_meta_data) values(owner,'{}'),(coach,'{}'),(stranger,'{}');
  insert into public.lili_buddy_links(requester_id,addressee_id,status) values(owner,coach,'accepted');
  insert into public.lili_supervision_policy(owner_id,enabled,scope,officer_scope) values(owner,true,'all','all');
  insert into public.lili_discipline_settings(user_id,settings) values(owner,'{"weekly_target_minutes":1,"start_time":"09:00"}');
  insert into public.lili_discipline_events(user_id,id,event_type,event_date,occurred_at,metadata,requires_explanation) values
    (owner,start_id,'start_work',day,(day::text||'T09:47:00+08:00')::timestamptz,
      '{"actual_start_source":"explicit_focus_start","planned_start":"09:00"}',false),
    (owner,source,'late_start',day,(day::text||'T09:47:00+08:00')::timestamptz,
      jsonb_build_object('minutes_late',47,'planned_start',day::text||'T09:00:00+08:00'),true);
  perform set_config('request.jwt.claim.sub',coach::text,true);
  perform public.lili_start_supervision(owner,'normal');
  denied:=false;
  begin perform public.lili_coaching_case_action(owner,'request_explanation',aid,null,source); exception when others then denied:=true; end;
  if not denied then raise exception 'Normal mode generated formal discipline'; end if;
  perform public.lili_start_supervision(owner,'officer');
  result:=public.lili_coaching_case_action(owner,'request_explanation',aid,null,source);
  cid:=(result->'case'->>'id')::uuid; rev:=(result->'case'->>'revision')::bigint;
  if result->'case'->>'state'<>'pending' or result->'case'->>'title'<>'迟到 47 分钟' then raise exception 'Pending case failed'; end if;
  previous:=public.lili_coaching_case_action(owner,'request_explanation',aid,cid,null,0);
  if previous->'case'->>'revision'<>rev::text then raise exception 'Retry changed case revision'; end if;
  denied:=false;
  begin perform public.lili_coaching_case_action(owner,'forgive',gen_random_uuid(),cid,null,0); exception when others then denied:=true; end;
  if not denied then raise exception 'Stale device overwrote case'; end if;
  perform set_config('request.jwt.claim.sub',owner::text,true);
  result:=public.lili_coaching_case_action(owner,'explain',gen_random_uuid(),cid,null,rev,0,'上午临时开会。');
  rev:=(result->'case'->>'revision')::bigint;
  if result->'case'->>'state'<>'explained' then raise exception 'Explanation failed'; end if;
  denied:=false;
  begin perform public.lili_coaching_case_action(owner,'forgive',gen_random_uuid(),cid,null,rev); exception when others then denied:=true; end;
  if not denied then raise exception 'Owner gained supervisor action'; end if;
  perform set_config('request.jwt.claim.sub',coach::text,true);
  result:=public.lili_coaching_case_action(owner,'reject',gen_random_uuid(),cid,null,rev,0,'请再说明会议时间。');
  rev:=(result->'case'->>'revision')::bigint;
  if result->'case'->>'state'<>'rejected' then raise exception 'Review reject failed'; end if;
  perform set_config('request.jwt.claim.sub',owner::text,true);
  result:=public.lili_coaching_case_action(owner,'explain',gen_random_uuid(),cid,null,rev,0,'九点开会，九点四十分结束。');
  rev:=(result->'case'->>'revision')::bigint;
  perform set_config('request.jwt.claim.sub',coach::text,true);
  result:=public.lili_coaching_case_action(owner,'approve_makeup',gen_random_uuid(),cid,null,rev,20,'说明通过，补二十分钟。');
  if result->'case'->>'state'<>'active' then raise exception 'Partial approval did not start makeup'; end if;
  -- Time travel only in the rollback fixture: actual API cannot alter accepted_at.
  update public.lili_coaching_cases set accepted_at=now()-interval '25 minutes' where id=cid returning revision into rev;
  insert into public.lili_focus_segments(user_id,segment_id,session_id,device_id,start_at,end_at) values
    (owner,'case-a','a','pc1',now()-interval '25 minutes',now()-interval '20 minutes'),
    (owner,'case-a-duplicate','a','pc2',now()-interval '25 minutes',now()-interval '20 minutes');
  if public.lili_coaching_focus_seconds(owner,now()-interval '25 minutes')<>300 then raise exception 'Overlapping devices counted twice'; end if;
  perform set_config('request.jwt.claim.sub',owner::text,true);
  result:=public.lili_coaching_case_action(owner,'complete',gen_random_uuid(),cid,null,rev);
  if not (result->>'completion_pending')::boolean or result->'case'->>'state'<>'active' then raise exception 'Wall time prematurely completed makeup'; end if;
  insert into public.lili_focus_segments(user_id,segment_id,session_id,device_id,start_at,end_at) values
    (owner,'case-b','b','pc1',now()-interval '20 minutes',now()-interval '5 minutes');
  aid:=gen_random_uuid(); result:=public.lili_coaching_case_action(owner,'complete',aid,cid,null,rev);
  if result->'case'->>'state'<>'completed' then raise exception 'Focus completion failed'; end if;
  previous:=public.lili_coaching_case_action(owner,'complete',aid,cid,null,rev);
  if previous->'case'->>'revision'<>result->'case'->>'revision' then raise exception 'Complete retry was not idempotent'; end if;
  if (select requires_explanation from public.lili_discipline_events where user_id=owner and id=source) then raise exception 'Closed case remained pending'; end if;
  -- Independent cursor returns each case version once, and no configuration on idle poll.
  result:=public.lili_coaching_sync_delta(null,null,'[]',0,false,0);
  if jsonb_array_length(result->'coaching_cases')<>1 or result ? 'settings' then raise exception 'Case delta/config separation failed'; end if;
  previous:=public.lili_coaching_sync_delta(null,null,'[]',0,false,(result->>'next_case_revision')::bigint);
  if previous->'coaching_cases'<>'[]' then raise exception 'Case delta repeated unchanged history'; end if;
  perform set_config('request.jwt.claim.sub',stranger::text,true);
  denied:=false;
  begin perform public.lili_coaching_case_action(owner,'complete',gen_random_uuid(),cid,null,rev); exception when others then denied:=true; end;
  if not denied then raise exception 'Unrelated user accessed case'; end if;
  denied:=false;
  begin perform public.lili_buddy_study_overview(owner); exception when others then denied:=true; end;
  if not denied then raise exception 'Unrelated user read explanations'; end if;
  -- Revocation immediately prevents actions/read access while retaining history.
  update public.lili_supervision_policy set enabled=false where owner_id=owner;
  perform set_config('request.jwt.claim.sub',coach::text,true);
  result:=public.lili_buddy_study_overview(owner);
  if result->'coaching_cases'<>'[]' or result->'coaching_candidates'<>'[]' then raise exception 'Revocation leaked case history'; end if;
  if not exists(select 1 from public.lili_coaching_cases where id=cid and state='completed') then raise exception 'Revocation deleted history'; end if;
  if has_table_privilege('authenticated','public.lili_coaching_cases','update')
     or has_table_privilege('authenticated','public.lili_coaching_cases','select')
     or not (select relrowsecurity from pg_class where oid='public.lili_coaching_cases'::regclass)
     or has_function_privilege('anon','public.lili_coaching_case_action(uuid,text,uuid,uuid,uuid,bigint,integer,text)','execute')
     or has_function_privilege('authenticated','public.lili_coaching_focus_seconds(uuid,timestamptz)','execute')
     then raise exception 'Case RPC/RLS privilege boundary failed'; end if;
end; $$;
do $$
declare owner uuid:=gen_random_uuid(); coach uuid:=gen_random_uuid(); source uuid:=gen_random_uuid();
  cid uuid; rev bigint; result jsonb; day date:=(now() at time zone 'Asia/Shanghai')::date;
begin
  insert into auth.users(id,raw_user_meta_data) values(owner,'{}'),(coach,'{}');
  insert into public.lili_buddy_links(requester_id,addressee_id,status) values(owner,coach,'accepted');
  insert into public.lili_supervision_policy(owner_id,enabled,scope,officer_scope) values(owner,true,'all','all');
  perform set_config('request.jwt.claim.sub',coach::text,true);
  perform public.lili_start_supervision(owner,'officer');
  insert into public.lili_discipline_events(user_id,id,event_type,event_date,occurred_at,metadata,requires_explanation)
    values(owner,source,'focus_shortfall',day-1,now()-interval '1 day','{"gap_seconds":1800}',true);
  result:=public.lili_coaching_case_action(owner,'tomorrow_makeup',gen_random_uuid(),null,source,0,30);
  cid:=(result->'case'->>'id')::uuid; rev:=(result->'case'->>'revision')::bigint;
  perform set_config('request.jwt.claim.sub',owner::text,true);
  result:=public.lili_coaching_case_action(owner,'accept',gen_random_uuid(),cid,null,rev);
  if result->'case'->>'state'<>'active' or (result->'case'->>'accepted_at')::timestamptz
     <> (day+1)::timestamp at time zone 'Asia/Shanghai' then raise exception 'Tomorrow acceptance boundary failed'; end if;
  rev:=(result->'case'->>'revision')::bigint;
  perform set_config('request.jwt.claim.sub',coach::text,true);
  result:=public.lili_coaching_case_action(owner,'forgive',gen_random_uuid(),cid,null,rev);
  if result->'case'->>'state'<>'forgiven' or result->'case'->>'required_seconds'<>'1800'
     then raise exception 'Forgiveness lost original makeup/history'; end if;
  source:=gen_random_uuid();
  insert into public.lili_discipline_events(user_id,id,event_type,event_date,occurred_at,metadata,requires_explanation)
    values(owner,source,'focus_shortfall',day-1,now()-interval '1 day','{"gap_seconds":1200}',true);
  result:=public.lili_coaching_case_action(owner,'week_makeup',gen_random_uuid(),null,source,0,20);
  cid:=(result->'case'->>'id')::uuid; rev:=(result->'case'->>'revision')::bigint;
  perform set_config('request.jwt.claim.sub',owner::text,true);
  result:=public.lili_coaching_case_action(owner,'accept',gen_random_uuid(),cid,null,rev);
  if result->'case'->>'state'<>'active' or result->'case'->>'schedule'<>'week'
     or (result->'case'->>'accepted_at')::timestamptz<>now() then raise exception 'Week makeup acceptance failed'; end if;
  -- A settled long break opens once; replay, idle hydration and rendering cannot reopen it.
  source:=gen_random_uuid();
  result:=public.lili_coaching_sync_delta(null,null,jsonb_build_array(jsonb_build_object(
    'id',source,'event_type','long_break','event_date',day,'occurred_at',now(),
    'requires_explanation',true,'metadata',jsonb_build_object('rule_key',source::text,'overtime_seconds',1080,'duration_seconds',2880,'limit_minutes',30))),0,false,0);
  if not exists(select 1 from public.lili_coaching_cases where owner_id=owner and source_event_id=source
    and state='pending' and title='长休超时 18 分钟' and detail='允许休息 30 分钟 · 实际休息 48 分钟')
    then raise exception 'New settled strict issue or explanation details failed'; end if;
  perform public.lili_coaching_sync_delta(null,null,'[]',0,false,0);
  if (select count(*) from public.lili_coaching_cases where owner_id=owner and source_event_id=source)<>1
    then raise exception 'Idle hydration duplicated case'; end if;
  -- Earlier-device corrections dismiss the case without pretending makeup was completed.
  update public.lili_discipline_events set metadata='{"gap_seconds":0}',requires_explanation=false
    where user_id=owner and id=(select source_event_id from public.lili_coaching_cases where id=cid);
  perform public.lili_coaching_sync_delta(null,null,'[]',0,false,0);
  if not exists(select 1 from public.lili_coaching_cases where id=cid and state='forgiven'
    and history->-1->>'action'='fact_corrected') then raise exception 'Corrected source kept unjustified makeup'; end if;
end; $$;
rollback;
