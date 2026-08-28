-- Presence is a live projection, not a durable history table.  A stale
-- heartbeat from a previous Beijing day must never be allowed to resurrect
-- that day's focus time in today's buddy card.

create or replace function public.lili_effective_focus_today_seconds(p_user_id uuid)
returns integer
language sql
stable
security definer
set search_path = ''
as $$
  select greatest(
    case
      when p.focus_today_date = (now() at time zone 'Asia/Shanghai')::date
        then greatest(0, least(86400, coalesce(p.focus_today_seconds, 0)))
      else 0
    end,
    case
      when f.focus_date = (now() at time zone 'Asia/Shanghai')::date
       and f.last_seen > now() - interval '2 minutes'
        then greatest(0, least(86400, coalesce(f.today_seconds, 0)))
      else 0
    end
  )::integer
  from public.lili_profiles p
  left join public.lili_focus_presence f on f.user_id = p.user_id
  where p.user_id = p_user_id;
$$;

revoke execute on function public.lili_effective_focus_today_seconds(uuid)
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
begin
  for item in
    select value
    from jsonb_array_elements(coalesce(p_people, '[]'::jsonb))
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
      select p.show_exact_time and p.visibility = 'friends'
        into can_show_exact_time
        from public.lili_profiles p
        where p.user_id = person_id;

      if found then
        item := jsonb_set(
          item,
          '{today_seconds}',
          case
            when can_show_exact_time
              then to_jsonb(public.lili_effective_focus_today_seconds(person_id))
            else 'null'::jsonb
          end,
          true
        );
      end if;
    end if;

    result := result || jsonb_build_array(item);
  end loop;
  return result;
end;
$$;

revoke execute on function public.lili_normalize_focus_today_people(jsonb)
  from public, anon, authenticated;

-- 20260828000100 owns the public dashboard wrapper.  Normalize every
-- projection returned by that wrapper, including active visits and the local
-- member projection, without duplicating the large dashboard query.
create or replace function public.lili_dashboard() returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  payload jsonb;
  me_id uuid := (select auth.uid());
begin
  if me_id is null then
    raise exception '需要登录';
  end if;

  payload := public.lili_dashboard_presence_base_20260828();
  -- Keep the never-seen protection from 20260828000100 before applying the
  -- date/freshness correction.  A profile row alone is not proof that this
  -- account has ever sent a presence heartbeat.
  payload := jsonb_set(
    payload,
    '{buddies}',
    public.lili_zero_never_seen_presence(coalesce(payload -> 'buddies', '[]'::jsonb)),
    true
  );
  payload := jsonb_set(
    payload,
    '{room_people}',
    public.lili_zero_never_seen_presence(coalesce(payload -> 'room_people', '[]'::jsonb)),
    true
  );
  payload := jsonb_set(
    payload,
    '{active_visits}',
    public.lili_zero_never_seen_presence(coalesce(payload -> 'active_visits', '[]'::jsonb)),
    true
  );
  if jsonb_typeof(payload -> 'current_room') = 'object' then
    payload := jsonb_set(
      payload,
      '{current_room,room_people}',
      public.lili_zero_never_seen_presence(
        coalesce(payload -> 'current_room' -> 'room_people', '[]'::jsonb)
      ),
      true
    );
  end if;
  payload := jsonb_set(
    payload,
    '{buddies}',
    public.lili_normalize_focus_today_people(coalesce(payload -> 'buddies', '[]'::jsonb)),
    true
  );
  payload := jsonb_set(
    payload,
    '{room_people}',
    public.lili_normalize_focus_today_people(coalesce(payload -> 'room_people', '[]'::jsonb)),
    true
  );
  payload := jsonb_set(
    payload,
    '{active_visits}',
    public.lili_normalize_focus_today_people(coalesce(payload -> 'active_visits', '[]'::jsonb)),
    true
  );

  if jsonb_typeof(payload -> 'current_room') = 'object' then
    payload := jsonb_set(
      payload,
      '{current_room,room_people}',
      public.lili_normalize_focus_today_people(
        coalesce(payload -> 'current_room' -> 'room_people', '[]'::jsonb)
      ),
      true
    );
  end if;

  if jsonb_typeof(payload -> 'me_presence') = 'object' then
    payload := jsonb_set(
      payload,
      '{me_presence,today_seconds}',
      to_jsonb(public.lili_effective_focus_today_seconds(me_id)),
      true
    );
  end if;

  return payload;
end;
$$;

revoke execute on function public.lili_dashboard() from public, anon;
grant execute on function public.lili_dashboard() to authenticated;

-- The room endpoint is merged into the dashboard after lili_dashboard() has
-- returned, so it needs the same boundary.  Preserve its existing room
-- summary/goal/activity implementation behind a private base function.
do $$
begin
  if to_regprocedure('public.lili_room_dashboard(uuid)') is not null
     and to_regprocedure('public.lili_room_dashboard_presence_base_20260828(uuid)') is null then
    execute 'alter function public.lili_room_dashboard(uuid) rename to lili_room_dashboard_presence_base_20260828';
  end if;
end;
$$;

revoke execute on function public.lili_room_dashboard_presence_base_20260828(uuid)
  from public, anon, authenticated;

create or replace function public.lili_room_dashboard(p_room_id uuid) returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  payload jsonb;
begin
  payload := public.lili_room_dashboard_presence_base_20260828(p_room_id);
  if jsonb_typeof(payload -> 'current_room') = 'object' then
    payload := jsonb_set(
      payload,
      '{current_room,room_people}',
      public.lili_normalize_focus_today_people(
        coalesce(payload -> 'current_room' -> 'room_people', '[]'::jsonb)
      ),
      true
    );
  end if;
  return payload;
end;
$$;

revoke execute on function public.lili_room_dashboard(uuid) from public, anon;
grant execute on function public.lili_room_dashboard(uuid) to authenticated;
