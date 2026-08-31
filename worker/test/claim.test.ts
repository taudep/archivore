import { SELF, env } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";

const AUTH = { Authorization: `Bearer ${env.QUEUE_API_TOKEN}`, "Content-Type": "application/json" };

async function claim(items: unknown[]) {
  const res = await SELF.fetch("https://example.com/claim", {
    method: "POST",
    headers: AUTH,
    body: JSON.stringify({ items }),
  });
  return { status: res.status, body: await res.json() as { results: unknown[] } };
}

beforeEach(async () => {
  await env.DB.exec("DELETE FROM queue");
});

describe("POST /claim", () => {
  it("claims a brand-new item", async () => {
    const { status, body } = await claim([
      { item_id: "1", source: "hn", comments_url: "https://x", article_url: null },
    ]);
    expect(status).toBe(200);
    expect(body.results).toEqual([{ item_id: "1", claimed: true, status: "pending", retries: 0 }]);
  });

  it("reports an already-done item as not claimed", async () => {
    await env.DB.prepare(
      `INSERT INTO queue (item_id, source, comments_url, status, queued_at, updated_at)
       VALUES ('1', 'hn', 'https://x', 'done', '2026-01-01', '2026-01-01')`
    ).run();

    const { body } = await claim([
      { item_id: "1", source: "hn", comments_url: "https://x", article_url: null },
    ]);
    expect(body.results).toEqual([{ item_id: "1", claimed: false, status: "done", retries: 0 }]);
  });

  it("reports retries for an already-failed item", async () => {
    await env.DB.prepare(
      `INSERT INTO queue (item_id, source, comments_url, status, retries, queued_at, updated_at)
       VALUES ('1', 'hn', 'https://x', 'failed', 2, '2026-01-01', '2026-01-01')`
    ).run();

    const { body } = await claim([
      { item_id: "1", source: "hn", comments_url: "https://x", article_url: null },
    ]);
    expect(body.results).toEqual([{ item_id: "1", claimed: false, status: "failed", retries: 2 }]);
  });

  it("handles a mixed batch of new and existing items in one call", async () => {
    await env.DB.prepare(
      `INSERT INTO queue (item_id, source, comments_url, status, queued_at, updated_at)
       VALUES ('existing', 'hn', 'https://x', 'done', '2026-01-01', '2026-01-01')`
    ).run();

    const { body } = await claim([
      { item_id: "new", source: "hn", comments_url: "https://y", article_url: null },
      { item_id: "existing", source: "hn", comments_url: "https://x", article_url: null },
    ]);
    expect(body.results).toEqual([
      { item_id: "new", claimed: true, status: "pending", retries: 0 },
      { item_id: "existing", claimed: false, status: "done", retries: 0 },
    ]);
  });

  it("returns an empty result for an empty batch without touching the database", async () => {
    const { body } = await claim([]);
    expect(body.results).toEqual([]);
  });

  it("returns 400 for a non-JSON request body", async () => {
    const res = await SELF.fetch("https://example.com/claim", {
      method: "POST",
      headers: AUTH,
      body: "not json",
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "invalid request body" });
  });

  it("returns 400 when items is present but not an array", async () => {
    const res = await SELF.fetch("https://example.com/claim", {
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
    const res = await SELF.fetch("https://example.com/claim", {
      method: "POST",
      headers: AUTH,
      body: jsonBody,
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "invalid request body" });
  });

  it("handles a larger batch mixing new and existing items with different statuses", async () => {
    await env.DB.prepare(
      `INSERT INTO queue (item_id, source, comments_url, status, queued_at, updated_at)
       VALUES ('done-1', 'hn', 'https://x', 'done', '2026-01-01', '2026-01-01')`
    ).run();
    await env.DB.prepare(
      `INSERT INTO queue (item_id, source, comments_url, status, retries, queued_at, updated_at)
       VALUES ('failed-1', 'hn', 'https://x', 'failed', 3, '2026-01-01', '2026-01-01')`
    ).run();
    const freshTimestamp = new Date(Date.now() - 2 * 60 * 1000).toISOString(); // 2 minutes ago, well under the staleness threshold
    await env.DB.prepare(
      `INSERT INTO queue (item_id, source, comments_url, status, queued_at, updated_at)
       VALUES ('pending-1', 'hn', 'https://x', 'pending', ?, ?)`
    )
      .bind(freshTimestamp, freshTimestamp)
      .run();

    const { body } = await claim([
      { item_id: "new-1", source: "hn", comments_url: "https://y", article_url: null },
      { item_id: "done-1", source: "hn", comments_url: "https://x", article_url: null },
      { item_id: "new-2", source: "hn", comments_url: "https://y", article_url: null },
      { item_id: "failed-1", source: "hn", comments_url: "https://x", article_url: null },
      { item_id: "pending-1", source: "hn", comments_url: "https://x", article_url: null },
    ]);

    expect(body.results).toEqual([
      { item_id: "new-1", claimed: true, status: "pending", retries: 0 },
      { item_id: "done-1", claimed: false, status: "done", retries: 0 },
      { item_id: "new-2", claimed: true, status: "pending", retries: 0 },
      { item_id: "failed-1", claimed: false, status: "failed", retries: 3 },
      { item_id: "pending-1", claimed: false, status: "pending", retries: 0 },
    ]);
  });

  it("reclaims a stale pending item (updated more than the staleness threshold ago)", async () => {
    const staleTimestamp = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(); // 2 hours ago
    await env.DB.prepare(
      `INSERT INTO queue (item_id, source, comments_url, status, queued_at, updated_at)
       VALUES ('stale-1', 'hn', 'https://x', 'pending', ?, ?)`
    )
      .bind(staleTimestamp, staleTimestamp)
      .run();

    const { body } = await claim([
      { item_id: "stale-1", source: "hn", comments_url: "https://x", article_url: null },
    ]);

    expect(body.results).toEqual([{ item_id: "stale-1", claimed: true, status: "pending", retries: 0 }]);
  });

  it("does NOT reclaim a fresh pending item (updated moments ago)", async () => {
    const freshTimestamp = new Date(Date.now() - 2 * 60 * 1000).toISOString(); // 2 minutes ago
    await env.DB.prepare(
      `INSERT INTO queue (item_id, source, comments_url, status, queued_at, updated_at)
       VALUES ('fresh-1', 'hn', 'https://x', 'pending', ?, ?)`
    )
      .bind(freshTimestamp, freshTimestamp)
      .run();

    const { body } = await claim([
      { item_id: "fresh-1", source: "hn", comments_url: "https://x", article_url: null },
    ]);

    expect(body.results).toEqual([{ item_id: "fresh-1", claimed: false, status: "pending", retries: 0 }]);
  });

  it("does not touch retries when reclaiming a stale pending item", async () => {
    const staleTimestamp = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(); // 2 hours ago
    await env.DB.prepare(
      `INSERT INTO queue (item_id, source, comments_url, status, retries, queued_at, updated_at)
       VALUES ('stale-2', 'hn', 'https://x', 'pending', 0, ?, ?)`
    )
      .bind(staleTimestamp, staleTimestamp)
      .run();

    const { body } = await claim([
      { item_id: "stale-2", source: "hn", comments_url: "https://x", article_url: null },
    ]);

    expect(body.results).toEqual([{ item_id: "stale-2", claimed: true, status: "pending", retries: 0 }]);

    const { results: rows } = await env.DB.prepare(
      "SELECT retries FROM queue WHERE item_id = ?"
    )
      .bind("stale-2")
      .all();
    expect((rows as { retries: number }[])[0].retries).toBe(0);
  });
});
