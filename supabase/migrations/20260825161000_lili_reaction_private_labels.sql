-- Use the viewer's private buddy remarks in reaction labels.  These values
-- are intentionally resolved for auth.uid() only and never alter public
-- profile names or shared leaderboard data.

create or replace function public.lili_taunt_state()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  me uuid := (select auth.uid());
  result jsonb;
begin
  if me is null then raise exception '请先登录'; end if;

  if exists (
    select 1 from public.lili_focus_presence f
    where f.user_id = me and f.working and f.last_seen > now() - interval '2 minutes'
  ) then
    update public.lili_buddy_taunts t
    set started_working_at = coalesce(t.started_working_at, now()),
        work_started_at = now(),
        worked_seconds = least(1200, greatest(0, t.worked_seconds + case
          when t.work_started_at is null then 0
          else floor(extract(epoch from (now() - t.work_started_at)))::integer
        end))
    where t.receiver_id = me and t.released_at is null;
    update public.lili_buddy_taunts t
    set released_at = now(), work_started_at = null
    where t.receiver_id = me and t.released_at is null and t.worked_seconds >= 1200;
  else
    update public.lili_buddy_taunts t
    set work_started_at = null, worked_seconds = 0
    where t.receiver_id = me and t.released_at is null;
  end if;

  select jsonb_build_object(
    'active', true,
    'id', t.id,
    'sender_id', t.sender_id,
    'sender_nickname', public.lili_owner_nickname(t.sender_id),
    'sender_display_name', coalesce((
      select n.private_note_name
      from public.lili_buddy_private_notes n
      where n.owner_user_id = me and n.buddy_user_id = t.sender_id
    ), public.lili_owner_nickname(t.sender_id)),
    'sender_nicknames', coalesce((
      select jsonb_agg(public.lili_owner_nickname(active_t.sender_id)
                       order by active_t.created_at desc)
      from public.lili_buddy_taunts active_t
      where active_t.receiver_id = me and active_t.released_at is null
    ), '[]'::jsonb),
    'sender_display_names', coalesce((
      select jsonb_agg(coalesce((
        select n.private_note_name
        from public.lili_buddy_private_notes n
        where n.owner_user_id = me and n.buddy_user_id = active_t.sender_id
      ), public.lili_owner_nickname(active_t.sender_id))
      order by active_t.created_at desc)
      from public.lili_buddy_taunts active_t
      where active_t.receiver_id = me and active_t.released_at is null
    ), '[]'::jsonb),
    'created_at', t.created_at,
    'message', coalesce(nullif(t.message, ''), '怎么，今天准备靠意念完成？'),
    'messages', coalesce((
      select jsonb_agg(coalesce(nullif(active_t.message, ''), '怎么，今天准备靠意念完成？')
                       order by active_t.created_at desc)
      from public.lili_buddy_taunts active_t
      where active_t.receiver_id = me and active_t.released_at is null
    ), '[]'::jsonb),
    'support_count', (select count(*)
      from public.lili_buddy_taunts active_t
      where active_t.receiver_id = me and active_t.released_at is null),
    'remaining_work_seconds', greatest(0, 1200 - t.worked_seconds - case
      when t.work_started_at is null then 0
      else floor(extract(epoch from (now() - t.work_started_at)))::integer
    end)
  ) into result
  from public.lili_buddy_taunts t
  where t.receiver_id = me and t.released_at is null
  order by t.created_at desc limit 1;

  return coalesce(result, jsonb_build_object('active', false));
end;
$$;

create or replace function public.lili_encouragement_state()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  me uuid := (select auth.uid());
  result jsonb;
begin
  if me is null then raise exception '请先登录'; end if;
  update public.lili_buddy_encouragements e
  set ended_at = now()
  where e.receiver_id = me and e.ended_at is null
    and (e.expires_at <= now() or not exists (
      select 1 from public.lili_focus_presence f
      where f.user_id = me and f.working and f.last_seen > now() - interval '2 minutes'
    ));
  select jsonb_build_object(
    'active', true,
    'id', e.id,
    'sender_id', e.sender_id,
    'sender_nickname', public.lili_owner_nickname(e.sender_id),
    'sender_display_name', coalesce((
      select n.private_note_name
      from public.lili_buddy_private_notes n
      where n.owner_user_id = me and n.buddy_user_id = e.sender_id
    ), public.lili_owner_nickname(e.sender_id)),
    'message', e.message,
    'created_at', e.created_at,
    'expires_at', e.expires_at,
    'support_count', (select count(*)
      from public.lili_buddy_encouragements active_e
      where active_e.receiver_id = me and active_e.ended_at is null and active_e.expires_at > now())
  ) into result
  from public.lili_buddy_encouragements e
  where e.receiver_id = me and e.ended_at is null and e.expires_at > now()
  order by e.created_at desc limit 1;
  return coalesce(result, jsonb_build_object('active', false));
end;
$$;

revoke execute on function public.lili_taunt_state() from public, anon;
revoke execute on function public.lili_encouragement_state() from public, anon;
grant execute on function public.lili_taunt_state() to authenticated;
grant execute on function public.lili_encouragement_state() to authenticated;
