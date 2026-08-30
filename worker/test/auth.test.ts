import { SELF, env } from "cloudflare:test";
import { describe, expect, it } from "vitest";

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
