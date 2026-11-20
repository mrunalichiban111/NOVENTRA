import assert from "node:assert/strict";
import test from "node:test";

import { ApiError, executeApiRequest } from "./api-response.js";

test("200 with JSON body succeeds", async () => {
  const result = await executeApiRequest(
    "/public/contact",
    undefined,
    async () => new Response('{"status":"ok"}', {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  assert.deepEqual(result, { status: "ok" });
});

test("200 with empty body succeeds", async () => {
  const result = await executeApiRequest("/public/contact", undefined,
    async () => new Response(null, { status: 200 }));
  assert.deepEqual(result, {});
});

test("204 succeeds", async () => {
  const result = await executeApiRequest("/public/contact", undefined,
    async () => new Response(null, { status: 204 }));
  assert.deepEqual(result, {});
});

test("429 returns the rate-limit message", async () => {
  await assert.rejects(
    executeApiRequest("/public/contact", undefined,
      async () => new Response('{"detail":"limited"}', { status: 429 })),
    (error) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, 429);
      assert.equal(error.code, "rate_limited");
      assert.equal(error.message, "Too many requests. Please wait a minute and try again.");
      return true;
    },
  );
});

test("502 returns the SMTP failure message", async () => {
  await assert.rejects(
    executeApiRequest("/public/contact", undefined,
      async () => new Response('{"detail":"send failed"}', { status: 502 })),
    (error) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, 502);
      assert.equal(error.code, "smtp_failure");
      assert.equal(error.message, "Couldn't send right now, please try again");
      return true;
    },
  );
});

test("network failure returns the network message", async () => {
  await assert.rejects(
    executeApiRequest("/public/contact", undefined,
      async () => { throw new TypeError("Failed to fetch"); }),
    (error) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, 0);
      assert.equal(error.code, "network");
      assert.equal(error.message, "Could not reach the server. Check your connection and try again.");
      return true;
    },
  );
});
