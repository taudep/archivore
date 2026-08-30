import type { ClaimRequestItem, ClaimResultItem, CompleteRequestItem, Env } from "./types";

function checkAuth(request: Request, env: Env): boolean {
  return request.headers.get("Authorization") === `Bearer ${env.QUEUE_API_TOKEN}`;
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
  const insertStmt = env.DB.prepare(
    `INSERT INTO queue (item_id, source, comments_url, article_url, status, queued_at, updated_at)
     VALUES (?, ?, ?, ?, 'pending', ?, ?)
     ON CONFLICT (item_id) DO NOTHING
     RETURNING item_id`
  );
  const insertResults = await env.DB.batch(
    items.map((i) =>
      insertStmt.bind(i.item_id, i.source, i.comments_url, i.article_url ?? null, now, now)
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

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (!checkAuth(request, env)) {
      return Response.json({ error: "unauthorized" }, { status: 401 });
    }

    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/claim") {
      return handleClaim(request, env);
    }
    if (request.method === "POST" && url.pathname === "/complete") {
      return handleComplete(request, env);
    }

    return Response.json({ error: "not found" }, { status: 404 });
  },
};
