-- Daily cake is a group celebration, not a one-to-one gift.
-- Each invitation remains an ordinary visit event so existing realtime/polling
-- and accept/later UI continue to work on old clients.

alter table public.lili_visit_events
  drop constraint if exists lili_visit_events_kind_check;
alter table public.lili_visit_events
  add constraint lili_visit_events_kind_check
  check (kind in (
    'visit', 'cheer', 'water', 'rest', 'food_coffee', 'food_milk_tea',
    'food_tea', 'food_cake', 'food_cake_share'
  ));

create index if not exists lili_cake_share_sender_date_idx
  on public.lili_visit_events(sender_id, created_at desc)
  where kind = 'food_cake_share';

create or replace function public.lili_create_cake_share(
  p_recipient_ids uuid[],
  p_message text default ''
) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  sender uuid := (select auth.uid());
  share_id uuid := extensions.gen_random_uuid();
  clean_message text := left(trim(coalesce(p_message, '')), 160);
  recipient_count integer;
begin
  if sender is null then
    raise exception '请先登录搭子自习室';
  end if;
  if p_recipient_ids is null or cardinality(p_recipient_ids) not between 1 and 3 then
    raise exception '小蛋糕需要邀请 1～3 位好友';
  end if;
  if exists (select 1 from unnest(p_recipient_ids) as ids(user_id) where ids.user_id is null) then
    raise exception '好友列表不完整';
  end if;
  if exists (
    select ids.user_id
    from unnest(p_recipient_ids) as ids(user_id)
    group by ids.user_id
    having count(*) > 1
  ) then
    raise exception '不能重复邀请同一位好友';
  end if;
  if sender = any(p_recipient_ids) then
    raise exception '不能邀请自己参加自己的蛋糕分享';
  end if;
  if exists (
    select 1
    from unnest(p_recipient_ids) as ids(user_id)
    where not public.lili_are_buddies(sender, ids.user_id)
       or not exists (
         select 1 from public.lili_profiles p
         where p.user_id = ids.user_id and p.allow_visits
       )
  ) then
    raise exception '只能邀请已建立关系且允许互动的搭子';
  end if;
  if exists (
    select 1 from public.lili_visit_events v
    where v.sender_id = sender
      and v.kind = 'food_cake_share'
      and v.created_at >= current_date
      and v.created_at < current_date + interval '1 day'
  ) then
    raise exception '每天最多发起一场蛋糕分享';
  end if;

  insert into public.lili_visit_events(sender_id, receiver_id, kind, payload)
  select sender, ids.user_id, 'food_cake_share', jsonb_build_object(
    'share_id', share_id,
    'message', coalesce(nullif(clean_message, ''), '今天值得庆祝一下。'),
    'duration_minutes', 0,
    'operation_key', share_id::text
  )
  from unnest(p_recipient_ids) as ids(user_id);

  select count(*)::integer into recipient_count from unnest(p_recipient_ids);
  return jsonb_build_object(
    'share_id', share_id,
    'recipient_count', recipient_count,
    'message', coalesce(nullif(clean_message, ''), '今天值得庆祝一下。'),
    'status', 'shared'
  );
end;
$$;

revoke execute on function public.lili_create_cake_share(uuid[], text) from public, anon;
grant execute on function public.lili_create_cake_share(uuid[], text) to authenticated;

-- Extend the already deployed dashboard without duplicating the large focus
-- and room projection. The old implementation remains private as a base.
alter function public.lili_dashboard() rename to lili_dashboard_base;
revoke execute on function public.lili_dashboard_base() from public, anon, authenticated;

create or replace function public.lili_dashboard() returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select public.lili_dashboard_base() || jsonb_build_object(
    'cake_shares', coalesce((
      select jsonb_agg(jsonb_build_object(
        'share_id', grouped.share_id,
        'message', grouped.message,
        'created_at', grouped.created_at,
        'members', grouped.members
      ) order by grouped.created_at desc)
      from (
        select
          v.payload ->> 'share_id' as share_id,
          max(coalesce(v.payload ->> 'message', '')) as message,
          min(v.created_at) as created_at,
          jsonb_agg(jsonb_build_object(
            'user_id', v.receiver_id,
            'nickname', public.lili_owner_nickname(v.receiver_id),
            'status', v.status,
            'responded_at', v.responded_at
          ) order by public.lili_owner_nickname(v.receiver_id)) as members
        from public.lili_visit_events v
        where v.sender_id = (select auth.uid())
          and v.kind = 'food_cake_share'
          and v.payload ->> 'share_id' is not null
        group by v.payload ->> 'share_id'
      ) grouped
    ), '[]'::jsonb)
  );
$$;

revoke execute on function public.lili_dashboard() from public, anon;
grant execute on function public.lili_dashboard() to authenticated;
