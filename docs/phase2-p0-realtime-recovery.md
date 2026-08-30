# Phase 2 P0 realtime recovery audit

Recorded: 2026-08-31 (Asia/Shanghai)

## Root cause

The user-visible banner is rendered by `social_ui.py` when either the cached
snapshot carries `_presence_grace_active` or the connection state is
`DEGRADED`. A recent cached Dashboard snapshot is deliberately marked
uncertain when it is no older than `PRESENCE_GRACE_SECONDS = 180`; this keeps
the last trusted buddy state while a read is retried. A snapshot older than
that grace period is marked stale and is then allowed to render offline.

The deterministic P0 display bug was that the homepage counted only the
normal `focus` status. During the uncertainty window `_presence_status()`
returns `unknown`, so the old counter converted “not currently confirmed” to
`0`; card rendering also treated uncertainty as offline-looking. In addition,
the overlay merge could retain transient degraded flags after a later healthy
Dashboard response. This explains the combination of a recovery banner,
cached buddy data, offline-looking Presence, and `现在 0 位搭子正在专注`.

The historical runtime log does not include the transport subtype for the
specific Dashboard read that first caused the screenshot (`kind/status/error`
was not logged by the old catch path), so the evidence does not justify
calling it a schema mismatch, Relay failure, or a particular HTTP code. The
hosted API log shows core Dashboard/upsert calls returning 200; the observed
404 is the optional `lili_achievement_witness_inbox` endpoint and is unrelated
to the core Dashboard chain. There is no hosted evidence of a missing Phase 2
Presence relation or RPC at the time of this audit.

The active local client lifecycle log after 23:35 showed `v0.23.182` behavior:
Dashboard completion roughly every 27–36 seconds and Heartbeat roughly every
15.8–18.0 seconds. Aggregate API rows with approximately five-second gaps
were mixed across users/clients and cannot be attributed to this account.

## Minimal repair

- Keep Dashboard at 30 seconds, Reaction at 60 seconds, Leaderboard at 600
  seconds, and the independent Heartbeat at approximately 15 seconds.
- Keep recent trusted Presence and the last confirmed working count during
  the 180-second uncertainty grace period.
- Show an uncertainty message/count rather than converting unknown to offline
  or zero.
- Clear transient degraded/sync flags on a healthy complete Dashboard merge.
- Render uncertain Presence as unknown/recovering; only stale Presence is
  rendered offline.
- Log sanitized Dashboard failure kind/status/error-code fields so a future
  outage can distinguish transport, auth, timeout, and schema errors.
- Keep the local work button bound to the local timer; remote account working
  never starts a local FocusSession.

No FocusSession, statistics, synchronization, database, or Heartbeat cadence
was changed by this P0 repair.

## Verification

The focused social/UI/window tests pass after the repair. The regression
asserts that recent cached working Presence remains visible, the homepage does
not show `现在 0 位搭子正在专注`, a healthy response clears the banner, and a
single Dashboard failure does not immediately turn all buddies offline.
