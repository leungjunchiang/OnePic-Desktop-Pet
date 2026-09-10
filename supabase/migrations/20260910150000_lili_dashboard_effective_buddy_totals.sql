-- Replace compatibility totals in peer-facing cards with the same effective
-- server projection used by the leaderboard and the viewer's own card.
-- Keep NULL totals hidden for profiles that do not share exact time.

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
  target_user_id uuid;
begin
  if jsonb_typeof(coalesce(p_people, '[]'::jsonb)) <> 'array' then
    return result;
  end if;

  for item in
    select value from jsonb_array_elements(coalesce(p_people, '[]'::jsonb))
  loop
    target_user_id := null;
    begin
      target_user_id := nullif(item ->> 'user_id', '')::uuid;
    exception when invalid_text_representation then
      target_user_id := null;
    end;

    if target_user_id is not null then
      if item ? 'today_seconds' and item -> 'today_seconds' <> 'null'::jsonb then
        item := jsonb_set(
          item,
          '{today_seconds}',
          to_jsonb(public.lili_effective_focus_today_seconds(target_user_id)),
          true
        );
      end if;
      if item ? 'week_seconds' and item -> 'week_seconds' <> 'null'::jsonb then
        item := jsonb_set(
          item,
          '{week_seconds}',
          to_jsonb(public.lili_effective_focus_week_seconds(target_user_id)),
          true
        );
      end if;
    end if;

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

comment on function public.lili_mark_canonical_focus_totals(jsonb) is
  'Projects visible peer totals from the canonical interval union plus frozen legacy floor; NULL values remain hidden.';
