# archivore

_A personal web-reading capture system and AI second brain._

## Quick install (new machine)

Already have an [archivore-queue](#multi-machine-sync-archivore-queue) Worker+D1 instance running somewhere? Get a new machine reading from that same shared queue in a few commands:

```bash
git clone https://github.com/taudep/archivore.git && cd archivore
uv sync --extra firefox    # firefox extra adds lz4 session decoding
uv tool install .          # puts `archivore` on your PATH
```

Then create `~/.config/archivore/config.yaml` with the shared queue's credentials and your vault path:

```yaml
queue_api_url: https://archivore-queue.taude.workers.dev
queue_api_token: "your token here"
output_dir: ~/Library/Mobile Documents/com~apple~CloudDocs/Todd's Obsidian Vault/Archivore/Raw
```

```bash
archivore run
```

That's the whole client setup — this machine now dedups against every other machine pointed at the same `queue_api_url`. `output_dir` should point at the same synced location (iCloud, Dropbox, etc.) every machine shares, so they all read/write the same vault.

Don't have a Worker+D1 instance yet? See [Multi-machine sync](#multi-machine-sync-archivore-queue) further down — that's the one-time infrastructure setup, done once total, not per machine. For config details, `qmd` semantic search, and an env-var alternative to the token, see [Setup](#setup) below.

## Vision

Everything you read online gets captured, converted to Markdown, and fed into a local knowledge base. An AI agent then synthesizes that knowledge into publishable long-form articles and social posts, publishing a few pieces per week and engaging with the communities where the ideas live. The goal is a Karpathy-style wiki that grows with you — passive ingestion, active output.

## What's built

A `click`-based CLI (`archivore`) with two commands, structured as a `uv`-managed package:

```
archivore/
  cli.py           — click entry point (archivore snapshot / run)
  config.py        — XDG-style config (defaults ← ~/.config/archivore/config.yaml ← ./archivore.yaml)
  sources.py       — pure history transforms: per-source extraction, domain dedup
  render.py        — all Markdown output
  commands/        — snapshot + run (fetch pipeline + logging/notifications) orchestration
  clients/         — browser readers, HN/Reddit/X resolvers, async fetcher
  repository/      — SQLite access (snapshot DB)
tests/             — pytest suite for the pure transforms
```

### `archivore snapshot`

Snapshots your open tabs and 90 days of browser history (Chrome + Firefox) into SQLite, then optionally exports a deduplicated Markdown digest. Domain-level deduplication surfaces the most-visited URL per domain and filters noise sites (Gmail, Amazon, Facebook).

```
~/tabs.db          — SQLite with tabs + domain history tables
~/tabs.md          — optional Markdown export
```

### `archivore run`

Scans browser history since the last successful run (tracked in the user config, not a fixed schedule), pulls out HN and X URLs (Reddit is currently disabled — see below), fetches the linked articles, converts them to Markdown, and writes an index. Downloads run concurrently with a Rich live TUI. Instead of a local queue, it coordinates with the [archivore-queue Cloudflare Worker + D1 API](#multi-machine-sync-archivore-queue) — a batch `/claim` before fetching, a `/complete` flush after each phase, and a final `/items` to rebuild the index from global state — so the same article is never fetched twice, even across multiple machines. The qmd semantic index is refreshed automatically after each run.

**Reddit is off by default** (`enable_reddit: false`) — the old.reddit.com scraping approach it uses has no real auth story yet and is prone to being blocked or rate-limited. The code is all still there (`archivore/clients/reddit.py`, `sources.extract_reddit_items`); set `enable_reddit: true` in config to turn it back on once that's sorted. When enabled, posts are filtered by subreddit — only `reddit_subreddits` (in the config, case-insensitive) get ingested, so browsing off-topic subreddits doesn't pollute the knowledge base. Set it to an empty list to disable that filter without disabling Reddit entirely.

It also appends a summary (counts + newly-saved titles) to a log file every time, and can send
a native macOS notification and/or an email when it finishes — both inert until configured, so
it's equally safe to run by hand or schedule from cron.

```
Todd's Obsidian Vault/Archivore/Raw/   — default output_dir (an iCloud-synced Obsidian vault)
  index.md         — linked table of contents (HN / Reddit / X sections)
  *.md             — one file per article
~/Library/Logs/archivore/run.log   — append-only summary of every run (default; see log_path)
```

The default `output_dir` is `~/Library/Mobile Documents/com~apple~CloudDocs/Todd's Obsidian Vault/Archivore/Raw` — fully overridable via `output_dir` in config, same as any other `Config` field.

See [Automation (cron)](#automation-cron) below for scheduling and notification setup.

## Setup

```bash
uv sync --extra firefox    # firefox extra adds lz4 session decoding
```

`archivore run` requires a running [archivore-queue](#multi-machine-sync-archivore-queue) instance — there's no offline fallback, so set `queue_api_url` and `queue_api_token` before your first run (see [Multi-machine sync](#multi-machine-sync-archivore-queue) for what these point at, and [`worker/README.md`](worker/README.md) if you're standing up your own instance):

```yaml
queue_api_url: https://archivore-queue.taude.workers.dev
queue_api_token: "your token here"
```

`queue_api_token` can also come from the `ARCHIVORE_QUEUE_API_TOKEN` environment variable instead
of the config file — useful if you'd rather keep it in a secret manager or CI environment. A value
in `config.yaml` always wins over the environment variable if both are set. Every run prints the
full resolved config to the console at startup (secrets redacted to their last 4 characters) so
it's easy to confirm which value actually took effect.

For semantic search over captured articles, install [qmd](https://github.com/tobi/qmd):

```bash
npm install -g @tobilu/qmd
qmd collection add "~/Library/Mobile Documents/com~apple~CloudDocs/Todd's Obsidian Vault/Archivore/Raw" --name archivore
qmd embed
```

(Point `qmd collection add` at whatever `output_dir` is configured to, if you've overridden the default.)

## Usage

```bash
# Snapshot current tabs + 90-day history → ~/tabs.db and ~/tabs.md
uv run archivore snapshot --markdown ~/tabs.md

# Fetch reading since the last run (HN, Reddit, X) → output_dir
uv run archivore run

# Search the knowledge base
qmd query "postgres performance"
```

Defaults can be overridden in `~/.config/archivore/config.yaml` or a local `archivore.yaml` (keys match the `Config` dataclass: `history_days`, `ignore_domains`, `reddit_subreddits`, `output_dir`, `concurrency`, …). For example, to change the Reddit allowlist:

```yaml
reddit_subreddits:
  - localllm
  - LocalLLaMA
  - DoomEmacs
  - snowflake
  - git
  - dotfiles
  - dotnet
```

To install the CLI on your PATH: `uv tool install .`

## Automation (cron)

`archivore run` is safe to schedule as often as you like — it only processes what's new since the
last invocation. A typical crontab entry, running every morning at 8am:

```cron
0 8 * * * cd /path/to/archivore && /path/to/uv run archivore run >> ~/Library/Logs/archivore/cron.log 2>&1
```

The redirect is optional — `archivore run` keeps its own summary log at `log_path` regardless
(default `~/Library/Logs/archivore/run.log`) — but it's a useful safety net for a stack trace if
the pipeline itself fails before it gets a chance to log.

Notifications are config-driven, in `~/.config/archivore/config.yaml`:

```yaml
# macOS notification banner with the run's counts (on by default; needs osascript, i.e. macOS)
notify_macos: true

# Optional email summary — sent only when smtp_host and email_to are both set
smtp_host: smtp.gmail.com
smtp_port: 587
smtp_user: you@gmail.com
smtp_password: "an app password, not your account password"
email_to: you@gmail.com
email_from: you@gmail.com   # defaults to smtp_user

# Where the run log is written
log_path: ~/Library/Logs/archivore/run.log
```

Since `smtp_password` is a credential, restrict the config file's permissions:
`chmod 600 ~/.config/archivore/config.yaml`.

## Multi-machine sync (archivore-queue)

If you run archivore on more than one machine, the same article used to get fetched twice — once
per machine, since each kept a separate local reading queue. `worker/` is a small, free-tier
Cloudflare Worker + D1 database that coordinates across machines instead, so the same item is
only ever fetched once, without running a server yourself. Full design rationale is in
[docs/superpowers/specs/2026-08-29-multi-machine-reading-queue-sync-design.md](docs/superpowers/specs/2026-08-29-multi-machine-reading-queue-sync-design.md).

**Status:** the Worker is built, tested, deployed live, and wired up — `archivore run` no longer
has a local queue at all; every run coordinates through this API. To use it on a machine, add
`queue_api_url` and `queue_api_token` to `config.yaml` (see [Setup](#setup)); once every machine
you run archivore on is configured the same way, `archivore run` transparently dedups across all
of them.

**Deployed instance:**

| Resource | Name |
|---|---|
| Worker | `archivore-queue` (`https://archivore-queue.taude.workers.dev`) |
| D1 database | `taude-archivore` |

The Worker and the database are separate Cloudflare resources with independent names — that's
intentional, not a naming mismatch.

**API** — three endpoints, all requiring `Authorization: Bearer <token>`, all batch-only (one
call covers every item a run needs, never one call per item):

- `POST /claim` — claim a batch of item_ids. Atomic per-row via the `item_id` primary key plus
  `INSERT ... ON CONFLICT ... RETURNING`, so two machines racing to claim the same item can never
  both win. Jobs are idempotent: a `pending` row left untouched for over an hour (an orphan from a
  crashed run) is atomically reclaimed on the next `/claim`, so a crash self-heals without manual
  intervention.
- `POST /complete` — report a batch of outcomes (`done` / `failed` / `skipped`) after fetching.
- `GET /items` — list the full queue (optionally `?since=<timestamp>`), used to rebuild the
  index from global state rather than just what one machine fetched.

**Setup, redeployment, and operations:** see [`worker/README.md`](worker/README.md) for the
complete walkthrough — prerequisites, first-time setup (`wrangler login`, `wrangler d1 create`,
migrations, generating and setting `QUEUE_API_TOKEN`, `wrangler deploy`, verifying against the
live database), local development and tests, schema changes, and a detailed writeup of the
`[vars]`-vs-secret gotcha that once caused a real production outage (a code deploy silently wiped
the live token). The historical [Worker implementation
plan](docs/superpowers/plans/2026-08-30-archivore-queue-worker.md) documents how it was originally
built, task by task, but `worker/README.md` is the source of truth for actually operating it.

```bash
cd worker
npm install
npm test              # 35 tests, vitest + @cloudflare/vitest-pool-workers
npx tsc --noEmit       # type-check
```

## Roadmap

### Phase 2 — Knowledge Base

- [x] Vector embeddings index for semantic search across all captured articles (via [qmd](https://github.com/tobi/qmd))
- [x] CLI to query the knowledge base by keyword or topic (`qmd search` / `qmd query`)
- [x] Auto-refresh the index after each run
- [x] Multi-machine dedup coordination API (Cloudflare Worker + D1) — built, tested, deployed
- [x] Wire `archivore run` to the coordination API, retiring the local per-machine queue
- [ ] Record the capturing machine's `hostname` on each item in the `taude-archivore` D1 database —
      whichever client instance locks (claims) an article first is the one that captures it, so
      stamp the hostname at claim time for provenance/debugging across multi-machine runs.
- [ ] Topic-based wiki directory structure (auto-organize articles by tag/domain)
- [ ] Auto-link related articles during ingestion
- [ ] LLM-based auto-tagging: call a configurable OpenAI-compatible chat endpoint to generate
      `tags` for each captured article's front matter. New config: `llm_endpoint` (the base URL)
      and `llm_api_key`, with the key overridable via an `ARCHIVORE_LLM_API_KEY` env var — same
      config-file-wins-over-env-var pattern as `queue_api_token`/`ARCHIVORE_QUEUE_API_TOKEN`. The
      endpoint is meant to be reused for more than tagging once this lands (summarization, etc.),
      so keep it a generic chat-completion client rather than a tagging-only helper.

### Phase 3 — AI Content Generation (Claude API)

- [ ] Weekly synthesis: read this week's articles → generate a "what I learned" digest
- [ ] Long-form article generator: prompt Claude with a topic + relevant KB chunks
- [ ] Social post generator: tweet-length and thread-length variants per article
- [ ] Human review/edit step before any publishing

### Phase 4 — Publishing Pipeline

- [ ] Static blog output (Markdown → GitHub Pages / Jekyll)
- [ ] X/Twitter posting via API (articles + thread summaries)
- [ ] Reddit posting to relevant subreddits
- [ ] Weekly publishing schedule (2–3 articles/week via cron or GitHub Actions)

### Phase 5 — Social Engagement

- [ ] Monitor replies to published content (HN, Reddit, X)
- [ ] AI-assisted reply drafts for review
- [ ] Follow/discover accounts in areas of interest
- [ ] Engagement metrics dashboard (Markdown report)
- [ ] Daily pretty-printed report emailed via Gmail (summary of the day's captured items)

## Architecture

```
browser history
     │
     ▼
archivore snapshot ────────► ~/tabs.db  (tabs + domain history)
     │
     ▼
archivore run ◄────────────► archivore-queue Worker + D1  (dedup across machines)
     │
     ▼
output_dir  (per-article .md + index)
     │
     ▼
qmd (BM25 + vectors) ──────► semantic search over everything captured
     │
     ▼
[Phase 3] Claude agent ────► drafts/  (articles + social posts)
     │
     ▼
[Phase 4] publisher ───────► blog, X, Reddit
```

## Dependencies

| Package     | Purpose                                        |
|-------------|------------------------------------------------|
| `click`     | CLI commands and argument parsing              |
| `requests`  | Synchronous HTTP (metadata resolution)         |
| `aiohttp`   | Concurrent async article downloads             |
| `rich`      | Live TUI progress display                      |
| `html2text` | HTML → Markdown conversion                     |
| `pyyaml`    | Config file parsing                            |
| `lz4`       | Firefox `.jsonlz4` session decoding (optional) |
