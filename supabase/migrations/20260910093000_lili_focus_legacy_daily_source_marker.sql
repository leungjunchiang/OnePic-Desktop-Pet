-- Mark peer rows as canonical totals with the bounded legacy daily fallback.
-- Mixed-version clients use this marker to avoid adding a second live
-- interval to a value that already includes server-side live projection.

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
        'focus_totals_source', 'canonical_interval_union_legacy_daily_compat'
      )
    );
  end loop;
  return result;
end;
$$;

revoke execute on function public.lili_mark_canonical_focus_totals(jsonb)
  from public, anon, authenticated;
