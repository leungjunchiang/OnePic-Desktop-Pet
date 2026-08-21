-- 隐身仍保留在搭子列表，但对其他人只展示为离线。
-- 同步同一账号在不同电脑上的本周专注时间，并提供专注排行榜。

alter table public.lili_profiles
  add column if not exists focus_week_start_date date not null default date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date;

alter table public.lili_profiles
  add column if not exists focus_week_seconds integer not null default 0;

alter table public.lili_profiles
  drop constraint if exists lili_profiles_focus_week_seconds_check;
alter table public.lili_profiles
  add constraint lili_profiles_focus_week_seconds_check
  check (focus_week_seconds between 0 and 604800);

create or replace function public.lili_sync_personal_state(
  p_focus_date date default (now() at time zone 'Asia/Shanghai')::date,
  p_today_seconds integer default 0,
  p_lifetime_seconds bigint default 0,
  p_outfit_key text default null,
  p_outfit_set boolean default false,
  p_week_start date default date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date,
  p_week_seconds integer default 0
) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  target_date date := coalesce(p_focus_date, (now() at time zone 'Asia/Shanghai')::date);
  target_week date := coalesce(p_week_start, date_trunc('week', target_date)::date);
  merged_today integer;
  merged_lifetime bigint;
  merged_week integer;
  merged_outfit text;
begin
  update public.lili_profiles p
  set focus_today_seconds = case
        when target_date > p.focus_today_date
          then greatest(0, least(86400, coalesce(p_today_seconds, 0)))
        when target_date = p.focus_today_date
          then greatest(p.focus_today_seconds, greatest(0, least(86400, coalesce(p_today_seconds, 0))))
        else p.focus_today_seconds
      end,
      focus_today_date = greatest(p.focus_today_date, target_date),
      focus_lifetime_seconds = greatest(p.focus_lifetime_seconds, greatest(0, coalesce(p_lifetime_seconds, 0))),
      focus_week_seconds = case
        when target_week > p.focus_week_start_date
          then greatest(0, least(604800, coalesce(p_week_seconds, 0)))
        when target_week = p.focus_week_start_date
          then greatest(p.focus_week_seconds, greatest(0, least(604800, coalesce(p_week_seconds, 0))))
        else p.focus_week_seconds
      end,
      focus_week_start_date = greatest(p.focus_week_start_date, target_week),
      outfit_key = case
        when coalesce(p_outfit_set, false)
          then left(btrim(coalesce(p_outfit_key, '')), 60)
        else p.outfit_key
      end,
      updated_at = now()
  where p.user_id = (select auth.uid())
  returning p.focus_today_seconds, p.focus_lifetime_seconds,
            p.focus_week_seconds, p.outfit_key
    into merged_today, merged_lifetime, merged_week, merged_outfit;

  if not found then
    raise exception '搭子资料不存在';
  end if;

  return jsonb_build_object(
    'focus_today_date', target_date,
    'focus_today_seconds', merged_today,
    'focus_lifetime_seconds', merged_lifetime,
    'focus_week_start_date', target_week,
    'focus_week_seconds', merged_week,
    'outfit_key', merged_outfit
  );
end;
$$;

revoke execute on function public.lili_sync_personal_state(date, integer, bigint, text, boolean, date, integer) from public, anon;
grant execute on function public.lili_sync_personal_state(date, integer, bigint, text, boolean, date, integer) to authenticated;

create or replace function public.lili_focus_weekly_leaderboard(p_period text default 'week') returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(jsonb_agg(
    jsonb_build_object(
      'user_id', p.user_id,
      'nickname', public.lili_owner_nickname(p.user_id),
      'week_start', p.focus_week_start_date,
      'week_seconds', case
        when p.focus_week_start_date = date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date
          then greatest(0, p.focus_week_seconds)
        else 0
      end
    )
    order by
      case when p.focus_week_start_date = date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date
        then greatest(0, p.focus_week_seconds) else 0 end desc,
      public.lili_owner_nickname(p.user_id)
  ), '[]'::jsonb)
  from public.lili_profiles p
  where (p.wealth_leaderboard_enabled or not p.wealth_leaderboard_preference_set)
    and (p.user_id = (select auth.uid()) or public.lili_are_buddies((select auth.uid()), p.user_id));
$$;

revoke execute on function public.lili_focus_weekly_leaderboard(text) from public, anon;
grant execute on function public.lili_focus_weekly_leaderboard(text) to authenticated;

create or replace function public.lili_dashboard() returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select jsonb_build_object(
    'me', (select to_jsonb(p) from public.lili_profiles p where p.user_id = (select auth.uid())),
    'me_presence', coalesce((
      select jsonb_build_object(
        'user_id', f.user_id,
        'working', case when f.working and f.last_seen > now() - interval '2 minutes' then true else false end,
        'status', case
          when f.last_seen is null or f.last_seen <= now() - interval '2 minutes' then 'offline'
          when f.working then 'focus' else 'rest' end,
        'session_started_at', f.session_started_at,
        'session_seconds', case
          when f.working and f.session_started_at is not null and f.last_seen > now() - interval '2 minutes'
            then greatest(0, floor(extract(epoch from (now() - f.session_started_at)))::int)
          else 0 end,
        'today_seconds', greatest(coalesce(p.focus_today_seconds, 0), coalesce(f.today_seconds, 0)),
        'week_seconds', case
          when p.focus_week_start_date = date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date
            then coalesce(p.focus_week_seconds, 0) else 0 end,
        'outfit_key', coalesce(f.outfit_key, p.outfit_key, ''),
        'room_id', f.room_id,
        'online', coalesce(f.last_seen > now() - interval '2 minutes', false),
        'last_seen_at', f.last_seen,
        'status_updated_at', f.updated_at,
        'server_timestamp', now()
      )
      from public.lili_focus_presence f
      join public.lili_profiles p on p.user_id = f.user_id
      where f.user_id = (select auth.uid())
    ), jsonb_build_object(
      'user_id', (select auth.uid()), 'working', false, 'status', 'idle',
      'session_seconds', 0, 'today_seconds', 0, 'week_seconds', 0,
      'online', false, 'server_timestamp', now()
    )),
    'requests', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', l.id,
        'nickname', public.lili_owner_nickname(l.requester_id),
        'owner_nickname', public.lili_owner_nickname(l.requester_id),
        'created_at', l.created_at
      ))
      from public.lili_buddy_links l
      where l.addressee_id = (select auth.uid()) and l.status = 'pending'
    ), '[]'::jsonb),
    'buddies', coalesce((
      select jsonb_agg(jsonb_build_object(
        'user_id', p.user_id,
        'nickname', public.lili_owner_nickname(p.user_id),
        'owner_nickname', public.lili_owner_nickname(p.user_id),
        'outfit_key', coalesce(f.outfit_key, p.outfit_key),
        'working', case when p.visibility = 'friends' and f.working
          and f.last_seen > now() - interval '2 minutes' then true else false end,
        'status', case
          when p.visibility <> 'friends' then 'offline'
          when f.last_seen is null or f.last_seen <= now() - interval '2 minutes' then 'offline'
          when f.working then 'focus' else 'rest' end,
        'session_started_at', case when p.visibility = 'friends' then f.session_started_at else null end,
        'session_seconds', case
          when p.visibility = 'friends' and f.working and f.session_started_at is not null
            and f.last_seen > now() - interval '2 minutes'
            then greatest(0, floor(extract(epoch from (now() - f.session_started_at)))::int)
          else 0 end,
        'today_seconds', case
          when p.visibility = 'friends' and p.show_exact_time then greatest(coalesce(p.focus_today_seconds, 0), coalesce(f.today_seconds, 0))
          else null end,
        'week_seconds', case
          when p.visibility = 'friends' and p.show_exact_time
            and p.focus_week_start_date = date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date
            then coalesce(p.focus_week_seconds, 0)
          else null end,
        'online', case when p.visibility = 'friends'
          then coalesce(f.last_seen > now() - interval '2 minutes', false) else false end,
        'last_seen_at', case when p.visibility = 'friends' then f.last_seen else null end,
        'status_updated_at', case when p.visibility = 'friends' then f.updated_at else null end,
        'server_timestamp', now(),
        'is_self', false,
        'subscribed', coalesce((
          select s.on_focus_start or s.on_focus_end
          from public.lili_buddy_subscriptions s
          where s.subscriber_id = (select auth.uid())
            and s.buddy_id = p.user_id and not s.muted
        ), false)
      ) order by public.lili_owner_nickname(p.user_id))
      from public.lili_profiles p
      left join public.lili_focus_presence f on f.user_id = p.user_id
      where public.lili_are_buddies((select auth.uid()), p.user_id)
    ), '[]'::jsonb),
    'rooms', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', r.id, 'name', r.name, 'invite_code', r.invite_code,
        'members', (select count(*) from public.lili_room_members m2 where m2.room_id = r.id)
      ))
      from public.lili_study_rooms r
      join public.lili_room_members m on m.room_id = r.id
      where m.user_id = (select auth.uid())
    ), '[]'::jsonb),
    'room_people', coalesce((
      select jsonb_agg(distinct jsonb_build_object(
        'user_id', p.user_id,
        'nickname', public.lili_owner_nickname(p.user_id),
        'owner_nickname', public.lili_owner_nickname(p.user_id),
        'outfit_key', coalesce(f.outfit_key, p.outfit_key),
        'working', case when p.visibility = 'friends' and f.working
          and f.last_seen > now() - interval '2 minutes' then true else false end,
        'status', case
          when p.visibility <> 'friends' then 'offline'
          when f.last_seen is null or f.last_seen <= now() - interval '2 minutes' then 'offline'
          when f.working then 'focus' else 'rest' end,
        'session_started_at', case when p.visibility = 'friends' then f.session_started_at else null end,
        'session_seconds', case
          when p.visibility = 'friends' and f.working and f.session_started_at is not null
            and f.last_seen > now() - interval '2 minutes'
            then greatest(0, floor(extract(epoch from (now() - f.session_started_at)))::int)
          else 0 end,
        'today_seconds', case when p.visibility = 'friends' and p.show_exact_time
          then greatest(coalesce(p.focus_today_seconds, 0), coalesce(f.today_seconds, 0)) else null end,
        'week_seconds', case when p.visibility = 'friends' and p.show_exact_time
          and p.focus_week_start_date = date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date
          then coalesce(p.focus_week_seconds, 0) else null end,
        'online', case when p.visibility = 'friends'
          then coalesce(f.last_seen > now() - interval '2 minutes', false) else false end,
        'is_self', p.user_id = (select auth.uid())
      ))
      from public.lili_room_members mine
      join public.lili_room_members other on other.room_id = mine.room_id
      join public.lili_profiles p on p.user_id = other.user_id
      left join public.lili_focus_presence f on f.user_id = p.user_id
      where mine.user_id = (select auth.uid()) and other.user_id <> (select auth.uid())
    ), '[]'::jsonb),
    'visits', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', v.id, 'sender_id', v.sender_id,
        'nickname', public.lili_owner_nickname(v.sender_id),
        'owner_nickname', public.lili_owner_nickname(v.sender_id),
        'kind', v.kind, 'payload', coalesce(v.payload, '{}'::jsonb),
        'created_at', v.created_at
      ))
      from public.lili_visit_events v
      where v.receiver_id = (select auth.uid()) and v.status = 'pending' and v.expires_at > now()
    ), '[]'::jsonb),
    'active_visits', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', v.id, 'peer_id', p.user_id,
        'nickname', public.lili_owner_nickname(p.user_id),
        'owner_nickname', public.lili_owner_nickname(p.user_id),
        'outfit_key', coalesce(f.outfit_key, p.outfit_key),
        'working', case when p.visibility = 'friends' and f.working
          and f.last_seen > now() - interval '2 minutes' then true else false end,
        'status', case
          when p.visibility <> 'friends' then 'offline'
          when f.last_seen is null or f.last_seen <= now() - interval '2 minutes' then 'offline'
          when f.working then 'focus' else 'rest' end,
        'session_started_at', case when p.visibility = 'friends' then f.session_started_at else null end,
        'session_seconds', case when p.visibility = 'friends' and f.working
          and f.session_started_at is not null and f.last_seen > now() - interval '2 minutes'
          then greatest(0, floor(extract(epoch from (now() - f.session_started_at)))::int) else 0 end,
        'today_seconds', case when p.visibility = 'friends' and p.show_exact_time
          then greatest(coalesce(p.focus_today_seconds, 0), coalesce(f.today_seconds, 0)) else null end,
        'week_seconds', case when p.visibility = 'friends' and p.show_exact_time
          and p.focus_week_start_date = date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date
          then coalesce(p.focus_week_seconds, 0) else null end,
        'online', case when p.visibility = 'friends'
          then coalesce(f.last_seen > now() - interval '2 minutes', false) else false end,
        'last_seen_at', case when p.visibility = 'friends' then f.last_seen else null end,
        'server_timestamp', now(),
        'visit_started_at', coalesce(v.responded_at, v.created_at)
      ))
      from public.lili_visit_events v
      join public.lili_profiles p on p.user_id = case
        when (select auth.uid()) = v.sender_id then v.receiver_id else v.sender_id end
      left join public.lili_focus_presence f on f.user_id = p.user_id
      where (select auth.uid()) in (v.sender_id, v.receiver_id)
        and v.status = 'accepted' and v.responded_at > now() - interval '2 hours'
    ), '[]'::jsonb)
  );
$$;

revoke execute on function public.lili_dashboard() from public, anon;
grant execute on function public.lili_dashboard() to authenticated;
