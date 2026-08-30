import { SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";

describe("worker scaffold", () => {
  it("returns 404 for unknown routes", async () => {
    const res = await SELF.fetch("https://example.com/unknown");
    expect(res.status).toBe(404);
  });
});
