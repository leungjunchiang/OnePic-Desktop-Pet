# 六毛自习室中转服务

Supabase is the only production database and authentication source of truth.
The Tencent CloudBase HTTP function in `cloudbase-function/` is a restricted
fallback proxy for mainland networks; it does not create collections, store
sessions, or persist business data in CloudBase.

```text
六毛客户端
  ├─ normal: Supabase Direct
  └─ network fallback: CloudBase HTTP Proxy → Supabase REST/Auth/RPC
```

The desktop client uses one `BackendRouteManager`. It tries the direct route,
retries only one network failure, and switches to the proxy only for DNS,
timeout, connection, TLS, or 502/503/504 failures. Authentication/RLS/4xx
errors never cause route switching. After two spaced direct health successes,
the client returns to Supabase Direct.

The network-check button performs one lightweight health request and does not
load friends, rooms, presence, or Realtime. Room data is refreshed separately
by the normal 30-second snapshot timer. Presence writes are limited to state
changes and a 90-second heartbeat; the local focus timer never uploads every
second. There is no WebSocket listener created by the health check and no
unbounded retry loop.

## CloudBase deployment

Upload the contents of `relay/cloudbase-function` as a Node.js 20 HTTP
function. Configure these CloudBase environment variables:

- `SUPABASE_URL`
- `SUPABASE_PUBLISHABLE_KEY` (or the compatible `SUPABASE_ANON_KEY`)
- optional `ALLOWED_ORIGIN`

Never configure a `service_role` key in the desktop app. If a service role is
ever needed for a server-only migration, it must stay in the server secret
store and must not be committed here.

The public function URL is configured in `config/social_backend.json` as
`social_api_base_url`. Test `/health`, login, dashboard, room membership and
heartbeat from an ordinary mainland network after deployment.

## Supabase responsibilities

Supabase owns Auth, PostgreSQL/RLS, room membership, focus sessions, profiles,
events, interactions, and the existing migrations. Realtime remains a direct
Supabase capability when available; the current desktop snapshot fallback is
low-frequency HTTPS polling. CloudBase never becomes a second database.
