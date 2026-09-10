# Phase 2 multi-device focus baseline and design

Recorded: 2026-08-30 (Asia/Shanghai)

This document freezes the pre-change state for the independent Phase 2 release.
The production database was queried read-only. No production DDL or data write
was performed while collecting this baseline.

## Release and repository baseline

- Branch: `codex/phase2-multidevice-focus`
- Starting commit: `33e6127` (`Document phase one release verification`)
- Starting application version: `0.23.182`
- Latest GitHub Release: `v0.23.182`
- Working tree before Phase 2 product changes: clean
- The user's existing `F:\Lili\Lili.exe` process was left running and was not
  stopped, restarted, or used to inject a synthetic focus session.

## Production schema baseline

- Supabase project: `zkgctfntrioffpifiggk` (ACTIVE_HEALTHY, PostgreSQL 17.6)
- `lili_focus_presence` has one row per account: primary key `(user_id)`.
- `lili_upsert_focus_presence` updates that row with `ON CONFLICT (user_id)`.
- A second device therefore overwrites the first device's online/working tuple.
- `device_id` and `presence_sequence` exist, but currently implement a
  single-device lease rather than simultaneous per-device presence.
- `lili_focus_segments` already stores immutable interval facts with primary key
  `(user_id, segment_id)` and includes `session_id`, `start_at`, `end_at`, and
  `device_id`.
- Canonical today/week, dashboard, work-report and leaderboard functions already
  calculate interval union from `lili_focus_segments`; they do not add raw
  overlapping durations.
- Heartbeat cadence remains approximately 15 seconds.

Production-wide read-only counts at baseline:

- Presence rows: 6
- Focus segment rows: 224
- Accounts with focus segments: 4
- Invalid segments (`end_at <= start_at`): 0

## Current-account immutable data baseline

The active account ID is intentionally omitted from this committed document.

- Focus segment rows: 159
- Distinct Session IDs: 51
- Invalid segments: 0
- Existing rows with a non-empty `device_id`: 0
- Effective today focus: 20,997 seconds
- Effective current-week focus: 185,975 seconds
- All-time raw closed duration: 689,671 seconds

History fingerprints sampled before modification:

| Segment ID | Session ID | Start (UTC) | End (UTC) |
| --- | --- | --- | --- |
| `33b67c1186c0485a9452154d83366438:10392` | `33b67c1186c0485a9452154d83366438` | `2026-08-30 12:24:31.850075+00` | `2026-08-30 14:52:47.850075+00` |
| `33b67c1186c0485a9452154d83366438:1496` | `33b67c1186c0485a9452154d83366438` | `2026-08-30 11:53:34.682820+00` | `2026-08-30 12:18:28.682820+00` |
| `33b67c1186c0485a9452154d83366438:2` | `33b67c1186c0485a9452154d83366438` | `2026-08-30 09:43:34.858842+00` | `2026-08-30 09:43:36.858842+00` |
| `c6d2792c6fd94baf89424f19a2d0bee4:10605` | `c6d2792c6fd94baf89424f19a2d0bee4` | `2026-08-30 01:33:30.606911+00` | `2026-08-30 03:14:14.606911+00` |
| `legacy:288` | `legacy:288` | `2026-08-29 16:14:40.867574+00` | `2026-08-29 17:30:41.867574+00` |

The same local closed-fact ledger contained 159 parsed segments and produced
the same 20,997-second day and 185,975-second week interval-union totals.

## Existing behavior that must remain unchanged

- New focus time is synchronized as closed Session facts, not cumulative
  minute increments.
- `(user_id, segment_id)` makes repeated segment synchronization idempotent.
- The shared interval aggregator intersects facts with the requested Beijing
  calendar window, removes invalid facts, sorts, merges overlaps, and sums the
  union.
- A cross-midnight interval contributes to both Beijing calendar dates.
- Study-room start/pause/finish controls are driven by the local timer snapshot,
  not by remote account presence. A remote working state must never start a
  local Session.

## Minimal Phase 2 design

1. Keep `lili_focus_presence` as the account-level compatibility projection so
   existing Dashboard, room, reaction, visit and taunt readers retain their
   current contract.
2. Add `lili_focus_device_presence` with primary key `(user_id, device_id)` for
   per-device liveness only. Enable RLS and expose no direct client table writes;
   authenticated clients use the existing heartbeat RPC.
3. Change the existing heartbeat RPC to upsert the caller's stable device row,
   apply its sequence fence per device, and recompute the account projection
   from device rows fresh within two minutes.
4. Account online/working is `any(fresh device)`, while the RPC response also
   returns the caller's own device state plus active/working device counts.
5. Add the stable local device ID to newly created focus facts. Existing facts
   stay byte-for-byte unchanged and retain an empty device ID.
6. Keep all user-visible time totals on the existing canonical interval-union
   source. Add diagnostics for raw per-device sum, effective union, and overlap;
   diagnostics must not become a second statistics source.
7. Keep local buttons bound exclusively to the local timer. Remote account
   state may add an informational message only.

## Migration risk and rollback boundary

- No production migration is authorized by this design record alone.
- The migration is additive for facts: it creates one device-presence table and
  replaces compatible RPC/dashboard wrappers; it does not update or delete any
  `lili_focus_segments` row.
- The compatibility account table remains in place, limiting blast radius for
  existing social readers and older clients.
- Legacy direct presence writes require explicit compatibility handling so an
  old build cannot erase another device's state.
- Rollback restores the prior heartbeat/dashboard functions and drops only the
  additive device-presence table after confirming no current client depends on
  its diagnostic fields. Focus history requires no rollback.
- Production deployment must wait for local tests, SQL contract tests, an
  isolated database acceptance run, and explicit approval to apply the reviewed
  migration.

## Advisor baseline

Supabase security and performance advisors were read before design. Existing
project-wide warnings predate Phase 2 (including intentionally exposed
authenticated security-definer RPCs and unrelated RLS/index suggestions). The
Phase 2 migration must introduce no new warning for its table or functions.
