# archivore

_A personal web-reading capture system and AI second brain._

## Vision

Everything you read online gets captured, converted to Markdown, and fed into a local knowledge base. An AI agent then synthesizes that knowledge into publishable long-form articles and social posts, publishing a few pieces per week and engaging with the communities where the ideas live. The goal is a Karpathy-style wiki that grows with you — passive ingestion, active output.

## What's built

A `click`-based CLI (`archivore`) with two commands, structured as a `uv`-managed package:

```
archivore/
  cli.py           — click entry point (archivore snapshot / archivore weekly)
  config.py        — XDG-style config (defaults ← ~/.config/archivore/config.yaml ← ./archivore.yaml)
  sources.py       — pure history transforms: per-source extraction, domain dedup
  render.py        — all Markdown output
  commands/        — snapshot + weekly orchestration
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

### `archivore weekly`

Scans browser history for the past 7 days, pulls out HN, Reddit, and X URLs, fetches the linked articles, converts them to Markdown, and writes an index. Downloads run concurrently with a Rich live TUI. A SQLite queue makes runs resumable, and the qmd semantic index is refreshed automatically after each run.

```
hn_this_week/
  index.md         — linked table of contents (HN / Reddit / X sections)
  *.md             — one file per article
  queue.db         — resumable download queue
```

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

# Fetch this week's reading (HN, Reddit, X) → hn_this_week/
uv run archivore weekly

# Search the knowledge base
qmd query "postgres performance"
```

Defaults can be overridden in `~/.config/archivore/config.yaml` or a local `archivore.yaml` (keys match the `Config` dataclass: `history_days`, `ignore_domains`, `output_dir`, `concurrency`, …).

To install the CLI on your PATH: `uv tool install .`

## Roadmap

### Phase 2 — Knowledge Base

- [x] Vector embeddings index for semantic search across all captured articles (via [qmd](https://github.com/tobi/qmd))
- [x] CLI to query the knowledge base by keyword or topic (`qmd search` / `qmd query`)
- [x] Auto-refresh the index after each weekly run
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
archivore weekly ──────────► hn_this_week/  (per-article .md + index)
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
