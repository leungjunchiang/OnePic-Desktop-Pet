-- Same-account state shared by every Lili desktop.
-- Focus totals are merged by maximum value so two computers cannot double
-- count the same running segment or overwrite a newer checkpoint with zero.

alter table public.lili_profiles
  add column if not exists focus_lifetime_seconds bigint not null default 0;

alter table public.lili_profiles
  add column if not exists focus_today_date date not null default current_date;

alter table public.lili_profiles
  add column if not exists focus_today_seconds integer not null default 0;

alter table public.lili_profiles
  drop constraint if exists lili_profiles_focus_lifetime_seconds_check;
alter table public.lili_profiles
  add constraint lili_profiles_focus_lifetime_seconds_check
  check (focus_lifetime_seconds >= 0);

alter table public.lili_profiles
  drop constraint if exists lili_profiles_focus_today_seconds_check;
alter table public.lili_profiles
  add constraint lili_profiles_focus_today_seconds_check
  check (focus_today_seconds between 0 and 86400);

create or replace function public.lili_sync_personal_state(
  p_focus_date date default current_date,
  p_today_seconds integer default 0,
  p_lifetime_seconds bigint default 0,
  p_outfit_key text default null,
  p_outfit_set boolean default false
) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  target_date date := coalesce(p_focus_date, current_date);
  merged_today integer;
  merged_lifetime bigint;
  merged_outfit text;
begin
  update public.lili_profiles p
  set focus_today_seconds = case
        when target_date > p.focus_today_date
          then greatest(0, least(86400, coalesce(p_today_seconds, 0)))
        when target_date = p.focus_today_date
          then greatest(p.focus_today_seconds, greatest(0, least(86400, coalesce(p_today_seconds, 0))))
        else p.focus_today_seconds
      end,
      focus_today_date = greatest(p.focus_today_date, target_date),
      focus_lifetime_seconds = greatest(p.focus_lifetime_seconds, greatest(0, coalesce(p_lifetime_seconds, 0))),
      outfit_key = case
        when coalesce(p_outfit_set, false)
          then left(btrim(coalesce(p_outfit_key, '')), 60)
        else p.outfit_key
      end,
      updated_at = now()
  where p.user_id = (select auth.uid())
  returning p.focus_today_seconds, p.focus_lifetime_seconds, p.outfit_key
    into merged_today, merged_lifetime, merged_outfit;

  if not found then
    raise exception '搭子资料不存在';
  end if;

  return jsonb_build_object(
    'focus_today_date', target_date,
    'focus_today_seconds', merged_today,
    'focus_lifetime_seconds', merged_lifetime,
    'outfit_key', merged_outfit
  );
end;
$$;

revoke execute on function public.lili_sync_personal_state(date, integer, bigint, text, boolean) from public, anon;
grant execute on function public.lili_sync_personal_state(date, integer, bigint, text, boolean) to authenticated;
