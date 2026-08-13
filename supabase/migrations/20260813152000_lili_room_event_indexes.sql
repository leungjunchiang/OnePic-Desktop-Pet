create index if not exists lili_room_events_actor_idx on public.lili_room_events(actor_id);
create index if not exists lili_room_events_target_idx on public.lili_room_events(target_id);
create index if not exists lili_room_goals_created_by_idx on public.lili_room_goals(created_by);

