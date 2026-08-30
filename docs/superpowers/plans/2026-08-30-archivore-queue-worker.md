# Archivore Queue Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and deploy the Cloudflare Worker + D1 coordination API (`/claim`, `/complete`, `/items`) that lets multiple archivore machines dedup reading-queue work without a server anyone runs.

**Architecture:** A single-file Worker (`src/index.ts`) routes three endpoints against one D1 `queue` table. `/claim` and `/complete` are batch-only (array in, array/count out) — never one call per item. Auth is a single shared bearer token checked against a Worker secret.

**Tech Stack:** TypeScript, Cloudflare Workers, D1 (SQLite-compatible), Wrangler CLI, Vitest + `@cloudflare/vitest-pool-workers`.

## Global Constraints

- Free-tier Cloudflare only — no paid plan, no infrastructure the user runs or patches.
- `/claim` and `/complete` are batch endpoints. Never design a handler that only accepts one item_id per call.
- Per-row claim atomicity comes from the `item_id` PRIMARY KEY constraint plus `ON CONFLICT DO NOTHING RETURNING`, not application logic.
- Auth: `Authorization: Bearer <token>` header, checked against `env.QUEUE_API_TOKEN`, on every request including `/items`.
- Node/npm/Wrangler are already installed on this machine (`wrangler 4.127.1` confirmed) — no toolchain installation steps needed.

---

## File Structure

```
worker/
  package.json           - npm project, scripts, devDependencies
  tsconfig.json           - TypeScript compiler config for Workers
  wrangler.toml            - Worker + D1 binding config
  vitest.config.ts         - test runner config (vitest-pool-workers)
  migrations/
    0001_init.sql          - queue table schema
  src/
    types.ts               - Env interface + request/response shapes
    index.ts                - router, auth, and all three handlers
  test/
    auth.test.ts
    claim.test.ts
    complete.test.ts
    items.test.ts
```

One handler file is intentional: three endpoints over one table is small enough that splitting further would be premature — see the spec's Components section, which describes this as "a small HTTPS API."

---

### Task 1: Scaffold the worker project

**Files:**
- Create: `worker/package.json`
- Create: `worker/tsconfig.json`
- Create: `worker/wrangler.toml`
- Create: `worker/migrations/0001_init.sql`
- Create: `worker/src/types.ts`
- Create: `worker/src/index.ts` (stub)
- Create: `worker/vitest.config.ts`
- Create: `worker/test/smoke.test.ts`

**Interfaces:**
- Produces: `Env` interface (`DB: D1Database`, `QUEUE_API_TOKEN: string`) in `src/types.ts`, used by every later task.

- [ ] **Step 1: Create the project directory and npm project**

```bash
mkdir -p worker/src worker/test worker/migrations
cd worker
npm init -y
```

- [ ] **Step 2: Install dependencies**

```bash
npm install -D typescript wrangler vitest @cloudflare/workers-types @cloudflare/vitest-pool-workers
```

- [ ] **Step 3: Write `worker/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022"],
    "module": "ES2022",
    "moduleResolution": "Bundler",
    "types": ["@cloudflare/workers-types"],
    "strict": true,
    "skipLibCheck": true,
    "noEmit": true
  },
  "include": ["src/**/*.ts", "test/**/*.ts"]
}
```

- [ ] **Step 4: Write `worker/migrations/0001_init.sql`**

```sql
CREATE TABLE queue (
    item_id      TEXT PRIMARY KEY,
    source       TEXT NOT NULL DEFAULT 'hn',
    title        TEXT,
    article_url  TEXT,
    comments_url TEXT NOT NULL,
    is_selfpost  INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'pending',
    retries      INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    filename     TEXT,
    queued_at    TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX idx_queue_status ON queue(status);
CREATE INDEX idx_queue_source ON queue(source);
```

- [ ] **Step 5: Write `worker/wrangler.toml`**

```toml
name = "archivore-queue"
main = "src/index.ts"
compatibility_date = "2024-09-23"

[[d1_databases]]
binding = "DB"
database_name = "taude-archivore"
database_id = "placeholder-replaced-in-task-6"
migrations_dir = "migrations"
```

Note: `name` (the Worker's own service name, `archivore-queue`) and `database_name`
(the D1 database, `taude-archivore`) are deliberately different — they're separate
Cloudflare resources with independent names, not a naming inconsistency. Every `d1`
CLI command below (`d1 create`, `d1 migrations apply`, `d1 execute`) must use the
database name, `taude-archivore`, not the Worker name.

- [ ] **Step 6: Write `worker/src/types.ts`**

```typescript
export interface Env {
  DB: D1Database;
  QUEUE_API_TOKEN: string;
}

export interface ClaimRequestItem {
  item_id: string;
  source: string;
  comments_url: string;
  article_url: string | null;
}

export interface ClaimResultItem {
  item_id: string;
  claimed: boolean;
  status: string;
  retries: number;
}

export interface CompleteRequestItem {
  item_id: string;
  status: string;
  title: string | null;
  is_selfpost: boolean | null;
  filename: string | null;
  last_error: string | null;
}
```

- [ ] **Step 7: Write a stub `worker/src/index.ts`**

```typescript
import type { Env } from "./types";

export default {
  async fetch(_request: Request, _env: Env): Promise<Response> {
    return Response.json({ error: "not found" }, { status: 404 });
  },
};
```

- [ ] **Step 8: Write `worker/vitest.config.ts`**

```typescript
import { defineWorkersConfig } from "@cloudflare/vitest-pool-workers/config";

export default defineWorkersConfig({
  test: {
    poolOptions: {
      workers: {
        wrangler: { configPath: "./wrangler.toml" },
      },
    },
  },
});
```

- [ ] **Step 9: Write the failing smoke test `worker/test/smoke.test.ts`**

```typescript
import { SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";

describe("worker scaffold", () => {
  it("returns 404 for unknown routes", async () => {
    const res = await SELF.fetch("https://example.com/unknown");
    expect(res.status).toBe(404);
  });
});
```

- [ ] **Step 10: Add a `test` script to `package.json`**

Edit `worker/package.json`, add under `"scripts"`:

```json
"test": "vitest run"
```

- [ ] **Step 11: Run the test and verify it passes**

```bash
cd worker && npm test
```

Expected: 1 test passes (`returns 404 for unknown routes`). If `vitest-pool-workers` reports it can't find the D1 binding, confirm `wrangler.toml`'s `[[d1_databases]]` block is present — the pool provisions a local D1 instance from that config even though `database_id` is still a placeholder at this point (it's not used for local test runs).

- [ ] **Step 12: Commit**

```bash
git add worker/
git commit -m "Scaffold archivore-queue Worker project"
```

---

### Task 2: Bearer-token auth

**Files:**
- Modify: `worker/src/index.ts`
- Test: `worker/test/auth.test.ts`

**Interfaces:**
- Consumes: `Env` from `src/types.ts` (Task 1).
- Produces: `checkAuth(request: Request, env: Env): boolean`, used by every handler task from here on.

- [ ] **Step 1: Write the failing test `worker/test/auth.test.ts`**

```typescript
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
```

- [ ] **Step 2: Set a test token binding**

Edit `worker/wrangler.toml`, add at the bottom (this value is only used for local `vitest`/`wrangler dev` runs — the real deployed secret is set separately in Task 6 and always overrides this):

```toml
[vars]
QUEUE_API_TOKEN = "test-token"
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
cd worker && npm test
```

Expected: FAIL — the stub handler returns 404 for everything regardless of auth, so the two 401 assertions fail (the third, "accepts," incidentally passes since 404 !== 401 — that's fine, it's the two rejection cases that must currently fail).

- [ ] **Step 4: Implement auth in `worker/src/index.ts`**

```typescript
import type { Env } from "./types";

function checkAuth(request: Request, env: Env): boolean {
  return request.headers.get("Authorization") === `Bearer ${env.QUEUE_API_TOKEN}`;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (!checkAuth(request, env)) {
      return Response.json({ error: "unauthorized" }, { status: 401 });
    }
    return Response.json({ error: "not found" }, { status: 404 });
  },
};
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd worker && npm test
```

Expected: 4 tests pass (1 from Task 1, 3 from this task).

- [ ] **Step 6: Commit**

```bash
git add worker/
git commit -m "Add bearer-token auth to the queue Worker"
```

---

### Task 3: `POST /claim`

**Files:**
- Modify: `worker/src/index.ts`
- Test: `worker/test/claim.test.ts`

**Interfaces:**
- Consumes: `Env`, `ClaimRequestItem`, `ClaimResultItem` from `src/types.ts`; `checkAuth` from Task 2.
- Produces: the `/claim` route, exercised directly by later tasks' manual verification and by the Python `queue_api.claim()` client (separate plan).

- [ ] **Step 1: Write the failing test `worker/test/claim.test.ts`**

```typescript
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
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd worker && npm test
```

Expected: FAIL — all `/claim` requests currently hit the stub's 401/404 fallthrough (404, since auth now passes with the right token, then the router has no `/claim` case).

- [ ] **Step 3: Implement `/claim` in `worker/src/index.ts`**

```typescript
import type { ClaimRequestItem, ClaimResultItem, Env } from "./types";

function checkAuth(request: Request, env: Env): boolean {
  return request.headers.get("Authorization") === `Bearer ${env.QUEUE_API_TOKEN}`;
}

async function handleClaim(request: Request, env: Env): Promise<Response> {
  const body = (await request.json()) as { items: ClaimRequestItem[] };
  const items = body.items ?? [];
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

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (!checkAuth(request, env)) {
      return Response.json({ error: "unauthorized" }, { status: 401 });
    }

    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/claim") {
      return handleClaim(request, env);
    }

    return Response.json({ error: "not found" }, { status: 404 });
  },
};
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd worker && npm test
```

Expected: 9 tests pass (4 previous + 5 new).

- [ ] **Step 5: Commit**

```bash
git add worker/
git commit -m "Implement batch POST /claim"
```

---

### Task 4: `POST /complete`

**Files:**
- Modify: `worker/src/index.ts`
- Test: `worker/test/complete.test.ts`

**Interfaces:**
- Consumes: `Env`, `CompleteRequestItem` from `src/types.ts`; `checkAuth` from Task 2.
- Produces: the `/complete` route.

- [ ] **Step 1: Write the failing test `worker/test/complete.test.ts`**

```typescript
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
  return { status: res.status, body: await res.json() as { updated: number } };
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
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd worker && npm test
```

Expected: FAIL — `/complete` isn't routed yet (404).

- [ ] **Step 3: Implement `/complete` in `worker/src/index.ts`**

Add this handler and route it (full file shown; new pieces are `handleComplete` and its dispatch in `fetch`):

```typescript
import type { ClaimRequestItem, ClaimResultItem, CompleteRequestItem, Env } from "./types";

function checkAuth(request: Request, env: Env): boolean {
  return request.headers.get("Authorization") === `Bearer ${env.QUEUE_API_TOKEN}`;
}

async function handleClaim(request: Request, env: Env): Promise<Response> {
  const body = (await request.json()) as { items: ClaimRequestItem[] };
  const items = body.items ?? [];
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
  const body = (await request.json()) as { items: CompleteRequestItem[] };
  const items = body.items ?? [];
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
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd worker && npm test
```

Expected: 13 tests pass (9 previous + 4 new).

- [ ] **Step 5: Commit**

```bash
git add worker/
git commit -m "Implement batch POST /complete"
```

---

### Task 5: `GET /items`

**Files:**
- Modify: `worker/src/index.ts`
- Test: `worker/test/items.test.ts`

**Interfaces:**
- Consumes: `Env` from `src/types.ts`; `checkAuth` from Task 2.
- Produces: the `/items` route — the data source `render.write_index()` will consume on the Python side (separate plan).

- [ ] **Step 1: Write the failing test `worker/test/items.test.ts`**

```typescript
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
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd worker && npm test
```

Expected: FAIL — `/items` isn't routed yet (404).

- [ ] **Step 3: Implement `/items` in `worker/src/index.ts`**

Add `handleItems` and route it:

```typescript
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
```

And in `fetch`'s router, add before the final `return Response.json({ error: "not found" }, ...)`:

```typescript
    if (request.method === "GET" && url.pathname === "/items") {
      return handleItems(request, env);
    }
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd worker && npm test
```

Expected: 15 tests pass (13 previous + 2 new).

- [ ] **Step 5: Commit**

```bash
git add worker/
git commit -m "Implement GET /items"
```

---

### Task 6: Deploy for real

**Files:**
- Modify: `worker/wrangler.toml` (replace placeholder `database_id`)

This task requires interactive steps only the account owner can complete — `wrangler login` opens a browser against the user's own Cloudflare account. Run these yourself; they aren't automatable by an agent.

- [ ] **Step 1: Log in to Cloudflare**

```bash
cd worker && wrangler login
```

Expected: opens a browser tab, completes OAuth, and prints "Successfully logged in."

- [ ] **Step 2: Create the D1 database**

```bash
wrangler d1 create taude-archivore
```

Expected output includes a `[[d1_databases]]` block with a real `database_id`. Copy that `database_id`.

- [ ] **Step 3: Paste the real database_id into `worker/wrangler.toml`**

Replace the `database_id = "placeholder-replaced-in-task-6"` line with the real value from Step 2.

- [ ] **Step 4: Apply the migration to the real (remote) database**

```bash
wrangler d1 migrations apply taude-archivore --remote
```

Expected: confirms `0001_init.sql` applied.

- [ ] **Step 5: Set the real auth secret**

Generate a real random token and store it — this exact value also goes into every machine's `config.yaml` in the client-integration plan:

```bash
openssl rand -hex 32
wrangler secret put QUEUE_API_TOKEN
```

Paste the generated value when prompted. Save it somewhere durable (e.g. a password manager) — it won't be shown again.

- [ ] **Step 6: Deploy**

```bash
wrangler deploy
```

Expected output includes the live Worker URL, e.g. `https://archivore-queue.<your-subdomain>.workers.dev`. Record this URL — it's `queue_api_url` in the client-integration plan.

- [ ] **Step 7: Verify the deployed Worker for real**

Using the URL and token from Steps 5–6:

```bash
curl -X POST https://archivore-queue.<your-subdomain>.workers.dev/claim \
  -H "Authorization: Bearer <your-real-token>" \
  -H "Content-Type: application/json" \
  -d '{"items": [{"item_id": "smoke-test-1", "source": "hn", "comments_url": "https://example.com", "article_url": null}]}'
```

Expected: `{"results":[{"item_id":"smoke-test-1","claimed":true,"status":"pending","retries":0}]}`

Run it a second time — expected: `{"results":[{"item_id":"smoke-test-1","claimed":false,"status":"pending","retries":0}]}` (proves the dedup actually works against the real deployed database, not just the local test suite).

- [ ] **Step 8: Clean up the smoke-test row**

```bash
wrangler d1 execute taude-archivore --remote --command "DELETE FROM queue WHERE item_id = 'smoke-test-1'"
```

- [ ] **Step 9: Commit the real database_id**

```bash
git add worker/wrangler.toml
git commit -m "Deploy archivore-queue Worker; record real D1 database_id"
```

---

## Self-Review Notes

- **Spec coverage:** batch `/claim` (Task 3), batch `/complete` (Task 4), `GET /items` with `?since=` (Task 5), bearer auth on every route (Task 2), D1 schema matching the spec exactly (Task 1), free-tier-only deploy with no paid resources (Task 6). The `RETURNING`-based atomicity and `D1Database.batch()` usage both match the spec's Components section.
- **Deviation from the spec's SQL sketch, noted deliberately:** the spec illustrated `/claim` as one hand-built multi-row `VALUES (...), (...), ...` statement. This plan implements it as `N` single-row `INSERT ... RETURNING` statements passed to `D1Database.batch()` instead — Cloudflare's documented pattern for "many similar statements, one round trip." Same atomicity guarantee, same one-HTTP-call-per-run property, simpler to implement correctly (no dynamic placeholder-count SQL string building).
- **Placeholder scan:** every step has real, complete code — no TBD/TODO.
- **Type consistency:** `ClaimRequestItem`/`ClaimResultItem`/`CompleteRequestItem` defined once in `src/types.ts` (Task 1) and used identically by name in every later task.

---

**Plan complete and saved to `docs/superpowers/plans/2026-08-30-archivore-queue-worker.md`.**
