# archivore-queue

A small Cloudflare Worker + D1 database that coordinates reading-queue work across multiple
machines running `archivore` — so the same article is never fetched twice, without running a
server yourself. See [`../docs/superpowers/specs/2026-08-29-multi-machine-reading-queue-sync-design.md`](../docs/superpowers/specs/2026-08-29-multi-machine-reading-queue-sync-design.md)
for the full design rationale.

## Currently deployed instance

| Resource | Name |
|---|---|
| Worker | `archivore-queue` (`https://archivore-queue.taude.workers.dev`) |
| D1 database | `taude-archivore` |

The Worker and the database are separate Cloudflare resources with independent names —
that's intentional, not a naming mismatch. Every `wrangler d1` command below needs the
**database** name (`taude-archivore`), not the Worker name.

## API

Three endpoints, all behind `Authorization: Bearer <token>`, all batch-only (one call covers
every item a run needs — never one call per item):

- **`POST /claim`** — `{"items": [{"item_id", "source", "comments_url", "article_url"}]}` →
  `{"results": [{"item_id", "claimed", "status", "retries"}]}`. Atomic per-row via the
  `item_id` primary key plus `INSERT ... ON CONFLICT ... RETURNING`, so two machines racing to
  claim the same item can never both win. A `pending` row untouched for more than
  `STALE_PENDING_MINUTES` (60, see `src/index.ts`) is treated as an orphan from a crashed run
  and gets atomically reclaimed — jobs are idempotent, a crash self-heals on the next run
  without manual intervention.
- **`POST /complete`** — `{"items": [{"item_id", "status", "title", "is_selfpost", "filename", "last_error"}]}`
  → `{"updated": N}`. Reports a batch of outcomes (`done` / `failed` / `skipped`) after
  fetching. `title`/`is_selfpost`/`filename` use `COALESCE` against the existing row, so a
  completion that doesn't know a field (e.g. a `failed` result before metadata was resolved)
  doesn't null out a value written by an earlier attempt.
- **`GET /items`** — optionally `?since=<ISO timestamp>` → `{"items": [...]}`. Lists the full
  queue (or everything updated since a point in time), used by clients to rebuild their local
  index from global state rather than just what that machine fetched.

## Prerequisites

- A free Cloudflare account — no paid plan needed at this scale (Workers Free tier: 100k
  requests/day; D1 free tier: 5GB storage — a personal reading queue is nowhere close).
- Node.js and npm (or your package manager of choice).
- `wrangler` — install globally (`npm install -g wrangler`) or rely on the project-local one
  installed via `npm install` below.

## First-time setup (standing up your own instance)

Run these from this directory (`worker/`).

```bash
npm install
```

**1. Log in to Cloudflare** (opens a browser for OAuth):

```bash
wrangler login
```

**2. Create the D1 database:**

```bash
wrangler d1 create archivore-queue   # pick any name; the deployed instance uses "taude-archivore"
```

Copy the `database_id` from the output.

**3. Paste the `database_id` into `wrangler.toml`:**

```toml
[[d1_databases]]
binding = "DB"
database_name = "archivore-queue"        # whatever you named it in step 2
database_id = "<paste the real id here>"
migrations_dir = "migrations"
```

**4. Apply the schema migration to the real (remote) database:**

```bash
wrangler d1 migrations apply archivore-queue --remote   # use your database name from step 2
```

**5. Generate and set the real auth token:**

```bash
openssl rand -hex 32
wrangler secret put QUEUE_API_TOKEN
```

Paste the generated value when prompted. Save it somewhere durable (a password manager) — it
won't be shown again, and it's what goes into every machine's `queue_api_token` config (or the
`ARCHIVORE_QUEUE_API_TOKEN` environment variable — see the main [README](../README.md#setup)).

**6. Deploy:**

```bash
wrangler deploy
```

The output includes your live Worker URL (`https://<worker-name>.<your-subdomain>.workers.dev`)
— that's `queue_api_url` for every archivore client.

**7. Verify it for real:**

```bash
curl -X POST https://<your-worker-url>/claim \
  -H "Authorization: Bearer <your-real-token>" \
  -H "Content-Type: application/json" \
  -d '{"items": [{"item_id": "smoke-test-1", "source": "hn", "comments_url": "https://example.com", "article_url": null}]}'
```

Expected: `{"results":[{"item_id":"smoke-test-1","claimed":true,"status":"pending","retries":0}]}`.
Run it again — expected: `{"results":[{"item_id":"smoke-test-1","claimed":false,"status":"pending","retries":0}]}`
(proves dedup works against the real database). Clean up the smoke-test row:

```bash
wrangler d1 execute archivore-queue --remote --command "DELETE FROM queue WHERE item_id = 'smoke-test-1'"
```

## ⚠️ Before you redeploy: the `[vars]` gotcha

**Never add `QUEUE_API_TOKEN` (or any secret) to `wrangler.toml`'s `[vars]` block.** This bit us
in production once already: `wrangler deploy` syncs `wrangler.toml`'s `[vars]` on *every*
deploy, and if a var with the same name as a secret exists, deploying can silently overwrite or
remove the real secret set via `wrangler secret put`. This actually happened during
development — a code deploy wiped the live `QUEUE_API_TOKEN`, causing a real (safe — the
Worker fails closed with a 500, not open access) outage until the secret was re-set.

The only place `QUEUE_API_TOKEN` should ever have a fixed, known value is in
`vitest.config.ts`'s Miniflare `bindings` (for local test runs only — that mechanism is
never touched by `wrangler deploy`). The real secret lives *only* in Cloudflare's encrypted
secret store, set via `wrangler secret put`, and stays there across ordinary code deploys.

**Deploying to production is a real, hard-to-fully-reverse action** — treat every `wrangler
deploy` against the live instance deliberately, not as a routine step in a larger task.

## Local development and tests

```bash
npm install
npm test              # 35 tests, vitest + @cloudflare/vitest-pool-workers
npx tsc --noEmit       # type-check
wrangler dev           # local dev server (needs its own QUEUE_API_TOKEN — see the gotcha above;
                        # a .dev.vars file, gitignored, is the right place for a local-dev-only value)
```

## Redeploying after a code change

```bash
npm test && npx tsc --noEmit   # confirm green first
wrangler deploy
```

No new secret or D1 setup needed for an ordinary code change — the existing secret and database
binding carry over. Verify afterward the same way as step 7 above (a quick authenticated
`/claim` round trip) rather than assuming the deploy succeeded silently.

## Schema changes

Add a new numbered file under `migrations/` (e.g. `0002_*.sql`), then:

```bash
wrangler d1 migrations apply <database-name> --remote
```

Never edit `0001_init.sql` in place once it's been applied to the live database.
