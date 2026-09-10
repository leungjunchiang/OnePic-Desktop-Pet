-- An empty owner_nickname is absence, not an intentional display name.
-- Without the empty-string guard it wins COALESCE and erases the public
-- fallback in buddy, room, visit, and leaderboard projections.

create or replace function public.lili_owner_nickname(p_user_id uuid)
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(
    nullif(
      nullif(
        nullif(left(trim(p.owner_nickname), 24), ''),
        '六毛'
      ),
      '六毛搭子'
    ),
    nullif(
      nullif(left(trim(p.nickname), 24), ''),
      '六毛搭子'
    ),
    '搭子'
  )
  from public.lili_profiles p
  where p.user_id = p_user_id;
$$;

revoke execute on function public.lili_owner_nickname(uuid)
  from public, anon, authenticated;
