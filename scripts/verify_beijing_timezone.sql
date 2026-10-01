-- Real database types and UTC/Beijing semantics; no personal records read.
-- Synthetic account and facts are rolled back. Only this transaction's timezone changes.
begin;
set local time zone 'UTC';
do $$
declare owner uuid:=gen_random_uuid(); col record; day date:='2026-09-24'; found_type text;
  ts timestamptz:='2026-10-01T03:36:00Z'; payload jsonb;
begin
  if exists(select 1 from information_schema.columns where table_schema='public'
      and table_name like 'lili\_%' escape '\' and data_type='timestamp without time zone'
      and (column_name like '%\_at' escape '\' or column_name in ('last_seen','session_start','session_end','checkpoint'))) then
    raise exception 'Business instant stored in naive timestamp column';
  end if;
  for col in select * from (values
    ('lili_focus_segments','start_at'),('lili_focus_segments','end_at'),
    ('lili_focus_presence','last_seen'),('lili_focus_presence','session_started_at'),
    ('lili_focus_device_presence','last_seen'),('lili_discipline_events','occurred_at'),
    ('lili_visit_events','created_at'),('lili_visit_events','responded_at'),
    ('lili_coaching_cases','created_at'),('lili_coaching_cases','updated_at'),
    ('lili_coaching_cases','accepted_at'),('lili_coaching_cases','closed_at')) v(tbl,field)
  loop
    select data_type into found_type from information_schema.columns
      where table_schema='public' and table_name=col.tbl and column_name=col.field;
    if found_type is distinct from 'timestamp with time zone' then
      raise exception 'Wrong instant type %.%: %',col.tbl,col.field,found_type;
    end if;
  end loop;
  if to_char(ts at time zone 'Asia/Shanghai','HH24:MI')<>'11:36'
     or to_jsonb(ts)::text not like '%+00:00%' then raise exception 'UTC server shape / Beijing clock failed'; end if;
  if ('2026-09-30T18:30:00Z'::timestamptz at time zone 'Asia/Shanghai')::date<>'2026-10-01'::date
     or ('2026-09-30T19:36:00Z'::timestamptz at time zone 'Asia/Shanghai')::date<>'2026-10-01'::date
     or extract(isodow from '2026-10-04T16:30:00Z'::timestamptz at time zone 'Asia/Shanghai')<>1 then
    raise exception 'Beijing natural day/exemption/week failed'; end if;
  if extract(epoch from ('2026-10-01T01:16:00Z'::timestamptz - '2026-10-01T09:00:00+08:00'::timestamptz))/60<>16 then
    raise exception '09:00 wall clock / lateness failed'; end if;
  insert into auth.users(id,raw_user_meta_data) values(owner,'{}');
  insert into public.lili_focus_segments(user_id,segment_id,session_id,device_id,start_at,end_at) values
    (owner,'tz-pre-six','before','pc1',((day-1)::text||'T21:55:00Z')::timestamptz,((day-1)::text||'T22:20:00Z')::timestamptz);
  if public.lili_actual_work_start_clock(owner,day) is not null then raise exception 'Cross-six session manufactured start'; end if;
  insert into public.lili_focus_segments(user_id,segment_id,session_id,device_id,start_at,end_at) values
    (owner,'tz-real','real','pc1',(day::text||'T00:52:00Z')::timestamptz,(day::text||'T01:52:00Z')::timestamptz);
  -- A legacy machine's US timezone metadata must not move the calendar or clock.
  insert into public.lili_discipline_events(user_id,id,event_type,event_date,occurred_at,metadata) values
    (owner,gen_random_uuid(),'start_work',day,(day::text||'T01:16:00Z')::timestamptz,
      '{"actual_start_source":"explicit_focus_start","utc_offset_minutes":-420}');
  if public.lili_actual_work_start_clock(owner,day)<>'08:52' then raise exception 'Beijing actual start / legacy offset failed'; end if;
  perform set_config('TimeZone','Asia/Tokyo',true);
  if public.lili_actual_work_start_clock(owner,day)<>'08:52' then raise exception 'Server session timezone leaked into start'; end if;
  if has_function_privilege('authenticated','public.lili_actual_work_start_clock(uuid,date)','execute') then
    raise exception 'Internal helper became a public RPC'; end if;
end $$;
rollback;
