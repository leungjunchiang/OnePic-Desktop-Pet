-- Client-computed daily/week aggregates are compatibility input only.
-- Canonical duration comes from sealed FocusSegments plus fresh valid presence,
-- then uses the frozen legacy floor per Beijing day.

create or replace function public.lili_sync_personal_state(
  p_focus_date date default ((now() at time zone 'Asia/Shanghai')::date),
  p_today_seconds integer default 0,
  p_lifetime_seconds bigint default 0,
  p_outfit_key text default null,
  p_outfit_set boolean default false
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  current_user_id uuid := (select auth.uid());
  current_day date := ((now() at time zone 'Asia/Shanghai')::date);
  stats jsonb;
  merged_lifetime bigint;
  merged_outfit text;
begin
  if current_user_id is null then
    raise exception '需要登录';
  end if;

  -- Client day totals are deliberately ignored. They are derived values, not
  -- FocusSession evidence, and an old paused client may keep increasing them.
  update public.lili_profiles p
  set focus_lifetime_seconds = greatest(
        p.focus_lifetime_seconds,
        greatest(0, coalesce(p_lifetime_seconds, 0))
      ),
      outfit_key = case
        when coalesce(p_outfit_set, false)
          then left(btrim(coalesce(p_outfit_key, '')), 60)
        else p.outfit_key
      end,
      updated_at = now()
  where p.user_id = current_user_id
  returning p.focus_lifetime_seconds, p.outfit_key
    into merged_lifetime, merged_outfit;

  if not found then
    raise exception '搭子资料不存在';
  end if;

  stats := public.lili_effective_focus_stats(current_user_id);
  return jsonb_build_object(
    'focus_today_date', current_day,
    'focus_today_seconds', coalesce((stats->>'today_seconds')::integer, 0),
    'focus_lifetime_seconds', merged_lifetime,
    'outfit_key', merged_outfit
  );
end;
$$;

create or replace function public.lili_sync_personal_state(
  p_focus_date date default ((now() at time zone 'Asia/Shanghai')::date),
  p_today_seconds integer default 0,
  p_lifetime_seconds bigint default 0,
  p_outfit_key text default null,
  p_outfit_set boolean default false,
  p_week_start date default (date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date),
  p_week_seconds integer default 0
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  current_user_id uuid := (select auth.uid());
  current_day date := ((now() at time zone 'Asia/Shanghai')::date);
  current_week date := (date_trunc('week', (now() at time zone 'Asia/Shanghai'))::date);
  stats jsonb;
  merged_lifetime bigint;
  merged_outfit text;
begin
  if current_user_id is null then
    raise exception '需要登录';
  end if;

  -- p_focus_date/p_today_seconds/p_week_start/p_week_seconds remain in the
  -- signature only so installed old clients continue to receive a response.
  update public.lili_profiles p
  set focus_lifetime_seconds = greatest(
        p.focus_lifetime_seconds,
        greatest(0, coalesce(p_lifetime_seconds, 0))
      ),
      outfit_key = case
        when coalesce(p_outfit_set, false)
          then left(btrim(coalesce(p_outfit_key, '')), 60)
        else p.outfit_key
      end,
      updated_at = now()
  where p.user_id = current_user_id
  returning p.focus_lifetime_seconds, p.outfit_key
    into merged_lifetime, merged_outfit;

  if not found then
    raise exception '搭子资料不存在';
  end if;

  stats := public.lili_effective_focus_stats(current_user_id);
  return jsonb_build_object(
    'focus_today_date', current_day,
    'focus_today_seconds', coalesce((stats->>'today_seconds')::integer, 0),
    'focus_lifetime_seconds', merged_lifetime,
    'focus_week_start_date', current_week,
    'focus_week_seconds', coalesce((stats->>'week_seconds')::integer, 0),
    'outfit_key', merged_outfit
  );
end;
$$;

revoke execute on function public.lili_sync_personal_state(
  date, integer, bigint, text, boolean
) from public, anon;
grant execute on function public.lili_sync_personal_state(
  date, integer, bigint, text, boolean
) to authenticated, service_role;

revoke execute on function public.lili_sync_personal_state(
  date, integer, bigint, text, boolean, date, integer
) from public, anon;
grant execute on function public.lili_sync_personal_state(
  date, integer, bigint, text, boolean, date, integer
) to authenticated, service_role;

create or replace function public.lili_sync_focus_history(
  p_history jsonb default '[]'::jsonb
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
begin
  if current_user_id is null then
    raise exception '需要登录后才能同步专注历史';
  end if;
  if jsonb_typeof(coalesce(p_history, '[]'::jsonb)) <> 'array' then
    raise exception '专注历史格式无效';
  end if;

  -- The input is intentionally ignored. Return a read-only effective view so
  -- old clients remain compatible without mutating lili_focus_daily.
  return jsonb_build_object(
    'focus_date', today,
    'days', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'focus_date', day_value,
          'seconds', greatest(
            public.lili_focus_union_seconds(
              current_user_id,
              day_value::timestamp at time zone 'Asia/Shanghai',
              (day_value + 1)::timestamp at time zone 'Asia/Shanghai'
            ),
            coalesce((
              select f.seconds
              from public.lili_focus_legacy_daily_floor f
              where f.user_id = current_user_id
                and f.focus_date = day_value
            ), 0)
          )
        )
        order by day_value
      )
      from generate_series(today - 7, today, interval '1 day') generated(day_value)
    ), '[]'::jsonb)
  );
end;
$$;

revoke execute on function public.lili_sync_focus_history(jsonb)
  from public, anon;
grant execute on function public.lili_sync_focus_history(jsonb)
  to authenticated, service_role;

comment on function public.lili_sync_personal_state(
  date, integer, bigint, text, boolean, date, integer
) is 'Compatibility RPC. Client day/week duration scalars are ignored; response uses canonical interval union plus frozen legacy daily floor.';
comment on function public.lili_sync_focus_history(jsonb) is
  'Read-only compatibility RPC. Incoming client history is ignored and returned days are server effective projections.';

do $verify$
declare
  personal_definition text;
  history_definition text;
begin
  personal_definition := pg_get_functiondef(
    'public.lili_sync_personal_state(date,integer,bigint,text,boolean,date,integer)'::regprocedure
  );
  history_definition := pg_get_functiondef(
    'public.lili_sync_focus_history(jsonb)'::regprocedure
  );
  if position('insert into public.lili_focus_daily' in lower(personal_definition)) > 0
     or position('insert into public.lili_focus_daily' in lower(history_definition)) > 0 then
    raise exception 'legacy duration write barrier verification failed';
  end if;
end;
$verify$;
