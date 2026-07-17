# archivore

_A personal web-reading capture system and AI second brain._

## Vision

Everything you read online gets captured, converted to Markdown, and fed into a local knowledge base. An AI agent then synthesizes that knowledge into publishable long-form articles and social posts, publishing a few pieces per week and engaging with the communities where the ideas live. The goal is a Karpathy-style wiki that grows with you — passive ingestion, active output.

## What's built

### Browser harvester (`main.py`)

Snapshots your open tabs and 90 days of browser history (Chrome + Firefox) into SQLite, then optionally exports a deduplicated Markdown digest. Domain-level deduplication surfaces the most-visited URL per domain and filters noise sites (Gmail, Amazon, Facebook).

```
~/tabs.db          — SQLite with tabs + domain history tables
~/tabs.md          — optional Markdown export
```

Flags: `--markdown [FILE]`, `--days N`, `--db FILE`, `--no-db`

### Weekly reading digest (`this_week.py`)

Scans browser history for the past 7 days, pulls out HN, Reddit, and X URLs, fetches the linked articles, converts them to Markdown, and writes an index. Downloads run concurrently with a Rich live TUI. A SQLite queue makes runs resumable — already-fetched articles are skipped.

```
hn_this_week/
  index.md         — linked table of contents (HN / Reddit / X sections)
  *.md             — one file per article
  queue.db         — resumable download queue
```

## Setup

```bash
pip install aiohttp rich html2text
pip install lz4   # optional — Firefox tab/session support
```

## Usage

```bash
# Snapshot current tabs + 90-day history → ~/tabs.db and ~/tabs.md
python3 main.py --markdown

# Fetch this week's reading (HN, Reddit, X) → hn_this_week/
python3 this_week.py
```

## Roadmap

### Phase 2 — Knowledge Base

- [ ] Topic-based wiki directory structure (auto-organize articles by tag/domain)
- [ ] Vector embeddings index for semantic search across all captured articles
- [ ] Auto-link related articles during ingestion
- [ ] CLI to query the knowledge base by keyword or topic

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
main.py ──────────────────► ~/tabs.db  (tabs + domain history)
     │
     ▼
this_week.py ─────────────► hn_this_week/  (per-article .md + index)
     │
     ▼
[Phase 2] knowledge base ──► wiki/  (tagged, linked, searchable)
     │
     ▼
[Phase 3] Claude agent ────► drafts/  (articles + social posts)
     │
     ▼
[Phase 4] publisher ───────► blog, X, Reddit
```

## Dependencies

| Package      | Used by         | Purpose                                      |
|--------------|-----------------|----------------------------------------------|
| `aiohttp`    | `this_week.py`  | Concurrent async article downloads           |
| `rich`       | `this_week.py`  | Live TUI progress display                    |
| `html2text`  | `this_week.py`  | HTML → Markdown conversion                   |
| `lz4`        | `main.py`       | Firefox `.jsonlz4` session decoding (optional)|
