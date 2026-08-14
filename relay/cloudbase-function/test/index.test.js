const test = require("node:test");
const assert = require("node:assert/strict");
const { main } = require("../index.js");

test("health endpoint is public and reports CloudBase relay", async () => {
  const previousUrl = process.env.SUPABASE_URL;
  const previousKey = process.env.SUPABASE_PUBLISHABLE_KEY;
  process.env.SUPABASE_URL = "https://example.supabase.co";
  process.env.SUPABASE_PUBLISHABLE_KEY = "sb_publishable_test";
  try {
    const result = await main({ httpMethod: "GET", path: "/lili-social-relay-v2/health", headers: {} }, {});
    assert.equal(result.statusCode, 200);
    const body = JSON.parse(result.body);
    assert.equal(body.ok, true);
    assert.equal(body.backend, "cloudbase-function");
    assert.equal(body.supabase_configured, true);
  } finally {
    if (previousUrl === undefined) delete process.env.SUPABASE_URL;
    else process.env.SUPABASE_URL = previousUrl;
    if (previousKey === undefined) delete process.env.SUPABASE_PUBLISHABLE_KEY;
    else process.env.SUPABASE_PUBLISHABLE_KEY = previousKey;
  }
});

test("unknown route is rejected without contacting Supabase", async () => {
  const result = await main({ httpMethod: "GET", path: "/not-a-route", headers: {} }, {});
  assert.equal(result.statusCode, 404);
  assert.match(result.body, /找不到/);
});
