-- Make the intentional direct-table deny explicit for RLS/Advisor tooling.
-- Clients use the authenticated Heartbeat RPC; the device table is not a
-- Data API read/write surface.

drop policy if exists "lili_focus_device_presence_no_direct_access"
  on public.lili_focus_device_presence;
create policy "lili_focus_device_presence_no_direct_access"
  on public.lili_focus_device_presence
  for all
  to authenticated
  using (false)
  with check (false);
