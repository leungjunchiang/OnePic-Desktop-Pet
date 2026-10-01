const test = require("node:test");
const assert = require("node:assert/strict");
const { main } = require("../index.js");

const env = {
  SUPABASE_URL: "https://example.supabase.co",
  SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test",
  ALLOWED_ORIGIN: "*",
};

test("CloudBase proxy health performs one lightweight Supabase health request", async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push([String(url), options]);
    return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } });
  };
  try {
    const result = await main({ httpMethod: "GET", path: "/lili-social-relay-v2/health", headers: {} }, { env });
    assert.equal(result.statusCode, 200);
    assert.equal(JSON.parse(result.body).source_of_truth, "supabase");
    assert.equal(calls.length, 1);
    assert.match(calls[0][0], /\/auth\/v1\/health$/);
  } finally {
    global.fetch = originalFetch;
  }
});

test("room dashboard query is forwarded through one CloudBase invocation", async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push([String(url), options]);
    return new Response(JSON.stringify({}), { status: 200, headers: { "content-type": "application/json" } });
  };
  try {
    const result = await main({
      httpMethod: "GET",
      path: "/lili-social-relay-v2/dashboard?room_id=room-1",
      headers: { authorization: "Bearer user.jwt.token" },
    }, { env });
    assert.equal(result.statusCode, 200);
    assert.equal(calls.length, 3);
    assert.ok(calls.every(([, options]) => options.headers.Authorization === "Bearer user.jwt.token"));
    assert.ok(calls.every(([url]) => url.startsWith(env.SUPABASE_URL)));
  } finally {
    global.fetch = originalFetch;
  }
});

test("unknown route is rejected without an upstream request", async () => {
  const result = await main({ httpMethod: "GET", path: "/not-a-route", headers: {} }, { env });
  assert.equal(result.statusCode, 404);
});

test("modern idle and exit presence use one small RPC; legacy keeps v2", async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push([String(url), JSON.parse(options.body)]);
    return new Response(JSON.stringify({ accepted: true }), { status: 200 });
  };
  try {
    for (const state of ["online", "offline", null]) {
      const body = { working: false, device_id: "pc", sequence: 3,
        today_seconds: 999, work_plan: { private: true }, outfit_key: "x" };
      if (state) Object.assign(body, { presence_state: state, activity_state: "idle" });
      const result = await main({ httpMethod: "POST", path: "/presence/heartbeat",
        headers: { authorization: "Bearer user.jwt.token" }, body: JSON.stringify(body) }, { env });
      assert.equal(result.statusCode, 200);
    }
    assert.equal(calls.length, 3);
    assert.ok(calls[0][0].endsWith("lili_presence_heartbeat"));
    assert.equal(calls[0][1].p_activity_state, "idle");
    assert.equal(calls[1][1].p_presence_state, "offline");
    assert.ok(calls[2][0].endsWith("lili_upsert_focus_presence_v2"));
    assert.equal(Object.keys(calls[0][1]).length, 9);
    assert.ok(calls.every(([, body]) => !JSON.stringify(body).includes("private")));
  } finally { global.fetch = originalFetch; }
});
