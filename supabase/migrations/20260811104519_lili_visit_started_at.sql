-- 为已接受串门返回统一的开始时间；仍不增加聊天、任务或动作同步字段。
create or replace function public.lili_dashboard() returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object(
    'me',(select to_jsonb(p) from public.lili_profiles p where p.user_id=auth.uid()),
    'requests',coalesce((select jsonb_agg(jsonb_build_object('id',l.id,'nickname',p.nickname,'created_at',l.created_at))
      from public.lili_buddy_links l join public.lili_profiles p on p.user_id=l.requester_id
      where l.addressee_id=auth.uid() and l.status='pending'),'[]'::jsonb),
    'buddies',coalesce((select jsonb_agg(jsonb_build_object('user_id',p.user_id,'nickname',p.nickname,'outfit_key',coalesce(f.outfit_key,p.outfit_key),
      'working',coalesce(f.working,false),'session_started_at',f.session_started_at,'today_seconds',case when p.show_exact_time then coalesce(f.today_seconds,0) else null end,
      'online',coalesce(f.last_seen>now()-interval '2 minutes',false)))
      from public.lili_profiles p left join public.lili_focus_presence f on f.user_id=p.user_id
      where public.lili_are_buddies(auth.uid(),p.user_id) and p.visibility='friends'),'[]'::jsonb),
    'rooms',coalesce((select jsonb_agg(jsonb_build_object('id',r.id,'name',r.name,'invite_code',r.invite_code,
      'members',(select count(*) from public.lili_room_members m2 where m2.room_id=r.id)))
      from public.lili_study_rooms r join public.lili_room_members m on m.room_id=r.id where m.user_id=auth.uid()),'[]'::jsonb),
    'room_people',coalesce((select jsonb_agg(distinct jsonb_build_object('user_id',p.user_id,'nickname',p.nickname,'outfit_key',coalesce(f.outfit_key,p.outfit_key),
      'working',coalesce(f.working,false),'session_started_at',f.session_started_at,'today_seconds',case when p.show_exact_time then coalesce(f.today_seconds,0) else null end,
      'online',coalesce(f.last_seen>now()-interval '2 minutes',false)))
      from public.lili_room_members mine join public.lili_room_members other using(room_id)
      join public.lili_profiles p on p.user_id=other.user_id left join public.lili_focus_presence f on f.user_id=p.user_id
      where mine.user_id=auth.uid() and other.user_id<>auth.uid() and p.visibility='friends'),'[]'::jsonb),
    'visits',coalesce((select jsonb_agg(jsonb_build_object('id',v.id,'sender_id',v.sender_id,'nickname',p.nickname,'kind',v.kind,'created_at',v.created_at))
      from public.lili_visit_events v join public.lili_profiles p on p.user_id=v.sender_id
      where v.receiver_id=auth.uid() and v.status='pending' and v.expires_at>now()),'[]'::jsonb),
    'active_visits',coalesce((select jsonb_agg(jsonb_build_object('id',v.id,'peer_id',p.user_id,'nickname',p.nickname,
      'outfit_key',coalesce(f.outfit_key,p.outfit_key),'working',coalesce(f.working,false),'session_started_at',f.session_started_at,
      'today_seconds',case when p.show_exact_time then coalesce(f.today_seconds,0) else null end,
      'online',coalesce(f.last_seen>now()-interval '2 minutes',false),'visit_started_at',coalesce(v.responded_at,v.created_at)))
      from public.lili_visit_events v join public.lili_profiles p on p.user_id=case when v.sender_id=auth.uid() then v.receiver_id else v.sender_id end
      left join public.lili_focus_presence f on f.user_id=p.user_id
      where auth.uid() in (v.sender_id,v.receiver_id) and v.status='accepted' and v.responded_at>now()-interval '2 hours'),'[]'::jsonb)
  );
$$;

revoke execute on function public.lili_dashboard() from public, anon;
grant execute on function public.lili_dashboard() to authenticated;
