"use strict";

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

function headersOf(event) {
  return event && event.headers && typeof event.headers === "object" ? event.headers : {};
}

function header(event, name) {
  const wanted = name.toLowerCase();
  const headers = headersOf(event);
  const entry = Object.entries(headers).find(([key]) => key.toLowerCase() === wanted);
  return entry ? String(entry[1] || "") : "";
}

function methodOf(event) {
  // CloudBase HTTP functions expose `method`; API Gateway compatible events
  // may expose `httpMethod` or requestContext.http.method instead.
  return String(event?.method || event?.httpMethod || event?.requestContext?.http?.method || "GET").toUpperCase();
}

function pathOf(event) {
  let path = String(event?.path || event?.requestContext?.http?.path || event?.requestContext?.path || "/");
  path = path.split("?")[0].replace(/\/+$/, "") || "/";
  // CloudBase HTTP access may include the function name in the gateway path.
  path = path.replace(/^\/(?:lili-social-relay-v2|lili-social-relay)(?=\/|$)/i, "") || "/";
  return path;
}

function bodyOf(event) {
  let raw = event?.body;
  if (raw === undefined || raw === null || raw === "") return {};
  if (event?.isBase64Encoded && typeof raw === "string") {
    raw = Buffer.from(raw, "base64").toString("utf8");
  }
  if (typeof raw === "object") return raw;
  try {
    const value = JSON.parse(String(raw));
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("not object");
    return value;
  } catch {
    throw new RelayError(400, "请求内容不是有效的 JSON。");
  }
}

function corsHeaders(event, env) {
  const requestedOrigin = header(event, "origin");
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

function response(event, env, data, status = 200, extraHeaders = {}) {
  return {
    statusCode: status,
    headers: {
      ...corsHeaders(event, env),
      "Content-Type": "application/json; charset=utf-8",
      ...extraHeaders,
    },
    body: JSON.stringify(data),
    isBase64Encoded: false,
  };
}

function emptyResponse(event, env, status = 204) {
  return {
    statusCode: status,
    headers: corsHeaders(event, env),
    body: "",
    isBase64Encoded: false,
  };
}

function errorResponse(event, env, error) {
  const status = error instanceof RelayError ? error.status : 502;
  const message = error instanceof RelayError
    ? error.message
    : "中转服务暂时不可用，请稍后重试。";
  console.error("lili-social-relay", { status, message, error: String(error?.stack || error) });
  return response(event, env, { error: message }, status);
}

function requireConfig(env) {
  const url = String(env.SUPABASE_URL || "").replace(/\/+$/, "");
  const key = String(env.SUPABASE_PUBLISHABLE_KEY || "").trim();
  if (!url || !key) throw new RelayError(503, "中转服务尚未配置 Supabase。");
  return { url, key };
}

function bearer(event) {
  const value = header(event, "authorization");
  if (!/^Bearer\s+\S+$/i.test(value)) throw new RelayError(401, "请先登录六毛搭子自习室。");
  return value;
}

function userIdFromBearer(value) {
  const token = value.replace(/^Bearer\s+/i, "").split(".");
  if (token.length !== 3) throw new RelayError(401, "登录令牌无效，请重新登录。");
  try {
    const encoded = token[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = encoded + "=".repeat((4 - (encoded.length % 4)) % 4);
    const payload = JSON.parse(Buffer.from(padded, "base64").toString("utf8"));
    if (!payload.sub) throw new Error("missing sub");
    return String(payload.sub);
  } catch {
    throw new RelayError(401, "登录令牌无效，请重新登录。");
  }
}

function safeBody(body, keys) {
  return Object.fromEntries(keys.filter((key) => Object.prototype.hasOwnProperty.call(body, key)).map((key) => [key, body[key]]));
}

async function callSupabase(env, event, path, { method = "POST", body, authenticated = true, headers = {} } = {}) {
  const { url, key } = requireConfig(env);
  const upstreamHeaders = {
    apikey: key,
    Accept: "application/json",
    "Content-Type": "application/json",
    ...headers,
  };
  if (authenticated) upstreamHeaders.Authorization = bearer(event);
  const init = { method, headers: upstreamHeaders };
  if (body !== undefined) init.body = JSON.stringify(body);
  let upstream;
  try {
    upstream = await fetch(`${url}${path}`, init);
  } catch (error) {
    throw new RelayError(502, "上游自习室服务暂时不可达。");
  }
  const text = await upstream.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!upstream.ok) {
    const message = typeof data === "object" && data
      ? data.msg || data.message || data.error_description || data.error
      : data;
    throw new RelayError(upstream.status, String(message || "Supabase 请求失败").slice(0, 300));
  }
  return data;
}

async function callRpc(env, event, name, body = {}) {
  if (!RPC_ALLOWLIST.has(name)) throw new RelayError(404, "不支持这个自习室接口。");
  return callSupabase(env, event, `/rest/v1/rpc/${encodeURIComponent(name)}`, { body });
}

async function handleDashboard(env, event, roomId = "") {
  const data = await callRpc(env, event, "lili_dashboard", {});
  if (!roomId) return data || {};
  if (!UUID_RE.test(roomId)) throw new RelayError(400, "房间编号格式不正确。");
  const room = await callRpc(env, event, "lili_room_dashboard", { p_room_id: roomId });
  const result = { ...(data || {}), ...(room || {}) };
  try {
    const rituals = await callRpc(env, event, "lili_room_room_rituals", { p_room_id: roomId });
    Object.assign(result, rituals || {});
  } catch (error) {
    if (!(error instanceof RelayError) || error.status >= 500) throw error;
  }
  return result;
}

async function handlePresence(env, event, body) {
  const auth = bearer(event);
  const userId = userIdFromBearer(auth);
  const now = new Date().toISOString();
  const payload = {
    ...safeBody(body, ["working", "session_started_at", "focus_date", "today_seconds", "outfit_key", "room_id", "quick_status", "quick_status_expires_at", "last_seen"]),
    user_id: userId,
    focus_date: String(body.focus_date || now.slice(0, 10)),
    last_seen: String(body.last_seen || now),
    updated_at: now,
  };
  payload.working = Boolean(payload.working);
  payload.today_seconds = Math.min(86400, Math.max(0, Number(payload.today_seconds) || 0));
  payload.room_id = payload.room_id ? String(payload.room_id) : null;
  payload.outfit_key = String(payload.outfit_key || "").slice(0, 60);
  payload.quick_status = String(payload.quick_status || "").trim().slice(0, 40);
  payload.quick_status_expires_at = payload.quick_status_expires_at ? String(payload.quick_status_expires_at) : null;
  return callSupabase(env, event, "/rest/v1/lili_focus_presence?on_conflict=user_id", {
    body: payload,
    headers: { Prefer: "resolution=merge-duplicates,return=minimal" },
  });
}

async function handleRequest(event, env) {
  const method = methodOf(event);
  const path = pathOf(event);
  if (method === "OPTIONS") return emptyResponse(event, env);
  if (method === "GET" && path === "/health") {
    return response(event, env, {
      ok: true,
      service: "lili-social-relay",
      backend: "cloudbase-function",
      realtime: "desktop short-polling",
      supabase_configured: Boolean(env.SUPABASE_URL && env.SUPABASE_PUBLISHABLE_KEY),
    });
  }
  if (method === "GET" && path === "/") {
    return response(event, env, { service: "lili-social-relay", status: "ok", realtime: "desktop short-polling" });
  }

  if (path === "/auth/signup" && method === "POST") {
    const body = bodyOf(event);
    const query = body.redirect_to ? `?redirect_to=${encodeURIComponent(String(body.redirect_to))}` : "";
    return response(event, env, await callSupabase(env, event, `/auth/v1/signup${query}`, {
      body: safeBody(body, ["email", "password", "data"]), authenticated: false,
    }));
  }
  if (path === "/auth/signin" && method === "POST") {
    const body = bodyOf(event);
    return response(event, env, await callSupabase(env, event, "/auth/v1/token?grant_type=password", {
      body: safeBody(body, ["email", "password"]), authenticated: false,
    }));
  }
  if (path === "/auth/refresh" && method === "POST") {
    const body = bodyOf(event);
    return response(event, env, await callSupabase(env, event, "/auth/v1/token?grant_type=refresh_token", {
      body: safeBody(body, ["refresh_token"]), authenticated: false,
    }));
  }

  if (path === "/dashboard" && method === "GET") return response(event, env, await handleDashboard(env, event));
  const roomMatch = path.match(/^\/rooms\/([^/]+)$/);
  if (roomMatch && method === "GET") return response(event, env, await handleDashboard(env, event, decodeURIComponent(roomMatch[1])));

  if (path === "/profile" && method === "PATCH") {
    const userId = userIdFromBearer(bearer(event));
    const body = bodyOf(event);
    const profile = safeBody(body, ["nickname", "owner_nickname", "visibility", "show_exact_time", "allow_visits", "outfit_key"]);
    if (profile.owner_nickname !== undefined) profile.owner_nickname = String(profile.owner_nickname).trim().slice(0, 24);
    if (profile.nickname !== undefined) profile.nickname = String(profile.nickname).trim().slice(0, 24) || "搭子";
    if (profile.outfit_key !== undefined) profile.outfit_key = String(profile.outfit_key).slice(0, 60);
    return response(event, env, await callSupabase(env, event, `/rest/v1/lili_profiles?user_id=eq.${encodeURIComponent(userId)}`, {
      method: "PATCH", body: profile, headers: { Prefer: "return=minimal" },
    }));
  }
  if (path === "/presence/heartbeat" && method === "POST") return response(event, env, await handlePresence(env, event, bodyOf(event)));

  if (method === "POST" && ROUTE_TO_RPC.has(path)) return response(event, env, await callRpc(env, event, ROUTE_TO_RPC.get(path), bodyOf(event)));
  const genericRpc = path.match(/^\/rpc\/([a-z0-9_]+)$/i);
  if (method === "POST" && genericRpc) return response(event, env, await callRpc(env, event, genericRpc[1], bodyOf(event)));
  throw new RelayError(404, "找不到这个自习室接口。");
}

async function main(event, context) {
  const env = {
    ...(context?.env || {}),
    SUPABASE_URL: process.env.SUPABASE_URL,
    SUPABASE_PUBLISHABLE_KEY: process.env.SUPABASE_PUBLISHABLE_KEY,
    ALLOWED_ORIGIN: process.env.ALLOWED_ORIGIN || "*",
  };
  try {
    return await handleRequest(event || {}, env);
  } catch (error) {
    return errorResponse(event || {}, env, error);
  }
}

module.exports = { main, handleRequest };
