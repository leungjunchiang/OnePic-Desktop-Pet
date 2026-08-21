const RPC_ALLOWLIST = new Set([
  "lili_add_buddy_by_code",
  "lili_respond_buddy",
  "lili_create_room",
  "lili_join_room",
  "lili_send_visit",
  "lili_respond_visit",
  "lili_dashboard",
  "lili_room_dashboard",
  "lili_record_room_event",
  "lili_send_interaction",
  "lili_set_room_goal",
  "lili_leave_room",
  "lili_set_room_schedule",
  "lili_set_room_challenge",
  "lili_set_buddy_subscription",
  "lili_room_room_rituals",
  "lili_buddy_private_notes",
  "lili_set_buddy_private_note",
  "lili_sync_personal_state",
]);

const ROUTE_TO_RPC = new Map([
  ["/buddies/request", "lili_add_buddy_by_code"],
  ["/buddies/accept", "lili_respond_buddy"],
  ["/visits/send", "lili_send_visit"],
  ["/visits/accept", "lili_respond_visit"],
  ["/rooms/create", "lili_create_room"],
  ["/rooms/join", "lili_join_room"],
  ["/rooms/goal", "lili_set_room_goal"],
  ["/rooms/leave", "lili_leave_room"],
  ["/rooms/interaction", "lili_send_interaction"],
  ["/rooms/events", "lili_record_room_event"],
]);

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

class RelayError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function corsHeaders(request, env) {
  const requestedOrigin = request.headers.get("Origin") || "";
  const configured = String(env.ALLOWED_ORIGIN || "*").trim();
  let allowOrigin = "*";
  if (configured !== "*") {
    const allowed = configured.split(",").map((item) => item.trim()).filter(Boolean);
    allowOrigin = allowed.includes(requestedOrigin) ? requestedOrigin : allowed[0] || "null";
  }
  return {
    "Access-Control-Allow-Origin": allowOrigin,
    "Access-Control-Allow-Headers": "Authorization, Content-Type, X-Client-Key",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
    "Access-Control-Max-Age": "86400",
    "Cache-Control": "no-store",
    Vary: "Origin",
  };
}

function jsonResponse(data, request, env, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      ...corsHeaders(request, env),
      "Content-Type": "application/json; charset=utf-8",
      ...extraHeaders,
    },
  });
}

function errorResponse(error, request, env) {
  const status = error instanceof RelayError ? error.status : 502;
  const message = error instanceof RelayError ? error.message : "中转服务暂时不可用，请稍后重试。";
  return jsonResponse({ error: message }, request, env, status);
}

function requireConfig(env) {
  const url = String(env.SUPABASE_URL || "").replace(/\/+$/, "");
  const key = String(env.SUPABASE_PUBLISHABLE_KEY || "").trim();
  if (!url || !key) {
    throw new RelayError(503, "中转服务尚未配置 Supabase。请先设置 Worker secrets。");
  }
  return { url, key };
}

function bearer(request) {
  const value = request.headers.get("Authorization") || "";
  if (!/^Bearer\s+\S+$/i.test(value)) {
    throw new RelayError(401, "请先登录六毛搭子自习室。");
  }
  return value;
}

function userIdFromBearer(value) {
  const token = value.replace(/^Bearer\s+/i, "").split(".");
  if (token.length !== 3) throw new RelayError(401, "登录令牌无效，请重新登录。");
  try {
    const encoded = token[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = encoded + "=".repeat((4 - (encoded.length % 4)) % 4);
    const payload = JSON.parse(atob(padded));
    if (!payload.sub) throw new Error("missing sub");
    return String(payload.sub);
  } catch {
    throw new RelayError(401, "登录令牌无效，请重新登录。");
  }
}

async function parseJsonBody(request) {
  const raw = await request.text();
  if (!raw.trim()) return {};
  try {
    const value = JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("not object");
    }
    return value;
  } catch {
    throw new RelayError(400, "请求内容不是有效的 JSON。");
  }
}

function safeBody(body, keys) {
  return Object.fromEntries(keys.filter((key) => Object.prototype.hasOwnProperty.call(body, key)).map((key) => [key, body[key]]));
}

async function callSupabase(env, request, path, { method = "POST", body, authenticated = true, headers = {} } = {}) {
  const { url, key } = requireConfig(env);
  const upstreamHeaders = {
    apikey: key,
    Accept: "application/json",
    "Content-Type": "application/json",
    ...headers,
  };
  if (authenticated) upstreamHeaders.Authorization = bearer(request);
  const init = { method, headers: upstreamHeaders };
  if (body !== undefined) init.body = JSON.stringify(body);
  const response = await fetch(`${url}${path}`, init);
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!response.ok) {
    const message = typeof data === "object" && data
      ? data.msg || data.message || data.error_description || data.error
      : data;
    throw new RelayError(response.status, String(message || "Supabase 请求失败").slice(0, 300));
  }
  return data;
}

async function callRpc(env, request, name, body = {}) {
  if (!RPC_ALLOWLIST.has(name)) throw new RelayError(404, "不支持这个自习室接口。");
  return callSupabase(env, request, `/rest/v1/rpc/${encodeURIComponent(name)}`, { body });
}

async function handleDashboard(env, request, roomId = "") {
  const data = await callRpc(env, request, "lili_dashboard", {});
  if (!roomId) return data || {};
  if (!UUID_RE.test(roomId)) throw new RelayError(400, "房间编号格式不正确。");
  const room = await callRpc(env, request, "lili_room_dashboard", { p_room_id: roomId });
  const result = { ...(data || {}), ...(room || {}) };
  try {
    const rituals = await callRpc(env, request, "lili_room_room_rituals", { p_room_id: roomId });
    Object.assign(result, rituals || {});
  } catch (error) {
    // Rituals are optional for older Supabase projects; room state remains usable.
    if (!(error instanceof RelayError) || error.status >= 500) throw error;
  }
  return result;
}

async function handlePresence(env, request, body) {
  const auth = bearer(request);
  const userId = userIdFromBearer(auth);
  const now = new Date().toISOString();
  const payload = {
    ...safeBody(body, [
      "working", "session_started_at", "focus_date", "today_seconds", "outfit_key", "room_id", "quick_status", "quick_status_expires_at",
    ]),
    user_id: userId,
    focus_date: String(body.focus_date || now.slice(0, 10)),
    // Presence freshness must use this server's clock, never the desktop's.
    last_seen: now,
    updated_at: now,
  };
  payload.working = Boolean(payload.working);
  payload.today_seconds = Math.min(86400, Math.max(0, Number(payload.today_seconds) || 0));
  payload.room_id = payload.room_id ? String(payload.room_id) : null;
  payload.outfit_key = String(payload.outfit_key || "").slice(0, 60);
  payload.quick_status = String(payload.quick_status || "").trim().slice(0, 40);
  payload.quick_status_expires_at = payload.quick_status_expires_at ? String(payload.quick_status_expires_at) : null;
  return callSupabase(env, request, "/rest/v1/lili_focus_presence?on_conflict=user_id", {
    body: payload,
    headers: { Prefer: "resolution=merge-duplicates,return=minimal" },
  });
}

async function handleRequest(request, env) {
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/+$/, "") || "/";
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders(request, env) });
  if (request.method === "GET" && path === "/health") {
    return jsonResponse({
      ok: true,
      service: "lili-social-relay",
      backend: "cloudflare-worker",
      realtime: "desktop short-polling",
      supabase_configured: Boolean(env.SUPABASE_URL && env.SUPABASE_PUBLISHABLE_KEY),
    }, request, env);
  }
  if (request.method === "GET" && path === "/") {
    return jsonResponse({ service: "lili-social-relay", status: "ok", realtime: "desktop short-polling" }, request, env);
  }

  if (path === "/auth/signup" && request.method === "POST") {
    const body = await parseJsonBody(request);
    const query = body.redirect_to ? `?redirect_to=${encodeURIComponent(String(body.redirect_to))}` : "";
    const signup = safeBody(body, ["email", "password", "data"]);
    return jsonResponse(await callSupabase(env, request, `/auth/v1/signup${query}`, { body: signup, authenticated: false }), request, env);
  }
  if (path === "/auth/signin" && request.method === "POST") {
    const body = await parseJsonBody(request);
    return jsonResponse(await callSupabase(env, request, "/auth/v1/token?grant_type=password", { body: safeBody(body, ["email", "password"]), authenticated: false }), request, env);
  }
  if (path === "/auth/refresh" && request.method === "POST") {
    const body = await parseJsonBody(request);
    return jsonResponse(await callSupabase(env, request, "/auth/v1/token?grant_type=refresh_token", { body: safeBody(body, ["refresh_token"]), authenticated: false }), request, env);
  }

  if (path === "/dashboard" && request.method === "GET") {
    return jsonResponse(await handleDashboard(env, request), request, env);
  }
  const roomMatch = path.match(/^\/rooms\/([^/]+)$/);
  if (roomMatch && request.method === "GET") {
    return jsonResponse(await handleDashboard(env, request, decodeURIComponent(roomMatch[1])), request, env);
  }

  if (path === "/profile" && request.method === "PATCH") {
    const auth = bearer(request);
    const userId = userIdFromBearer(auth);
    const body = await parseJsonBody(request);
    const profile = safeBody(body, ["nickname", "owner_nickname", "visibility", "show_exact_time", "allow_visits", "outfit_key"]);
    if (profile.owner_nickname !== undefined) profile.owner_nickname = String(profile.owner_nickname).trim().slice(0, 24);
    if (profile.nickname !== undefined) profile.nickname = String(profile.nickname).trim().slice(0, 24) || "搭子";
    if (profile.outfit_key !== undefined) profile.outfit_key = String(profile.outfit_key).slice(0, 60);
    return jsonResponse(await callSupabase(env, request, `/rest/v1/lili_profiles?user_id=eq.${encodeURIComponent(userId)}`, {
      method: "PATCH", body: profile, headers: { Prefer: "return=minimal" },
    }), request, env);
  }
  if (path === "/presence/heartbeat" && request.method === "POST") {
    return jsonResponse(await handlePresence(env, request, await parseJsonBody(request)), request, env);
  }

  if (request.method === "POST" && ROUTE_TO_RPC.has(path)) {
    return jsonResponse(await callRpc(env, request, ROUTE_TO_RPC.get(path), await parseJsonBody(request)), request, env);
  }
  const genericRpc = path.match(/^\/rpc\/([a-z0-9_]+)$/i);
  if (request.method === "POST" && genericRpc) {
    return jsonResponse(await callRpc(env, request, genericRpc[1], await parseJsonBody(request)), request, env);
  }
  throw new RelayError(404, "找不到这个自习室接口。");
}

export default {
  async fetch(request, env) {
    try {
      return await handleRequest(request, env);
    } catch (error) {
      return errorResponse(error, request, env);
    }
  },
};

