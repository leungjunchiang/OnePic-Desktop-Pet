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
