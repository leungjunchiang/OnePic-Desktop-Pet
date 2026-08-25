-- Index added after the production migration apply.
create index if not exists lili_buddy_taunts_sender_idx
  on public.lili_buddy_taunts(sender_id);
