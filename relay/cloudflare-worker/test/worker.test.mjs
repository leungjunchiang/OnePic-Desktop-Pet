import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";

const env = {
  SUPABASE_URL: "https://example.supabase.co",
  SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test",
  ALLOWED_ORIGIN: "*",
};

function request(path, options = {}) {
  return new Request(`https://relay.example.test${path}`, options);
}

test("health is public and does not expose secrets", async () => {
  const response = await worker.fetch(request("/health"), env);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    ok: true,
    service: "lili-social-relay",
    backend: "cloudflare-worker",
    realtime: "desktop short-polling",
    supabase_configured: true,
  });
});

test("CORS preflight is handled without touching Supabase", async () => {
  const response = await worker.fetch(request("/dashboard", { method: "OPTIONS" }), env);
  assert.equal(response.status, 204);
  assert.equal(response.headers.get("Access-Control-Allow-Methods"), "GET, POST, PATCH, OPTIONS");
});

test("dashboard is translated to the allowlisted authenticated RPC", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (input, init) => {
    calls.push({ input: String(input), init });
    return new Response(JSON.stringify({ me: { nickname: "测试" }, rooms: [] }), { status: 200 });
  };
  try {
    const token = "eyJhbGciOiJub25lIn0.eyJzdWIiOiJ1c2VyLTEifQ.signature";
    const response = await worker.fetch(request("/dashboard", { headers: { Authorization: `Bearer ${token}` } }), env);
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { me: { nickname: "测试" }, rooms: [] });
    assert.equal(calls.length, 1);
    assert.match(calls[0].input, /\/rest\/v1\/rpc\/lili_dashboard$/);
    assert.equal(calls[0].init.headers.Authorization, `Bearer ${token}`);
    assert.equal(calls[0].init.headers.apikey, env.SUPABASE_PUBLISHABLE_KEY);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("room dashboard merges optional rituals and rejects arbitrary RPC names", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (input) => {
    calls.push(String(input));
    if (String(input).endsWith("lili_dashboard")) return new Response(JSON.stringify({ rooms: [] }));
    if (String(input).endsWith("lili_room_dashboard")) return new Response(JSON.stringify({ room_summary: { member_count: 2 } }));
    return new Response(JSON.stringify({ room_schedule: { enabled: true } }));
  };
  try {
    const token = "eyJhbGciOiJub25lIn0.eyJzdWIiOiJ1c2VyLTEifQ.signature";
    const response = await worker.fetch(request("/rooms/123e4567-e89b-12d3-a456-426614174000", { headers: { Authorization: `Bearer ${token}` } }), env);
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), {
      rooms: [],
      room_summary: { member_count: 2 },
      room_schedule: { enabled: true },
    });
    assert.equal(calls.length, 3);

    const invalid = await worker.fetch(request("/rpc/not_allowed", { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: "{}" }), env);
    assert.equal(invalid.status, 404);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
