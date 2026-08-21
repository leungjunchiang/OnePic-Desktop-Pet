"use strict";

// CloudBase is an HTTP fallback proxy only. Supabase Auth, PostgreSQL/RLS,
// Realtime and all durable business data remain the single source of truth.

const RPC_ALLOWLIST = new Set([
  "lili_add_buddy_by_code", "lili_respond_buddy", "lili_create_room", "lili_join_room",
  "lili_send_visit", "lili_respond_visit", "lili_dashboard", "lili_room_dashboard",
  "lili_record_room_event", "lili_send_interaction", "lili_create_cake_share", "lili_set_room_goal", "lili_leave_room",
  "lili_set_room_schedule", "lili_set_room_challenge", "lili_set_buddy_subscription",
  "lili_room_room_rituals",
  "lili_buddy_private_notes", "lili_set_buddy_private_note",
  "lili_sync_personal_state", "lili_focus_weekly_leaderboard",
]);

const ROUTE_TO_RPC = new Map([
  ["/buddies/request", "lili_add_buddy_by_code"], ["/buddies/accept", "lili_respond_buddy"],
  ["/visits/send", "lili_send_visit"], ["/visits/accept", "lili_respond_visit"],
  ["/rooms/create", "lili_create_room"], ["/rooms/join", "lili_join_room"],
  ["/rooms/goal", "lili_set_room_goal"], ["/rooms/schedule", "lili_set_room_schedule"],
  ["/rooms/challenge", "lili_set_room_challenge"], ["/rooms/leave", "lili_leave_room"],
  ["/rooms/interaction", "lili_send_interaction"], ["/rooms/cake-share", "lili_create_cake_share"], ["/rooms/events", "lili_record_room_event"],
  ["/leaderboard/focus-week", "lili_focus_weekly_leaderboard"],
]);

class RelayError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

function headersOf(event) { return event && event.headers && typeof event.headers === "object" ? event.headers : {}; }
function header(event, name) {
  const wanted = name.toLowerCase();
  const item = Object.entries(headersOf(event)).find(([key]) => key.toLowerCase() === wanted);
  return item ? String(item[1] || "") : "";
}
function methodOf(event) { return String(event?.method || event?.httpMethod || event?.requestContext?.http?.method || "GET").toUpperCase(); }
function pathOf(event) {
  let value = String(event?.path || event?.requestContext?.http?.path || event?.requestContext?.path || "/");
  value = value.split("?")[0].replace(/\/+$/, "") || "/";
  return value.replace(/^\/(?:lili-social-relay-v2|lili-social-relay)(?=\/|$)/i, "") || "/";
}
function queryOf(event, key) {
  const params = event?.queryStringParameters;
  if (params && typeof params === "object" && params[key] !== undefined) return String(params[key] || "");
  const raw = String(event?.rawQueryString || event?.queryString || event?.path || "").split("?")[1] || "";
  return new URLSearchParams(raw).get(key) || "";
}
function bodyOf(event) {
  let raw = event?.body;
  if (raw === undefined || raw === null || raw === "") return {};
  if (event?.isBase64Encoded && typeof raw === "string") raw = Buffer.from(raw, "base64").toString("utf8");
  if (typeof raw === "object") return raw;
  try {
    const value = JSON.parse(String(raw));
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("not object");
    return value;
  } catch { throw new RelayError(400, "请求内容不是有效的 JSON。"); }
}
function corsHeaders(event, env) {
  const configured = String(env.ALLOWED_ORIGIN || "*").trim();
  const requested = header(event, "origin");
  const allowed = configured === "*" ? "*" : configured.split(",").map((item) => item.trim()).filter(Boolean).includes(requested) ? requested : "null";
  return { "Access-Control-Allow-Origin": allowed, "Access-Control-Allow-Headers": "Authorization, Content-Type", "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS", "Access-Control-Max-Age": "86400", "Cache-Control": "no-store", Vary: "Origin" };
}
function response(event, env, data, status = 200) {
  return { statusCode: status, headers: { ...corsHeaders(event, env), "Content-Type": "application/json; charset=utf-8", "X-Lili-Server-Time": new Date().toISOString() }, body: JSON.stringify(data), isBase64Encoded: false };
}
function emptyResponse(event, env, status = 204) { return { statusCode: status, headers: corsHeaders(event, env), body: "", isBase64Encoded: false }; }
function bearer(event) {
  const value = header(event, "authorization");
  if (!/^Bearer\s+\S+$/i.test(value)) throw new RelayError(401, "请先登录六毛搭子自习室。");
  return value;
}
function userIdFromBearer(value) {
  const pieces = String(value).replace(/^Bearer\s+/i, "").split(".");
  if (pieces.length !== 3) return "";
  try {
    const encoded = pieces[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = encoded + "=".repeat((4 - (encoded.length % 4)) % 4);
    return String(JSON.parse(Buffer.from(padded, "base64").toString("utf8")).sub || "");
  } catch { return ""; }
}
function config(env) {
  const url = String(env.SUPABASE_URL || "").replace(/\/+$/, "");
  const key = String(env.SUPABASE_PUBLISHABLE_KEY || env.SUPABASE_ANON_KEY || "").trim();
  if (!url || !key) throw new RelayError(503, "Supabase proxy 尚未配置。");
  return { url, key };
}
async function supabaseFetch(env, event, path, { method = "POST", body, authenticated = true, headers: extraHeaders = {} } = {}) {
  const { url, key } = config(env);
  const headers = { apikey: key, Accept: "application/json", "Content-Type": "application/json", ...extraHeaders };
  if (authenticated) headers.Authorization = bearer(event);
  let upstream;
  try {
    upstream = await fetch(`${url}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (error) {
    throw new RelayError(502, "Supabase 上游暂时不可达。");
  }
  const text = await upstream.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!upstream.ok) {
    const message = typeof data === "object" && data ? data.msg || data.message || data.error_description || data.error : data;
    throw new RelayError(upstream.status, String(message || "Supabase 请求失败").slice(0, 300));
  }
  return data;
}
async function callRpc(env, event, name, body = {}) {
  if (!RPC_ALLOWLIST.has(name)) throw new RelayError(404, "不支持这个自习室接口。");
  return supabaseFetch(env, event, `/rest/v1/rpc/${encodeURIComponent(name)}`, { body });
}
async function handleDashboard(env, event, roomId = "") {
  const data = await callRpc(env, event, "lili_dashboard", {});
  if (!roomId) return data || {};
  const result = { ...(data || {}) };
  try {
    Object.assign(result, await callRpc(env, event, "lili_room_dashboard", { p_room_id: roomId }) || {});
  } catch (error) {
    if (!(error instanceof RelayError) || error.status !== 404) throw error;
    result._room_endpoint_unavailable = true;
    return result;
  }
  try { Object.assign(result, await callRpc(env, event, "lili_room_room_rituals", { p_room_id: roomId }) || {}); } catch (error) {
    if (!(error instanceof RelayError) || error.status >= 500) throw error;
  }
  return result;
}
async function handlePresence(env, event, body) {
  const token = bearer(event);
  const now = new Date().toISOString();
  // Never trust a desktop clock for presence freshness. The database trigger
  // is authoritative; this server timestamp also protects proxy-only traffic.
  const payload = { user_id: userIdFromBearer(token), working: Boolean(body.working), session_started_at: body.session_started_at || null, focus_date: String(body.focus_date || now.slice(0, 10)), last_seen: now, updated_at: now, today_seconds: Math.min(86400, Math.max(0, Number(body.today_seconds) || 0)), room_id: body.room_id ? String(body.room_id) : null, outfit_key: String(body.outfit_key || "").slice(0, 60), quick_status: String(body.quick_status || "").trim().slice(0, 40), quick_status_expires_at: body.quick_status_expires_at ? String(body.quick_status_expires_at) : null };
  return supabaseFetch(env, event, "/rest/v1/lili_focus_presence?on_conflict=user_id", {
    body: payload,
    headers: { Prefer: "resolution=merge-duplicates,return=minimal" },
  });
}
async function handleRequest(event, env) {
  const method = methodOf(event); const path = pathOf(event);
  if (method === "OPTIONS") return emptyResponse(event, env);
  if (method === "GET" && (path === "/health" || path === "/")) {
    // This is one light upstream Auth health request, never a database read.
    await supabaseFetch(env, event, "/auth/v1/health", { method: "GET", authenticated: false });
    return response(event, env, { ok: true, service: "lili-social-relay", backend: "supabase-via-cloudbase", route: "CLOUDBASE_PROXY", source_of_truth: "supabase", realtime: "supabase-direct-preferred" });
  }
  if (path === "/auth/signup" && method === "POST") { const body = bodyOf(event); const query = body.redirect_to ? `?redirect_to=${encodeURIComponent(String(body.redirect_to))}` : ""; return response(event, env, await supabaseFetch(env, event, `/auth/v1/signup${query}`, { body: { email: body.email, password: body.password, data: body.data }, authenticated: false })); }
  if (path === "/auth/signin" && method === "POST") { const body = bodyOf(event); return response(event, env, await supabaseFetch(env, event, "/auth/v1/token?grant_type=password", { body: { email: body.email, password: body.password }, authenticated: false })); }
  if (path === "/auth/refresh" && method === "POST") { const body = bodyOf(event); return response(event, env, await supabaseFetch(env, event, "/auth/v1/token?grant_type=refresh_token", { body: { refresh_token: body.refresh_token }, authenticated: false })); }
  if (path === "/dashboard" && method === "GET") return response(event, env, await handleDashboard(env, event, queryOf(event, "room_id").trim()));
  const roomMatch = path.match(/^\/rooms\/([^/]+)$/);
  if (roomMatch && method === "GET") return response(event, env, await handleDashboard(env, event, decodeURIComponent(roomMatch[1])));
  if (path === "/profile" && method === "PATCH") {
    const userId = userIdFromBearer(bearer(event)); const body = bodyOf(event); const clean = {};
    for (const key of ["nickname", "owner_nickname", "visibility", "show_exact_time", "allow_visits", "outfit_key", "wealth_leaderboard_enabled", "wealth_leaderboard_preference_set"]) if (Object.prototype.hasOwnProperty.call(body, key)) clean[key] = body[key];
    if (clean.nickname !== undefined) clean.nickname = String(clean.nickname).trim().slice(0, 24) || "搭子";
    if (clean.owner_nickname !== undefined) clean.owner_nickname = String(clean.owner_nickname).trim().slice(0, 24);
    return response(event, env, await supabaseFetch(env, event, `/rest/v1/lili_profiles?user_id=eq.${encodeURIComponent(userId)}`, { method: "PATCH", body: clean }));
  }
  if (path === "/presence/heartbeat" && method === "POST") return response(event, env, await handlePresence(env, event, bodyOf(event)));
  if (method === "POST" && ROUTE_TO_RPC.has(path)) return response(event, env, await callRpc(env, event, ROUTE_TO_RPC.get(path), bodyOf(event)));
  const generic = path.match(/^\/rpc\/([a-z0-9_]+)$/i);
  if (generic && method === "POST") return response(event, env, await callRpc(env, event, generic[1], bodyOf(event)));
  throw new RelayError(404, "找不到这个自习室接口。");
}
async function main(event, context) {
  const env = { ...(context?.env || {}), SUPABASE_URL: context?.env?.SUPABASE_URL || process.env.SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY: context?.env?.SUPABASE_PUBLISHABLE_KEY || process.env.SUPABASE_PUBLISHABLE_KEY, SUPABASE_ANON_KEY: context?.env?.SUPABASE_ANON_KEY || process.env.SUPABASE_ANON_KEY, ALLOWED_ORIGIN: context?.env?.ALLOWED_ORIGIN || process.env.ALLOWED_ORIGIN || "*" };
  try { return await handleRequest(event || {}, env); } catch (error) { const status = error instanceof RelayError ? error.status : 502; const message = error instanceof RelayError ? error.message : "自习室代理暂时不可用，请稍后重试。"; console.error("lili-social-relay", { status, message, error: String(error?.stack || error) }); return response(event || {}, env, { error: message }, status); }
}

module.exports = { main, handleRequest, RelayError, pathOf, queryOf };

