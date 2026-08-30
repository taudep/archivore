import { SELF, env } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";

const AUTH = { Authorization: `Bearer ${env.QUEUE_API_TOKEN}`, "Content-Type": "application/json" };

async function seed(itemId: string) {
  await env.DB.prepare(
    `INSERT INTO queue (item_id, source, comments_url, status, queued_at, updated_at)
     VALUES (?, 'hn', 'https://x', 'pending', '2026-01-01', '2026-01-01')`
  )
    .bind(itemId)
    .run();
}

async function complete(items: unknown[]) {
  const res = await SELF.fetch("https://example.com/complete", {
    method: "POST",
    headers: AUTH,
    body: JSON.stringify({ items }),
  });
  return { status: res.status, body: (await res.json()) as { updated: number } };
}

beforeEach(async () => {
  await env.DB.exec("DELETE FROM queue");
});

describe("POST /complete", () => {
  it("marks an item done and stores its title and filename", async () => {
    await seed("1");
    const { status, body } = await complete([
      { item_id: "1", status: "done", title: "My Title", is_selfpost: false, filename: "1-my-title.md", last_error: null },
    ]);
    expect(status).toBe(200);
    expect(body.updated).toBe(1);

    const row = await env.DB.prepare("SELECT * FROM queue WHERE item_id = '1'").first();
    expect(row?.status).toBe("done");
    expect(row?.title).toBe("My Title");
    expect(row?.filename).toBe("1-my-title.md");
    expect(row?.is_selfpost).toBe(0);
  });

  it("marks an item failed and increments retries", async () => {
    await seed("1");
    await complete([{ item_id: "1", status: "failed", title: null, is_selfpost: null, filename: null, last_error: "HTTP 500" }]);

    const row = await env.DB.prepare("SELECT * FROM queue WHERE item_id = '1'").first();
    expect(row?.status).toBe("failed");
    expect(row?.last_error).toBe("HTTP 500");
    expect(row?.retries).toBe(1);
  });

  it("updates every item in a mixed batch", async () => {
    await seed("1");
    await seed("2");
    const { body } = await complete([
      { item_id: "1", status: "done", title: "T1", is_selfpost: false, filename: "1.md", last_error: null },
      { item_id: "2", status: "skipped", title: null, is_selfpost: null, filename: "2.md", last_error: "non-HTML" },
    ]);
    expect(body.updated).toBe(2);

    const row2 = await env.DB.prepare("SELECT * FROM queue WHERE item_id = '2'").first();
    expect(row2?.status).toBe("skipped");
    expect(row2?.filename).toBe("2.md");
  });

  it("returns 0 for an empty batch", async () => {
    const { body } = await complete([]);
    expect(body.updated).toBe(0);
  });

  it("returns 400 for a non-JSON request body", async () => {
    const res = await SELF.fetch("https://example.com/complete", {
      method: "POST",
      headers: AUTH,
      body: "not json",
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "invalid request body" });
  });

  it("returns 400 when items is present but not an array", async () => {
    const res = await SELF.fetch("https://example.com/complete", {
      method: "POST",
      headers: AUTH,
      body: JSON.stringify({ items: "not-an-array" }),
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "invalid request body" });
  });

  it.each([
    ["null", "null"],
    ["a bare array", "[1,2,3]"],
    ["a bare string", '"hello"'],
    ["a bare number", "42"],
  ])("returns 400 for a body that is valid JSON but not a plain object (%s)", async (_label, jsonBody) => {
    const res = await SELF.fetch("https://example.com/complete", {
      method: "POST",
      headers: AUTH,
      body: jsonBody,
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "invalid request body" });
  });
});
