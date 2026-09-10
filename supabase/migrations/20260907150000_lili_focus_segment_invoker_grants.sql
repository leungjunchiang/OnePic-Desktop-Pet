-- The composite-cursor delta RPC is SECURITY INVOKER so the caller's RLS
-- identity remains the security boundary.  The original table migration
-- revoked every table privilege from authenticated clients because the former
-- RPC was SECURITY DEFINER.  Grant only the operations now required by the
-- invoker function; owner-scoped RLS policies still restrict every row.

revoke all on table public.lili_focus_segments from anon;
revoke delete, truncate, references, trigger
on table public.lili_focus_segments from authenticated;
grant select, insert, update
on table public.lili_focus_segments to authenticated;

