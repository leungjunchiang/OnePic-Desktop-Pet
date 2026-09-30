-- Real owner-scoped RPC / ACK / cursor / compatibility tests; synthetic rows roll back.
begin;
do $$
declare owner uuid:=gen_random_uuid(); other_user uuid:=gen_random_uuid(); event_id uuid:=gen_random_uuid();
  report_id uuid:=gen_random_uuid(); bad_id uuid:=gen_random_uuid(); payload jsonb; replay jsonb; revision bigint;
  facts bigint; denied boolean; incoming jsonb; day date:=(now() at time zone 'Asia/Shanghai')::date;
begin
  select count(*) into facts from public.lili_focus_segments;
  insert into auth.users(id,raw_user_meta_data) values(owner,'{}'),(other_user,'{}');
  perform set_config('request.jwt.claim.sub',owner::text,true);
  incoming:=jsonb_build_array(jsonb_build_object('id',event_id,'event_type','start_work','event_date',day,
    'occurred_at',day::text||'T09:26:00+08:00','metadata',jsonb_build_object('rule_key',day||':actual_start','actual_start','09:26')),
    jsonb_build_object('id',bad_id,'event_type','start_break','event_date',day,'occurred_at',now(),'metadata','{}'::jsonb));
  payload:=public.lili_discipline_sync_delta('{"plan_version":2,"start_time":"09:00","weekly_target_minutes":2100}',now(),incoming,0,true);
  if jsonb_array_length(payload->'acknowledged_ids')<>1 or not payload->'acknowledged_ids' ? event_id::text then raise exception 'Valid-only ACK failed'; end if;
  if jsonb_array_length(payload->'events')<>1 then raise exception 'Process event entered ledger'; end if;
  if payload->'supervision'->'policy' ? 'selected_ids' or payload->'supervision' ? 'supervising' then raise exception 'Runtime snapshot exported full consent'; end if;
  if payload->'settings'->>'start_time'<>'09:00' then raise exception 'Explicit config read failed'; end if;
  revision:=(payload->>'next_revision')::bigint;
  replay:=public.lili_discipline_sync_delta(null,null,incoming,revision,false);
  if replay ? 'settings' or replay ? 'client_updated_at' then raise exception 'Idle poll returned config'; end if;
  if replay->'events'<>'[]'::jsonb or (replay->>'next_revision')::bigint<>revision then raise exception 'Replay changed event cursor'; end if;
  replay:=public.lili_discipline_sync_delta(null,null,jsonb_build_array(jsonb_build_object(
    'id',gen_random_uuid(),'event_type','late_start','event_date',day,'occurred_at',day::text||'T09:26:00+08:00',
    'metadata',jsonb_build_object('rule_key',day||':late_start','minutes_late',26,'planned_start',day::text||'T09:00:00+08:00'),
    'requires_explanation',true)),revision,false);
  revision:=(replay->>'next_revision')::bigint;
  -- Earlier real start from an offline second device corrects the canonical event.
  replay:=public.lili_discipline_sync_delta(null,null,jsonb_build_array(jsonb_build_object(
    'id',gen_random_uuid(),'event_type','start_work','event_date',day,'occurred_at',day::text||'T07:30:00+08:00',
    'metadata',jsonb_build_object('rule_key',day||':actual_start','actual_start','07:30'))),revision,false);
  if jsonb_array_length(replay->'events')<>2 or replay->'events'->0->'metadata'->>'actual_start'<>'07:30'
    or replay->'events'->1->'metadata'->>'minutes_late'<>'0'
    or (replay->'events'->1->>'requires_explanation')::boolean then raise exception 'Earlier-device correction failed'; end if;
  revision:=(replay->>'next_revision')::bigint;
  replay:=public.lili_discipline_sync_delta(null,null,jsonb_build_array(jsonb_build_object(
    'id',gen_random_uuid(),'event_type','start_work','event_date',day,'occurred_at',day::text||'T06:10:00+08:00',
    'metadata',jsonb_build_object('source','restore','actual_start','06:10'))),revision,false);
  if replay->'acknowledged_ids'<>'[]'::jsonb or public.lili_actual_work_start_clock(owner,day)<>'07:30' then raise exception 'Restore generated an actual start'; end if;
  -- A new local result after an earlier ACK is independently acknowledged and read.
  incoming:=jsonb_build_array(jsonb_build_object('id',report_id,'event_type','daily_report','event_date',day,
    'occurred_at',day::text||'T18:00:00+08:00','metadata',jsonb_build_object('rule_key',day||':daily_report','today_seconds',3600)));
  replay:=public.lili_discipline_sync_delta(null,null,incoming,revision,false);
  revision:=(replay->>'next_revision')::bigint;
  incoming:=jsonb_set(jsonb_set(incoming,'{0,occurred_at}',to_jsonb(day::text||'T20:00:00+08:00')),'{0,metadata,today_seconds}','7200');
  replay:=public.lili_discipline_sync_delta(null,null,incoming,revision,false);
  if replay->'events'->0->'metadata'->>'today_seconds'<>'7200' then raise exception 'Final summary remained first-round total'; end if;
  event_id:=gen_random_uuid();
  incoming:=jsonb_build_array(jsonb_build_object('id',event_id,'event_type','focus_shortfall','event_date',day,
    'occurred_at',day::text||'T12:00:00+08:00','requires_explanation',true,
    'metadata',jsonb_build_object('rule_key',day||':focus_shortfall','gap_seconds',3600)));
  replay:=public.lili_discipline_sync_delta(null,null,incoming,0,false);
  incoming:=jsonb_set(jsonb_set(jsonb_set(incoming,'{0,occurred_at}',to_jsonb(day::text||'T20:00:00+08:00')),
    '{0,requires_explanation}','false'),'{0,metadata,gap_seconds}','0');
  replay:=public.lili_discipline_sync_delta(null,null,incoming,0,false);
  if exists(select 1 from public.lili_discipline_events where user_id=owner and id=event_id
    and (requires_explanation or metadata->>'gap_seconds'<>'0')) then raise exception 'Completed goal kept stale explanation'; end if;
  -- Legacy IDs without a rule key are accepted without perpetual unique violations.
  event_id:=gen_random_uuid();
  insert into public.lili_discipline_events(user_id,id,event_type,event_date,occurred_at,metadata)
    values(owner,event_id,'late_start',day,now(),'{}');
  replay:=public.lili_discipline_sync_delta(null,null,jsonb_build_array(jsonb_build_object(
    'id',event_id,'event_type','late_start','event_date',day,'occurred_at',now(),'metadata','{}'::jsonb)),0,false);
  if not replay->'acknowledged_ids' ? event_id::text then raise exception 'Legacy event ACK failed'; end if;
  -- Cursor pages are bounded and still contain late writes.
  insert into public.lili_discipline_events(user_id,id,event_type,event_date,occurred_at,metadata)
    select owner,gen_random_uuid(),'long_break',day,now(),'{}' from generate_series(1,105);
  replay:=public.lili_discipline_sync_delta(null,null,'[]',0,false);
  if jsonb_array_length(replay->'events')<>100 or not (replay->>'has_more')::boolean then raise exception 'Bounded pagination failed'; end if;
  perform set_config('request.jwt.claim.sub',other_user::text,true);
  replay:=public.lili_discipline_sync_delta(null,null,'[]',0,true);
  if replay->'events'<>'[]'::jsonb or replay->'settings'<>'{}'::jsonb then raise exception 'Other account saw owner data'; end if;
  perform set_config('request.jwt.claim.sub','',true);
  denied:=false;
  begin perform public.lili_discipline_sync_delta(); exception when others then denied:=true; end;
  if not denied then raise exception 'Missing auth accepted'; end if;
  if has_function_privilege('anon','public.lili_discipline_sync_delta(jsonb,timestamptz,jsonb,bigint,boolean)','EXECUTE')
    or has_function_privilege('authenticated','public.lili_discipline_runtime_snapshot()','EXECUTE')
    or has_table_privilege('authenticated','public.lili_discipline_events','SELECT') then raise exception 'Private ledger privileges widened'; end if;
  if (select count(*) from public.lili_focus_segments)<>facts then raise exception 'Focus facts changed'; end if;
end; $$;
rollback;
