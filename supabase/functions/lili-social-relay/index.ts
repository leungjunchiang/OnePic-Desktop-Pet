type Env = Record<string, string | undefined>;

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
  "lili_set_room_schedule",
  "lili_set_room_challenge",
  "lili_leave_room",
  "lili_set_buddy_subscription",
  "lili_room_room_rituals",
  "lili_buddy_private_notes",
  "lili_set_buddy_private_note",
]);

const ROUTE_TO_RPC = new Map([
  ["/buddies/request", "lili_add_buddy_by_code"],
  ["/buddies/accept", "lili_respond_buddy"],
  ["/buddies/subscription", "lili_set_buddy_subscription"],
  ["/visits/send", "lili_send_visit"],
  ["/visits/accept", "lili_respond_visit"],
  ["/rooms/create", "lili_create_room"],
  ["/rooms/join", "lili_join_room"],
  ["/rooms/goal", "lili_set_room_goal"],
  ["/rooms/schedule", "lili_set_room_schedule"],
  ["/rooms/challenge", "lili_set_room_challenge"],
  ["/rooms/leave", "lili_leave_room"],
  ["/rooms/interaction", "lili_send_interaction"],
  ["/rooms/events", "lili_record_room_event"],
]);

class RelayError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function envKey(env: Env): string {
  const modern = env.SUPABASE_PUBLISHABLE_KEYS;
  if (modern) {
    try {
      const keys = JSON.parse(modern) as Record<string, string>;
      if (keys.default) return keys.default;
    } catch {
      // Fall through to the legacy environment variable.
    }
  }
  return String(env.SUPABASE_ANON_KEY || "").trim();
}

function baseUrl(env: Env): string {
  const value = String(env.SUPABASE_URL || "").replace(/\/+$/, "");
  if (!value) throw new RelayError(503, "Supabase relay is not configured");
  if (!envKey(env)) throw new RelayError(503, "Supabase publishable key is not configured");
  return value;
}

function corsHeaders(request: Request, env: Env): HeadersInit {
  const configured = String(env.ALLOWED_ORIGIN || "*").trim();
  const origin = request.headers.get("Origin") || "";
  const allowed = configured === "*"
    ? "*"
    : configured.split(",").map((item) => item.trim()).filter(Boolean).includes(origin)
      ? origin
      : "null";
  return {
    "Access-Control-Allow-Origin": allowed,
    "Access-Control-Allow-Headers": "Authorization, Content-Type, X-Client-Key, apikey",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
    "Access-Control-Max-Age": "86400",
    "Cache-Control": "no-store",
    "Vary": "Origin",
  };
}

function json(request: Request, env: Env, data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...corsHeaders(request, env), "Content-Type": "application/json; charset=utf-8" },
  });
}

function bearer(request: Request): string {
  const value = request.headers.get("Authorization") || "";
  if (!/^Bearer\s+\S+$/i.test(value)) {
    throw new RelayError(401, "Authentication is required");
  }
  return value;
}

async function parseBody(request: Request): Promise<Record<string, unknown>> {
  const text = await request.text();
  if (!text.trim()) return {};
  try {
    const value = JSON.parse(text);
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("not an object");
    return value as Record<string, unknown>;
  } catch {
    throw new RelayError(400, "Request body must be JSON");
  }
}

async function supabaseFetch(
  request: Request,
  env: Env,
  path: string,
  options: { method?: string; body?: unknown; auth?: boolean } = {},
): Promise<unknown> {
  const key = request.headers.get("X-Client-Key")?.trim() || envKey(env);
  const headers = new Headers({
    apikey: key,
    Accept: "application/json",
    "Content-Type": "application/json",
  });
  if (options.auth) headers.set("Authorization", bearer(request));
  if (path.startsWith("/rest/v1/lili_focus_presence") && options.method === "POST") {
    headers.set("Prefer", "resolution=merge-duplicates,return=minimal");
  }
  if (path.startsWith("/rest/v1/lili_profiles") && options.method === "PATCH") {
    headers.set("Prefer", "return=minimal");
  }
  const response = await fetch(`${baseUrl(env)}${path}`, {
    method: options.method || "POST",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const text = await response.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!response.ok) {
    const value = typeof data === "object" && data !== null
      ? (data as Record<string, unknown>).message || (data as Record<string, unknown>).msg || (data as Record<string, unknown>).error
      : data;
    throw new RelayError(response.status, String(value || "Supabase request failed").slice(0, 300));
  }
  return data;
}

async function requireUser(request: Request, env: Env): Promise<Record<string, unknown>> {
  return await supabaseFetch(request, env, "/auth/v1/user", { method: "GET", auth: true }) as Record<string, unknown>;
}

async function rpc(request: Request, env: Env, name: string, body: Record<string, unknown>): Promise<unknown> {
  if (!RPC_ALLOWLIST.has(name)) throw new RelayError(404, "Unsupported study-room operation");
  await requireUser(request, env);
  return await supabaseFetch(request, env, `/rest/v1/rpc/${encodeURIComponent(name)}`, { body, auth: true });
}

async function dashboard(request: Request, env: Env, roomId = ""): Promise<Record<string, unknown>> {
  const dashboardData = await rpc(request, env, "lili_dashboard", {}) as Record<string, unknown> | null;
  const result: Record<string, unknown> = { ...(dashboardData || {}) };
  if (!roomId) return result;
  const room = await rpc(request, env, "lili_room_dashboard", { p_room_id: roomId });
  Object.assign(result, room || {});
  try {
    const rituals = await rpc(request, env, "lili_room_room_rituals", { p_room_id: roomId });
    Object.assign(result, rituals || {});
  } catch (error) {
    if (error instanceof RelayError && error.status < 500) return result;
    throw error;
  }
  return result;
}

async function presence(request: Request, env: Env, body: Record<string, unknown>): Promise<unknown> {
  const user = await requireUser(request, env);
  const now = new Date().toISOString();
  const payload: Record<string, unknown> = {
    working: Boolean(body.working),
    session_started_at: body.session_started_at || null,
    focus_date: String(body.focus_date || now.slice(0, 10)),
    today_seconds: Math.min(86400, Math.max(0, Number(body.today_seconds) || 0)),
    outfit_key: String(body.outfit_key || "").slice(0, 60),
    room_id: body.room_id ? String(body.room_id) : null,
    quick_status: String(body.quick_status || "").trim().slice(0, 40),
    quick_status_expires_at: body.quick_status_expires_at || null,
    // Presence freshness must use this server's clock, never the desktop's.
    last_seen: now,
    updated_at: now,
    user_id: String(user.id || ""),
  };
  return await supabaseFetch(request, env, "/rest/v1/lili_focus_presence?on_conflict=user_id", {
    body: payload,
    auth: true,
    method: "POST",
  });
}

const FUNCTION_SLUGS = ["lili-social-relay-v2", "lili-social-relay"];

function stripFunctionSlug(pathname: string): string {
  for (const slug of FUNCTION_SLUGS) {
    const prefix = `/${slug}`;
    if (pathname === prefix) return "/";
    if (pathname.startsWith(`${prefix}/`)) return pathname.slice(prefix.length) || "/";
  }
  return pathname;
}

function relativePath(url: URL): string {
  let pathname = url.pathname.replace(/\/+$/, "") || "/";
  const gatewayPrefix = "/functions/v1/";
  const prefixIndex = pathname.indexOf(gatewayPrefix);
  if (prefixIndex >= 0) {
    pathname = `/${pathname.slice(prefixIndex + gatewayPrefix.length)}`;
  }
  return stripFunctionSlug(pathname);
}

async function handle(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const path = relativePath(url);
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders(request, env) });
  if (request.method === "GET" && (path === "/health" || path === "/")) {
    return json(request, env, {
      ok: true,
      service: "lili-social-relay",
      backend: "supabase-edge-function",
      transport: "https-rest",
      realtime: "desktop short-polling",
      supabase_configured: Boolean(env.SUPABASE_URL && envKey(env)),
    });
  }
  if (path === "/auth/signup" && request.method === "POST") {
    const body = await parseBody(request);
    return json(request, env, await supabaseFetch(request, env, `/auth/v1/signup${body.redirect_to ? `?redirect_to=${encodeURIComponent(String(body.redirect_to))}` : ""}`, {
      body: { email: body.email, password: body.password, data: body.data },
      method: "POST",
    }));
  }
  if (path === "/auth/signin" && request.method === "POST") {
    const body = await parseBody(request);
    return json(request, env, await supabaseFetch(request, env, "/auth/v1/token?grant_type=password", {
      body: { email: body.email, password: body.password },
      method: "POST",
    }));
  }
  if (path === "/auth/refresh" && request.method === "POST") {
    const body = await parseBody(request);
    return json(request, env, await supabaseFetch(request, env, "/auth/v1/token?grant_type=refresh_token", {
      body: { refresh_token: body.refresh_token },
      method: "POST",
    }));
  }
  if (path === "/dashboard" && request.method === "GET") return json(request, env, await dashboard(request, env));
  const roomMatch = path.match(/^\/rooms\/([^/]+)$/);
  if (roomMatch && request.method === "GET") return json(request, env, await dashboard(request, env, decodeURIComponent(roomMatch[1])));
  if (path === "/profile" && request.method === "PATCH") {
    const user = await requireUser(request, env);
    const body = await parseBody(request);
    const clean: Record<string, unknown> = {};
    for (const key of ["nickname", "owner_nickname", "visibility", "show_exact_time", "allow_visits", "outfit_key"]) {
      if (key in body) clean[key] = body[key];
    }
    if (clean.nickname !== undefined) clean.nickname = String(clean.nickname).trim().slice(0, 24) || "搭子";
    if (clean.owner_nickname !== undefined) clean.owner_nickname = String(clean.owner_nickname).trim().slice(0, 24);
    if (clean.outfit_key !== undefined) clean.outfit_key = String(clean.outfit_key).slice(0, 60);
    return json(request, env, await supabaseFetch(request, env, `/rest/v1/lili_profiles?user_id=eq.${encodeURIComponent(String(user.id || ""))}`, {
      method: "PATCH", body: clean, auth: true,
    }));
  }
  if (path === "/presence/heartbeat" && request.method === "POST") return json(request, env, await presence(request, env, await parseBody(request)));
  if (request.method === "POST" && ROUTE_TO_RPC.has(path)) return json(request, env, await rpc(request, env, ROUTE_TO_RPC.get(path)!, await parseBody(request)));
  const generic = path.match(/^\/rpc\/([a-z0-9_]+)$/i);
  if (generic && request.method === "POST") return json(request, env, await rpc(request, env, generic[1], await parseBody(request)));
  console.warn(JSON.stringify({ event: "route_not_found", method: request.method, pathname: url.pathname, route: path }));
  throw new RelayError(404, "Study-room route not found");
}

Deno.serve(async (request: Request) => {
  try {
    return await handle(request, Deno.env.toObject());
  } catch (error) {
    const status = error instanceof RelayError ? error.status : 502;
    const message = error instanceof RelayError ? error.message : "Study-room relay temporarily unavailable";
    return json(request, Deno.env.toObject(), { error: message }, status);
  }
});

