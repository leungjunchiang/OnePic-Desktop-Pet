# Phase 1 Supabase Egress Baseline

Snapshot taken before any Phase 1 code change, from the `v0.23.180` Release
commit (`5187253a72aeac6a46155ee2714f8ff13e16d6ee`).

## Data snapshot

- Snapshot time: 2026-08-30 20:32:13 +08:00.
- Server Dashboard `focus_today_seconds`: `12556`.
- Server Dashboard `focus_week_seconds`: `177534`.
- Local durable raw analytics records: `158`.
- Local closed-session file entries: `2`.
- Cross-Beijing-day closed session in the local baseline: none observed.
- The read-only Dashboard payload exposes aggregate focus values, not the
  number of rows in `lili_focus_segments`. The authenticated
  `lili_sync_focus_segments` RPC returns/merges raw facts and is not a safe
  read-only count operation, so it was not called for baseline collection.

The two local session samples were retained in the local diagnostic record
used for this audit:

| sample | session id | started at | ended at |
| --- | --- | --- | --- |
| recent | `ade87e019f474709a933ceabe36707b3` | `2026-08-30T19:53:37.204866+08:00` | `2026-08-30T20:18:31.204866+08:00` |
| earlier | `38209f79b59b43cb9e7e904f9ec48232` | `2026-08-30T17:43:35.802012+08:00` | `2026-08-30T17:43:37.802012+08:00` |

No production row, session, migration, or RPC was changed during baseline
collection.

## Observed request cadence

The packaged logged-in application was observed through its lifecycle log
(`lifecycle.log.1`) from 17:43:06 to 20:07:20 +08:00:

- `social_sync_thread.finished`: `1527` events over `8643` seconds; mean
  completion interval `5.66` seconds (minimum `2`, maximum `14`). This is the
  observable aggregate for the passive dashboard sync loop.
- `social.heartbeat.sent`: `544` events over `8637` seconds; mean interval
  `15.91` seconds (minimum `1`, maximum `19`). The short intervals are caused
  by state transitions/retries; the normal heartbeat target remains about 15
  seconds.

The lifecycle log does not include per-endpoint HTTP records. Source tracing
therefore records the current chain separately: the dashboard path calls
`lili_dashboard`, optionally the two room RPCs, `lili_buddy_requests`, and
`lili_buddy_private_notes`; reaction and weekly leaderboard are gated by the
current 30-second and 120-second client timers respectively.

## Pre-change client gates

| path | current gate |
| --- | ---: |
| passive social/dashboard timer | 5 seconds |
| reaction state | 30 seconds |
| weekly focus leaderboard | 120 seconds |
| Heartbeat | 15 seconds |
| buddy requests/private notes | every dashboard call; no 5-minute auxiliary TTL |

Phase 1 is limited to these read-only polling/cache gates. FocusSession facts,
their sync RPCs, time aggregation, reports, database schema, RLS, leaderboard
SQL, and production data are explicitly out of scope.

## Post-edit integrity re-read

After the Phase 1 source edit, the same local account-scoped data was read
again at 2026-08-30 20:42:43 +08:00. The old packaged application was still
running a pre-edit active session, so the server aggregate increased naturally
from `12556`/`177534` to `13181`/`178159`; this is not attributed to the code
change. The historical file remained at `2` entries, the raw analytics file
remained at `158` records, and both retained sample IDs and timestamps exactly.
No old FocusSession was edited, removed, duplicated, or re-sliced by the
Phase 1 change.

## New read-only gates and modeled reduction

The released client uses the following passive gates:

| path | before | after |
| --- | ---: | ---: |
| passive social/dashboard timer | 5 seconds | 30 seconds |
| reaction state | 30 seconds | 60 seconds |
| weekly focus leaderboard | 120 seconds | 600 seconds |
| buddy requests | every dashboard call | at most every 300 seconds in background |
| private notes | every dashboard call | at most every 300 seconds in background |
| Heartbeat | about 15 seconds | unchanged |

Opening the relevant Interaction or Mine tab, selecting a room, calling the
existing manual refresh, or completing an applicable buddy action forces the
auxiliary read immediately. The Heartbeat worker remains separate from the
social/dashboard timer.

Because the available lifecycle log has no endpoint-level records, the exact
post-release server request count cannot be measured from that log alone. A
read-only-chain model anchored to the observed 1,527 passive cycles over 8,643
seconds estimates approximately 5,485 calls before and 1,048 calls after for a
no-room session (dashboard, buddy requests, private notes, reaction,
leaderboard, and unchanged Heartbeat), a modeled reduction of about 80.9%.
If the two room reads are included on every dashboard cycle, the corresponding
estimate is approximately 8,539 before and 1,624 after, or about 81.0%.
These are estimates for the changed read-only chain; unchanged personal sync
and FocusSession traffic is deliberately excluded. Per-path theoretical
reductions are 83.3% for Dashboard, 50% for Reaction, 80% for leaderboard,
and approximately 98% for each five-minute auxiliary cache path.

## Phase 1 verification

- `pytest -q --disable-warnings`: `776 passed`, 2 existing Pillow deprecation
  warnings.
- `python -m compileall -q src`: passed.
- Windows PyInstaller build for `v0.23.182`: passed.
- Isolated packaged-app startup/clean exit: passed with exit code 0.
- Release API verification: formal, non-draft `v0.23.182` with 9 assets and
  target commit `e63e50f8e7aa686786ced7fdaba637c1f8c6fc33`.

The existing logged-in `v0.23.180` process had an active user FocusSession
during validation and was not interrupted. Consequently, a new production
manual start/stop session was not injected into that account; automated
start/end/sync coverage and the historical-data integrity comparison passed,
but this specific manual production step remains explicitly unclaimed.

Phase 1 did not modify FocusSession data semantics, time-statistics
algorithms, synchronization semantics or frequency, database schema, RLS,
statistical RPC logic, or production data.
