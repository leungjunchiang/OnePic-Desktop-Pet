-- 允许已登录客户端执行自习室 RLS 使用的安全辅助函数。
-- 202608110003_lili_security_hardening.sql 曾将这些函数从
-- authenticated 一并撤销，导致 dashboard/heartbeat 在 PostgREST 中返回 403。
-- 函数仍保持 SECURITY DEFINER，且匿名角色不获得任何执行权限。
grant execute on function public.lili_are_buddies(uuid, uuid) to authenticated;
grant execute on function public.lili_share_room(uuid, uuid) to authenticated;

