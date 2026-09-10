-- The segment table already has owner-scoped INSERT/SELECT/UPDATE RLS
-- policies.  Keep the new delta endpoint under those policies instead of
-- exposing another SECURITY DEFINER function to authenticated users.

alter function public.lili_sync_focus_segments_delta(jsonb, timestamptz)
  security invoker;
