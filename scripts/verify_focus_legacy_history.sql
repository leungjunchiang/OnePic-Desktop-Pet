-- Verify the real RPC with two disposable accounts. Roll back all test rows.
begin;
do $$
declare
  owner_id uuid := gen_random_uuid();
  other_id uuid := gen_random_uuid();
  old_day date := (now() at time zone 'Asia/Shanghai')::date - 800;
  recent_day date := old_day + 1;
  last_seen timestamptz := now() - interval '1 day';
  page jsonb;
  anchor timestamptz;
begin
  if has_function_privilege('anon',
    'public.lili_focus_legacy_history_page(text,date,timestamptz,timestamptz,timestamptz,date,integer)',
    'EXECUTE') or has_table_privilege('authenticated',
    'public.lili_focus_legacy_compatibility_ledger', 'SELECT') then
    raise exception 'legacy history grants are broader than the account RPC';
  end if;

  insert into auth.users (id, raw_user_meta_data)
    values (owner_id, '{}'), (other_id, '{}');
  insert into public.lili_focus_legacy_compatibility_ledger
    (user_id, focus_date, legacy_seconds, legacy_source, last_seen_at)
    values (owner_id, old_day, 28800, 'verification', last_seen),
           (owner_id, recent_day, 3600, 'verification', last_seen),
           (other_id, old_day, 123, 'verification', last_seen);

  perform set_config('request.jwt.claim.sub', owner_id::text, true);
  execute 'set local role authenticated';

  page := public.lili_focus_legacy_history_page('full', p_limit => 1);
  anchor := (page->>'scan_anchor')::timestamptz;
  if page->>'account_id' is distinct from owner_id::text
      or page->>'has_more' is distinct from 'true'
      or jsonb_array_length(page->'days') is distinct from 1
      or (page#>>'{days,0,focus_date}')::date is distinct from old_day
      or page#>>'{days,0,legacy_seconds}' is distinct from '28800'
      or anchor is null then
    raise exception 'first page failed to return the account''s old daily total';
  end if;
  page := public.lili_focus_legacy_history_page('full',
    p_after_date => (page->>'next_after_date')::date,
    p_scan_anchor => anchor, p_limit => 1);
  if page->>'has_more' is distinct from 'false'
      or jsonb_array_length(page->'days') is distinct from 1
      or (page#>>'{days,0,focus_date}')::date is distinct from recent_day
      or page#>>'{days,0,legacy_seconds}' is distinct from '3600' then
    raise exception 'full history did not resume at the saved cursor';
  end if;

  page := public.lili_focus_legacy_history_page('delta',
    p_scan_anchor => anchor, p_since => last_seen, p_limit => 1);
  if page->>'has_more' is distinct from 'true'
      or (page#>>'{days,0,focus_date}')::date is distinct from old_day then
    raise exception 'delta did not include the inclusive watermark';
  end if;
  page := public.lili_focus_legacy_history_page('delta',
    p_scan_anchor => anchor, p_since => last_seen,
    p_after_updated_at => (page->>'next_updated_at')::timestamptz,
    p_after_updated_date => (page->>'next_updated_date')::date, p_limit => 1);
  if page->>'has_more' is distinct from 'false'
      or jsonb_array_length(page->'days') is distinct from 1
      or (page#>>'{days,0,focus_date}')::date is distinct from recent_day then
    raise exception 'delta cursor did not distinguish days at the same timestamp';
  end if;

  perform set_config('request.jwt.claim.sub', other_id::text, true);
  page := public.lili_focus_legacy_history_page('full');
  if page->>'account_id' is distinct from other_id::text
      or jsonb_array_length(page->'days') is distinct from 1
      or page#>>'{days,0,legacy_seconds}' is distinct from '123' then
    raise exception 'legacy history returned a different account''s data';
  end if;
end $$;
rollback;
