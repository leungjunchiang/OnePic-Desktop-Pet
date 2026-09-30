-- Real auth.uid / RPC tests, isolated synthetic accounts, always rolled back.
begin;
do $$
declare owner uuid:=gen_random_uuid(); a uuid:=gen_random_uuid(); b uuid:=gen_random_uuid(); outsider uuid:=gen_random_uuid();
  payload jsonb; p jsonb; revision bigint; denied boolean; meta jsonb;
begin
  insert into auth.users(id,raw_user_meta_data) values(owner,'{}'),(a,'{}'),(b,'{}'),(outsider,'{}');
  insert into public.lili_buddy_links(requester_id,addressee_id,status) values(owner,a,'accepted'),(owner,b,'accepted');
  insert into public.lili_discipline_settings(user_id,settings) values(owner,'{"mode":"off","weekly_target_minutes":2100,"private_note":"MUST_NOT_EXPORT"}');
  insert into public.lili_discipline_events(user_id,id,event_type,event_date,occurred_at,metadata)
    values(owner,gen_random_uuid(),'daily_report',current_date,now(),'{"today_seconds":3600,"week_seconds":7200,"daily_target_seconds":19800,"lateness_minutes":37,"actual_start":"09:37","long_break_count":2,"private_note":"MUST_NOT_EXPORT"}');
  perform set_config('request.jwt.claim.sub',a::text,true);
  if (public.lili_buddy_study_overview(owner)->'peer_permission'->>'eligible')::boolean then raise exception 'Default consent must be off'; end if;
  denied:=false;
  begin perform public.lili_start_supervision(owner,'normal'); exception when others then denied:=true; end;
  if not denied then raise exception 'Off owner allowed supervision'; end if;
  perform set_config('request.jwt.claim.sub',owner::text,true);
  p:=public.lili_supervision_snapshot()->'policy';
  p:=p||jsonb_build_object('enabled',true,'scope','all');
  payload:=public.lili_set_supervision_policy(p,0);
  revision:=(payload->'policy'->>'revision')::bigint;
  perform set_config('request.jwt.claim.sub',a::text,true);
  perform public.lili_start_supervision(owner,'normal'); -- No approval/request required.
  denied:=false;
  begin perform public.lili_start_supervision(owner,'officer'); exception when others then denied:=true; end;
  if not denied then raise exception 'Officer default broadened'; end if;
  perform public.lili_supervision_nudge(owner,'start');
  denied:=false;
  begin perform public.lili_supervision_nudge(owner,'rest'); exception when others then denied:=true; end;
  if not denied then raise exception 'Nudge cooldown failed'; end if;
  payload:=public.lili_buddy_study_overview(owner);
  if (payload->'peer_plan'->>'weekly_target_minutes')::int<>2100 or payload::text like '%MUST_NOT_EXPORT%' then raise exception 'Plan whitelist failed'; end if;
  perform set_config('request.jwt.claim.sub',b::text,true);
  perform public.lili_start_supervision(owner,'normal'); -- Concurrent second supervisor.
  perform set_config('request.jwt.claim.sub',owner::text,true);
  payload:=public.lili_supervision_snapshot();
  if jsonb_array_length(payload->'supervisors')<>2 or payload->>'effective_mode'<>'normal' then raise exception 'Multiple supervisors failed'; end if;
  denied:=false;
  begin perform public.lili_set_supervision_policy(p,revision); exception when others then denied:=true; end;
  if not denied then raise exception 'Stale device overwrite allowed'; end if;
  p:=payload->'policy';
  p:=p||jsonb_build_object('scope','selected','selected_ids',jsonb_build_array(a),'officer_ids',jsonb_build_array(a),
    'view_plan',false,'view_lateness',false,'view_progress',false,'view_rest',false);
  payload:=public.lili_set_supervision_policy(p,(payload->'policy'->>'revision')::bigint);
  perform set_config('request.jwt.claim.sub',b::text,true);
  if (public.lili_buddy_study_overview(owner)->'peer_permission'->>'eligible')::boolean then raise exception 'Selected scope leaked'; end if;
  perform set_config('request.jwt.claim.sub',a::text,true);
  perform public.lili_start_supervision(owner,'officer');
  meta:=public.lili_discipline_supervisor_report(owner)->'reports'->0->'metadata';
  if meta ? 'today_seconds' or meta ? 'week_seconds' or meta ? 'lateness_minutes' or meta ? 'actual_start'
    or meta ? 'long_break_count' or meta ? 'daily_target_seconds' or meta::text like '%MUST_NOT_EXPORT%' then raise exception 'Report field redaction failed'; end if;
  perform public.lili_mark_discipline_report_read(owner,current_date);
  perform set_config('request.jwt.claim.sub',owner::text,true);
  payload:=public.lili_discipline_sync('{"enabled":true,"scope":"all","mode":"off"}',now(),'[]');
  if payload->'supervision'->>'effective_mode'<>'officer' or payload->'supervision'->'policy'->>'scope'<>'selected' then raise exception 'Work settings overwrote consent'; end if;
  p:=payload->'supervision'->'policy';
  payload:=public.lili_set_supervision_policy(p||'{"enabled":false}',(p->>'revision')::bigint);
  if payload->>'effective_mode'<>'off' then raise exception 'Owner off failed'; end if;
  perform set_config('request.jwt.claim.sub',a::text,true);
  payload:=public.lili_buddy_study_overview(owner);
  if payload->'peer_plan'<>'null'::jsonb or (payload->>'can_read_reports')::boolean or payload->'active_mode'<>'null'::jsonb then raise exception 'Owner off shared data'; end if;
  denied:=false;
  begin perform public.lili_discipline_supervisor_report(owner); exception when others then denied:=true; end;
  if not denied then raise exception 'Legacy report bypassed off'; end if;
  denied:=false;
  begin perform public.lili_mark_discipline_report_read(owner,current_date); exception when others then denied:=true; end;
  if not denied then raise exception 'Read receipt bypassed off'; end if;
  denied:=false;
  begin perform public.lili_respond_discipline_supervisor(gen_random_uuid(),true); exception when others then denied:=true; end;
  if not denied then raise exception 'Old approval RPC reopened consent'; end if;
  denied:=false;
  begin perform public.lili_supervision_nudge(owner,'start'); exception when others then denied:=true; end;
  if not denied then raise exception 'Nudge bypassed off'; end if;
  perform set_config('request.jwt.claim.sub',owner::text,true);
  payload:=public.lili_supervision_snapshot(); p:=payload->'policy';
  payload:=public.lili_set_supervision_policy(p||'{"enabled":true,"scope":"invited","officer_ids":[]}',(p->>'revision')::bigint);
  perform public.lili_invite_supervisor(b,true);
  perform set_config('request.jwt.claim.sub',b::text,true);
  if not (public.lili_buddy_study_overview(owner)->'peer_permission'->>'eligible')::boolean then raise exception 'Invite did not open eligibility'; end if;
  perform set_config('request.jwt.claim.sub',a::text,true);
  if (public.lili_buddy_study_overview(owner)->'peer_permission'->>'eligible')::boolean then raise exception 'Invited scope leaked'; end if;
  perform set_config('request.jwt.claim.sub',outsider::text,true);
  denied:=false;
  begin perform public.lili_buddy_study_overview(owner); exception when others then denied:=true; end;
  if not denied then raise exception 'Non-buddy allowed'; end if;
  perform set_config('request.jwt.claim.sub',owner::text,true);
  if (select count(*) from public.lili_discipline_events where user_id=owner)<>1
    or (select count(*) from public.lili_supervision_sessions where owner_id=owner)<>2 then raise exception 'History removed'; end if;
  perform public.lili_invite_supervisor(b,false);
  perform set_config('request.jwt.claim.sub',b::text,true);
  if (public.lili_buddy_study_overview(owner)->'peer_permission'->>'eligible')::boolean then raise exception 'Invite removal failed'; end if;
  if has_function_privilege('anon','public.lili_supervision_permission(uuid,uuid)','EXECUTE')
    or has_function_privilege('authenticated','public.lili_supervision_permission(uuid,uuid)','EXECUTE')
    or has_table_privilege('authenticated','public.lili_supervision_policy','SELECT') then raise exception 'Private policy exposed'; end if;
end;
$$;
rollback;
