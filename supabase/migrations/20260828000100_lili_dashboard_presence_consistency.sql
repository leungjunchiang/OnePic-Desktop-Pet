-- A profile row can exist before the user has ever sent a presence
-- heartbeat.  Do not expose old profile totals as current social time for
-- that account.  Keep the large dashboard implementation intact and wrap it
-- with a small, account-safe normalisation layer.

create or replace function public.lili_zero_never_seen_presence(p_people jsonb)
returns jsonb
language sql
immutable
set search_path = ''
as $$
  select coalesce(jsonb_agg(
    case
      when coalesce(item ->> 'is_self', 'false') <> 'true'
       and item ? 'last_seen_at'
       and item -> 'last_seen_at' = 'null'::jsonb
        then item || jsonb_build_object(
          'today_seconds', 0,
          'week_seconds', 0,
          'session_seconds', 0,
          'presence_never_seen', true
        )
      else item
    end
    order by ordinal
  ), '[]'::jsonb)
  from jsonb_array_elements(coalesce(p_people, '[]'::jsonb))
    with ordinality as people(item, ordinal);
$$;

revoke execute on function public.lili_zero_never_seen_presence(jsonb)
  from public, anon, authenticated;

-- 20260822000100 owns the current public dashboard body.  Rename it to a
-- private base so this migration only changes the inconsistent presence
-- totals and cannot drift from the existing room/visit/cake projections.
do $$
begin
  if to_regprocedure('public.lili_dashboard()') is not null
     and to_regprocedure('public.lili_dashboard_presence_base_20260828()') is null then
    execute 'alter function public.lili_dashboard() rename to lili_dashboard_presence_base_20260828';
  end if;
end;
$$;
revoke execute on function public.lili_dashboard_presence_base_20260828()
  from public, anon, authenticated;

create or replace function public.lili_dashboard() returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  payload jsonb;
begin
  if (select auth.uid()) is null then
    raise exception '需要登录';
  end if;

  payload := public.lili_dashboard_presence_base_20260828();
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

  return payload;
end;
$$;

revoke execute on function public.lili_dashboard() from public, anon;
grant execute on function public.lili_dashboard() to authenticated;

