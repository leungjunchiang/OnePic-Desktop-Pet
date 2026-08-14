"use strict";

const http = require("node:http");
const { main } = require("./index.js");

const port = Number(process.env.PORT || 9000);
const maxBodyBytes = 1024 * 1024;

function eventFromRequest(request, body) {
  const url = new URL(request.url || "/", `http://${request.headers.host || "localhost"}`);
  return {
    httpMethod: request.method,
    path: url.pathname,
    headers: request.headers,
    queryStringParameters: Object.fromEntries(url.searchParams.entries()),
    body,
    isBase64Encoded: false,
    requestContext: { http: { method: request.method, path: url.pathname } },
  };
}

const server = http.createServer((request, response) => {
  const chunks = [];
  let size = 0;
  let rejected = false;

  request.on("data", (chunk) => {
    size += chunk.length;
    if (size > maxBodyBytes) {
      rejected = true;
      request.destroy();
      return;
    }
    chunks.push(chunk);
  });

  request.on("end", async () => {
    if (rejected) {
      response.statusCode = 413;
      response.setHeader("Content-Type", "application/json; charset=utf-8");
      response.end(JSON.stringify({ error: "请求内容过大。" }));
      return;
    }
    const body = Buffer.concat(chunks).toString("utf8");
    const result = await main(eventFromRequest(request, body), { env: process.env });
    response.statusCode = Number(result?.statusCode || 200);
    for (const [name, value] of Object.entries(result?.headers || {})) response.setHeader(name, value);
    response.end(result?.body || "");
  });
});

server.listen(port, "0.0.0.0", () => {
  console.log(`lili-social-relay listening on ${port}`);
});