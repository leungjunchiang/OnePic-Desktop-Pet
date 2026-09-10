-- lili_owner_nickname is only a helper for SECURITY DEFINER dashboard/RPC
-- functions; it is not a public client RPC.
revoke execute on function public.lili_owner_nickname(uuid) from public, anon, authenticated;
