-- Keep cumulative focus time per room instead of deriving it from a user's
-- global daily counter (which would mix unrelated rooms).
create table if not exists public.lili_room_focus_totals (
  room_id uuid primary key references public.lili_study_rooms(id) on delete cascade,
  cumulative_seconds bigint not null default 0 check (cumulative_seconds >= 0),
  updated_at timestamptz not null default now()
);
alter table public.lili_room_focus_totals enable row level security;
drop policy if exists lili_room_focus_totals_read on public.lili_room_focus_totals;
create policy lili_room_focus_totals_read on public.lili_room_focus_totals for select to authenticated using (
  exists(select 1 from public.lili_room_members m
    where m.room_id=public.lili_room_focus_totals.room_id and m.user_id=auth.uid())
);

create or replace function public.lili_log_presence_event() returns trigger
language plpgsql security definer set search_path = '' as $$
declare elapsed bigint;
begin
  if tg_op = 'INSERT' and new.room_id is not null then
    insert into public.lili_room_events(room_id, actor_id, kind)
    values(new.room_id, new.user_id, 'join');
  elsif tg_op = 'UPDATE' then
    if old.room_id is not null and old.working and
       (new.room_id is distinct from old.room_id or not new.working) and
       old.session_started_at is not null then
      elapsed := greatest(0, floor(extract(epoch from (now()-old.session_started_at)))::bigint);
      insert into public.lili_room_focus_totals(room_id, cumulative_seconds, updated_at)
      values(old.room_id, elapsed, now())
      on conflict(room_id) do update set cumulative_seconds=public.lili_room_focus_totals.cumulative_seconds+excluded.cumulative_seconds,
        updated_at=now();
    end if;
    if old.room_id is distinct from new.room_id then
      if old.room_id is not null then
        insert into public.lili_room_events(room_id, actor_id, kind)
        values(old.room_id, new.user_id, 'leave');
      end if;
      if new.room_id is not null then
        insert into public.lili_room_events(room_id, actor_id, kind)
        values(new.room_id, new.user_id, 'join');
      end if;
    elsif new.room_id is not null and old.working is distinct from new.working then
      insert into public.lili_room_events(room_id, actor_id, kind)
      values(new.room_id, new.user_id, case when new.working then 'focus_start' else 'focus_pause' end);
    end if;
  end if;
  return new;
end;
$$;

create or replace function public.lili_room_dashboard(room_id uuid) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare result jsonb;
begin
  if not exists(select 1 from public.lili_room_members m where m.room_id=room_id and m.user_id=auth.uid()) then
    raise exception '你不在这个自习室里';
  end if;
  select jsonb_build_object(
    'id', r.id, 'name', r.name, 'invite_code', r.invite_code,
    'room_people', coalesce((select jsonb_agg(jsonb_build_object(
      'user_id',p.user_id,'nickname',p.nickname,'outfit_key',coalesce(f.outfit_key,p.outfit_key),
      'working',case when f.working and f.last_seen>now()-interval '2 minutes' then true else false end,
      'status',case when f.last_seen is null or f.last_seen<=now()-interval '2 minutes' then 'offline' when f.working then 'focus' else 'rest' end,
      'session_started_at',f.session_started_at,
      'session_seconds',case when f.working and f.session_started_at is not null then greatest(0,floor(extract(epoch from (now()-f.session_started_at)))::int) else 0 end,
      'today_seconds',case when p.show_exact_time then coalesce(f.today_seconds,0) else null end,
      'online',coalesce(f.last_seen>now()-interval '2 minutes',false), 'is_self',p.user_id=auth.uid()
    ) order by p.nickname) from public.lili_room_members m join public.lili_profiles p on p.user_id=m.user_id
      left join public.lili_focus_presence f on f.user_id=p.user_id where m.room_id=r.id and p.visibility='friends'),'[]'::jsonb),
    'room_summary', jsonb_build_object(
      'member_count',(select count(*) from public.lili_room_members m where m.room_id=r.id),
      'focus_count',(select count(*) from public.lili_room_members m join public.lili_focus_presence f on f.user_id=m.user_id
        where m.room_id=r.id and f.working and f.last_seen>now()-interval '2 minutes'),
      'shared_focus_seconds',coalesce((select t.cumulative_seconds from public.lili_room_focus_totals t where t.room_id=r.id),0)
        + coalesce((select sum(greatest(0,floor(extract(epoch from (now()-f.session_started_at)))::bigint))
          from public.lili_room_members m join public.lili_focus_presence f on f.user_id=m.user_id
          where m.room_id=r.id and f.working and f.session_started_at is not null and f.last_seen>now()-interval '2 minutes'),0)
    ),
    'room_goal', coalesce((select jsonb_build_object('title',g.title,'target_seconds',g.target_seconds,'due_at',g.due_at,
      'completed_seconds',coalesce((select t.cumulative_seconds from public.lili_room_focus_totals t where t.room_id=r.id),0)
        + coalesce((select sum(greatest(0,floor(extract(epoch from (now()-f.session_started_at)))::bigint))
          from public.lili_room_members m join public.lili_focus_presence f on f.user_id=m.user_id
          where m.room_id=r.id and f.working and f.session_started_at is not null and f.last_seen>now()-interval '2 minutes'),0))
      from public.lili_room_goals g where g.room_id=r.id),'{}'::jsonb),
    'room_activity', coalesce((select jsonb_agg(jsonb_build_object(
      'id',e.id,'kind',e.kind,'message',e.message,'created_at',e.created_at,
      'nickname',coalesce(ap.nickname,'搭子'),'target_nickname',tp.nickname
    ) order by e.created_at desc) from public.lili_room_events e join public.lili_profiles ap on ap.user_id=e.actor_id
      left join public.lili_profiles tp on tp.user_id=e.target_id where e.room_id=r.id),'[]'::jsonb)
  ) into result from public.lili_study_rooms r where r.id=room_id;
  return jsonb_build_object('current_room', result);
end;
$$;

revoke execute on function public.lili_room_dashboard(uuid) from public, anon;
grant execute on function public.lili_room_dashboard(uuid) to authenticated;

