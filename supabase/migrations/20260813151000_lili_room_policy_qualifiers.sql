-- Qualify room columns in the RLS predicates so the membership check cannot
-- accidentally collapse into the tautology ``room_id = room_id``.
drop policy if exists lili_room_goals_read on public.lili_room_goals;
create policy lili_room_goals_read on public.lili_room_goals for select to authenticated using (
  exists(select 1 from public.lili_room_members m
    where m.room_id=public.lili_room_goals.room_id and m.user_id=auth.uid())
);
drop policy if exists lili_room_events_read on public.lili_room_events;
create policy lili_room_events_read on public.lili_room_events for select to authenticated using (
  exists(select 1 from public.lili_room_members m
    where m.room_id=public.lili_room_events.room_id and m.user_id=auth.uid())
);
