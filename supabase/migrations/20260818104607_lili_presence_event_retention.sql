-- Presence is current state; room events are durable activity.
-- Keep the focus-session ledger and only remove transient Presence history.

drop trigger if exists lili_presence_room_event on public.lili_focus_presence;

-- Kept for compatibility with earlier migration history. The trigger is
-- intentionally detached above; Presence must not append room history.
create or replace function public.lili_log_presence_event() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  return new;
end;
$$;

revoke execute on function public.lili_log_presence_event() from public, anon, authenticated;

-- Remove only transient Presence-derived history. Keep completed focus rounds,
-- room goals/challenges/schedules, buddy interactions and other durable events.
delete from public.lili_room_events
where kind in ('join','leave','focus_start','focus_pause');

alter table public.lili_room_events drop constraint if exists lili_room_events_kind_check;
alter table public.lili_room_events add constraint lili_room_events_kind_check check (
  kind in (
    'focus_finish','poke','cheer','drink','phrase','goal_set',
    'challenge_set','challenge_complete','schedule_set',
    'schedule_start','schedule_end'
  )
);

create index if not exists lili_room_events_durable_room_time_idx
  on public.lili_room_events(room_id, created_at desc)
  where kind not in ('join','leave','focus_start','focus_pause');