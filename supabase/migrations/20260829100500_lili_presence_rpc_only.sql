-- Presence ordering is enforced by the authenticated RPC.  Do not allow a
-- client to bypass the sequence fence with a direct PostgREST INSERT/UPDATE.
-- The security-definer RPCs and context RPC continue to write as the owner.

revoke insert, update, delete on table public.lili_focus_presence
  from public, anon, authenticated;

