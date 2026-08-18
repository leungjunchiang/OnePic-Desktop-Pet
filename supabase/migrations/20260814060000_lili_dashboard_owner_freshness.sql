-- Return the explicit owner nickname from the top-level dashboard too.
-- The room RPC was repaired in the previous migration, but the home page
-- still used the legacy lili_profiles.nickname field.

create or replace function public.lili_dashboard() returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object(
    'me',(select to_jsonb(p) from public.lili_profiles p where p.user_id=(select auth.uid())),
    'me_presence',coalesce((select jsonb_build_object(
      'user_id',f.user_id,
      'working',case when f.working and f.last_seen > now()-interval '2 minutes' then true else false end,
      'status',case when f.last_seen is null or f.last_seen <= now()-interval '2 minutes' then 'offline' when f.working then 'focus' else 'rest' end,
      'session_started_at',f.session_started_at,
      'session_seconds',case when f.working and f.session_started_at is not null and f.last_seen > now()-interval '2 minutes' then greatest(0,floor(extract(epoch from (now()-f.session_started_at)))::int) else 0 end,
      'today_seconds',coalesce(f.today_seconds,0),'outfit_key',coalesce(f.outfit_key,''),'room_id',f.room_id,
      'online',coalesce(f.last_seen > now()-interval '2 minutes',false),
      'last_seen_at',f.last_seen,'status_updated_at',f.updated_at,'server_timestamp',now()
    ) from public.lili_focus_presence f where f.user_id=(select auth.uid())),
      jsonb_build_object('user_id',(select auth.uid()),'working',false,'status','idle','session_seconds',0,'today_seconds',0,'online',false,'server_timestamp',now())),
    'requests',coalesce((select jsonb_agg(jsonb_build_object('id',l.id,'nickname',public.lili_owner_nickname(l.requester_id),'owner_nickname',public.lili_owner_nickname(l.requester_id),'created_at',l.created_at))
      from public.lili_buddy_links l where l.addressee_id=(select auth.uid()) and l.status='pending'),'[]'::jsonb),
    'buddies',coalesce((select jsonb_agg(jsonb_build_object(
      'user_id',p.user_id,'nickname',public.lili_owner_nickname(p.user_id),'owner_nickname',public.lili_owner_nickname(p.user_id),'outfit_key',coalesce(f.outfit_key,p.outfit_key),
      'working',case when f.working and f.last_seen > now()-interval '2 minutes' then true else false end,
      'status',case when f.last_seen is null or f.last_seen <= now()-interval '2 minutes' then 'offline' when f.working then 'focus' else 'rest' end,
      'session_started_at',f.session_started_at,
      'session_seconds',case when f.working and f.session_started_at is not null and f.last_seen > now()-interval '2 minutes' then greatest(0,floor(extract(epoch from (now()-f.session_started_at)))::int) else 0 end,
      'today_seconds',case when p.show_exact_time then coalesce(f.today_seconds,0) else null end,
      'online',coalesce(f.last_seen > now()-interval '2 minutes',false),'last_seen_at',f.last_seen,'status_updated_at',f.updated_at,'server_timestamp',now(),'is_self',false,
      'subscribed',coalesce((select s.on_focus_start or s.on_focus_end from public.lili_buddy_subscriptions s where s.subscriber_id=(select auth.uid()) and s.buddy_id=p.user_id and not s.muted),false)))
      from public.lili_profiles p left join public.lili_focus_presence f on f.user_id=p.user_id
      where public.lili_are_buddies((select auth.uid()),p.user_id) and p.visibility='friends'),'[]'::jsonb),
    'rooms',coalesce((select jsonb_agg(jsonb_build_object('id',r.id,'name',r.name,'invite_code',r.invite_code,'members',(select count(*) from public.lili_room_members m2 where m2.room_id=r.id)))
      from public.lili_study_rooms r join public.lili_room_members m on m.room_id=r.id where m.user_id=(select auth.uid())),'[]'::jsonb),
    'room_people',coalesce((select jsonb_agg(distinct jsonb_build_object(
      'user_id',p.user_id,'nickname',public.lili_owner_nickname(p.user_id),'owner_nickname',public.lili_owner_nickname(p.user_id),'outfit_key',coalesce(f.outfit_key,p.outfit_key),
      'working',case when f.working and f.last_seen > now()-interval '2 minutes' then true else false end,
      'status',case when f.last_seen is null or f.last_seen <= now()-interval '2 minutes' then 'offline' when f.working then 'focus' else 'rest' end,
      'session_started_at',f.session_started_at,
      'session_seconds',case when f.working and f.session_started_at is not null and f.last_seen > now()-interval '2 minutes' then greatest(0,floor(extract(epoch from (now()-f.session_started_at)))::int) else 0 end,
      'today_seconds',case when p.show_exact_time then coalesce(f.today_seconds,0) else null end,
      'online',coalesce(f.last_seen > now()-interval '2 minutes',false),'last_seen_at',f.last_seen,'status_updated_at',f.updated_at,'server_timestamp',now(),'is_self',false))
      from public.lili_room_members mine join public.lili_room_members other using(room_id)
      join public.lili_profiles p on p.user_id=other.user_id left join public.lili_focus_presence f on f.user_id=p.user_id
      where mine.user_id=(select auth.uid()) and other.user_id<>(select auth.uid()) and p.visibility='friends'),'[]'::jsonb),
    'visits',coalesce((select jsonb_agg(jsonb_build_object('id',v.id,'sender_id',v.sender_id,'nickname',public.lili_owner_nickname(v.sender_id),'owner_nickname',public.lili_owner_nickname(v.sender_id),'kind',v.kind,'created_at',v.created_at))
      from public.lili_visit_events v where v.receiver_id=(select auth.uid()) and v.status='pending' and v.expires_at>now()),'[]'::jsonb),
    'active_visits',coalesce((select jsonb_agg(jsonb_build_object(
      'id',v.id,'peer_id',p.user_id,'nickname',public.lili_owner_nickname(p.user_id),'owner_nickname',public.lili_owner_nickname(p.user_id),'outfit_key',coalesce(f.outfit_key,p.outfit_key),
      'working',case when f.working and f.last_seen > now()-interval '2 minutes' then true else false end,
      'status',case when f.last_seen is null or f.last_seen <= now()-interval '2 minutes' then 'offline' when f.working then 'focus' else 'rest' end,
      'session_started_at',f.session_started_at,
      'session_seconds',case when f.working and f.session_started_at is not null and f.last_seen > now()-interval '2 minutes' then greatest(0,floor(extract(epoch from (now()-f.session_started_at)))::int) else 0 end,
      'today_seconds',case when p.show_exact_time then coalesce(f.today_seconds,0) else null end,
      'online',coalesce(f.last_seen > now()-interval '2 minutes',false),'last_seen_at',f.last_seen,'status_updated_at',f.updated_at,'server_timestamp',now(),
      'visit_started_at',coalesce(v.responded_at,v.created_at)))
      from public.lili_visit_events v join public.lili_profiles p on p.user_id=case when (select auth.uid())=v.sender_id then v.receiver_id else v.sender_id end
      left join public.lili_focus_presence f on f.user_id=p.user_id
      where (select auth.uid()) in (v.sender_id,v.receiver_id) and v.status='accepted' and v.responded_at>now()-interval '2 hours'),'[]'::jsonb)
  );
$$;

revoke execute on function public.lili_dashboard() from public, anon;
grant execute on function public.lili_dashboard() to authenticated;
