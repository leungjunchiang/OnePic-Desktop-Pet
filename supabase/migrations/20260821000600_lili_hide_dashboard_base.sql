-- Keep the renamed dashboard implementation private; only the wrapper is an API.
revoke execute on function public.lili_dashboard_base() from public, anon, authenticated;
