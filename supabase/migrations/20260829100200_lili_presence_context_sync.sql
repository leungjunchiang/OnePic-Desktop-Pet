-- Room association and presentation metadata are not heartbeat data.
-- Keep them on a separate background RPC so presence remains liveness-only:
-- no duration, daily/weekly totals, or client clock is accepted here.

create or replace function public.lili_update_presence_context(
  p_room_id uuid default null,
  p_outfit_key text default null,
  p_quick_status text default null,
  p_quick_status_expires_at timestamptz default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  me uuid := (select auth.uid());
  clean_status text := nullif(left(btrim(coalesce(p_quick_status, '')), 40), '');
  clean_expiry timestamptz := case
    when clean_status is not null and p_quick_status_expires_at > now()
      then p_quick_status_expires_at
    else null
  end;
  updated boolean := false;
begin
  if me is null then
    raise exception '请先登录';
  end if;

  if p_room_id is not null and not exists (
    select 1 from public.lili_room_members m
    where m.room_id = p_room_id and m.user_id = me
  ) then
    raise exception 'room_membership_required';
  end if;

  -- Do not create a row here: creating one would make an account appear
  -- online without a real liveness heartbeat.  The next heartbeat creates
  -- the clean inactive/active row when needed.
  update public.lili_focus_presence f
  set room_id = p_room_id,
      outfit_key = case
        when p_outfit_key is null then f.outfit_key
        else left(btrim(p_outfit_key), 60)
      end,
      quick_status = clean_status,
      quick_status_expires_at = clean_expiry,
      updated_at = now()
  where f.user_id = me;
  updated := found;

  return jsonb_build_object(
    'updated', updated,
    'user_id', me,
    'room_id', p_room_id,
    'server_timestamp', now()
  );
end;
$$;

revoke execute on function public.lili_update_presence_context(uuid, text, text, timestamptz)
  from public, anon;
grant execute on function public.lili_update_presence_context(uuid, text, text, timestamptz)
  to authenticated;
