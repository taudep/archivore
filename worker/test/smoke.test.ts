import { SELF, env } from "cloudflare:test";
import { describe, expect, it } from "vitest";

describe("worker scaffold", () => {
  it("returns 404 for unknown routes", async () => {
    const res = await SELF.fetch("https://example.com/unknown", {
      headers: { Authorization: `Bearer ${env.QUEUE_API_TOKEN}` },
    });
    expect(res.status).toBe(404);
  });
});
