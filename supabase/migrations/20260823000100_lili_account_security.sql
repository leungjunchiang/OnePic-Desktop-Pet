-- Account deletion is intentionally a narrow, authenticated RPC. The client
-- never receives a service_role key and cannot choose another user's id.
create or replace function public.lili_delete_my_account()
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if (select auth.uid()) is null then
    raise exception '需要登录';
  end if;

  delete from auth.users
  where id = (select auth.uid());
end;
$$;

revoke all on function public.lili_delete_my_account() from public, anon, authenticated;
grant execute on function public.lili_delete_my_account() to authenticated;
