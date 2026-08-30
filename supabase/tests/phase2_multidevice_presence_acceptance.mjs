// Local isolated PostgreSQL acceptance for Phase 2 multi-device presence.
//
// Run with a temporary PGlite module, for example:
//   PGLITE_MODULE=<...>/dist/index.js node phase2_multidevice_presence_acceptance.mjs
//
// This never connects to Supabase. It creates a fresh in-memory database,
// installs the production migration, and exercises the RPC as two devices.

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const modulePath = process.env.PGLITE_MODULE;
if (!modulePath) {
  throw new Error("PGLITE_MODULE must point to @electric-sql/pglite/dist/index.js");
}
const { PGlite } = await import(pathToFileURL(path.resolve(modulePath)).href);
const migration = await readFile(
  path.join(here, "..", "migrations", "20260830180000_lili_multi_device_focus_presence.sql"),
  "utf8",
);
const dashboardCompatMigration = await readFile(
  path.join(here, "..", "migrations", "20260831100000_lili_multi_device_dashboard_compat.sql"),
  "utf8",
);
const devicePresencePolicyMigration = await readFile(
  path.join(here, "..", "migrations", "20260831103000_lili_multi_device_presence_policy.sql"),
  "utf8",
);
const db = await PGlite.create();

const userId = "11111111-1111-1111-1111-111111111111";
const otherUserId = "22222222-2222-2222-2222-222222222222";

async function query(sql, params = []) {
  return db.query(sql, params);
}

async function scalar(sql, params = []) {
  const result = await query(sql, params);
  const row = result.rows[0] || {};
  return row[Object.keys(row)[0]];
}

async function asUser(id = userId, requestPath = "/rest/v1/rpc/lili_upsert_focus_presence") {
  await query("select set_config('request.jwt.claim.sub', $1, false)", [id]);
  await query("select set_config('request.path', $1, false)", [requestPath]);
}

async function heartbeat(deviceId, sequence, working, sessionId = null) {
  const startedAt = working ? "2026-08-30T09:00:00+08:00" : null;
  const result = await query(
    `select public.lili_upsert_focus_presence(
       $1::boolean, $2::boolean, $3::text, $4::timestamptz, $5::text, $6::bigint
     ) as payload`,
    [working, working, sessionId, startedAt, deviceId, sequence],
  );
  return result.rows[0].payload;
}

async function accountRow() {
  const result = await query(
    `select working, session_active, session_id, session_started_at, device_id,
            presence_sequence, last_seen
       from public.lili_focus_presence where user_id = $1::uuid`,
    [userId],
  );
  return result.rows[0];
}

try {
  await db.exec(`
    create schema auth;
    create role anon;
    create role authenticated;
    create role service_role;
    create table auth.users (id uuid primary key);
    create or replace function auth.uid()
    returns uuid language sql stable as $$
      select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
    $$;
    grant usage on schema auth to authenticated;
    grant execute on function auth.uid() to authenticated;

    create table public.lili_study_rooms (id uuid primary key);
    create table public.lili_room_members (room_id uuid, user_id uuid);
    create table public.lili_focus_presence (
      user_id uuid primary key references auth.users(id) on delete cascade,
      working boolean not null default false,
      session_started_at timestamptz,
      focus_date date not null default current_date,
      today_seconds integer not null default 0,
      outfit_key text not null default '',
      room_id uuid references public.lili_study_rooms(id) on delete set null,
      last_seen timestamptz not null default now(),
      updated_at timestamptz not null default now(),
      quick_status text,
      quick_status_expires_at timestamptz,
      session_active boolean not null default false,
      work_state text not null default 'idle',
      pause_reason text,
      device_id text not null default '',
      device_claim boolean not null default false,
      session_id text,
      presence_sequence bigint not null default 0,
      constraint lili_focus_presence_live_state_check check (
        (working and session_active and session_id is not null and session_started_at is not null)
        or (not working and not session_active and session_id is null and session_started_at is null)
      )
    );
    alter table public.lili_focus_presence enable row level security;

    create table public.lili_focus_segments (
      user_id uuid not null,
      segment_id text not null,
      session_id text not null,
      start_at timestamptz not null,
      end_at timestamptz not null,
      device_id text not null default '',
      primary key (user_id, segment_id)
    );
    insert into auth.users(id) values ('${userId}'), ('${otherUserId}');
    insert into public.lili_focus_segments(user_id, segment_id, session_id, start_at, end_at, device_id)
    values ('${userId}', 'history-1', 'history-session', '2026-08-29T15:30:00+08:00', '2026-08-29T16:00:00+08:00', '');

    create or replace function public.lili_touch_presence_server_timestamp()
    returns trigger language plpgsql security definer set search_path = '' as $$
    begin
      new.last_seen := now();
      new.updated_at := now();
      return new;
    end;
    $$;
    create or replace function public.lili_guard_legacy_direct_presence_write()
    returns trigger language plpgsql security definer set search_path = '' as $$
    begin
      return new;
    end;
    $$;
    create trigger lili_presence_legacy_direct_guard
    before insert or update on public.lili_focus_presence
    for each row execute function public.lili_guard_legacy_direct_presence_write();
    create trigger lili_presence_server_timestamp
    before insert or update on public.lili_focus_presence
    for each row execute function public.lili_touch_presence_server_timestamp();

    create or replace function public.lili_dashboard()
    returns jsonb language sql stable security definer set search_path = '' as $$
      select jsonb_build_object('me_presence', jsonb_build_object())
    $$;
    grant select, insert, update on public.lili_focus_presence to authenticated;
  `);

  const historyBefore = await query(
    "select * from public.lili_focus_segments order by user_id, segment_id",
  );
  await db.exec(migration);
  await db.exec(dashboardCompatMigration);
  await db.exec(devicePresencePolicyMigration);

  assert.equal(
    await scalar(
      "select relrowsecurity from pg_class where oid = 'public.lili_focus_device_presence'::regclass",
    ),
    true,
    "device presence must enable RLS",
  );
  assert.equal(
    await scalar(
      "select has_table_privilege('authenticated', 'public.lili_focus_device_presence', 'insert')",
    ),
    false,
    "authenticated must not receive direct device-table INSERT",
  );
  assert.equal(
    await scalar(
      "select has_function_privilege('authenticated', 'public.lili_upsert_focus_presence(boolean,boolean,text,timestamptz,text,bigint)', 'execute')",
    ),
    true,
    "authenticated must retain the heartbeat RPC",
  );

  await asUser();
  await query("set role authenticated");
  await assert.rejects(
    () => query("select * from public.lili_focus_device_presence"),
    /permission denied/i,
    "authenticated must not read device-presence rows directly",
  );
  const first = await heartbeat("device-a", 1, true, "session-a");
  await query("reset role");
  assert.equal(first.accepted, true);
  assert.equal(first.account_working, true);
  assert.equal(first.working_device_count, 1);
  const firstAccount = await accountRow();
  assert.equal(firstAccount.working, true);
  assert.equal(firstAccount.session_id, "session-a");

  const idleB = await heartbeat("device-b", 1, false);
  assert.equal(idleB.account_working, true, "idle B must not clear working A");
  assert.equal(idleB.active_device_count, 2);
  assert.equal(idleB.working_device_count, 1);
  assert.equal((await accountRow()).session_id, "session-a");

  const workingB = await heartbeat("device-b", 2, true, "session-b");
  assert.equal(workingB.account_working, true);
  assert.equal(workingB.working_device_count, 2, "two devices may work together");
  const overlappingAccount = await accountRow();
  assert.equal(overlappingAccount.session_id, "session-a");
  assert.equal(
    String(overlappingAccount.session_started_at).slice(0, 19),
    String(firstAccount.session_started_at).slice(0, 19),
    "representative switching must not split the account live episode",
  );

  const stale = await heartbeat("device-b", 2, false);
  assert.equal(stale.accepted, false, "delayed same-device heartbeat must be rejected");
  assert.equal(stale.sequence, 2, "client can adopt the server device sequence");
  assert.equal((await accountRow()).working, true, "rejected packet cannot clear A");

  const stoppedA = await heartbeat("device-a", 2, false);
  assert.equal(stoppedA.account_working, true, "stopping A keeps working B alive");
  assert.equal(stoppedA.working_device_count, 1);
  const stoppedB = await heartbeat("device-b", 3, false);
  assert.equal(stoppedB.account_working, false, "account ends only after last device stops");
  assert.equal((await accountRow()).working, false);

  await heartbeat("device-a", 3, true, "session-a-2");
  await query(
    "update public.lili_focus_device_presence set last_seen = now() - interval '3 minutes' where user_id = $1::uuid and device_id = 'device-a'",
    [userId],
  );
  const freshIdleB = await heartbeat("device-b", 4, false);
  assert.equal(freshIdleB.account_online, true, "fresh idle B keeps the account online");
  assert.equal(freshIdleB.account_working, false, "expired A no longer keeps account working");
  assert.equal(freshIdleB.active_device_count, 1);

  await heartbeat("device-a", 4, true, "session-a-3");
  await asUser(userId, "/rest/v1/lili_focus_presence");
  await query(
    "update public.lili_focus_presence set working=false, session_active=false, session_id=null, session_started_at=null where user_id=$1::uuid",
    [userId],
  );
  const legacyAccount = await accountRow();
  assert.equal(legacyAccount.working, true, "legacy idle direct write cannot erase modern A");
  assert.equal(
    await scalar(
      "select working from public.lili_focus_device_presence where user_id=$1::uuid and device_id='legacy-direct'",
      [userId],
    ),
    false,
  );

  await asUser();
  const dashboard = await scalar("select public.lili_dashboard()");
  assert.equal(dashboard.me_presence.account_online, true);
  assert.equal(dashboard.me_presence.account_working, true);
  assert.equal(dashboard.me_presence.online, true);
  assert.equal(dashboard.me_presence.working, true);
  assert.equal(dashboard.me_presence.session_active, true);
  assert.equal(dashboard.me_presence.status, "focus");
  assert.ok(dashboard.me_presence.active_device_count >= 1);
  assert.ok(dashboard.me_presence.working_device_count >= 1);

  await query(
    "update public.lili_focus_device_presence set last_seen = now() - interval '3 minutes' where user_id = $1::uuid",
    [userId],
  );
  const expiredDashboard = await scalar("select public.lili_dashboard()");
  assert.equal(expiredDashboard.me_presence.account_online, false);
  assert.equal(expiredDashboard.me_presence.account_working, false);
  assert.equal(expiredDashboard.me_presence.online, false);
  assert.equal(expiredDashboard.me_presence.working, false);
  assert.equal(expiredDashboard.me_presence.session_active, false);
  assert.equal(expiredDashboard.me_presence.status, "offline");
  assert.equal(expiredDashboard.me_presence.session_seconds, 0);

  const historyAfter = await query(
    "select * from public.lili_focus_segments order by user_id, segment_id",
  );
  assert.deepEqual(historyAfter.rows, historyBefore.rows, "migration must not modify FocusSession facts");
  console.log("phase2 local PGlite acceptance: passed");
} finally {
  await db.close();
}
