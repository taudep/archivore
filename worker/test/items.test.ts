import { SELF, env } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";

const AUTH = { Authorization: `Bearer ${env.QUEUE_API_TOKEN}` };

async function seed(itemId: string, updatedAt: string, status = "done") {
  await env.DB.prepare(
    `INSERT INTO queue (item_id, source, comments_url, status, queued_at, updated_at)
     VALUES (?, 'hn', 'https://x', ?, '2026-01-01', ?)`
  )
    .bind(itemId, status, updatedAt)
    .run();
}

async function seedWithSource(itemId: string, source: string, updatedAt: string) {
  await env.DB.prepare(
    `INSERT INTO queue (item_id, source, comments_url, status, queued_at, updated_at)
     VALUES (?, ?, 'https://x', 'done', '2026-01-01', ?)`
  )
    .bind(itemId, source, updatedAt)
    .run();
}

beforeEach(async () => {
  await env.DB.exec("DELETE FROM queue");
});

describe("GET /items", () => {
  it("returns every row when no filter is given", async () => {
    await seed("1", "2026-01-01");
    await seed("2", "2026-01-02");
    const res = await SELF.fetch("https://example.com/items", { headers: AUTH });
    const body = (await res.json()) as { items: { item_id: string }[] };
    expect(body.items.map((i) => i.item_id).sort()).toEqual(["1", "2"]);
  });

  it("filters by ?since=", async () => {
    await seed("1", "2026-01-01");
    await seed("2", "2026-01-05");
    const res = await SELF.fetch("https://example.com/items?since=2026-01-03", { headers: AUTH });
    const body = (await res.json()) as { items: { item_id: string }[] };
    expect(body.items.map((i) => i.item_id)).toEqual(["2"]);
  });

  it("orders by source ascending, then item_id descending", async () => {
    // Two sources ('hn' < 'reddit' lexicographically), each with two item_ids
    // chosen so that plain ascending/descending string sorts on item_id alone
    // (ignoring source) would produce a different order than the real
    // `ORDER BY source, item_id DESC`. This guards against a regression that
    // drops the source grouping or flips the item_id direction.
    //
    // String comparisons: "20" < "3" and "100" < "9" (compared char-by-char,
    // not numerically), so DESC on item_id puts "3" before "20" within hn,
    // and "9" before "100" within reddit.
    await seedWithSource("20", "hn", "2026-01-01");
    await seedWithSource("3", "hn", "2026-01-01");
    await seedWithSource("100", "reddit", "2026-01-01");
    await seedWithSource("9", "reddit", "2026-01-01");

    const res = await SELF.fetch("https://example.com/items", { headers: AUTH });
    const body = (await res.json()) as { items: { item_id: string; source: string }[] };

    // Expected by hand: hn group first (source ASC), item_id DESC within
    // each group -> ["3", "20"] then ["9", "100"].
    expect(body.items.map((i) => i.item_id)).toEqual(["3", "20", "9", "100"]);
  });
});
