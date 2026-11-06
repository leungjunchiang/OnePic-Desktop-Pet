-- Read each account's complete compatibility ledger without manufacturing
-- interval facts. The table itself intentionally has no authenticated SELECT
-- grants, so this owner-scoped RPC is the narrow read boundary.

begin;

create schema if not exists lili_focus_history_private;
revoke all on schema lili_focus_history_private from public, anon, authenticated;
grant usage on schema lili_focus_history_private to authenticated;

create or replace function lili_focus_history_private.lili_focus_legacy_history_page_impl(
  p_mode text,
  p_after_date date default null,
  p_scan_anchor timestamptz default null,
  p_since timestamptz default null,
  p_after_updated_at timestamptz default null,
  p_after_updated_date date default null,
  p_limit integer default 500
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  current_user_id uuid := (select auth.uid());
  today date := (now() at time zone 'Asia/Shanghai')::date;
  scan_watermark timestamptz := coalesce(p_scan_anchor, now());
  page_limit integer := greatest(1, least(500, coalesce(p_limit, 500)));
  page jsonb := '[]'::jsonb;
  next_after_date date := null;
  next_updated_at timestamptz := null;
  next_updated_date date := null;
  has_more boolean := false;
begin
  if current_user_id is null then
    raise exception 'authentication required for legacy focus history';
  end if;
  if p_mode is null or p_mode not in ('full', 'delta') then
    raise exception 'invalid legacy focus history mode' using errcode = '22023';
  end if;

  if p_mode = 'full' then
    with selected as materialized (
      select l.focus_date, l.legacy_seconds, l.legacy_source
      from public.lili_focus_legacy_compatibility_ledger l
      where l.user_id = current_user_id
        and l.focus_date <= today
        and l.last_seen_at <= scan_watermark
        and (p_after_date is null or l.focus_date > p_after_date)
      order by l.focus_date
      limit page_limit
    )
    select
      coalesce(jsonb_agg(jsonb_build_object(
        'focus_date', selected.focus_date,
        'legacy_seconds', selected.legacy_seconds,
        'legacy_source', selected.legacy_source
      ) order by selected.focus_date), '[]'::jsonb),
      max(selected.focus_date)
    into page, next_after_date
    from selected;

    if next_after_date is not null then
      select exists(
        select 1
        from public.lili_focus_legacy_compatibility_ledger l
        where l.user_id = current_user_id
          and l.focus_date <= today
          and l.last_seen_at <= scan_watermark
          and l.focus_date > next_after_date
      ) into has_more;
    end if;
  else
    with selected as materialized (
      select l.focus_date, l.legacy_seconds, l.legacy_source, l.last_seen_at
      from public.lili_focus_legacy_compatibility_ledger l
      where l.user_id = current_user_id
        and l.focus_date <= today
        and l.last_seen_at <= scan_watermark
        and (p_since is null or l.last_seen_at >= p_since)
        and (
          p_after_updated_at is null
          or l.last_seen_at > p_after_updated_at
          or (l.last_seen_at = p_after_updated_at
              and l.focus_date > coalesce(p_after_updated_date, '-infinity'::date))
        )
      order by l.last_seen_at, l.focus_date
      limit page_limit
    )
    select
      coalesce(jsonb_agg(jsonb_build_object(
        'focus_date', selected.focus_date,
        'legacy_seconds', selected.legacy_seconds,
        'legacy_source', selected.legacy_source,
        'updated_at', selected.last_seen_at
      ) order by selected.last_seen_at, selected.focus_date), '[]'::jsonb),
      max(selected.last_seen_at),
      (array_agg(selected.focus_date order by selected.last_seen_at desc, selected.focus_date desc))[1]
    into page, next_updated_at, next_updated_date
    from selected;

    if next_updated_at is not null and next_updated_date is not null then
      select exists(
        select 1
        from public.lili_focus_legacy_compatibility_ledger l
        where l.user_id = current_user_id
          and l.focus_date <= today
          and l.last_seen_at <= scan_watermark
          and (p_since is null or l.last_seen_at >= p_since)
          and (
            l.last_seen_at > next_updated_at
            or (l.last_seen_at = next_updated_at and l.focus_date > next_updated_date)
          )
      ) into has_more;
    end if;
  end if;

  return jsonb_build_object(
    'account_id', current_user_id,
    'sync_mode', p_mode,
    'scan_anchor', scan_watermark,
    'days', page,
    'has_more', has_more,
    'next_after_date', next_after_date,
    'next_updated_at', next_updated_at,
    'next_updated_date', next_updated_date
  );
end;
$$;

revoke all on function lili_focus_history_private.lili_focus_legacy_history_page_impl(
  text, date, timestamptz, timestamptz, timestamptz, date, integer
) from public, anon, authenticated;
grant execute on function lili_focus_history_private.lili_focus_legacy_history_page_impl(
  text, date, timestamptz, timestamptz, timestamptz, date, integer
) to authenticated;

create or replace function public.lili_focus_legacy_history_page(
  p_mode text,
  p_after_date date default null,
  p_scan_anchor timestamptz default null,
  p_since timestamptz default null,
  p_after_updated_at timestamptz default null,
  p_after_updated_date date default null,
  p_limit integer default 500
)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
  select lili_focus_history_private.lili_focus_legacy_history_page_impl(
    p_mode,
    p_after_date,
    p_scan_anchor,
    p_since,
    p_after_updated_at,
    p_after_updated_date,
    p_limit
  );
$$;

revoke all on function public.lili_focus_legacy_history_page(
  text, date, timestamptz, timestamptz, timestamptz, date, integer
) from public, anon, authenticated;
grant execute on function public.lili_focus_legacy_history_page(
  text, date, timestamptz, timestamptz, timestamptz, date, integer
) to authenticated;

comment on function lili_focus_history_private.lili_focus_legacy_history_page_impl(
  text, date, timestamptz, timestamptz, timestamptz, date, integer
) is
  'Owner-scoped bounded full/delta reads of server legacy daily totals; never creates interval records.';
comment on function public.lili_focus_legacy_history_page(
  text, date, timestamptz, timestamptz, timestamptz, date, integer
) is
  'Authenticated invoker wrapper for owner-scoped bounded reads of legacy daily totals.';

notify pgrst, 'reload schema';
commit;
