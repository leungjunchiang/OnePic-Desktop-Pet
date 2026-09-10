-- Legacy one-recipient cake calls must use the group-share flow now.
create or replace function public.lili_send_food_interaction(
  p_target uuid,
  p_kind text,
  p_payload jsonb default '{}'::jsonb
) returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  result uuid;
  clean_kind text := lower(trim(coalesce(p_kind, '')));
  clean_payload jsonb := coalesce(p_payload, '{}'::jsonb);
begin
  if clean_kind = 'food_cake' then
    raise exception '小蛋糕必须在补给站邀请 1～3 位好友一起分享';
  end if;
  if not public.lili_are_buddies(auth.uid(), p_target) then
    raise exception '只有已接受的搭子可以发送食物互动';
  end if;
  if not exists (
    select 1 from public.lili_profiles
    where user_id = p_target and allow_visits
  ) then
    raise exception '对方暂时不接受搭子互动';
  end if;
  if clean_kind not in ('food_coffee', 'food_milk_tea', 'food_tea') then
    raise exception '食物互动类型不受支持';
  end if;
  if jsonb_typeof(clean_payload) <> 'object' then
    raise exception '互动内容格式不正确';
  end if;
  if octet_length(clean_payload::text) > 2000 then
    raise exception '互动内容过长';
  end if;
  insert into public.lili_visit_events(sender_id, receiver_id, kind, payload)
  values (auth.uid(), p_target, clean_kind, clean_payload)
  returning id into result;
  return result;
end;
$$;

revoke execute on function public.lili_send_food_interaction(uuid, text, jsonb) from public, anon;
grant execute on function public.lili_send_food_interaction(uuid, text, jsonb) to authenticated;
