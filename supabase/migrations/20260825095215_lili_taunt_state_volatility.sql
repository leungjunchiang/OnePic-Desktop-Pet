-- The state RPC reconciles the first working heartbeat, so it must remain
-- VOLATILE (the default for PL/pgSQL functions) rather than STABLE.
create or replace function public.lili_taunt_state()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  me uuid := (select auth.uid());
  result jsonb;
begin
  if me is null then raise exception '请先登录'; end if;
  update public.lili_buddy_taunts t
  set started_working_at = coalesce(t.started_working_at, now())
  where t.receiver_id = me and t.started_working_at is null
    and exists (
      select 1 from public.lili_focus_presence f
      where f.user_id = me and f.working
        and f.last_seen > now() - interval '2 minutes'
    );
  select jsonb_build_object(
    'active', true, 'id', t.id, 'sender_id', t.sender_id,
    'sender_nickname', public.lili_owner_nickname(t.sender_id),
    'created_at', t.created_at, 'started_working_at', t.started_working_at,
    'punishment_until', t.started_working_at + interval '20 minutes'
  ) into result
  from public.lili_buddy_taunts t
  where t.receiver_id = me
    and (t.started_working_at is null or t.started_working_at + interval '20 minutes' > now())
  order by t.created_at desc limit 1;
  return coalesce(result, jsonb_build_object('active', false));
end;
$$;
revoke execute on function public.lili_taunt_state() from public, anon;
grant execute on function public.lili_taunt_state() to authenticated;
