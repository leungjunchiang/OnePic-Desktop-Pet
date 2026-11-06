-- Exercise nickname edits and RLS with disposable accounts in one transaction.
begin;
do $$
declare
  owner_id uuid := gen_random_uuid();
  other_id uuid := gen_random_uuid();
  payload jsonb;
begin
  if has_function_privilege('anon', 'public.lili_focus_device_nicknames(uuid,text,text)', 'EXECUTE')
    or has_table_privilege('anon', 'public.lili_focus_device_nicknames', 'SELECT') then
    raise exception 'Device nicknames are available without an account';
  end if;

  insert into auth.users (id, raw_user_meta_data) values (owner_id, '{}'), (other_id, '{}');
  perform set_config('request.jwt.claim.sub', owner_id::text, true);
  execute 'set local role authenticated';

  perform public.lili_focus_device_nicknames(owner_id, 'device-1', '家里 Mac');
  payload := public.lili_focus_device_nicknames(owner_id);
  if payload->>'account_id' is distinct from owner_id::text
    or payload#>>'{nicknames,device-1}' is distinct from '家里 Mac' then
    raise exception 'Nickname is missing on a read by the same account';
  end if;

  perform set_config('request.jwt.claim.sub', other_id::text, true);
  payload := public.lili_focus_device_nicknames(other_id, 'device-1', '办公室');
  if payload->'nicknames' is distinct from '{"device-1":"办公室"}'::jsonb
    or exists (select 1 from public.lili_focus_device_nicknames where user_id = owner_id) then
    raise exception 'Device labels are not isolated between accounts';
  end if;

  begin
    perform public.lili_focus_device_nicknames(owner_id, 'device-1', '错误账号');
    raise exception 'A delayed edit accepted the wrong account';
  exception when insufficient_privilege then
    null;
  end;

  perform set_config('request.jwt.claim.sub', owner_id::text, true);
  payload := public.lili_focus_device_nicknames(owner_id, 'device-1', '');
  if payload->'nicknames' is distinct from '{}'::jsonb then
    raise exception 'Clearing the nickname did not restore the device-code fallback';
  end if;
  perform set_config('request.jwt.claim.sub', other_id::text, true);
  payload := public.lili_focus_device_nicknames(other_id);
  if payload#>>'{nicknames,device-1}' is distinct from '办公室' then
    raise exception 'Clearing a nickname affected a different account';
  end if;
end $$;
rollback;
