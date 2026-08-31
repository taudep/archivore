import type { ClaimRequestItem, ClaimResultItem, CompleteRequestItem, Env } from "./types";

type AuthResult = "ok" | "unauthorized" | "misconfigured";

// A /claim batch is assumed to fully resolve (through /complete) within this
// window. If concurrency/batch-size changes ever make legitimate runs
// regularly exceed it, raise this value — a too-low threshold risks
// reclaiming a still-in-flight item and causing duplicate fetch work.
const STALE_PENDING_MINUTES = 60;

function checkAuth(request: Request, env: Env): AuthResult {
  if (!env.QUEUE_API_TOKEN) {
    // Fail closed: an unset/empty secret must never be treated as a valid
    // token to compare against (e.g. a client literally sending
    // "Authorization: Bearer undefined" must not authenticate).
    return "misconfigured";
  }
  if (request.headers.get("Authorization") !== `Bearer ${env.QUEUE_API_TOKEN}`) {
    return "unauthorized";
  }
  return "ok";
}

async function handleClaim(request: Request, env: Env): Promise<Response> {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "invalid request body" }, { status: 400 });
  }

  if (body === null || typeof body !== "object" || Array.isArray(body)) {
    return Response.json({ error: "invalid request body" }, { status: 400 });
  }
  const { items: rawItems } = body as { items?: unknown };
  if (rawItems !== undefined && !Array.isArray(rawItems)) {
    return Response.json({ error: "invalid request body" }, { status: 400 });
  }
  const items = (rawItems as ClaimRequestItem[] | undefined) ?? [];
  if (items.length === 0) {
    return Response.json({ results: [] });
  }

  const now = new Date().toISOString();
  const staleCutoff = new Date(Date.now() - STALE_PENDING_MINUTES * 60 * 1000).toISOString();
  const insertStmt = env.DB.prepare(
    `INSERT INTO queue (item_id, source, comments_url, article_url, status, queued_at, updated_at)
     VALUES (?, ?, ?, ?, 'pending', ?, ?)
     ON CONFLICT (item_id) DO UPDATE SET
       updated_at = excluded.updated_at
     WHERE queue.status = 'pending' AND queue.updated_at < ?
     RETURNING item_id`
  );
  const insertResults = await env.DB.batch(
    items.map((i) =>
      insertStmt.bind(
        i.item_id, i.source, i.comments_url, i.article_url ?? null, now, now, staleCutoff
      )
    )
  );

  const claimedIds = new Set(
    insertResults.flatMap((r) => r.results as { item_id: string }[]).map((r) => r.item_id)
  );

  const toLookUp = items.filter((i) => !claimedIds.has(i.item_id));
  const existingById = new Map<string, { status: string; retries: number }>();
  if (toLookUp.length > 0) {
    const placeholders = toLookUp.map(() => "?").join(",");
    const { results: rows } = await env.DB.prepare(
      `SELECT item_id, status, retries FROM queue WHERE item_id IN (${placeholders})`
    )
      .bind(...toLookUp.map((i) => i.item_id))
      .all();
    for (const r of rows as { item_id: string; status: string; retries: number }[]) {
      existingById.set(r.item_id, r);
    }
  }

  const results: ClaimResultItem[] = items.map((i) => {
    if (claimedIds.has(i.item_id)) {
      return { item_id: i.item_id, claimed: true, status: "pending", retries: 0 };
    }
    const existing = existingById.get(i.item_id);
    return {
      item_id: i.item_id,
      claimed: false,
      status: existing?.status ?? "pending",
      retries: existing?.retries ?? 0,
    };
  });

  return Response.json({ results });
}

async function handleComplete(request: Request, env: Env): Promise<Response> {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "invalid request body" }, { status: 400 });
  }

  if (body === null || typeof body !== "object" || Array.isArray(body)) {
    return Response.json({ error: "invalid request body" }, { status: 400 });
  }
  const { items: rawItems } = body as { items?: unknown };
  if (rawItems !== undefined && !Array.isArray(rawItems)) {
    return Response.json({ error: "invalid request body" }, { status: 400 });
  }
  const items = (rawItems as CompleteRequestItem[] | undefined) ?? [];
  if (items.length === 0) {
    return Response.json({ updated: 0 });
  }

  const now = new Date().toISOString();
  const stmt = env.DB.prepare(
    `UPDATE queue SET
       status = ?,
       title = COALESCE(?, title),
       is_selfpost = COALESCE(?, is_selfpost),
       filename = COALESCE(?, filename),
       last_error = ?,
       updated_at = ?,
       retries = retries + 1
     WHERE item_id = ?`
  );
  await env.DB.batch(
    items.map((i) =>
      stmt.bind(
        i.status,
        i.title ?? null,
        i.is_selfpost === null || i.is_selfpost === undefined ? null : i.is_selfpost ? 1 : 0,
        i.filename ?? null,
        i.last_error ?? null,
        now,
        i.item_id
      )
    )
  );

  return Response.json({ updated: items.length });
}

async function handleItems(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const since = url.searchParams.get("since");
  const stmt = since
    ? env.DB.prepare(
        "SELECT * FROM queue WHERE updated_at >= ? ORDER BY source, item_id DESC"
      ).bind(since)
    : env.DB.prepare("SELECT * FROM queue ORDER BY source, item_id DESC");
  const { results } = await stmt.all();
  return Response.json({ items: results });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const auth = checkAuth(request, env);
    if (auth === "misconfigured") {
      return Response.json(
        { error: "server misconfigured: QUEUE_API_TOKEN is not set" },
        { status: 500 }
      );
    }
    if (auth === "unauthorized") {
      return Response.json({ error: "unauthorized" }, { status: 401 });
    }

    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/claim") {
      return handleClaim(request, env);
    }
    if (request.method === "POST" && url.pathname === "/complete") {
      return handleComplete(request, env);
    }
    if (request.method === "GET" && url.pathname === "/items") {
      return handleItems(request, env);
    }

    return Response.json({ error: "not found" }, { status: 404 });
  },
};
