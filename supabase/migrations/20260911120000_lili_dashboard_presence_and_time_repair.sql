-- Make the public dashboard reflect the same account-wide state that the
-- heartbeat RPC has already accepted.
--
-- The multi-device heartbeat writes a fresh per-device row and then projects
-- an account compatibility row.  Older dashboard bases only read the latter,
-- so a successful device heartbeat could still be rendered as offline when
-- the account projection was stale.  Repair the final JSON projection from
-- both sources.  A visible buddy's effective daily/weekly totals are also
-- returned regardless of the old show_exact_time scalar; the dashboard has
-- already restricted the row to an accepted visible buddy before this repair.

create or replace function public.lili_dashboard_presence_for_user(p_user_id uuid)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  with devices as (
    select
      coalesce(bool_or(d.last_seen > now() - interval '2 minutes'), false) as device_online,
      coalesce(bool_or(
        d.last_seen > now() - interval '2 minutes'
        and d.working
        and d.session_active
      ), false) as device_working,
      max(d.last_seen) filter (
        where d.last_seen > now() - interval '2 minutes'
      ) as device_last_seen,
      min(d.session_started_at) filter (
        where d.last_seen > now() - interval '2 minutes'
          and d.working
          and d.session_active
          and d.session_started_at is not null
      ) as device_started_at
    from public.lili_focus_device_presence d
    where d.user_id = p_user_id
  ), account_row as (
    select
      coalesce(bool_or(f.last_seen > now() - interval '2 minutes'), false) as account_online,
      coalesce(bool_or(
        f.last_seen > now() - interval '2 minutes'
        and f.working
        and f.session_active
      ), false) as account_working,
      max(f.last_seen) as account_last_seen,
      min(f.session_started_at) filter (
        where f.last_seen > now() - interval '2 minutes'
          and f.working
          and f.session_active
          and f.session_started_at is not null
      ) as account_started_at
    from public.lili_focus_presence f
    where f.user_id = p_user_id
  ), merged as (
    select
      d.device_online or a.account_online as online,
      d.device_working or a.account_working as working,
      case
        when d.device_last_seen is null then a.account_last_seen
        when a.account_last_seen is null then d.device_last_seen
        else greatest(d.device_last_seen, a.account_last_seen)
      end as last_seen_at,
      case
        when d.device_working then d.device_started_at
        when a.account_working then a.account_started_at
        else null
      end as session_started_at
    from devices d
    cross join account_row a
  )
  select jsonb_build_object(
    'online', online,
    'working', working,
    'status', case
      when online and working then 'focus'
      when online then 'rest'
      else 'offline'
    end,
    'last_seen_at', last_seen_at,
    'status_updated_at', last_seen_at,
    'session_started_at', session_started_at,
    'session_seconds', case
      when working and session_started_at is not null
        then greatest(0, floor(extract(epoch from (now() - session_started_at)))::integer)
      else 0
    end,
    'account_online', online,
    'account_working', working,
    'presence_source', case
      when online then 'fresh_device_or_account_presence'
      else 'stale_or_missing_presence'
    end
  )
  from merged;
$$;

revoke execute on function public.lili_dashboard_presence_for_user(uuid)
  from public, anon, authenticated;

create or replace function public.lili_repair_dashboard_people(p_people jsonb)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  with people as (
    select
      value as item,
      ordinal,
      nullif(value ->> 'user_id', '')::uuid as user_id
    from jsonb_array_elements(coalesce(p_people, '[]'::jsonb))
      with ordinality as rows(value, ordinal)
  )
  select coalesce(jsonb_agg(
    case
      when user_id is null then item
      else item || jsonb_build_object(
        'online', presence_data.presence -> 'online',
        'working', presence_data.presence -> 'working',
        'status', presence_data.presence ->> 'status',
        'last_seen_at', presence_data.presence -> 'last_seen_at',
        'status_updated_at', presence_data.presence -> 'status_updated_at',
        'session_started_at', presence_data.presence -> 'session_started_at',
        'session_seconds', presence_data.presence -> 'session_seconds',
        'account_online', presence_data.presence -> 'account_online',
        'account_working', presence_data.presence -> 'account_working',
        'presence_source', presence_data.presence ->> 'presence_source',
        'today_seconds', public.lili_effective_focus_today_seconds(user_id),
        'week_seconds', public.lili_effective_focus_week_seconds(user_id),
        'focus_totals_source', 'canonical_interval_union',
        'focus_totals_effective_source', 'canonical_interval_union_legacy_ledger'
      )
    end
    order by ordinal
  ), '[]'::jsonb)
  from people
  cross join lateral (
    select public.lili_dashboard_presence_for_user(people.user_id) as presence
  ) as presence_data
$$;

revoke execute on function public.lili_repair_dashboard_people(jsonb)
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
  payload := jsonb_set(payload, '{buddies}', public.lili_repair_dashboard_people(
    public.lili_mark_canonical_focus_totals(
      public.lili_normalize_focus_today_people(
        public.lili_zero_never_seen_presence(coalesce(payload -> 'buddies', '[]'::jsonb))
      )
    )
  ), true);
  payload := jsonb_set(payload, '{room_people}', public.lili_repair_dashboard_people(
    public.lili_mark_canonical_focus_totals(
      public.lili_normalize_focus_today_people(
        public.lili_zero_never_seen_presence(coalesce(payload -> 'room_people', '[]'::jsonb))
      )
    )
  ), true);
  payload := jsonb_set(payload, '{active_visits}', public.lili_repair_dashboard_people(
    public.lili_mark_canonical_focus_totals(
      public.lili_normalize_focus_today_people(
        public.lili_zero_never_seen_presence(coalesce(payload -> 'active_visits', '[]'::jsonb))
      )
    )
  ), true);
  if jsonb_typeof(payload -> 'current_room') = 'object' then
    payload := jsonb_set(payload, '{current_room,room_people}', public.lili_repair_dashboard_people(
      public.lili_mark_canonical_focus_totals(
        public.lili_normalize_focus_today_people(
          public.lili_zero_never_seen_presence(
            coalesce(payload -> 'current_room' -> 'room_people', '[]'::jsonb)
          )
        )
      )
    ), true);
  end if;

  if jsonb_typeof(payload -> 'me_presence') = 'object' then
    payload := jsonb_set(payload, '{me_presence,today_seconds}', to_jsonb(
      public.lili_effective_focus_today_seconds(me_id)
    ), true);
    payload := jsonb_set(payload, '{me_presence,week_seconds}', to_jsonb(
      public.lili_effective_focus_week_seconds(me_id)
    ), true);
    payload := jsonb_set(payload, '{me_presence,focus_totals_source}',
      to_jsonb('canonical_interval_union'::text), true);
    payload := jsonb_set(payload, '{me_presence,focus_totals_effective_source}',
      to_jsonb('canonical_interval_union_legacy_ledger'::text), true);
  end if;
  if jsonb_typeof(payload -> 'me') = 'object' then
    payload := jsonb_set(payload, '{me,focus_today_seconds}', to_jsonb(
      public.lili_effective_focus_today_seconds(me_id)
    ), true);
    payload := jsonb_set(payload, '{me,focus_week_seconds}', to_jsonb(
      public.lili_effective_focus_week_seconds(me_id)
    ), true);
    payload := jsonb_set(payload, '{me,focus_totals_source}',
      to_jsonb('canonical_interval_union'::text), true);
    payload := jsonb_set(payload, '{me,focus_totals_effective_source}',
      to_jsonb('canonical_interval_union_legacy_ledger'::text), true);
  end if;
  payload := jsonb_set(payload, '{focus_totals_source}',
    to_jsonb('canonical_interval_union'::text), true);
  payload := jsonb_set(payload, '{focus_totals_effective_source}',
    to_jsonb('canonical_interval_union_legacy_ledger'::text), true);
  return payload;
end;
$$;

revoke execute on function public.lili_dashboard() from public, anon;
grant execute on function public.lili_dashboard() to authenticated;

comment on function public.lili_dashboard_presence_for_user(uuid) is
  'Account presence is online when any fresh device or compatibility heartbeat exists; working is a separate live-focus state.';
comment on function public.lili_repair_dashboard_people(jsonb) is
  'Repairs accepted dashboard people from fresh device/account presence and returns effective daily/weekly totals.';
comment on function public.lili_dashboard() is
  'Final dashboard projection: fresh login is online/rest, active focus is focus, and accepted buddy cards expose effective totals.';
