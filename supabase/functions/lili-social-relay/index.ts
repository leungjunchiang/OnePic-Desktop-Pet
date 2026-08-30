type Env = Record<string, string | undefined>;

const RPC_ALLOWLIST = new Set([
  "lili_add_buddy_by_code",
  "lili_lookup_buddy_by_code",
  "lili_buddy_requests",
  "lili_respond_buddy",
  "lili_cancel_buddy_request",
  "lili_remove_buddy",
  "lili_create_room",
  "lili_join_room",
  "lili_send_visit",
  "lili_respond_visit",
  "lili_send_taunt",
  "lili_taunt_state",
  "lili_send_encouragement",
  "lili_encouragement_state",
  "lili_reaction_state",
  "lili_dashboard",
  "lili_room_dashboard",
  "lili_record_room_event",
  "lili_send_interaction",
  "lili_send_food_interaction",
  "lili_create_cake_share",
  "lili_set_buddy_interaction_mode",
  "lili_set_room_goal",
  "lili_set_room_schedule",
  "lili_set_room_challenge",
  "lili_leave_room",
  "lili_set_buddy_subscription",
  "lili_room_room_rituals",
  "lili_buddy_private_notes",
  "lili_set_buddy_private_note",
  "lili_sync_personal_state",
  "lili_sync_focus_history",
  "lili_sync_focus_segments",
  "lili_focus_weekly_leaderboard",
  "lili_update_presence_context",
  "lili_upsert_focus_presence",
]);

const ROUTE_TO_RPC = new Map([
  ["/buddies/request", "lili_add_buddy_by_code"],
  ["/buddies/lookup", "lili_lookup_buddy_by_code"],
  ["/buddies/requests", "lili_buddy_requests"],
  ["/buddies/accept", "lili_respond_buddy"],
  ["/buddies/cancel", "lili_cancel_buddy_request"],
  ["/buddies/remove", "lili_remove_buddy"],
  ["/buddies/subscription", "lili_set_buddy_subscription"],
  ["/visits/send", "lili_send_visit"],
  ["/visits/accept", "lili_respond_visit"],
  ["/buddies/taunt", "lili_send_taunt"],
  ["/buddies/taunt-state", "lili_taunt_state"],
  ["/buddies/encouragement", "lili_send_encouragement"],
  ["/buddies/encouragement-state", "lili_encouragement_state"],
  ["/buddies/reaction-state", "lili_reaction_state"],
  ["/rooms/create", "lili_create_room"],
  ["/rooms/join", "lili_join_room"],
  ["/rooms/goal", "lili_set_room_goal"],
  ["/rooms/schedule", "lili_set_room_schedule"],
  ["/rooms/challenge", "lili_set_room_challenge"],
  ["/rooms/leave", "lili_leave_room"],
  ["/rooms/interaction", "lili_send_interaction"],
  ["/rooms/food-interaction", "lili_send_food_interaction"],
  ["/rooms/cake-share", "lili_create_cake_share"],
  ["/profile/interaction-mode", "lili_set_buddy_interaction_mode"],
  ["/rooms/events", "lili_record_room_event"],
  ["/leaderboard/focus-week", "lili_focus_weekly_leaderboard"],
]);

const DASHBOARD_PERSON_LIST_FIELDS = new Set([
  "buddies",
  "room_people",
  "active_visits",
  "visits",
  "requests",
  "outgoing_requests",
  "leaderboard",
]);
const DASHBOARD_PERSON_ID_FIELDS = [
  "user_id",
  "buddy_user_id",
  "buddy_id",
  "peer_id",
  "owner_id",
  "actor_id",
  "sender_id",
  "receiver_id",
  "requester_id",
  "target_id",
];
const DASHBOARD_OPTIONAL_PERSON_FIELDS = [
  "pet_name",
  "pet_nickname",
  "liumao_name",
  "companion_name",
  "owner_nickname",
  "nickname",
  "display_name",
];

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function personIdentity(value: unknown): string {
  const item = record(value);
  if (!item) return "";
  for (const field of DASHBOARD_PERSON_ID_FIELDS) {
    const candidate = String(item[field] ?? "").trim();
    if (candidate) return candidate;
  }
  return "";
}

function mergeDashboardPeople(previous: unknown, incoming: unknown): unknown {
  if (!Array.isArray(incoming)) return incoming;
  const previousById = new Map<string, Record<string, unknown>>();
  if (Array.isArray(previous)) {
    for (const item of previous) {
      const identity = personIdentity(item);
      const itemRecord = record(item);
      if (identity && itemRecord) previousById.set(identity, itemRecord);
    }
  }
  return incoming.map((item) => {
    const itemRecord = record(item);
    if (!itemRecord) return item;
    const merged = { ...itemRecord };
    const previousItem = previousById.get(personIdentity(item));
    if (previousItem) {
      for (const field of DASHBOARD_OPTIONAL_PERSON_FIELDS) {
        if (!(field in itemRecord) && field in previousItem) merged[field] = previousItem[field];
      }
    }
    return merged;
  });
}

function mergeDashboardOverlay(
  previous: Record<string, unknown>,
  incoming: unknown,
): Record<string, unknown> {
  const incomingRecord = record(incoming);
  if (!incomingRecord) return { ...previous };
  const merged = { ...previous };
  for (const [key, value] of Object.entries(incomingRecord)) {
    if (DASHBOARD_PERSON_LIST_FIELDS.has(key) && Array.isArray(value)) {
      merged[key] = mergeDashboardPeople(merged[key], value);
    } else if (key === "current_room" && record(value)) {
      merged[key] = mergeDashboardOverlay(record(merged[key]) || {}, value);
    } else {
      merged[key] = value;
    }
  }
  return merged;
}

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
  let result: Record<string, unknown> = mergeDashboardOverlay({}, dashboardData || {});
  if (!roomId) return result;
  let room: unknown;
  try {
    room = await rpc(request, env, "lili_room_dashboard", { p_room_id: roomId });
  } catch (error) {
    if (!(error instanceof RelayError) || error.status !== 404) throw error;
    result._room_endpoint_unavailable = true;
    return result;
  }
  result = mergeDashboardOverlay(result, room);
  try {
    const rituals = await rpc(request, env, "lili_room_room_rituals", { p_room_id: roomId });
    result = mergeDashboardOverlay(result, rituals);
  } catch (error) {
    if (error instanceof RelayError && error.status < 500) return result;
    throw error;
  }
  return result;
}

async function presence(request: Request, env: Env, body: Record<string, unknown>): Promise<unknown> {
  // One authenticated RPC updates the complete live-state tuple.  This keeps
  // heartbeat independent from dashboard/statistics requests and prevents a
  // partially written presence row.
  return await supabaseFetch(request, env, "/rest/v1/rpc/lili_upsert_focus_presence", {
    body: {
      p_working: Boolean(body.working),
      p_session_active: Boolean(body.session_active),
      p_session_id: body.session_id ? String(body.session_id).slice(0, 160) : null,
      p_session_started_at: body.session_started_at || null,
      p_device_id: String(body.device_id || "").trim().slice(0, 120),
      p_sequence: Math.max(0, Number(body.sequence) || 0),
    },
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
  if (path === "/auth/resend" && request.method === "POST") {
    const body = await parseBody(request);
    return json(request, env, await supabaseFetch(request, env, "/auth/v1/resend", {
      body: {
        type: body.type || "signup",
        email: body.email,
        options: body.options,
      },
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
    for (const key of ["nickname", "owner_nickname", "pet_name", "visibility", "show_exact_time", "allow_visits", "outfit_key", "wealth_leaderboard_enabled", "wealth_leaderboard_preference_set"]) {
      if (key in body) clean[key] = body[key];
    }
    for (const key of ["nickname", "owner_nickname"] as const) {
      if (clean[key] === undefined) continue;
      // Phase 3 uses an explicit NULL to clear only the optional owner name.
      // Keep the legacy protection for empty strings from older clients.
      if (key === "owner_nickname" && clean[key] === null) continue;
      const value = String(clean[key]).trim().slice(0, 24);
      // Empty identity fields mean “keep the durable profile”. Do not let an
      // old client erase a social name while saving unrelated settings.
      if (value) clean[key] = value;
      else delete clean[key];
    }
    if (clean.pet_name !== undefined) {
      const value = String(clean.pet_name || "").trim().slice(0, 24);
      clean.pet_name = value || null;
    }
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


