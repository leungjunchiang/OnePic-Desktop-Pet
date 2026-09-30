-- Migrate only synthetic legacy rows, then roll back fixtures and DDL replay.
begin;
create temporary table supervision_upgrade_fixture(owner_id uuid,peer_id uuid,officer boolean);
do $$
declare owner uuid; peer uuid; request uuid; officer boolean;
begin
  for officer in select unnest(array[false,true]) loop
    owner:=gen_random_uuid(); peer:=gen_random_uuid(); request:=gen_random_uuid();
    insert into auth.users(id,raw_user_meta_data) values(owner,'{}'),(peer,'{}');
    insert into public.lili_buddy_links(requester_id,addressee_id,status) values(owner,peer,'accepted');
    insert into public.lili_discipline_settings(user_id,settings) values(owner,jsonb_build_object('mode',case when officer then 'officer' else 'off' end));
    insert into public.lili_discipline_supervisor_requests(id,owner_id,supervisor_id,status) values(request,owner,peer,'accepted');
    insert into public.lili_discipline_supervisor_access(owner_id,supervisor_id,request_id,view_plan,view_reports,view_lateness)
      values(owner,peer,request,false,true,false);
    insert into supervision_upgrade_fixture values(owner,peer,officer);
  end loop;
end;
$$;
-- REPLAY_MIGRATION_HERE
do $$
declare f record; p public.lili_supervision_policy; s public.lili_supervision_sessions;
begin
  for f in select * from supervision_upgrade_fixture loop
    select * into p from public.lili_supervision_policy where owner_id=f.owner_id;
    select * into s from public.lili_supervision_sessions where owner_id=f.owner_id and supervisor_id=f.peer_id;
    if not p.enabled or p.scope<>'selected' or p.selected_ids<>array[f.peer_id]
      or p.view_plan or not p.view_reports or p.view_lateness
      or (f.officer and p.officer_ids<>array[f.peer_id]) or (not f.officer and cardinality(p.officer_ids)<>0)
      or s.active<>f.officer then raise exception 'Legacy consent was broadened or lost'; end if;
    perform set_config('request.jwt.claim.sub',f.owner_id::text,true);
    perform public.lili_revoke_discipline_supervisor();
  end loop;
end;
$$;
-- REPLAY_MIGRATION_HERE
do $$
begin
  if exists(select 1 from public.lili_supervision_policy p join supervision_upgrade_fixture f on f.owner_id=p.owner_id
    where p.enabled) then raise exception 'Migration replay revived disabled consent'; end if;
end;
$$;
rollback;
