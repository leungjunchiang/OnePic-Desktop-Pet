-- Make every server-facing weekly total use the same account-level interval
-- union as the desktop report.  Profile counters and daily snapshots remain
-- compatibility projections; they are never evidence for a leaderboard.

create or replace function public.lili_effective_focus_week_seconds(
  p_user_id uuid
)
returns integer
language sql
stable
security definer
set search_path = ''
as $$
  select greatest(
    0,
    least(
      604800,
      public.lili_focus_union_seconds(
        p_user_id,
        (date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date::timestamp
          at time zone 'Asia/Shanghai'),
        ((date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date + 7)::timestamp
          at time zone 'Asia/Shanghai')
      )
    )
  );
$$;

revoke execute on function public.lili_effective_focus_week_seconds(uuid)
  from public, anon, authenticated;

comment on function public.lili_effective_focus_week_seconds(uuid) is
  'Canonical Beijing-week FocusSegment/live-presence interval union; never reads profile or daily aggregate counters.';
