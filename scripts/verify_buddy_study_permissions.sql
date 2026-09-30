-- Exercise real RPC authorization using synthetic accounts in a rolled-back
-- transaction. No existing account, plan, or report is selected or modified.
begin;
do $$
declare
  owner_id uuid := gen_random_uuid();
  supervisor_id uuid := gen_random_uuid();
  outsider_id uuid := gen_random_uuid();
  request_id uuid;
  payload jsonb;
  report_meta jsonb;
begin
  insert into auth.users(id, raw_user_meta_data) values
    (owner_id, '{"nickname":"授权验证"}'),
    (supervisor_id, '{"nickname":"监督验证"}'),
    (outsider_id, '{"nickname":"旁观验证"}');
  insert into public.lili_buddy_links(requester_id, addressee_id, status)
    values(owner_id, supervisor_id, 'accepted');
  insert into public.lili_discipline_settings(user_id, settings) values(owner_id,
    '{"weekly_target_minutes":2100,"daily_target_minutes":{"wed":330},"private_note":"MUST_NOT_EXPORT"}');
  insert into public.lili_discipline_events(user_id,id,event_type,event_date,occurred_at,metadata)
    values(owner_id,gen_random_uuid(),'daily_report',current_date,now(),
      '{"today_seconds":3600,"daily_target_seconds":19800,"lateness_minutes":37,"actual_start":"09:37","planned_start":"09:00","private_note":"MUST_NOT_EXPORT"}');
  perform set_config('request.jwt.claim.sub', owner_id::text, true);
  request_id := (public.lili_request_discipline_supervisor(supervisor_id)->>'request_id')::uuid;
  perform set_config('request.jwt.claim.sub', supervisor_id::text, true);
  payload := public.lili_buddy_study_overview(owner_id);
  if payload->'peer_plan' <> 'null'::jsonb or (payload->>'can_read_reports')::boolean then
    raise exception 'Pending consent exposed protected data';
  end if;
  perform public.lili_respond_discipline_supervisor(request_id, true);
  payload := public.lili_buddy_study_overview(owner_id);
  if (payload->'peer_plan'->>'weekly_target_minutes')::int <> 2100
    or payload::text like '%MUST_NOT_EXPORT%' then raise exception 'Plan allow-list failed'; end if;
  payload := public.lili_discipline_supervisor_report(owner_id);
  report_meta := payload->'reports'->0->'metadata';
  if (report_meta->>'lateness_minutes')::int <> 37 or payload::text like '%MUST_NOT_EXPORT%' then
    raise exception 'Report allow-list failed';
  end if;
  perform set_config('request.jwt.claim.sub', owner_id::text, true);
  perform public.lili_set_discipline_permissions(supervisor_id, false, true, false);
  perform set_config('request.jwt.claim.sub', supervisor_id::text, true);
  payload := public.lili_buddy_study_overview(owner_id);
  if payload->'peer_plan' <> 'null'::jsonb then raise exception 'Plan opt-out failed'; end if;
  report_meta := public.lili_discipline_supervisor_report(owner_id)->'reports'->0->'metadata';
  if report_meta ? 'lateness_minutes' or report_meta ? 'actual_start' or report_meta ? 'daily_target_seconds' then
    raise exception 'Report field opt-out failed';
  end if;
  perform set_config('request.jwt.claim.sub', owner_id::text, true);
  perform public.lili_set_discipline_permissions(supervisor_id, false, false, false);
  perform set_config('request.jwt.claim.sub', supervisor_id::text, true);
  begin
    perform public.lili_discipline_supervisor_report(owner_id);
    raise exception 'Report opt-out did not deny access';
  exception when others then
    if sqlerrm <> '你没有查看这位用户纪律摘要的授权' then raise; end if;
  end;
  perform set_config('request.jwt.claim.sub', outsider_id::text, true);
  begin
    perform public.lili_buddy_study_overview(owner_id);
    raise exception 'Non-buddy overview did not deny access';
  exception when others then
    if sqlerrm <> '只能查看已确认搭子的自习室' then raise; end if;
  end;
  perform set_config('request.jwt.claim.sub', owner_id::text, true);
  perform public.lili_revoke_discipline_supervisor();
  perform set_config('request.jwt.claim.sub', supervisor_id::text, true);
  payload := public.lili_buddy_study_overview(owner_id);
  if payload->'peer_plan' <> 'null'::jsonb or (payload->>'can_read_reports')::boolean then
    raise exception 'Consent revocation failed';
  end if;
end;
$$;
rollback;
