-- 搭子申请状态机与幂等写入。
-- 同一对用户仍然只保留一行关系，避免重复申请撞上唯一索引。

alter table public.lili_buddy_links
  drop constraint if exists lili_buddy_links_status_check;

alter table public.lili_buddy_links
  add constraint lili_buddy_links_status_check
  check (status in ('pending', 'accepted', 'declined', 'rejected', 'cancelled'));

drop function if exists public.lili_add_buddy_by_code(text);
create function public.lili_add_buddy_by_code(code text) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  me uuid := (select auth.uid());
  target uuid;
  link public.lili_buddy_links%rowtype;
begin
  if me is null then
    raise exception '请先登录';
  end if;

  select p.user_id
    into target
    from public.lili_profiles p
   where p.invite_code = upper(trim(code));

  if target is null then
    raise exception '没有找到这个搭子码';
  end if;
  if target = me then
    raise exception '不能添加自己';
  end if;

  -- 先复用已有行；数据库唯一索引仍是最后一道并发保护。
  select l.*
    into link
    from public.lili_buddy_links l
   where least(l.requester_id, l.addressee_id) = least(me, target)
     and greatest(l.requester_id, l.addressee_id) = greatest(me, target)
   for update;

  if link.id is null then
    insert into public.lili_buddy_links(requester_id, addressee_id, status, responded_at)
    values (me, target, 'pending', null)
    on conflict do nothing
    returning * into link;

    -- 两个客户端同时发起时，其中一个会走到这里；重新读取胜出的那一行。
    if link.id is null then
      select l.*
        into link
        from public.lili_buddy_links l
       where least(l.requester_id, l.addressee_id) = least(me, target)
         and greatest(l.requester_id, l.addressee_id) = greatest(me, target)
       for update;
    end if;
  end if;

  if link.status = 'accepted' then
    return jsonb_build_object(
      'state', 'accepted', 'relation_status', 'accepted',
      'request_id', link.id, 'user_id', target,
      'nickname', public.lili_owner_nickname(target),
      'owner_nickname', public.lili_owner_nickname(target),
      'message', '已经是你的搭子，无需重复添加。'
    );
  end if;

  if link.status = 'pending' and link.requester_id = me then
    return jsonb_build_object(
      'state', 'pending', 'request_direction', 'outgoing',
      'relation_status', 'pending', 'request_id', link.id, 'user_id', target,
      'nickname', public.lili_owner_nickname(target),
      'owner_nickname', public.lili_owner_nickname(target),
      'message', '搭子申请已经发送，等待对方回应。'
    );
  end if;

  if link.status = 'pending' then
    return jsonb_build_object(
      'state', 'incoming', 'request_direction', 'incoming',
      'relation_status', 'pending', 'request_id', link.id, 'user_id', target,
      'nickname', public.lili_owner_nickname(target),
      'owner_nickname', public.lili_owner_nickname(target),
      'message', '对方已经向你发送搭子申请，请到互动页处理。'
    );
  end if;

  -- 被拒绝或撤回的旧记录可以重新变成一条新的待处理申请。
  update public.lili_buddy_links
     set requester_id = me,
         addressee_id = target,
         status = 'pending',
         created_at = now(),
         responded_at = null
   where id = link.id
  returning * into link;

  return jsonb_build_object(
    'state', 'pending', 'request_direction', 'outgoing',
    'relation_status', 'pending', 'request_id', link.id, 'user_id', target,
    'nickname', public.lili_owner_nickname(target),
    'owner_nickname', public.lili_owner_nickname(target),
    'message', '搭子申请已经发送，等待对方回应。'
  );
end;
$$;

drop function if exists public.lili_lookup_buddy_by_code(text);
create function public.lili_lookup_buddy_by_code(code text) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  me uuid := (select auth.uid());
  target uuid;
  profile public.lili_profiles%rowtype;
  link public.lili_buddy_links%rowtype;
begin
  if me is null then
    raise exception '请先登录';
  end if;

  select p.* into profile
    from public.lili_profiles p
   where p.invite_code = upper(trim(code));
  if profile.user_id is null then
    raise exception '没有找到这个搭子码';
  end if;
  target := profile.user_id;

  if target <> me then
    select l.* into link
      from public.lili_buddy_links l
     where least(l.requester_id, l.addressee_id) = least(me, target)
       and greatest(l.requester_id, l.addressee_id) = greatest(me, target);
  end if;

  return jsonb_build_object(
    'state', case
      when target = me then 'self'
      when link.status = 'accepted' then 'accepted'
      when link.status = 'pending' and link.requester_id = me then 'pending'
      when link.status = 'pending' then 'incoming'
      else 'available'
    end,
    'request_direction', case
      when link.status = 'pending' and link.requester_id = me then 'outgoing'
      when link.status = 'pending' then 'incoming'
      else null
    end,
    'relation_status', link.status,
    'request_id', link.id,
    'user_id', target,
    'nickname', public.lili_owner_nickname(target),
    'owner_nickname', public.lili_owner_nickname(target),
    'outfit_key', profile.outfit_key
  );
end;
$$;

drop function if exists public.lili_respond_buddy(uuid, boolean);
create function public.lili_respond_buddy(request_id uuid, accept boolean) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  link public.lili_buddy_links%rowtype;
  next_status text;
begin
  select l.* into link
    from public.lili_buddy_links l
   where l.id = request_id;

  if link.id is null or link.addressee_id <> (select auth.uid()) then
    raise exception '搭子申请不存在或无权处理';
  end if;

  if link.status = 'accepted' and accept then
    return jsonb_build_object('status', 'accepted', 'request_id', link.id);
  end if;
  if link.status in ('declined', 'rejected') and not accept then
    return jsonb_build_object('status', 'rejected', 'request_id', link.id);
  end if;
  if link.status <> 'pending' then
    raise exception '搭子申请已经处理，不能重复操作';
  end if;

  next_status := case when accept then 'accepted' else 'rejected' end;
  update public.lili_buddy_links
     set status = next_status, responded_at = now()
   where id = link.id and addressee_id = (select auth.uid()) and status = 'pending'
  returning * into link;

  if link.id is null then
    raise exception '搭子申请已经被其他操作处理，请刷新互动页';
  end if;
  return jsonb_build_object('status', link.status, 'request_id', link.id);
end;
$$;

drop function if exists public.lili_cancel_buddy_request(uuid);
create function public.lili_cancel_buddy_request(request_id uuid) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  link public.lili_buddy_links%rowtype;
begin
  select l.* into link
    from public.lili_buddy_links l
   where l.id = request_id;
  if link.id is null or link.requester_id <> (select auth.uid()) then
    raise exception '搭子申请不存在或无权撤回';
  end if;
  if link.status = 'cancelled' then
    return jsonb_build_object('status', 'cancelled', 'request_id', link.id);
  end if;
  if link.status <> 'pending' then
    raise exception '只有待处理的搭子申请可以撤回';
  end if;

  update public.lili_buddy_links
     set status = 'cancelled', responded_at = now()
   where id = link.id and requester_id = (select auth.uid()) and status = 'pending'
  returning * into link;
  return jsonb_build_object('status', link.status, 'request_id', link.id);
end;
$$;

create function public.lili_buddy_requests() returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select jsonb_build_object(
    'incoming', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', l.id,
        'sender_id', l.requester_id,
        'nickname', public.lili_owner_nickname(l.requester_id),
        'owner_nickname', public.lili_owner_nickname(l.requester_id),
        'created_at', l.created_at
      ) order by l.created_at desc)
      from public.lili_buddy_links l
      where l.addressee_id = (select auth.uid()) and l.status = 'pending'
    ), '[]'::jsonb),
    'outgoing', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', l.id,
        'receiver_id', l.addressee_id,
        'nickname', public.lili_owner_nickname(l.addressee_id),
        'owner_nickname', public.lili_owner_nickname(l.addressee_id),
        'created_at', l.created_at
      ) order by l.created_at desc)
      from public.lili_buddy_links l
      where l.requester_id = (select auth.uid()) and l.status = 'pending'
    ), '[]'::jsonb)
  );
$$;

revoke execute on function public.lili_add_buddy_by_code(text) from public, anon;
revoke execute on function public.lili_lookup_buddy_by_code(text) from public, anon;
revoke execute on function public.lili_respond_buddy(uuid, boolean) from public, anon;
revoke execute on function public.lili_cancel_buddy_request(uuid) from public, anon;
revoke execute on function public.lili_buddy_requests() from public, anon;
grant execute on function public.lili_add_buddy_by_code(text) to authenticated;
grant execute on function public.lili_lookup_buddy_by_code(text) to authenticated;
grant execute on function public.lili_respond_buddy(uuid, boolean) to authenticated;
grant execute on function public.lili_cancel_buddy_request(uuid) to authenticated;
grant execute on function public.lili_buddy_requests() to authenticated;
