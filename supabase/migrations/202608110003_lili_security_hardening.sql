-- 明确撤销默认 PUBLIC/anon 函数权限，并补齐常用关系索引。
revoke execute on function public.lili_invite_code() from public, anon, authenticated;
revoke execute on function public.lili_new_profile() from public, anon, authenticated;
revoke execute on function public.lili_are_buddies(uuid,uuid) from public, anon, authenticated;
revoke execute on function public.lili_share_room(uuid,uuid) from public, anon, authenticated;

revoke execute on function public.lili_add_buddy_by_code(text) from public, anon;
revoke execute on function public.lili_respond_buddy(uuid,boolean) from public, anon;
revoke execute on function public.lili_create_room(text) from public, anon;
revoke execute on function public.lili_join_room(text) from public, anon;
revoke execute on function public.lili_send_visit(uuid,text) from public, anon;
revoke execute on function public.lili_respond_visit(uuid,boolean) from public, anon;
revoke execute on function public.lili_dashboard() from public, anon;

create index if not exists lili_links_requester_idx on public.lili_buddy_links(requester_id);
create index if not exists lili_links_addressee_idx on public.lili_buddy_links(addressee_id);
create index if not exists lili_rooms_owner_idx on public.lili_study_rooms(owner_id);
create index if not exists lili_members_user_idx on public.lili_room_members(user_id);
create index if not exists lili_presence_room_idx on public.lili_focus_presence(room_id);
create index if not exists lili_visits_sender_idx on public.lili_visit_events(sender_id,created_at desc);
create index if not exists lili_visits_receiver_idx on public.lili_visit_events(receiver_id,created_at desc);
