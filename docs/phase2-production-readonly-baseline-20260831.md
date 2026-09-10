# Phase 2 production read-only baseline

Captured: `2026-08-30T16:39:22.382085Z` (2026-08-31 Asia/Shanghai)

Project: `zkgctfntrioffpifiggk`. This capture used read-only SQL. No migration,
FocusSession write, cache rebuild, or data repair was performed.

## Immutable FocusSession facts

| Scope | Rows | Distinct sessions | Invalid intervals | Non-empty device IDs | Fingerprint |
| --- | ---: | ---: | ---: | ---: | --- |
| Production | 225 | — | — | — | `5369ed3784a0984067a71ff840d021f3` |
| Active account | 160 | 51 | 0 | 0 | `d20997698ab95d9009ebf9a6e7aad1f9` |

The fingerprint is an MD5 over the canonical row JSON ordered by stable keys;
the account fingerprint is ordered by `segment_id`. It includes the complete
segment row for production-wide comparison and the account sample includes
`segment_id`, `session_id`, `start_at`, `end_at`, and `device_id`.

Recent account samples:

| Segment ID | Session ID | Start (UTC) | End (UTC) | Device ID |
| --- | --- | --- | --- | --- |
| `33b67c1186c0485a9452154d83366438:11007` | `33b67c1186c0485a9452154d83366438` | `2026-08-30 15:22:37.692181+00` | `2026-08-30 15:32:52.692181+00` | empty |
| `33b67c1186c0485a9452154d83366438:10392` | `33b67c1186c0485a9452154d83366438` | `2026-08-30 12:24:31.850075+00` | `2026-08-30 14:52:47.850075+00` | empty |
| `33b67c1186c0485a9452154d83366438:1496` | `33b67c1186c0485a9452154d83366438` | `2026-08-30 11:53:34.682820+00` | `2026-08-30 12:18:28.682820+00` | empty |

Older account samples:

| Segment ID | Session ID | Start (UTC) | End (UTC) | Device ID |
| --- | --- | --- | --- | --- |
| `legacy:95` | `legacy:95` | `2026-08-20 04:20:42.480604+00` | `2026-08-20 07:58:01.480604+00` | empty |
| `legacy:97` | `legacy:97` | `2026-08-20 04:56:58.582240+00` | `2026-08-20 09:55:39.582240+00` | empty |
| `legacy:94` | `legacy:94` | `2026-08-20 06:00:14.876520+00` | `2026-08-20 06:08:19.876520+00` | empty |

No cross-Beijing-calendar-day sample existed in this account at capture time.

The server effective today and current-week values were both `0` seconds at
the capture instant. This is the same-window result from
`lili_effective_focus_stats`; the request crossed the Beijing calendar/week
boundary, so the earlier local baseline values are not interchangeable with
this capture.

## Presence and function baseline

- `lili_focus_presence` rows: 6; the device table did not exist yet.
- Active account compatibility Presence at capture: `online=true`,
  `working=false`, `session_active=false`, `today_seconds=6424`,
  `focus_date=2026-08-29`, with `last_seen` at
  `2026-08-30T16:39:43.427054Z`.
- `lili_dashboard()` returned the normal compatibility `me_presence` payload;
  its definition hash was `370e62549e5cbf498a074381a54c2f39`.
- `lili_upsert_focus_presence(boolean, boolean, text, timestamptz, text,
  bigint)` definition hash was
  `07c5e2344ef282dff2ebbec94bf4d11d`.
- `lili_effective_focus_stats(uuid)` definition hash was
  `9ea8016b417bd51be3665d4a9b656749`.

The active desktop client was observed sending its existing heartbeat at the
normal approximately 15-second cadence. The migration was not applied during
this capture.
