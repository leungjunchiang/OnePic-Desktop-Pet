-- Reassert canonical FocusSession totals after the multi-device dashboard
-- compatibility wrappers.  Those wrappers preserve the legacy presence shape,
-- but a partial rollout can otherwise expose the old profile/presence counter
-- to buddy cards while reports already use the interval union.
--
-- This migration only changes SECURITY DEFINER function bodies and response
-- metadata.  It does not accept client-supplied duration values and does not
-- modify profiles, relationships, presence rows, or sealed focus facts.

create or replace function public.lili_mark_canonical_focus_totals(p_people jsonb)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  item jsonb;
  result jsonb := '[]'::jsonb;
begin
  if jsonb_typeof(coalesce(p_people, '[]'::jsonb)) <> 'array' then
    return result;
  end if;
  for item in
    select value from jsonb_array_elements(coalesce(p_people, '[]'::jsonb))
  loop
    result := result || jsonb_build_array(
      item || jsonb_build_object(
        'focus_totals_source', 'canonical_interval_union'
      )
    );
  end loop;
  return result;
end;
$$;

revoke execute on function public.lili_mark_canonical_focus_totals(jsonb)
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
begin
  if me_id is null then
    raise exception 'authentication required';
  end if;

  payload := public.lili_dashboard_multidevice_base_20260830();

  -- First clear never-seen rows, then replace their visible day/week values
  -- with the same account-level interval union used by reports and rankings.
  payload := jsonb_set(
    payload,
    '{buddies}',
    public.lili_mark_canonical_focus_totals(
      public.lili_normalize_focus_today_people(
        public.lili_zero_never_seen_presence(
          coalesce(payload -> 'buddies', '[]'::jsonb)
        )
      )
    ),
    true
  );
  payload := jsonb_set(
    payload,
    '{room_people}',
    public.lili_mark_canonical_focus_totals(
      public.lili_normalize_focus_today_people(
        public.lili_zero_never_seen_presence(
          coalesce(payload -> 'room_people', '[]'::jsonb)
        )
      )
    ),
    true
  );
  payload := jsonb_set(
    payload,
    '{active_visits}',
    public.lili_mark_canonical_focus_totals(
      public.lili_normalize_focus_today_people(
        public.lili_zero_never_seen_presence(
          coalesce(payload -> 'active_visits', '[]'::jsonb)
        )
      )
    ),
    true
  );
  if jsonb_typeof(payload -> 'current_room') = 'object' then
    payload := jsonb_set(
      payload,
      '{current_room,room_people}',
      public.lili_mark_canonical_focus_totals(
        public.lili_normalize_focus_today_people(
          public.lili_zero_never_seen_presence(
            coalesce(payload -> 'current_room' -> 'room_people', '[]'::jsonb)
          )
        )
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
    payload := jsonb_set(
      payload,
      '{me_presence,week_seconds}',
      to_jsonb(public.lili_effective_focus_week_seconds(me_id)),
      true
    );
    payload := jsonb_set(
      payload,
      '{me_presence,focus_totals_source}',
      to_jsonb('canonical_interval_union'::text),
      true
    );
  end if;
  if jsonb_typeof(payload -> 'me') = 'object' then
    payload := jsonb_set(
      payload,
      '{me,focus_today_seconds}',
      to_jsonb(public.lili_effective_focus_today_seconds(me_id)),
      true
    );
    payload := jsonb_set(
      payload,
      '{me,focus_week_seconds}',
      to_jsonb(public.lili_effective_focus_week_seconds(me_id)),
      true
    );
    payload := jsonb_set(
      payload,
      '{me,focus_totals_source}',
      to_jsonb('canonical_interval_union'::text),
      true
    );
  end if;
  payload := jsonb_set(
    payload,
    '{focus_totals_source}',
    to_jsonb('canonical_interval_union'::text),
    true
  );
  return payload;
end;
$$;

revoke execute on function public.lili_dashboard() from public, anon;
grant execute on function public.lili_dashboard() to authenticated;

comment on function public.lili_dashboard() is
  'Dashboard peer and personal focus totals come from the canonical account-level FocusSession interval union; compatibility fields never overwrite them.';
