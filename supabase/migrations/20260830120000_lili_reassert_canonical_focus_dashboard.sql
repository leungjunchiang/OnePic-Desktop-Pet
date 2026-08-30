-- Reassert the canonical FocusSession projection after the production
-- migration chain.  Some deployed projects have an older function body with
-- the same signature, which makes buddy cards fall back to profile/presence
-- counters for today while the report uses raw FocusSession intervals.
--
-- This migration changes function bodies only.  It does not touch profile,
-- relationship, room, presence, or FocusSession rows.  Heartbeat remains a
-- liveness tuple; no duration or accumulated counter is accepted here.

create or replace function public.lili_effective_focus_today_seconds(p_user_id uuid)
returns integer
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(
    (public.lili_effective_focus_stats(p_user_id)->>'today_seconds')::integer,
    0
  );
$$;

revoke execute on function public.lili_effective_focus_today_seconds(uuid)
  from public, anon, authenticated;

create or replace function public.lili_effective_focus_week_seconds(p_user_id uuid)
returns integer
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(
    (public.lili_effective_focus_stats(p_user_id)->>'week_seconds')::integer,
    0
  );
$$;

revoke execute on function public.lili_effective_focus_week_seconds(uuid)
  from public, anon, authenticated;

create or replace function public.lili_normalize_focus_today_people(p_people jsonb)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  item jsonb;
  result jsonb := '[]'::jsonb;
  raw_user_id text;
  person_id uuid;
  can_show_exact_time boolean;
  presence_sequence bigint;
  presence_updated_at timestamptz;
begin
  for item in
    select value from jsonb_array_elements(coalesce(p_people, '[]'::jsonb))
  loop
    raw_user_id := nullif(coalesce(item ->> 'user_id', item ->> 'peer_id'), '');
    person_id := null;
    if raw_user_id is not null then
      begin
        person_id := raw_user_id::uuid;
      exception when invalid_text_representation then
        person_id := null;
      end;
    end if;

    if person_id is not null then
      -- A peer without a presence row has never advertised liveness.  Do not
      -- resurrect old profile totals for that account.
      if coalesce(item ->> 'presence_never_seen', 'false') = 'true' then
        item := item || jsonb_build_object(
          'today_seconds', 0,
          'week_seconds', 0,
          'session_seconds', 0,
          'presence_never_seen', true
        );
      else
        select p.show_exact_time and p.visibility = 'friends'
          into can_show_exact_time
          from public.lili_profiles p
          where p.user_id = person_id;

        if found then
          item := jsonb_set(
            item,
            '{today_seconds}',
            case when can_show_exact_time
              then to_jsonb(public.lili_effective_focus_today_seconds(person_id))
              else 'null'::jsonb end,
            true
          );
          item := jsonb_set(
            item,
            '{week_seconds}',
            case when can_show_exact_time
              then to_jsonb(public.lili_effective_focus_week_seconds(person_id))
              else 'null'::jsonb end,
            true
          );
        end if;
      end if;

      select f.presence_sequence, f.updated_at
        into presence_sequence, presence_updated_at
        from public.lili_focus_presence f
        where f.user_id = person_id;
      if found then
        item := jsonb_set(item, '{sequence}', to_jsonb(coalesce(presence_sequence, 0)), true);
        item := jsonb_set(item, '{server_updated_at}', to_jsonb(presence_updated_at), true);
      end if;
    end if;
    result := result || jsonb_build_array(item);
  end loop;
  return result;
end;
$$;

revoke execute on function public.lili_normalize_focus_today_people(jsonb)
  from public, anon, authenticated;

create or replace function public.lili_dashboard()
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  payload jsonb;
  me_id uuid := (select auth.uid());
  me_presence public.lili_focus_presence;
begin
  if me_id is null then
    raise exception '需要登录';
  end if;

  payload := public.lili_dashboard_presence_base_20260828();
  payload := jsonb_set(payload, '{buddies}', public.lili_zero_never_seen_presence(coalesce(payload -> 'buddies', '[]'::jsonb)), true);
  payload := jsonb_set(payload, '{room_people}', public.lili_zero_never_seen_presence(coalesce(payload -> 'room_people', '[]'::jsonb)), true);
  payload := jsonb_set(payload, '{active_visits}', public.lili_zero_never_seen_presence(coalesce(payload -> 'active_visits', '[]'::jsonb)), true);
  if jsonb_typeof(payload -> 'current_room') = 'object' then
    payload := jsonb_set(payload, '{current_room,room_people}', public.lili_zero_never_seen_presence(coalesce(payload -> 'current_room' -> 'room_people', '[]'::jsonb)), true);
  end if;

  -- All visible peer totals use the same FocusSession projection as the
  -- report and leaderboard.  The base dashboard's profile/presence totals are
  -- compatibility inputs only and cannot overwrite this result.
  payload := jsonb_set(payload, '{buddies}', public.lili_normalize_focus_today_people(coalesce(payload -> 'buddies', '[]'::jsonb)), true);
  payload := jsonb_set(payload, '{room_people}', public.lili_normalize_focus_today_people(coalesce(payload -> 'room_people', '[]'::jsonb)), true);
  payload := jsonb_set(payload, '{active_visits}', public.lili_normalize_focus_today_people(coalesce(payload -> 'active_visits', '[]'::jsonb)), true);
  if jsonb_typeof(payload -> 'current_room') = 'object' then
    payload := jsonb_set(payload, '{current_room,room_people}', public.lili_normalize_focus_today_people(coalesce(payload -> 'current_room' -> 'room_people', '[]'::jsonb)), true);
  end if;

  if jsonb_typeof(payload -> 'me_presence') = 'object' then
    payload := jsonb_set(payload, '{me_presence,today_seconds}', to_jsonb(public.lili_effective_focus_today_seconds(me_id)), true);
    payload := jsonb_set(payload, '{me_presence,week_seconds}', to_jsonb(public.lili_effective_focus_week_seconds(me_id)), true);
  end if;
  if jsonb_typeof(payload -> 'me') = 'object' then
    payload := jsonb_set(payload, '{me,focus_today_date}', to_jsonb((now() at time zone 'Asia/Shanghai')::date), true);
    payload := jsonb_set(payload, '{me,focus_today_seconds}', to_jsonb(public.lili_effective_focus_today_seconds(me_id)), true);
    payload := jsonb_set(payload, '{me,focus_week_start_date}', to_jsonb(date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date), true);
    payload := jsonb_set(payload, '{me,focus_week_seconds}', to_jsonb(public.lili_effective_focus_week_seconds(me_id)), true);
  end if;

  select * into me_presence
  from public.lili_focus_presence f
  where f.user_id = me_id;
  if found and jsonb_typeof(payload -> 'me_presence') = 'object' then
    payload := jsonb_set(payload, '{me_presence,session_id}', to_jsonb(me_presence.session_id), true);
    payload := jsonb_set(payload, '{me_presence,sequence}', to_jsonb(coalesce(me_presence.presence_sequence, 0)), true);
    payload := jsonb_set(payload, '{me_presence,server_updated_at}', to_jsonb(me_presence.updated_at), true);
  end if;
  return payload;
end;
$$;

revoke execute on function public.lili_dashboard() from public, anon;
grant execute on function public.lili_dashboard() to authenticated;

