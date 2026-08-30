# Multi-machine reading queue sync

## Context

Archivore currently runs independently on each machine, each with its own
local `hn_this_week/queue.db` and `hn_this_week/*.md` files. If the same HN
thread, Reddit post, or X status is opened on two machines, each machine
fetches and converts it separately — duplicate network calls, and each
machine's local `RAW/` corpus (see below) only reflects what *that* machine
personally read and fetched. Neither machine's `qmd` semantic index sees the
other's content.

The goal: dedup reading-queue work across all machines, and end up with
every machine holding the full article corpus for search, without running
or maintaining a server or a remote database.

## Goals

- No duplicate fetch/convert work when the same article is opened on
  multiple machines.
- Every machine ends up with the complete set of fetched Markdown, so a
  local `qmd` index run from any machine covers everything read anywhere.
- No infrastructure the user has to run or patch. Free-tier hosted services
  are acceptable if self-provisioned (not something Anthropic/a third party
  operates on the user's behalf).
- `~/tabs.db` (open tabs + domain-level browsing history from
  `archivore snapshot`) is explicitly **out of scope** — it stays local and
  per-machine, duplicated freely. Only the reading-digest queue
  (`archivore run`) is being centralized.

## Non-goals

- Multi-user support. This is one person's own machines; auth is a single
  shared secret, not per-user accounts.
- Offline queueing / local fallback when the coordination API is
  unreachable. A run that can't reach the API fails fast rather than
  risking divergent local state.
- Centralizing article *content* fetching/storage. Fetching still happens
  locally on whichever machine claims an item; only claim/status
  coordination is centralized.

## Architecture

Two independent layers, each doing exactly one job:

1. **Coordination layer** — a Cloudflare Worker + D1 database (Cloudflare's
   free tier; no server the user runs or patches). Its only responsibility:
   tracking which item_ids have been claimed and their fetch status.
   It never sees or stores article content.
2. **Content layer** — the existing local resolve/fetch/convert pipeline,
   unchanged, writing into a `RAW/` folder inside the user's Obsidian vault.
   iCloud Drive syncs that folder across machines automatically — no sync
   code needed for content at all.

Each machine still runs `archivore run` against its own local browser
history. The only behavioral change: instead of deduping against a local
SQLite queue, it asks the Worker whether each item has already been
claimed — in one batched call covering everything discovered in that run,
not one call per item. Network round trips are the expensive part here
(internet latency), so the client always gathers the full set of candidate
item_ids *before* making any coordination call, then makes exactly one
`/claim` call and (later) one `/complete` call per run, regardless of how
many items that involves.

```
machine A                    Cloudflare Worker + D1         machine B
    │                                 │                          │
    │  local history scan            │                          │  local history scan
    │  extract ALL item_ids          │                          │  extract ALL item_ids
    ▼  (X, Y, Z — no network yet)     │                          ▼  (X, W — no network yet)
    │  POST /claim {items: [X,Y,Z]}   │                          │
    ├────────────────────────────────►│                          │
    │◄── {X: claimed, Y: claimed,  ───┤                          │
    │     Z: claimed}                 │      POST /claim {items: [X,W]}
    ▼                                 │◄─────────────────────────┤
 fetch + convert X, Y, Z locally      │── {X: taken/done,───────►│
    │  write RAW/{x,y,z}.md           │    W: claimed}           ▼
    │  POST /complete {items:         │                    fetch + convert W only
    │    [X:done, Y:done, Z:failed]}  │                    (X already handled by A)
    ├────────────────────────────────►│                          │
    ▼                                 │                          │  POST /complete {items: [W:done]}
 iCloud syncs RAW/*.md ───────────────┼──────────────────────────┼──────────────►
                                       │                          ▼
                                       │                    iCloud syncs RAW/w.md;
                                       │                    x.md, y.md appear here too
```

## Components

### 1. Cloudflare Worker (`archivore-queue`)

A small HTTPS API, three endpoints. `/claim` and `/complete` are both
**batch** endpoints — one HTTP call covers every item_id a run needs,
never one call per item.

- **`POST /claim`** — body `{items: [{item_id, source, comments_url,
  article_url?}, ...]}`. Implemented as a single bulk statement:
  ```sql
  INSERT INTO queue (item_id, source, comments_url, article_url, status,
                      queued_at, updated_at)
  VALUES (?,?,?,?,'pending',?,?), (?,?,?,?,'pending',?,?), ...
  ON CONFLICT (item_id) DO NOTHING
  RETURNING item_id
  ```
  `RETURNING` reports exactly which item_ids were newly inserted — rows
  that hit the conflict (already existed) are silently excluded from it,
  so no separate "check rows written" step is needed. The primary-key
  constraint still enforces atomicity per-row exactly as in the
  single-item version; batching many rows into one statement doesn't
  weaken that.

  For any requested item_id *not* in the `RETURNING` set, a single
  follow-up `SELECT item_id, status, retries FROM queue WHERE item_id IN
  (...)` fetches their current state. Response:
  ```json
  {"results": [
    {"item_id": "X", "claimed": true,  "status": "pending", "retries": 0},
    {"item_id": "Y", "claimed": false, "status": "done",    "retries": 0},
    {"item_id": "Z", "claimed": false, "status": "failed",  "retries": 2}
  ]}
  ```
- **`POST /complete`** — body `{items: [{item_id, status, filename?,
  last_error?}, ...]}`. The Worker executes the per-item `UPDATE`
  statements via `D1Database.batch()` — Cloudflare's API for running
  multiple statements in one round trip to D1. This is still exactly one
  HTTP call from the Python client's perspective; the fact that the Worker
  internally issues several small statements to D1 doesn't matter, since
  that hop is same-datacenter, not over the client's internet connection.
- **`GET /items`** — returns the full `queue` table (or rows updated since
  an optional `?since=` timestamp), so any machine can rebuild `index.md`
  from global state. Already a single bulk call by nature; unaffected by
  this change.

Auth: a single shared bearer token, checked against a Worker secret set via
`wrangler secret put QUEUE_API_TOKEN`. Same token is copied into each
machine's `config.yaml` — same handling as the existing `smtp_password`
(credential, `chmod 600` the config file).

Written in TypeScript (Cloudflare's primary-supported language for
Workers + D1; Python Workers support is newer and less documented — not
worth the mismatch with the rest of the Python codebase for a few dozen
lines of code).

### 2. D1 database schema

One `queue` table, functionally identical to today's local schema:

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

### 3. Archivore (Python) changes

- New `archivore/clients/queue_api.py` — thin HTTP client with a batch-first
  interface: `claim(items: list[ClaimRequest]) -> list[ClaimResult]`,
  `complete(items: list[CompleteRequest]) -> None`, `list_items() ->
  list[dict]`. This is a deliberate departure from today's
  `archivore/repository/queue.py`, whose `insert()`/`mark()` are
  single-item — `commands/run.py`'s pipeline changes from "queue each item
  as it's discovered" to "discover everything, then claim the whole batch
  in one call."
- `archivore/repository/queue.py` (local SQLite queue) is removed. No
  offline fallback mode — see Non-goals.
- `archivore/config.py` additions:
  ```python
  queue_api_url: str | None = None
  queue_api_token: str | None = None
  ```
- `Config.output_dir` default changes from a repo-local `hn_this_week/` to:
  ```python
  output_dir: Path = field(
      default_factory=lambda: Path.home()
      / "Library/Mobile Documents/com~apple~CloudDocs"
      / "Todd's Obsidian Vault/Archivore/Raw"
  )
  ```
  i.e. `~/Library/Mobile Documents/com~apple~CloudDocs/Todd's Obsidian
  Vault/Archivore/Raw/` — the standard macOS local mount point for iCloud
  Drive, inside the existing Obsidian vault.

  This is only the *default*. `output_dir` is a normal `Config` field like
  any other (already in `_PATH_FIELDS`, so `load_config()` expands `~` and
  applies overrides the same way it does for `db_path`/`md_path`/`log_path`
  today) — every machine's `config.yaml` can set its own `output_dir` to
  point wherever that machine's vault actually lives. This matters in
  practice, not just hypothetically: any machine that isn't on this Apple ID
  or doesn't use this exact vault name needs its own value, and that's a
  first-class, fully-supported override, not a fallback edge case.
- `archivore/render.write_index()` now builds `index.md` from
  `queue_api.list_items()` (global state) instead of a local queue
  connection.
- `hn_this_week/queue.db` goes away entirely — D1 is now the only queue
  store.

## Data Flow

Per `archivore run` invocation, on any machine:

1. Scan local browser history since that machine's own `last_run`
   watermark (unchanged — browsing history is inherently per-machine, this
   stays local).
2. Extract HN/Reddit/X item_ids from that history — **all sources, fully,
   in memory** — before any network call to the coordination API happens.
   This is unchanged as pure local functions in `sources.py`; what changes
   is that the *caller* now waits until this is completely done before
   talking to the Worker at all.
3. One `POST /claim` call with every discovered item_id from step 2.
   Partition the response into a to-fetch set:
   - `claimed: true` → new item, fetch it.
   - `claimed: false, status` in `done`/`skipped` → nothing to do. The
     content either already exists locally (synced via iCloud) or will
     appear once the claiming machine finishes.
   - `claimed: false, status: "failed", retries < max_retries` → also
     goes into the to-fetch set (retry-eligible; the eventual `/complete`
     call updates the existing row rather than inserting a new one).
   - `claimed: false, status: "failed", retries >= max_retries` → nothing
     to do, permanently given up.
4. Run phase 1 (resolve metadata) for the to-fetch set, accumulating each
   outcome in memory. Self-posts are fully resolved in this phase (no
   phase-2 fetch needed) — flush them with one `POST /complete` call at
   the end of phase 1, before phase 2 starts. This bounds how much a
   phase-2 crash can lose: phase 1's completions are already durably
   recorded by the time phase 2 begins.
5. Run phase 2 (download + convert) for the remaining to-fetch set,
   writing `.md` files into `RAW/`, again accumulating outcomes in memory.
   Flush with a second `POST /complete` call at the end of phase 2.
6. `GET /items` to pull the full global done/skipped set and regenerate
   `index.md` — reflecting the whole cross-machine corpus, not just this
   run's local work.
7. Run `qmd embed` against `RAW/` as before — now indexing everything
   iCloud has synced in, regardless of which machine fetched it.

This brings the coordination-API cost of a run down to **4 HTTP calls
total** (`/claim`, two `/complete` flushes, `/items`), independent of how
many items were discovered — versus 2N+1 calls under a naive per-item
approach.

## Error Handling / Edge Cases

- **Concurrent claim race** — resolved by the atomic
  `INSERT ... ON CONFLICT` at the D1 level (primary-key constraint enforced
  by the storage engine, not application logic). No window where two
  machines can both believe they're the claimer.
- **Claiming machine fails to fetch** — item sits with `status='failed'`
  and incremented `retries` centrally. Any machine (not just the original
  claimer) may retry it on a later run, since `/claim` on an existing
  `failed` item with `retries < max_retries` should be treated as
  eligible-to-attempt by whichever machine asks next — the original
  claimer might be asleep or offline. Implementation detail: `/claim`
  returns `retries` in its response precisely so the client can decide
  whether to attempt a retry itself.
- **Stuck `pending` claims** — if a machine claims a batch of items and
  crashes or is killed before the corresponding `/complete` flush, none of
  those items get marked `failed`, so no other machine will pick them up
  (only `failed` items are retry-eligible; `pending` isn't). Batching
  `/complete` per-phase (rather than per-item) bounds this to at most one
  phase's worth of items per crash, not the item that happened to be
  in-flight — a real but accepted trade-off for going from O(N) to O(1)
  network calls. Accepted as a known v1 limitation: rare in practice for a
  tool run manually or via cron, and recoverable by manually resetting
  affected rows' status in D1 if it ever happens. A staleness timeout (e.g.
  treat `pending` older than N hours as retry-eligible) would close this
  gap but is deliberately left out for now — YAGNI until it's an actual
  recurring problem.
- **Worker unreachable** — `archivore run` fails fast for the queuing phase
  rather than falling back to local-only queueing. Divergent local state
  across machines is worse than telling the user to try again later.
- **iCloud sync lag** — a machine may see (via `GET /items`) that an item
  is `done` with a filename before the actual `.md` file has finished
  syncing down locally via iCloud. This mainly affects `qmd embed` running
  immediately after a run on a machine that didn't do the fetching. Not
  worth building a wait/poll mechanism for a personal tool — it resolves
  within iCloud's normal sync window.
- **Credential handling** — `queue_api_token` is a secret, handled exactly
  like the existing `smtp_password` (documented `chmod 600` guidance).

## Migration

One-time cutover, not an ongoing capability:

1. Provision the Worker + D1 database, deploy via `wrangler deploy`.
2. Bulk-import existing local `hn_this_week/queue.db` rows into D1 (a
   one-off script, not part of the shipped CLI).
3. Move existing `hn_this_week/*.md` files into the new `output_dir`
   default location (`~/Library/Mobile Documents/com~apple~CloudDocs/Todd's
   Obsidian Vault/Archivore/Raw/`).
4. Add `queue_api_url` / `queue_api_token` to each machine's `config.yaml`.
   Set `output_dir` too, on any machine where the built-in default path
   doesn't match where that machine's vault actually lives.
5. Retire `hn_this_week/queue.db` once migrated.

## Testing

- **Worker** — separate lightweight test setup (Miniflare/Vitest, the
  standard pattern for Workers + D1), covering: a batch `/claim` with a mix
  of new and already-existing item_ids in the same call (verifying
  `RETURNING` reports exactly the new ones), batch `/complete` updates
  every row it targets, retry eligibility when a row is `failed`.
- **Python client** — `clients/queue_api.py` is mocked in tests (no live
  network calls), verifying `commands/run.py` correctly partitions a
  `/claim` response into to-fetch vs. skip, and that both `/complete`
  flush points (after phase 1, after phase 2) send the right accumulated
  batches. Same style as existing tests (`test_sources.py`,
  `test_render.py` — pure functions, mocked I/O boundaries).

## Open Questions

- The existing Reddit-ingester bug (old.reddit.com blocking unauthenticated
  scraping — see `docs/plans/reddit-oauth-ingester.md`) is unrelated to
  this design and unaffected by it; fixing it is a separate, independent
  effort.
