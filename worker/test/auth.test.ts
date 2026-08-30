import { SELF, env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import worker from "../src/index";
import type { Env } from "../src/types";

describe("auth", () => {
  it("rejects requests with no Authorization header", async () => {
    const res = await SELF.fetch("https://example.com/items");
    expect(res.status).toBe(401);
  });

  it("rejects requests with the wrong token", async () => {
    const res = await SELF.fetch("https://example.com/items", {
      headers: { Authorization: "Bearer wrong-token" },
    });
    expect(res.status).toBe(401);
  });

  it("accepts requests with the correct token", async () => {
    const res = await SELF.fetch("https://example.com/items", {
      headers: { Authorization: `Bearer ${env.QUEUE_API_TOKEN}` },
    });
    expect(res.status).not.toBe(401);
  });
});

describe("auth - missing QUEUE_API_TOKEN secret (server misconfiguration)", () => {
  // These tests call the worker's fetch handler directly with a deliberately
  // broken env, since the shared vitest.config.ts / Miniflare bindings always
  // provide a valid QUEUE_API_TOKEN (see vitest.config.ts's
  // `miniflare.bindings`). Calling `worker.fetch(request, env)` directly lets
  // us simulate the real-world case of `wrangler secret put QUEUE_API_TOKEN`
  // never having been run, without touching the shared test bindings used by
  // every other test file.

  it("does NOT authenticate a client sending the literal string 'Bearer undefined' when the secret is unset", async () => {
    // This is the exact vulnerability: `env.QUEUE_API_TOKEN` is `undefined`,
    // so the old `Bearer ${env.QUEUE_API_TOKEN}` template literal becomes the
    // literal string "Bearer undefined", which would match this request and
    // silently authenticate it.
    const brokenEnv = { ...env, QUEUE_API_TOKEN: undefined } as unknown as Env;
    const request = new Request("https://example.com/items", {
      headers: { Authorization: "Bearer undefined" },
    });

    const res = await worker.fetch(request, brokenEnv);

    expect(res.status).not.toBe(200);
    expect(res.status).toBe(500);
  });

  it("returns 500 with a clear misconfiguration error when QUEUE_API_TOKEN is an empty string", async () => {
    const brokenEnv = { ...env, QUEUE_API_TOKEN: "" } as Env;
    const request = new Request("https://example.com/items", {
      headers: { Authorization: "Bearer whatever" },
    });

    const res = await worker.fetch(request, brokenEnv);

    expect(res.status).toBe(500);
    const body = await res.json();
    expect(body).toEqual({ error: "server misconfigured: QUEUE_API_TOKEN is not set" });
  });

  it("still returns 401 (not 500) for a missing Authorization header when the server IS properly configured", async () => {
    // Regression guard: the fail-closed check for the secret must not
    // swallow the normal "client sent no/wrong token" case.
    const res = await SELF.fetch("https://example.com/items");
    expect(res.status).toBe(401);
    const body = await res.json();
    expect(body).toEqual({ error: "unauthorized" });
  });
});
