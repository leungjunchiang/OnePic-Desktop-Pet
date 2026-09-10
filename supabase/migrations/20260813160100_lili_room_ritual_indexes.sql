-- Cover the foreign keys used by room rituals and subscriptions.
create index if not exists lili_room_schedules_created_by_idx
  on public.lili_room_schedules(created_by);
create index if not exists lili_room_challenges_created_by_idx
  on public.lili_room_challenges(created_by);
create index if not exists lili_buddy_subscriptions_buddy_idx
  on public.lili_buddy_subscriptions(buddy_id);
