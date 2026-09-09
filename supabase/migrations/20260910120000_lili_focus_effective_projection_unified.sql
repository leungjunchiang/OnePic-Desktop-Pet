-- Every peer surface consumes the same effective calendar totals.  Keep the
-- public source marker compatible with older clients so they know these
-- values already include server-side live projection and must not be added
-- again.  The separate marker retains the migration detail for new clients.

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
        'focus_totals_source', 'canonical_interval_union',
        'focus_totals_effective_source', 'canonical_interval_union_legacy_floor'
      )
    );
  end loop;
  return result;
end;
$$;

revoke execute on function public.lili_mark_canonical_focus_totals(jsonb)
  from public, anon, authenticated;

create or replace function public.lili_room_dashboard_social_pet_names_base(p_room_id uuid)
returns jsonb
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
      public.lili_mark_canonical_focus_totals(
        public.lili_normalize_focus_today_people(
          coalesce(payload -> 'current_room' -> 'room_people', '[]'::jsonb)
        )
      ),
      true
    );
  end if;
  payload := jsonb_set(
    payload,
    '{focus_totals_source}',
    to_jsonb('canonical_interval_union'::text),
    true
  );
  payload := jsonb_set(
    payload,
    '{focus_totals_effective_source}',
    to_jsonb('canonical_interval_union_legacy_floor'::text),
    true
  );
  return payload;
end;
$$;

revoke execute on function public.lili_room_dashboard_social_pet_names_base(uuid)
  from public, anon, authenticated;

comment on function public.lili_mark_canonical_focus_totals(jsonb) is
  'Marks server-owned effective day/week totals. Canonical marker remains backward-compatible; detail marker records frozen-floor compatibility.';
