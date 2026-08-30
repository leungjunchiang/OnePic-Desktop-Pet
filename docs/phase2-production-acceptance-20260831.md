# Phase 2 hosted Supabase acceptance

Recorded: 2026-08-31 (Asia/Shanghai)

Project `zkgctfntrioffpifiggk` was used directly. No paid Branch was created.

## Applied migrations

1. `20260830180000_lili_multi_device_focus_presence.sql` — creates the
   per-device Presence table, updates the existing heartbeat RPC, protects the
   legacy direct-write path, and wraps Dashboard.
2. `20260831100000_lili_multi_device_dashboard_compat.sql` — keeps legacy
   `online/working/status/session_active/session_*` fields consistent with the
   fresh-device aggregate when all devices expire.
3. `20260831103000_lili_multi_device_presence_policy.sql` — adds the explicit
   authenticated deny-only RLS policy required by Advisor clarity.

All three migrations completed successfully. None contains DML against
`lili_focus_segments`, and none changes its schema.

## Hosted schema/RLS checks

- `lili_focus_device_presence` exists with primary key `(user_id, device_id)`.
- RLS is enabled; the explicit policy is
  `lili_focus_device_presence_no_direct_access` with `USING (false)` and
  `WITH CHECK (false)`.
- `authenticated` has no direct SELECT/INSERT/UPDATE/DELETE privilege on the
  device table; an actual `SET ROLE authenticated` read returned PostgreSQL
  `42501 permission denied`.
- The authenticated Heartbeat RPC still executes successfully.
- Heartbeat and Dashboard remain `SECURITY DEFINER` with `search_path=''`.
- The existing legacy Presence triggers remain installed.

## Hosted multi-device transaction results

The synthetic Presence writes were wrapped in explicit transactions and rolled
back. No synthetic device rows remain.

| Scenario | Account result |
| --- | --- |
| A working, B idle | `account_online=true`, `account_working=true`, working devices `1` |
| A and B working | `account_online=true`, `account_working=true`, working devices `2` |
| A idle, B working | `account_online=true`, `account_working=true`, working devices `1`; A device is not working |
| B expired, A idle/fresh | `account_online=true`, `account_working=false`, working devices `0` |
| A and B both expired | `online=false`, `working=false`, `status=offline`, `session_active=false`, session cleared, account/device counts `0` |
| Delayed sequence | `accepted=false`; current device state remains working |

The first hosted expiry check deliberately left A fresh and returned online
idle; that was corrected in the second check by expiring both A and B. The
corrected result is the row above.

## FocusSession integrity

Before the first migration:

- Production: 225 rows; full-row fingerprint
  `5369ed3784a0984067a71ff840d021f3`.
- Active account: 160 rows, 51 sessions, 0 invalid intervals; full-row
  fingerprint `d20997698ab95d9009ebf9a6e7aad1f9`.
- Non-empty `device_id` facts: 0.

After the migrations:

- Production: 225 rows, 71 sessions, 0 invalid intervals.
- Active account: 160 rows, 51 sessions, 0 invalid intervals.
- Non-empty `device_id` facts: 0.
- Post stable-fact fingerprint over
  `user_id/session_id/segment_id/start_at/end_at/device_id`:
  production `bcc226fc5c2901a15143c1fe9be13e18`; account
  `64b646ee0d52ab6db49d9254594e0d13`.
- No synthetic FocusSession rows were created.

The initial full-row hash is intentionally not treated as an immutable fact
fingerprint because it included the volatile `updated_at` column. It changed
after the deployment while the existing client repeatedly called the already
deployed `lili_sync_focus_segments`; that RPC performs an idempotent upsert and
refreshes `updated_at` for existing rows. Hosted API logs show successful
`lili_sync_focus_segments` calls at `16:44:13`, `16:44:31`, `16:44:48`, and
later intervals. The migration statement itself contains no segment DML. The
stable fact fields, row counts, session counts, invalid-interval count, device
values, and sampled historical intervals remained unchanged.

The same-window server effective today and week values were both `0` seconds
at the capture instant; this was after the Beijing date/week boundary and is
not compared with the earlier local baseline from the previous calendar
window.

## Advisor

After all three migrations:

- Security: 45 warnings, all pre-existing/intentional classes; no new device
  table warning remains. The two Phase 2-related entries are the expected
  authenticated execution of the existing public Dashboard and Heartbeat
  SECURITY DEFINER RPCs.
- Performance: 23 notices; no Phase 2-specific notice.

The migration did not change unrelated historical Advisor findings.

## Hosted-only limitations

- The Relay path was not simulated end-to-end; Supabase Realtime/Relay
  behavior behind the production network still needs an operational soak.
- The direct SQL checks exercised hosted PostgreSQL, request claims, actual
  RLS role privileges, PostgREST-visible functions, and hosted Advisor output.
- No paid Preview Branch was needed for the local PGlite acceptance or the
  rolled-back hosted Presence transactions.
