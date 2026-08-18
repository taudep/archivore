# archivore

_A personal web-reading capture system and AI second brain._

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
  repository/      — SQLite access (snapshot DB + download queue)
tests/             — pytest suite for the pure transforms
```

### `archivore snapshot`

Snapshots your open tabs and 90 days of browser history (Chrome + Firefox) into SQLite, then optionally exports a deduplicated Markdown digest. Domain-level deduplication surfaces the most-visited URL per domain and filters noise sites (Gmail, Amazon, Facebook).

```
~/tabs.db          — SQLite with tabs + domain history tables
~/tabs.md          — optional Markdown export
```

### `archivore run`

Scans browser history since the last successful run (tracked in the user config, not a fixed schedule), pulls out HN, Reddit, and X URLs, fetches the linked articles, converts them to Markdown, and writes an index. Downloads run concurrently with a Rich live TUI. A SQLite queue makes runs resumable, and the qmd semantic index is refreshed automatically after each run.

Reddit posts are filtered by subreddit — only `reddit_subreddits` (in the config, case-insensitive) get ingested, so browsing off-topic subreddits doesn't pollute the knowledge base. Set it to an empty list to disable the filter.

It also appends a summary (counts + newly-saved titles) to a log file every time, and can send
a native macOS notification and/or an email when it finishes — both inert until configured, so
it's equally safe to run by hand or schedule from cron.

```
hn_this_week/
  index.md         — linked table of contents (HN / Reddit / X sections)
  *.md             — one file per article
  queue.db         — resumable download queue
~/Library/Logs/archivore/run.log   — append-only summary of every run (default; see log_path)
```

See [Automation (cron)](#automation-cron) below for scheduling and notification setup.

## Setup

```bash
uv sync --extra firefox    # firefox extra adds lz4 session decoding
```

For semantic search over captured articles, install [qmd](https://github.com/tobi/qmd):

```bash
npm install -g @tobilu/qmd
qmd collection add ./hn_this_week --name archivore
qmd embed
```

## Usage

```bash
# Snapshot current tabs + 90-day history → ~/tabs.db and ~/tabs.md
uv run archivore snapshot --markdown ~/tabs.md

# Fetch reading since the last run (HN, Reddit, X) → hn_this_week/
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

## Roadmap

### Phase 2 — Knowledge Base

- [x] Vector embeddings index for semantic search across all captured articles (via [qmd](https://github.com/tobi/qmd))
- [x] CLI to query the knowledge base by keyword or topic (`qmd search` / `qmd query`)
- [x] Auto-refresh the index after each run
- [ ] Topic-based wiki directory structure (auto-organize articles by tag/domain)
- [ ] Auto-link related articles during ingestion

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

## Architecture

```
browser history
     │
     ▼
archivore snapshot ────────► ~/tabs.db  (tabs + domain history)
     │
     ▼
archivore run ─────────────► hn_this_week/  (per-article .md + index)
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
