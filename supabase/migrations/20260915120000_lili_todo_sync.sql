-- Isolated cross-device Todo synchronization.
--
-- This migration owns only public.todos and its two Todo RPCs.  The table is
-- intentionally separate from every existing account, device, presence, and
-- focus table.  Deletion is a soft-delete so an offline device cannot revive
-- an item merely because it missed a hard-delete event.

create table if not exists public.todos (
  id uuid primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  title text not null default '未命名事项',
  content text not null default '',
  status text not null default 'pending'
    check (status in ('pending', 'completed', 'deleted')),
  priority smallint,
  due_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz,
  deleted_at timestamptz,
  updated_by_device_id text,
  metadata jsonb not null default '{}'::jsonb,
  check (priority is null or priority between 1 and 3),
  check (status <> 'deleted' or deleted_at is not null)
);

create index if not exists todos_user_updated_id_idx
  on public.todos (user_id, updated_at asc, id asc);

create index if not exists todos_user_active_idx
  on public.todos (user_id, updated_at desc)
  where deleted_at is null;

alter table public.todos enable row level security;

revoke all on table public.todos from public, anon, authenticated;
grant select on table public.todos to authenticated;

drop policy if exists todos_select_own on public.todos;
create policy todos_select_own
  on public.todos
  for select
  to authenticated
  using ((select auth.uid()) = user_id);

drop policy if exists todos_insert_own on public.todos;
create policy todos_insert_own
  on public.todos
  for insert
  to authenticated
  with check ((select auth.uid()) = user_id);

drop policy if exists todos_update_own on public.todos;
create policy todos_update_own
  on public.todos
  for update
  to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

comment on table public.todos is
  'The isolated Lili Todo synchronization domain; deleted rows are retained as tombstones.';

create or replace function public.lili_todo_pull(
  p_after_updated_at timestamptz default null,
  p_after_id uuid default null,
  p_limit integer default 100
)
returns setof public.todos
language sql
stable
security invoker
set search_path = public, pg_temp
as $function$
  select t.*
    from public.todos as t
   where t.user_id = (select auth.uid())
     and (
       p_after_updated_at is null
       or (t.updated_at, t.id) > (
         p_after_updated_at,
         coalesce(p_after_id, '00000000-0000-0000-0000-000000000000'::uuid)
       )
     )
   order by t.updated_at asc, t.id asc
   limit least(greatest(coalesce(p_limit, 100), 1), 100);
$function$;

revoke execute on function public.lili_todo_pull(timestamptz, uuid, integer)
  from public, anon;
grant execute on function public.lili_todo_pull(timestamptz, uuid, integer)
  to authenticated;

create or replace function public.lili_todo_upsert(p_todo jsonb)
returns setof public.todos
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $function$
declare
  v_user_id uuid := (select auth.uid());
  v_payload_user_id uuid;
  v_id uuid;
  v_title text;
  v_content text;
  v_status text;
  v_priority smallint;
  v_due_at timestamptz;
  v_created_at timestamptz;
  v_updated_at timestamptz;
  v_completed_at timestamptz;
  v_deleted_at timestamptz;
  v_device_id text;
  v_metadata jsonb;
  v_row public.todos%rowtype;
begin
  if v_user_id is null then
    raise exception using
      errcode = '42501',
      message = 'Todo 同步需要登录。';
  end if;

  if p_todo is null or jsonb_typeof(p_todo) <> 'object' then
    raise exception using
      errcode = '22023',
      message = 'Todo 数据格式无效。';
  end if;

  begin
    v_id := nullif(btrim(p_todo ->> 'id'), '')::uuid;
    if p_todo ? 'user_id' and nullif(btrim(p_todo ->> 'user_id'), '') is not null then
      v_payload_user_id := (p_todo ->> 'user_id')::uuid;
    end if;
    v_created_at := coalesce(nullif(btrim(p_todo ->> 'created_at'), '')::timestamptz, now());
    v_updated_at := coalesce(nullif(btrim(p_todo ->> 'updated_at'), '')::timestamptz, now());
    v_due_at := nullif(btrim(p_todo ->> 'due_at'), '')::timestamptz;
    v_completed_at := nullif(btrim(p_todo ->> 'completed_at'), '')::timestamptz;
    v_deleted_at := nullif(btrim(p_todo ->> 'deleted_at'), '')::timestamptz;
  exception
    when invalid_text_representation or datetime_field_overflow then
      raise exception using
        errcode = '22007',
        message = 'Todo 日期或 UUID 格式无效。';
  end;

  if v_id is null then
    raise exception using
      errcode = '22023',
      message = 'Todo UUID 不能为空。';
  end if;
  if v_payload_user_id is not null and v_payload_user_id <> v_user_id then
    raise exception using
      errcode = '42501',
      message = 'Todo 所属账号不匹配。';
  end if;

  v_title := left(coalesce(nullif(btrim(p_todo ->> 'title'), ''), '未命名事项'), 240);
  v_content := left(coalesce(p_todo ->> 'content', ''), 4000);
  v_status := lower(coalesce(nullif(btrim(p_todo ->> 'status'), ''), 'pending'));
  if v_status not in ('pending', 'completed', 'deleted') then
    raise exception using
      errcode = '22023',
      message = 'Todo 状态无效。';
  end if;

  if nullif(btrim(p_todo ->> 'priority'), '') is not null then
    begin
      v_priority := (p_todo ->> 'priority')::smallint;
    exception
      when invalid_text_representation or numeric_value_out_of_range then
        raise exception using
          errcode = '22023',
          message = 'Todo 优先级无效。';
    end;
    if v_priority not between 1 and 3 then
      raise exception using
        errcode = '22023',
        message = 'Todo 优先级无效。';
    end if;
  else
    v_priority := null;
  end if;

  v_metadata := coalesce(p_todo -> 'metadata', '{}'::jsonb);
  if jsonb_typeof(v_metadata) <> 'object' then
    raise exception using
      errcode = '22023',
      message = 'Todo metadata 格式无效。';
  end if;

  if v_status = 'deleted' then
    v_deleted_at := coalesce(v_deleted_at, v_updated_at, now());
  else
    v_deleted_at := null;
  end if;
  v_device_id := left(nullif(btrim(p_todo ->> 'updated_by_device_id'), ''), 128);

  if exists (
    select 1
      from public.todos as existing
     where existing.id = v_id
       and existing.user_id <> v_user_id
  ) then
    raise exception using
      errcode = '42501',
      message = 'Todo UUID 已属于其他账号。';
  end if;

  insert into public.todos (
    id,
    user_id,
    title,
    content,
    status,
    priority,
    due_at,
    created_at,
    updated_at,
    completed_at,
    deleted_at,
    updated_by_device_id,
    metadata
  ) values (
    v_id,
    v_user_id,
    v_title,
    v_content,
    v_status,
    v_priority,
    v_due_at,
    v_created_at,
    v_updated_at,
    v_completed_at,
    v_deleted_at,
    v_device_id,
    v_metadata
  )
  on conflict (id) do update
     set title = excluded.title,
         content = excluded.content,
         status = excluded.status,
         priority = excluded.priority,
         due_at = excluded.due_at,
         updated_at = excluded.updated_at,
         completed_at = excluded.completed_at,
         deleted_at = excluded.deleted_at,
         updated_by_device_id = excluded.updated_by_device_id,
         metadata = excluded.metadata
   where public.todos.user_id = v_user_id
     and public.todos.updated_at <= excluded.updated_at
  returning * into v_row;

  if not found then
    -- The remote row was newer.  Returning it makes the client converge and
    -- lets it drop only this Todo's obsolete queue entry.
    select current_row.*
      into v_row
      from public.todos as current_row
     where current_row.id = v_id
       and current_row.user_id = v_user_id;
  end if;
  if not found then
    raise exception using
      errcode = '42501',
      message = 'Todo 写入未被当前账号确认。';
  end if;

  return next v_row;
  return;
end;
$function$;

revoke execute on function public.lili_todo_upsert(jsonb)
  from public, anon;
grant execute on function public.lili_todo_upsert(jsonb)
  to authenticated;

comment on function public.lili_todo_upsert(jsonb) is
  'Idempotent, account-scoped Todo upsert with per-row last-write-wins and soft deletion.';
