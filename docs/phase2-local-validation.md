# Phase 2 local / isolated database validation

Recorded: 2026-08-30 (Asia/Shanghai)

This record intentionally excludes a paid Supabase development branch and any
production mutation. The migration was applied only to a fresh local PGlite
(WASM PostgreSQL) database with an isolated `auth` schema, RLS, authenticated
role, compatibility Presence table, legacy direct-write trigger, Dashboard
wrapper, and immutable FocusSession sample.

## 1. Reviewed migration content

The reviewed migration is
`supabase/migrations/20260830180000_lili_multi_device_focus_presence.sql`.

It does exactly the following:

1. Creates `lili_focus_device_presence`, keyed by `(user_id, device_id)`, for
   per-device liveness only.
2. Enables RLS and revokes all direct `anon`/`authenticated` table access.
3. Copies only currently fresh account Presence rows into the new device table;
   it never copies, changes, deletes, or synthesizes FocusSession facts.
4. Replaces the existing six-argument `lili_upsert_focus_presence` RPC. The
   RPC fences heartbeat sequence per device, then projects any-fresh-device
   online/working state back into the unchanged account table.
5. Replaces the legacy direct-write guard so an old client's idle heartbeat
   cannot clear a current device that is working.
6. Wraps the existing Dashboard function to add account/device-count metadata
   while preserving its current social-name and private-note wrapper chain.

The migration contains no `INSERT`, `UPDATE`, or `DELETE` against
`lili_focus_segments`.

## 2. Presence table relationship

| Data | `lili_focus_device_presence` | `lili_focus_presence` |
| --- | --- | --- |
| Key | `(user_id, device_id)` | `user_id` |
| Meaning | One installation's current liveness tuple | Compatibility projection for one account |
| Written by | Heartbeat RPC only | The same RPC after device aggregation; legacy guard for old clients |
| Online | This device was seen within two minutes | Any fresh device exists |
| Working | This device has an active local Session | Any fresh device is working |
| Focus duration | Never stored or calculated | Never stored or calculated |
| Consumers | Internal RPC/dashboard calculation | Existing dashboard, rooms, reaction, visit and taunt readers |

`lili_focus_segments` remains the immutable FocusSession-fact source. Existing
interval-union functions remain the sole source for day/week/report/leaderboard
duration.

## 3. Production rollback plan

Rollback is metadata-only and must run as one reviewed migration transaction:

1. Restore `lili_upsert_focus_presence` from
   `20260830143000_lili_presence_dashboard_beijing_repair.sql`.
2. Restore `lili_guard_legacy_direct_presence_write` from
   `20260830170000_lili_legacy_presence_write_compat.sql`.
3. Drop the new Dashboard wrapper, then rename
   `lili_dashboard_multidevice_base_20260830` back to `lili_dashboard`.
4. Revoke any remaining function access from the temporary wrapper and drop
   `lili_focus_device_presence`.

This discards only ephemeral device liveness. It neither restores nor changes
FocusSession rows, duration statistics, leaderboard inputs, reports, RLS on
existing tables, or user history. The old heartbeat signature is unchanged, so
the prior RPC remains callable by the new desktop build during rollback.

## 4. Lock assessment

- The migration does **not** run `ALTER TABLE` on existing
  `lili_focus_presence` or `lili_focus_segments`.
- `CREATE TABLE`, `CREATE INDEX`, RLS and grants lock only the new,
  initially empty device table.
- The fresh-row copy takes a normal read lock on existing account Presence rows
  and inserts into the new table. It does not rewrite or lock all rows for
  update.
- `CREATE OR REPLACE FUNCTION` and the Dashboard rename/recreate lock function
  metadata briefly. Existing database sessions may finish the prior function
  call, but no long table rewrite is performed.
- Runtime heartbeats serialize **only the same account** with a transaction
  advisory lock. Separate accounts do not block each other; two devices of the
  same account wait only for the small device-upsert plus aggregation section.

## 5. Existing data changes

At migration application time:

- Existing `lili_focus_presence` rows: read only; no row is updated or deleted.
- Fresh account Presence rows: copied into the new device-liveness table only.
- Existing `lili_focus_segments` / FocusSession facts: zero writes.

After release, normal heartbeat calls continue to update the existing
account-level Presence row, now with `any(device)` aggregation. New focus facts
gain a stable `device_id`; old facts retain their original empty device ID and
are not backfilled or modified.

## 6. Compatibility impact

- **Heartbeat:** no client or relay contract change. The existing RPC name and
  six parameters remain unchanged. Sequence acknowledgement is now
  device-scoped; the existing retry/adopt client path already handles it.
- **Dashboard:** adds `account_online`, `account_working`,
  `active_device_count` and `working_device_count` inside `me_presence`.
  Existing fields and the existing social-name/private-note wrapper remain.
- **Reaction, rooms, visits, taunts:** no query or client change. They keep
  reading `lili_focus_presence`, which remains the account-level projection.
- **Local UI:** an idle device can show “账号正在其他设备工作” yet retains the
  local “开始专注” button. A local working device shows a multi-device hint only
  when more than one device is working.

## 7. Completed local test results

### Isolated database acceptance

`supabase/tests/phase2_multidevice_presence_acceptance.mjs` passed against a
fresh PGlite PostgreSQL instance after applying the full migration.

Verified scenarios:

- A working + B idle: account remains online and working; idle B cannot clear A.
- A and B both working: two live device rows; account remains one continuous
  account-level live episode.
- A stops while B works: account remains working.
- Last working device stops: account becomes idle.
- Delayed same-device sequence: RPC returns `accepted=false` and the current
  device sequence; account state is unchanged.
- A heartbeat expires while B stays idle/fresh: account remains online but
  becomes non-working.
- Legacy direct idle write: mirrors to `legacy-direct` and cannot erase a
  modern working device.
- RLS/direct table access: device table RLS is enabled; `authenticated` has no
  direct read/insert privilege; authenticated RPC execution succeeds.
- Dashboard: compatibility payload includes account/device counts.
- History integrity: a seeded FocusSession fact table was byte-for-byte equal
  before and after migration execution.

### Application tests

- Full Python suite: **595 passed**.
- Existing FocusSession tests cover Beijing midnight slicing, overlapping-device
  union, exact hourly reconciliation and remote raw-fact merge.
- New tests cover device attribution, per-device raw sum/effective union/overlap
  diagnostics, duplicate remote segment idempotency, idle-local/working-remote
  button semantics, and multi-device local UI state.
- `git diff --check` and Python bytecode compilation passed.

## 8. Remaining hosted-Supabase-only checks

No paid branch is needed to complete local development or the checks above.
The following are deliberately deferred until a no-cost preview path is
available or an explicit pre-production authorization is given:

1. Real PostgREST request-path propagation for the legacy direct-write guard.
2. Hosted GoTrue JWT claim/RLS behavior and exact `SECURITY DEFINER` ownership.
3. Supabase database-advisor output after the migration, including hosted Data
   API exposure/default privileges.
4. Actual edge/Cloudflare/CloudBase relay round trips and connection-pool
   behavior under concurrent real-device heartbeats.
5. A pre/post production read-only fingerprint of the real account's FocusSession
   rows and effective day/week/report totals immediately around deployment.

These are hosted-platform integration checks, not missing implementation work.
They are the only reasons a remote Supabase branch would provide additional
evidence before production deployment; no branch has been created and no
production database operation has been performed.
