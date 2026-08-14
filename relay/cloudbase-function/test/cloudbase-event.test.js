const test = require("node:test");
const assert = require("node:assert/strict");
const { main } = require("../index.js");

test("CloudBase HTTP events use the method field for preflight routes", async () => {
  const result = await main({ method: "OPTIONS", path: "/auth/signin", headers: {} }, {});
  assert.equal(result.statusCode, 204);
});