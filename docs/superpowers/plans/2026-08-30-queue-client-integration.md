# Archivore Queue Client Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `archivore run` to the deployed archivore-queue Worker instead of a local SQLite queue, so reading-digest work is deduped across machines, and retire the local queue entirely.

**Architecture:** Discover every HN/Reddit/X item from local history first, claim the whole batch in one API call, resolve+download only what was actually claimed (accumulating outcomes in memory), flush completions in two batches (after phase 1, after phase 2), then rebuild `index.md` from the global item list.

**Tech Stack:** Python 3.12, `requests` (already a dependency) for the coordination API client.

## Global Constraints

- **Prerequisite:** the `docs/superpowers/plans/2026-08-30-archivore-queue-worker.md` plan must be fully executed first — this plan needs a real deployed Worker URL and token for its final end-to-end task.
- `queue_api.claim()` and `queue_api.complete()` are batch-only. Never call them once per item — that reintroduces the N-network-calls problem this design exists to avoid.
- No offline fallback: if the coordination API is unreachable, the run fails loudly. Do not add a local-queue fallback path.
- `output_dir` remains a normal, fully overridable `Config` field — its new default is a real path, not a placeholder, but every machine's `config.yaml` can still override it.
- Follow existing project conventions: `requests` for HTTP (not `httpx`/`urllib`), TypedDicts in `models.py` for wire-format shapes, `ruff format`/`ruff check` clean, tests mirror the existing style in `test_sources.py`/`test_render.py` (pure functions, mocked I/O boundaries, no live network calls in `pytest`).

---

## File Structure

```
archivore/
  models.py                    - add ClaimItem, ClaimResult, CompleteItem TypedDicts
  config.py                    - add queue_api_url/queue_api_token; change output_dir default
  clients/
    queue_api.py                - NEW: claim(), complete(), list_items()
    fetcher.py                   - rewrite fetch_article() to return CompleteItem, drop sqlite
  commands/
    run.py                       - rewrite: discover_items(), partition_claims(), phase1_resolve(),
                                    phase2_download(), _pipeline()
  repository/
    queue.py                     - DELETE (superseded by clients/queue_api.py)
tests/
  test_config.py                 - NEW
  test_queue_api.py              - NEW
  test_run.py                    - NEW (partition_claims)
scripts/
  migrate_queue_to_d1.py         - NEW, one-off, not part of the shipped CLI
README.md                        - update setup/config docs
```

---

### Task 1: Config and shared types

**Files:**
- Modify: `archivore/config.py`
- Modify: `archivore/models.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.queue_api_url: str | None`, `Config.queue_api_token: str | None`, new `Config.output_dir` default; `ClaimItem`, `ClaimResult`, `CompleteItem` TypedDicts in `archivore/models.py` — every later task in this plan imports these exact names.

- [ ] **Step 1: Write the failing test `tests/test_config.py`**

```python
"""Tests for Config defaults."""

from pathlib import Path

from archivore.config import Config


def test_output_dir_default_points_at_obsidian_vault():
    cfg = Config()
    assert cfg.output_dir == (
        Path.home()
        / "Library/Mobile Documents/com~apple~CloudDocs"
        / "Todd's Obsidian Vault/Archivore/Raw"
    )


def test_queue_api_fields_default_to_none():
    cfg = Config()
    assert cfg.queue_api_url is None
    assert cfg.queue_api_token is None
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_config.py -v
```

Expected: FAIL — `Config` has no `queue_api_url`/`queue_api_token` attributes yet, and `output_dir` still defaults to `Path("hn_this_week")`.

- [ ] **Step 3: Update `archivore/config.py`**

Change the `output_dir` field (currently `output_dir: Path = field(default_factory=lambda: Path("hn_this_week"))`) to:

```python
    output_dir: Path = field(
        default_factory=lambda: Path.home()
        / "Library/Mobile Documents/com~apple~CloudDocs"
        / "Todd's Obsidian Vault/Archivore/Raw"
    )
```

Add two new fields after `concurrency: int = 5`:

```python
    queue_api_url: str | None = None
    queue_api_token: str | None = None
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/test_config.py -v
```

Expected: PASS (2 tests).

- [ ] **Step 5: Add the wire-format TypedDicts to `archivore/models.py`**

Append to the end of the file (after `RunResult`):

```python
class ClaimItem(TypedDict):
    """One item sent to the coordination API's /claim endpoint."""

    item_id: str
    source: str
    comments_url: str
    article_url: str | None


class ClaimResult(TypedDict):
    """One result from the coordination API's /claim endpoint."""

    item_id: str
    claimed: bool
    status: str
    retries: int


class CompleteItem(TypedDict):
    """One outcome sent to the coordination API's /complete endpoint."""

    item_id: str
    status: str
    title: str | None
    is_selfpost: bool | None
    filename: str | None
    last_error: str | None
```

- [ ] **Step 6: Run the full test suite to confirm nothing broke**

```bash
uv run pytest -q
```

Expected: all existing tests plus the 2 new ones pass (18 total).

- [ ] **Step 7: Commit**

```bash
git add archivore/config.py archivore/models.py tests/test_config.py
git commit -m "Add queue-API config fields and wire-format types"
```

---

### Task 2: `archivore/clients/queue_api.py`

**Files:**
- Create: `archivore/clients/queue_api.py`
- Test: `tests/test_queue_api.py`

**Interfaces:**
- Consumes: `Config` from `archivore/config.py`; `ClaimItem`, `ClaimResult`, `CompleteItem` from `archivore/models.py` (Task 1).
- Produces: `claim(cfg, items) -> list[ClaimResult]`, `complete(cfg, items) -> None`, `list_items(cfg) -> list[dict]` — consumed by `commands/run.py` in Task 4.

- [ ] **Step 1: Write the failing test `tests/test_queue_api.py`**

```python
"""Tests for the archivore-queue HTTP client. No live network calls —
requests.post/get are mocked throughout."""

from unittest.mock import Mock, patch

from archivore.clients.queue_api import claim, complete, list_items
from archivore.config import Config


def _cfg() -> Config:
    cfg = Config()
    cfg.queue_api_url = "https://queue.example.workers.dev"
    cfg.queue_api_token = "test-token"
    return cfg


class TestClaim:
    def test_empty_items_makes_no_request(self):
        with patch("archivore.clients.queue_api.requests.post") as mock_post:
            result = claim(_cfg(), [])
        assert result == []
        mock_post.assert_not_called()

    def test_posts_items_with_auth_and_returns_results(self):
        mock_resp = Mock()
        mock_resp.json.return_value = {
            "results": [
                {"item_id": "1", "claimed": True, "status": "pending", "retries": 0}
            ]
        }
        with patch(
            "archivore.clients.queue_api.requests.post", return_value=mock_resp
        ) as mock_post:
            result = claim(
                _cfg(),
                [
                    {
                        "item_id": "1",
                        "source": "hn",
                        "comments_url": "https://x",
                        "article_url": None,
                    }
                ],
            )

        args, kwargs = mock_post.call_args
        assert args[0] == "https://queue.example.workers.dev/claim"
        assert kwargs["headers"]["Authorization"] == "Bearer test-token"
        assert kwargs["json"]["items"][0]["item_id"] == "1"
        assert result[0]["claimed"] is True
        mock_resp.raise_for_status.assert_called_once()


class TestComplete:
    def test_empty_items_makes_no_request(self):
        with patch("archivore.clients.queue_api.requests.post") as mock_post:
            complete(_cfg(), [])
        mock_post.assert_not_called()

    def test_posts_batch_with_auth(self):
        mock_resp = Mock()
        mock_resp.json.return_value = {"updated": 1}
        with patch(
            "archivore.clients.queue_api.requests.post", return_value=mock_resp
        ) as mock_post:
            complete(
                _cfg(),
                [
                    {
                        "item_id": "1",
                        "status": "done",
                        "title": "T",
                        "is_selfpost": False,
                        "filename": "1-t.md",
                        "last_error": None,
                    }
                ],
            )

        args, kwargs = mock_post.call_args
        assert args[0] == "https://queue.example.workers.dev/complete"
        assert kwargs["json"]["items"][0]["status"] == "done"
        mock_resp.raise_for_status.assert_called_once()


class TestListItems:
    def test_gets_items_with_auth(self):
        mock_resp = Mock()
        mock_resp.json.return_value = {"items": [{"item_id": "1"}]}
        with patch(
            "archivore.clients.queue_api.requests.get", return_value=mock_resp
        ) as mock_get:
            result = list_items(_cfg())

        args, kwargs = mock_get.call_args
        assert args[0] == "https://queue.example.workers.dev/items"
        assert kwargs["headers"]["Authorization"] == "Bearer test-token"
        assert result == [{"item_id": "1"}]
        mock_resp.raise_for_status.assert_called_once()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_queue_api.py -v
```

Expected: FAIL — `archivore.clients.queue_api` doesn't exist yet (ImportError).

- [ ] **Step 3: Write `archivore/clients/queue_api.py`**

```python
"""HTTP client for the archivore-queue coordination API (Cloudflare Worker
+ D1). claim() and complete() are batch-only — one call per run, never one
call per item; see docs/superpowers/specs/2026-08-29-multi-machine-reading-
queue-sync-design.md."""

import requests

from archivore.config import Config
from archivore.models import ClaimItem, ClaimResult, CompleteItem


def _headers(cfg: Config) -> dict:
    return {
        "Authorization": f"Bearer {cfg.queue_api_token}",
        "Content-Type": "application/json",
    }


def claim(cfg: Config, items: list[ClaimItem]) -> list[ClaimResult]:
    """Claim a batch of items in one call. Empty input makes no request."""
    if not items:
        return []
    resp = requests.post(
        f"{cfg.queue_api_url}/claim",
        json={"items": items},
        headers=_headers(cfg),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["results"]


def complete(cfg: Config, items: list[CompleteItem]) -> None:
    """Report a batch of outcomes in one call. Empty input makes no request."""
    if not items:
        return
    resp = requests.post(
        f"{cfg.queue_api_url}/complete",
        json={"items": items},
        headers=_headers(cfg),
        timeout=15,
    )
    resp.raise_for_status()


def list_items(cfg: Config) -> list[dict]:
    """Return every item in the global queue (all machines, all time)."""
    resp = requests.get(f"{cfg.queue_api_url}/items", headers=_headers(cfg), timeout=15)
    resp.raise_for_status()
    return resp.json()["items"]
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/test_queue_api.py -v
```

Expected: PASS (5 tests).

- [ ] **Step 5: Lint and commit**

```bash
uv run --frozen ruff format archivore/clients/queue_api.py tests/test_queue_api.py
uv run --frozen ruff check archivore/clients/queue_api.py tests/test_queue_api.py
git add archivore/clients/queue_api.py tests/test_queue_api.py
git commit -m "Add archivore-queue HTTP client"
```

---

### Task 3: Rewrite `archivore/clients/fetcher.py`

**Files:**
- Modify: `archivore/clients/fetcher.py`

**Interfaces:**
- Consumes: `CompleteItem` from `archivore/models.py` (Task 1).
- Produces: `fetch_article(session, item, state, output_dir, max_retries) -> CompleteItem` — signature changes from the current `(session, row, state, db_lock, conn, now, output_dir, max_retries) -> None`. Consumed by `commands/run.py`'s `phase2_download` in Task 4.

Self-posts never reach this function anymore (phase 1 fully resolves and completes them — see Task 4), so the existing `if row["is_selfpost"]:` early-return branch is removed entirely, not just modified.

- [ ] **Step 1: Replace the entire contents of `archivore/clients/fetcher.py`**

```python
"""Async article downloader used by phase 2 of the reading digest.

Every call returns a CompleteItem for the caller to report back to the
coordination API — this module never talks to that API directly, and
never handles self-posts (phase 1 resolves and completes those before
anything reaches here)."""

import asyncio
import ssl
from pathlib import Path

import aiohttp

from archivore.clients.http import BROWSER_HEADERS, aiohttp_ssl_ctx
from archivore.models import CompleteItem
from archivore.render import html_to_markdown, write_article_file

_PERMANENT_ERRORS = {400, 401, 403, 404, 405, 410, 451}

# aiohttp's default 8 KB header-line limit crashes on X.com's giant CSP header
_HEADER_LIMIT = 65536

StateMap = dict[str, tuple[str, str]]


async def fetch_article(
    session: aiohttp.ClientSession,
    item: dict,
    state: StateMap,
    output_dir: Path,
    max_retries: int,
) -> CompleteItem:
    """Download one article, convert to Markdown, and return its outcome.

    ``item`` needs keys: item_id, title, article_url, comments_url.
    """
    item_id = item["item_id"]
    title = item["title"]
    article_url = item["article_url"]
    comments_url = item["comments_url"]

    state[item_id] = ("⏳", "fetching…")

    def _skip(note: str, error: str, icon_status: str) -> CompleteItem:
        filename = write_article_file(
            output_dir, item_id, title, article_url, comments_url, note, ""
        )
        state[item_id] = ("⛔", icon_status)
        return CompleteItem(
            item_id=item_id,
            status="skipped",
            title=title,
            is_selfpost=False,
            filename=filename,
            last_error=error,
        )

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            async with session.get(
                article_url,
                headers=BROWSER_HEADERS,
                ssl=aiohttp_ssl_ctx(),
                timeout=aiohttp.ClientTimeout(total=25),
                max_line_size=_HEADER_LIMIT,
                max_field_size=_HEADER_LIMIT,
            ) as resp:
                if resp.status in _PERMANENT_ERRORS:
                    return _skip(
                        f"_Article unavailable (HTTP {resp.status})._\n",
                        f"HTTP {resp.status}",
                        f"HTTP {resp.status}",
                    )

                if resp.status == 429 or resp.status >= 500:
                    raise aiohttp.ClientResponseError(
                        resp.request_info, resp.history, status=resp.status
                    )

                content_type = resp.headers.get("Content-Type", "")
                if "text" not in content_type and "html" not in content_type:
                    short_type = content_type.split(";")[0]
                    return _skip(
                        f"_Skipped: non-HTML content ({short_type})._\n",
                        "non-HTML",
                        "non-HTML",
                    )

                page = await resp.text(errors="replace")
                md_body = html_to_markdown(page)
                filename = write_article_file(
                    output_dir, item_id, title, article_url, comments_url, "", md_body
                )
                state[item_id] = ("✅", "saved")
                return CompleteItem(
                    item_id=item_id,
                    status="done",
                    title=title,
                    is_selfpost=False,
                    filename=filename,
                    last_error=None,
                )

        except (aiohttp.ClientError, asyncio.TimeoutError, ssl.SSLError) as e:
            last_err = e
            state[item_id] = ("🔄", f"retry {attempt + 1}/{max_retries}")
            await asyncio.sleep(2**attempt)

    state[item_id] = ("❌", f"failed: {str(last_err)[:40]}")
    return CompleteItem(
        item_id=item_id,
        status="failed",
        title=title,
        is_selfpost=False,
        filename=None,
        last_error=str(last_err),
    )
```

- [ ] **Step 2: Run the full test suite to confirm nothing broke**

```bash
uv run pytest -q
```

Expected: all tests still pass — no existing test imports `fetcher.py` directly (it's exercised via live async network calls, same as before this change; there's no regression risk pytest can catch here, which matches the project's existing testing boundary for this file).

- [ ] **Step 3: Lint and commit**

```bash
uv run --frozen ruff format archivore/clients/fetcher.py
uv run --frozen ruff check archivore/clients/fetcher.py
git add archivore/clients/fetcher.py
git commit -m "Rewrite fetch_article to return CompleteItem instead of writing SQLite"
```

---

### Task 4: Rewire `archivore/commands/run.py`, retire the local queue

**Files:**
- Modify: `archivore/commands/run.py` (near-total rewrite of the pipeline functions)
- Delete: `archivore/repository/queue.py`
- Test: `tests/test_run.py`

**Interfaces:**
- Consumes: `queue_api.claim/complete/list_items` (Task 2), `fetcher.fetch_article` (Task 3), `ClaimItem`/`ClaimResult`/`CompleteItem` (Task 1).
- Produces: `discover_items(cfg, since) -> list[ClaimItem]`, `partition_claims(results, max_retries) -> list[str]` — the only two functions from this file worth unit testing (pure, no I/O beyond the already-tested `get_all_history`/`extract_*` calls this file just orchestrates).

- [ ] **Step 1: Write the failing test `tests/test_run.py`**

```python
"""Tests for the pure claim-partitioning logic in commands/run.py."""

from archivore.commands.run import partition_claims


def test_claimed_items_are_fetched():
    results = [{"item_id": "1", "claimed": True, "status": "pending", "retries": 0}]
    assert partition_claims(results, max_retries=4) == ["1"]


def test_done_items_are_not_refetched():
    results = [{"item_id": "1", "claimed": False, "status": "done", "retries": 0}]
    assert partition_claims(results, max_retries=4) == []


def test_skipped_items_are_not_refetched():
    results = [{"item_id": "1", "claimed": False, "status": "skipped", "retries": 0}]
    assert partition_claims(results, max_retries=4) == []


def test_failed_under_max_retries_is_retried():
    results = [{"item_id": "1", "claimed": False, "status": "failed", "retries": 2}]
    assert partition_claims(results, max_retries=4) == ["1"]


def test_failed_at_max_retries_is_not_retried():
    results = [{"item_id": "1", "claimed": False, "status": "failed", "retries": 4}]
    assert partition_claims(results, max_retries=4) == []


def test_mixed_batch_partitions_correctly():
    results = [
        {"item_id": "new", "claimed": True, "status": "pending", "retries": 0},
        {"item_id": "done", "claimed": False, "status": "done", "retries": 0},
        {"item_id": "retry", "claimed": False, "status": "failed", "retries": 1},
        {"item_id": "exhausted", "claimed": False, "status": "failed", "retries": 4},
    ]
    assert partition_claims(results, max_retries=4) == ["new", "retry"]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_run.py -v
```

Expected: FAIL — `partition_claims` doesn't exist yet (ImportError).

- [ ] **Step 3: Replace the entire contents of `archivore/commands/run.py`**

```python
"""Fetch reading since the last run: discover HN/Reddit/X items, claim them
against the shared coordination API, resolve metadata, download articles
concurrently, write the index, then log a summary and optionally notify.

Coordination with other machines costs exactly 4 API calls per run,
regardless of item count: one /claim, two /complete flushes (after phase 1,
after phase 2), one /items. See docs/superpowers/specs/2026-08-29-multi-
machine-reading-queue-sync-design.md for why.

Phase 1 resolves metadata sequentially (rate-limit friendly); phase 2
downloads link-post articles concurrently with a Rich live table. Logging
appends a summary to ``cfg.log_path`` on every run; macOS notification and
email are inert until configured, so scheduling this via cron needs no setup
beyond what an interactive run already needs.
"""

import asyncio
import shutil
import smtplib
import subprocess
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import aiohttp
from rich import box
from rich.console import Console
from rich.live import Live
from rich.table import Table

from archivore.clients import fetcher, hn, reddit, x
from archivore.clients import queue_api
from archivore.clients.browsers import get_all_history
from archivore.config import Config, save_last_run
from archivore.models import ClaimItem, ClaimResult, CompleteItem, ResolvedItem, RunResult
from archivore.render import write_article_file, write_index
from archivore.sources import (
    extract_hn_items,
    extract_reddit_items,
    extract_x_items,
)
from archivore.timeutil import days_ago

console = Console()


def discover_items(cfg: Config, since: datetime) -> list[ClaimItem]:
    """Scan browser history since ``since`` and extract every HN/Reddit/X
    item — fully, in memory — before any coordination-API call happens."""
    history = get_all_history(since)
    items: list[ClaimItem] = []

    hn_items = extract_hn_items(history)
    console.print(f"  HN:     [cyan]{len(hn_items)}[/cyan] item(s)")
    for item_id in hn_items:
        items.append(
            ClaimItem(
                item_id=item_id,
                source="hn",
                comments_url=f"https://news.ycombinator.com/item?id={item_id}",
                article_url=None,
            )
        )

    reddit_items = extract_reddit_items(history, cfg.reddit_subreddits)
    console.print(f"  Reddit: [cyan]{len(reddit_items)}[/cyan] item(s)")
    for post_id, orig_url in reddit_items.items():
        # Original URL goes in comments_url so phase 1 can rebuild old.reddit
        items.append(
            ClaimItem(
                item_id=post_id,
                source="reddit",
                comments_url=orig_url,
                article_url=None,
            )
        )

    x_items = extract_x_items(history)
    console.print(f"  X:      [cyan]{len(x_items)}[/cyan] item(s)")
    for xid, (kind, orig_url) in x_items.items():
        # kind|orig_url goes in article_url until phase 1 resolves it; the x_
        # prefix keeps numeric tweet IDs from colliding with HN item IDs
        items.append(
            ClaimItem(
                item_id=f"x_{xid}",
                source="x",
                comments_url=orig_url,
                article_url=f"{kind}|{orig_url}",
            )
        )

    return items


def partition_claims(results: list[ClaimResult], max_retries: int) -> list[str]:
    """Return the item_ids this run should fetch: newly claimed items, plus
    previously-failed items still under the retry limit."""
    to_fetch = []
    for r in results:
        if r["claimed"]:
            to_fetch.append(r["item_id"])
        elif r["status"] == "failed" and r["retries"] < max_retries:
            to_fetch.append(r["item_id"])
    return to_fetch


def _resolve_one(item: ClaimItem, cfg: Config) -> ResolvedItem | None:
    """Dispatch one claimed item to its source client, honoring rate delays."""
    source = item["source"]
    if source == "hn":
        result = hn.resolve(item["item_id"])
        time.sleep(cfg.hn_delay)
    elif source == "reddit":
        result = reddit.resolve(item["item_id"], item["comments_url"])
        time.sleep(cfg.meta_delay)
    elif source == "x":
        stored = item["article_url"]
        kind, orig_url = (
            stored.split("|", 1) if stored else ("tweet", item["comments_url"])
        )
        result = x.resolve(item["item_id"].removeprefix("x_"), kind, orig_url)
        time.sleep(cfg.meta_delay)
    else:
        result = None
    return result


def phase1_resolve(
    to_fetch: list[ClaimItem], cfg: Config
) -> tuple[list[CompleteItem], list[dict]]:
    """Resolve metadata for every claimed item.

    Returns (phase-1 completions to report immediately, items ready for a
    phase-2 download). Self-posts are fully resolved here and never reach
    phase 2.
    """
    if not to_fetch:
        return [], []

    console.print(f"\n[bold]Phase 1[/bold] — resolving {len(to_fetch)} item(s)…")
    completions: list[CompleteItem] = []
    to_download: list[dict] = []

    for claim_item in to_fetch:
        item_id = claim_item["item_id"]
        console.print(f"  [{claim_item['source']}:{item_id}] ", end="")
        try:
            item = _resolve_one(claim_item, cfg)
            if item is None or not item.title:
                reason = (
                    "no title"
                    if item is not None
                    else f"unknown source '{claim_item['source']}'"
                )
                console.print(f"[yellow]{reason}, skipping[/yellow]")
                completions.append(
                    CompleteItem(
                        item_id=item_id,
                        status="skipped",
                        title=None,
                        is_selfpost=None,
                        filename=None,
                        last_error=reason,
                    )
                )
                continue

            console.print(item.title[:70])

            if item.is_selfpost and item.selftext is not None:
                filename = write_article_file(
                    cfg.output_dir,
                    item_id,
                    item.title,
                    item.article_url,
                    claim_item["comments_url"],
                    item.selftext,
                    "",
                )
                completions.append(
                    CompleteItem(
                        item_id=item_id,
                        status="done",
                        title=item.title,
                        is_selfpost=True,
                        filename=filename,
                        last_error=None,
                    )
                )
            else:
                to_download.append(
                    {
                        "item_id": item_id,
                        "source": claim_item["source"],
                        "title": item.title,
                        "article_url": item.article_url,
                        "comments_url": claim_item["comments_url"],
                    }
                )
        except Exception as e:
            console.print(f"[red]error: {e}[/red]")
            completions.append(
                CompleteItem(
                    item_id=item_id,
                    status="failed",
                    title=None,
                    is_selfpost=None,
                    filename=None,
                    last_error=str(e),
                )
            )

    return completions, to_download


def _build_table(state: fetcher.StateMap, rows_by_id: dict) -> Table:
    table = Table(
        box=box.SIMPLE,
        show_header=True,
        header_style="bold cyan",
        expand=True,
        min_width=80,
    )
    table.add_column("", width=2, no_wrap=True)
    table.add_column("Source", width=8, no_wrap=True)
    table.add_column("Title", ratio=3)
    table.add_column("Status", ratio=2)
    color_map = {
        "✅": "green",
        "❌": "red",
        "⛔": "yellow",
        "⏳": "cyan",
        "🔄": "blue",
        "⚠": "yellow",
    }
    for item_id, (icon, status) in state.items():
        r = rows_by_id.get(item_id, {})
        title = (r.get("title") or "")[:50]
        color = color_map.get(icon, "white")
        table.add_row(icon, r.get("source", ""), title, f"[{color}]{status}[/]")
    return table


async def phase2_download(to_download: list[dict], cfg: Config) -> list[CompleteItem]:
    """Download all claimed link-post articles concurrently. Returns each
    item's outcome for the caller to report via queue_api.complete()."""
    if not to_download:
        console.print("\n[green]Nothing to download in phase 2.[/green]")
        return []

    rows_by_id = {item["item_id"]: item for item in to_download}
    state: fetcher.StateMap = {item["item_id"]: ("⏸", "queued") for item in to_download}
    completions: list[CompleteItem] = []

    console.print(
        f"\n[bold]Phase 2[/bold] — downloading {len(to_download)} article(s) "
        f"({cfg.concurrency} concurrent)…\n"
    )

    connector = aiohttp.TCPConnector(limit=cfg.concurrency, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        sem = asyncio.Semaphore(cfg.concurrency)

        async def bounded(item: dict) -> None:
            async with sem:
                result = await fetcher.fetch_article(
                    session, item, state, cfg.output_dir, cfg.max_retries
                )
                completions.append(result)

        with Live(
            _build_table(state, rows_by_id),
            refresh_per_second=4,
            console=console,
        ) as live:
            tasks = [asyncio.create_task(bounded(item)) for item in to_download]
            while not all(t.done() for t in tasks):
                live.update(_build_table(state, rows_by_id))
                await asyncio.sleep(0.25)
            await asyncio.gather(*tasks)
            live.update(_build_table(state, rows_by_id))

    return completions


def run_qmd_embed() -> None:
    """Refresh the qmd semantic index if qmd is installed."""
    qmd = shutil.which("qmd")
    if not qmd:
        console.print("[yellow]qmd not found in PATH — skipping embed[/yellow]")
        return
    console.print("\n[bold]Updating qmd index…[/bold]")
    result = subprocess.run([qmd, "embed"], capture_output=True, text=True)
    if result.returncode == 0:
        summary = next(
            (ln for ln in reversed(result.stdout.splitlines()) if ln.strip()),
            "qmd embed complete",
        )
        console.print(f"[green]{summary}[/green]")
    else:
        console.print(f"[yellow]qmd embed exited {result.returncode}[/yellow]")
        if result.stderr:
            console.print(result.stderr[:300])


def _resolve_since(cfg: Config, days_override: int | None) -> tuple[datetime, str]:
    """Pick the history cutoff and a description of why it was chosen."""
    if days_override is not None:
        return days_ago(days_override), f"last {days_override} days (override)"
    if cfg.last_run:
        since = datetime.fromisoformat(cfg.last_run)
        return since, f"since last run: {since.strftime('%Y-%m-%d %H:%M UTC')}"
    return (
        days_ago(cfg.digest_days),
        f"last {cfg.digest_days} days (no previous run recorded)",
    )


def _pipeline(
    cfg: Config, days_override: int | None = None, skip_embed: bool = False
) -> RunResult:
    """Execute the full fetch pipeline and return its outcome.

    Scans history since the last successful run (tracked in the user
    config) rather than a fixed window. Coordination with other machines
    happens in exactly 4 API calls total, regardless of item count.
    """
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    run_start = datetime.now(timezone.utc)

    since, why = _resolve_since(cfg, days_override)
    console.print(f"[bold]Scanning browser history[/bold] ({why})…")
    discovered = discover_items(cfg, since)
    console.print(f"Discovered [cyan]{len(discovered)}[/cyan] item(s) from history\n")

    by_id = {i["item_id"]: i for i in discovered}
    claim_results = queue_api.claim(cfg, discovered)
    to_fetch_ids = partition_claims(claim_results, cfg.max_retries)
    to_fetch = [by_id[iid] for iid in to_fetch_ids]
    console.print(f"Claimed [cyan]{len(to_fetch)}[/cyan] new/retryable item(s)\n")

    phase1_completions, to_download = phase1_resolve(to_fetch, cfg)
    queue_api.complete(cfg, phase1_completions)

    phase2_completions = asyncio.run(phase2_download(to_download, cfg))
    queue_api.complete(cfg, phase2_completions)

    all_items = queue_api.list_items(cfg)
    done_rows = [
        i for i in all_items if i["status"] in ("done", "skipped") and i["filename"]
    ]
    index_path, n = write_index(cfg.output_dir, done_rows, since, run_start)

    done = sum(1 for i in all_items if i["status"] == "done")
    skipped = sum(1 for i in all_items if i["status"] == "skipped")
    failed = sum(1 for i in all_items if i["status"] == "failed")
    retryable = sum(
        1
        for i in all_items
        if i["status"] == "failed" and i["retries"] < cfg.max_retries
    )

    new_items = [
        {"source": by_id[c["item_id"]]["source"], "title": c["title"]}
        for c in (*phase1_completions, *phase2_completions)
        if c["status"] == "done"
    ]

    console.print(
        f"\n[bold green]Done[/bold green] — "
        f"{done} saved, {skipped} skipped, {failed} failed"
    )
    if retryable:
        console.print(f"[yellow]{retryable} item(s) retryable — run again[/yellow]")
    console.print(f"Index: [cyan]{index_path}[/cyan]  ({n} articles)")

    if not skip_embed:
        run_qmd_embed()

    save_last_run(run_start.isoformat())

    return RunResult(
        new_queued=len(to_fetch),
        done=done,
        skipped=skipped,
        failed=failed,
        retryable=retryable,
        index_path=index_path,
        article_count=n,
        new_items=new_items,
    )


def _summary_lines(result: RunResult) -> list[str]:
    lines = [
        f"queued={result.new_queued} done={result.done} skipped={result.skipped} "
        f"failed={result.failed} retryable={result.retryable}",
        f"index: {result.index_path} ({result.article_count} articles)",
    ]
    if result.new_items:
        lines.append("new articles:")
        lines += [f"  [{item['source']}] {item['title']}" for item in result.new_items]
    return lines


def _write_log(log_path: Path, started: datetime, result: RunResult) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    header = f"=== {started.strftime('%Y-%m-%d %H:%M:%S UTC')} ==="
    block = "\n".join([header, *_summary_lines(result), ""])
    with log_path.open("a", encoding="utf-8") as f:
        f.write(block + "\n")


def _notify_macos(result: RunResult) -> None:
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return
    message = f"{result.done} saved, {result.skipped} skipped, {result.failed} failed"
    script = f'display notification "{message}" with title "Archivore"'
    try:
        subprocess.run(["osascript", "-e", script], check=False, timeout=10)
    except OSError as e:
        console.print(f"[yellow]macOS notification failed: {e}[/yellow]")


def _send_email(cfg: Config, result: RunResult) -> None:
    subject = (
        f"Archivore run: {result.done} saved"
        f"{f', {result.failed} failed' if result.failed else ''}"
    )
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.email_from or cfg.smtp_user
    msg["To"] = cfg.email_to
    msg.set_content("\n".join(_summary_lines(result)))

    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if cfg.smtp_user and cfg.smtp_password:
                smtp.login(cfg.smtp_user, cfg.smtp_password)
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        console.print(f"[yellow]Email notification failed: {e}[/yellow]")


def run(
    cfg: Config, days_override: int | None = None, skip_embed: bool = False
) -> None:
    """Run the fetch pipeline, then log a summary and notify if configured."""
    started = datetime.now(timezone.utc)
    result = _pipeline(cfg, days_override=days_override, skip_embed=skip_embed)

    _write_log(cfg.log_path, started, result)
    console.print(f"Logged run summary to [cyan]{cfg.log_path}[/cyan]")

    if cfg.notify_macos:
        _notify_macos(result)

    if cfg.smtp_host and cfg.email_to:
        _send_email(cfg, result)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/test_run.py -v
```

Expected: PASS (6 tests).

- [ ] **Step 5: Delete the retired local queue module**

```bash
rm archivore/repository/queue.py
```

- [ ] **Step 6: Confirm nothing else references it**

```bash
grep -rn "repository.queue\|repository import queue" archivore/ tests/
```

Expected: no output. If anything shows up, it's a leftover import that must be removed before continuing (this plan's Task 4 Step 3 already replaced the only caller, `commands/run.py`).

- [ ] **Step 7: Run the full test suite**

```bash
uv run pytest -q
```

Expected: all tests pass (29 total: 16 pre-existing + 2 from Task 1 + 5 from Task 2 + 6 from this task). `test_render.py`'s tests are unaffected since `write_index()`'s own signature and behavior didn't change in this plan — only its data source, in `run.py`, changed.

- [ ] **Step 8: Lint and commit**

```bash
uv run --frozen ruff format archivore/commands/run.py tests/test_run.py
uv run --frozen ruff check .
git add archivore/commands/run.py tests/test_run.py
git rm archivore/repository/queue.py
git commit -m "Rewire archivore run onto the coordination API; retire local queue"
```

---

### Task 5: Migration script

**Files:**
- Create: `scripts/migrate_queue_to_d1.py`

This is a one-off operational script, not part of the shipped CLI — it's run once by hand during cutover, not tested with `pytest` (matches the spec's Migration section: "a one-off script, not part of the shipped CLI").

- [ ] **Step 1: Create the scripts directory and the migration script**

```bash
mkdir -p scripts
```

`scripts/migrate_queue_to_d1.py`:

```python
#!/usr/bin/env python3
"""One-off: bulk-import an existing local hn_this_week/queue.db into the
archivore-queue D1 database via the coordination API, then report which
.md files still need to be moved into the new output_dir.

Usage:
    QUEUE_API_URL=https://archivore-queue.<sub>.workers.dev \\
    QUEUE_API_TOKEN=<token> \\
    python3 scripts/migrate_queue_to_d1.py hn_this_week/queue.db
"""

import os
import sqlite3
import sys

import requests


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: migrate_queue_to_d1.py <path-to-old-queue.db>")

    db_path = sys.argv[1]
    api_url = os.environ["QUEUE_API_URL"].rstrip("/")
    token = os.environ["QUEUE_API_TOKEN"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM queue").fetchall()
    conn.close()

    print(f"Found {len(rows)} row(s) in {db_path}")

    claim_items = [
        {
            "item_id": r["item_id"],
            "source": r["source"],
            "comments_url": r["comments_url"],
            "article_url": r["article_url"],
        }
        for r in rows
    ]
    resp = requests.post(f"{api_url}/claim", json={"items": claim_items}, headers=headers, timeout=30)
    resp.raise_for_status()
    print(f"Claimed {len(claim_items)} item(s) in D1")

    complete_items = [
        {
            "item_id": r["item_id"],
            "status": r["status"],
            "title": r["title"],
            "is_selfpost": bool(r["is_selfpost"]),
            "filename": r["filename"],
            "last_error": r["last_error"],
        }
        for r in rows
    ]
    resp = requests.post(
        f"{api_url}/complete", json={"items": complete_items}, headers=headers, timeout=30
    )
    resp.raise_for_status()
    print(f"Reported status/title/filename for {len(complete_items)} item(s)")

    filenames = [r["filename"] for r in rows if r["filename"]]
    print(f"\n{len(filenames)} .md file(s) still need to move by hand into the new output_dir:")
    print("  mv hn_this_week/*.md \"<your RAW folder path>/\"")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it against the real deployed Worker (manual, one-time)**

```bash
QUEUE_API_URL=https://archivore-queue.<your-subdomain>.workers.dev \
QUEUE_API_TOKEN=<your-real-token> \
python3 scripts/migrate_queue_to_d1.py hn_this_week/queue.db
```

Expected: prints the row count claimed/reported, then the `mv` reminder.

- [ ] **Step 3: Move the existing Markdown files by hand**

```bash
mkdir -p "$HOME/Library/Mobile Documents/com~apple~CloudDocs/Todd's Obsidian Vault/Archivore/Raw"
mv hn_this_week/*.md "$HOME/Library/Mobile Documents/com~apple~CloudDocs/Todd's Obsidian Vault/Archivore/Raw/"
```

- [ ] **Step 4: Retire the old local queue database and directory**

```bash
rm hn_this_week/queue.db
rmdir hn_this_week 2>/dev/null || echo "hn_this_week/ not empty — check for leftover files before removing"
```

- [ ] **Step 5: Commit the script**

```bash
git add scripts/migrate_queue_to_d1.py
git commit -m "Add one-off queue.db -> D1 migration script"
```

---

### Task 6: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the `archivore run` description**

Replace:
```
Scans browser history since the last successful run (tracked in the user config, not a fixed schedule), pulls out HN, Reddit, and X URLs, fetches the linked articles, converts them to Markdown, and writes an index. Downloads run concurrently with a Rich live TUI. A SQLite queue makes runs resumable, and the qmd semantic index is refreshed automatically after each run.
```
with:
```
Scans browser history since the last successful run (tracked in the user config, not a fixed schedule), pulls out HN, Reddit, and X URLs, fetches the linked articles, converts them to Markdown, and writes an index. Downloads run concurrently with a Rich live TUI.

Dedup across multiple machines runs through a small self-hosted Cloudflare Worker + D1 database (`worker/` — see [docs/superpowers/specs/2026-08-29-multi-machine-reading-queue-sync-design.md](docs/superpowers/specs/2026-08-29-multi-machine-reading-queue-sync-design.md)). Every machine claims discovered items against the same coordination API in one batched call, so the same article never gets fetched twice. Fetched Markdown lands in `output_dir` (an iCloud-synced Obsidian vault folder by default), so it syncs to every machine automatically — no separate content-sync step needed. The qmd semantic index is refreshed automatically after each run.
```

- [ ] **Step 2: Update the output-structure block**

Replace:
```
hn_this_week/
  index.md         — linked table of contents (HN / Reddit / X sections)
  *.md             — one file per article
  queue.db         — resumable download queue
~/Library/Logs/archivore/run.log   — append-only summary of every run (default; see log_path)
```
with:
```
<output_dir>/  (default: ~/Library/Mobile Documents/com~apple~CloudDocs/Todd's Obsidian Vault/Archivore/Raw/)
  index.md         — linked table of contents (HN / Reddit / X sections)
  *.md             — one file per article
~/Library/Logs/archivore/run.log   — append-only summary of every run (default; see log_path)
```

- [ ] **Step 3: Update the Setup section**

Replace:
```
For semantic search over captured articles, install [qmd](https://github.com/tobi/qmd):

```bash
npm install -g @tobilu/qmd
qmd collection add ./hn_this_week --name archivore
qmd embed
```
```
with:
```
### Multi-machine dedup (archivore-queue)

Deploy the coordination API once (see `worker/README.md` or `docs/superpowers/plans/2026-08-30-archivore-queue-worker.md` for the full walkthrough — free Cloudflare account, `wrangler login`, `wrangler d1 create`, `wrangler deploy`). Then add to each machine's `config.yaml`:

```yaml
queue_api_url: https://archivore-queue.<your-subdomain>.workers.dev
queue_api_token: <the token you set with `wrangler secret put`>
```

### Semantic search (qmd)

For semantic search over captured articles, install [qmd](https://github.com/tobi/qmd):

```bash
npm install -g @tobilu/qmd
qmd collection add "$HOME/Library/Mobile Documents/com~apple~CloudDocs/Todd's Obsidian Vault/Archivore/Raw" --name archivore
qmd embed
```
```

- [ ] **Step 4: Update the architecture diagram**

Replace:
```
archivore run ─────────────► hn_this_week/  (per-article .md + index)
```
with:
```
archivore run ─────────────► archivore-queue (Worker + D1) ──► dedup decision
     │                                                               │
     ▼                                                               ▼
RAW/ (iCloud-synced Obsidian vault) ◄── per-article .md + index ─────┘
```

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "Document multi-machine queue setup in README"
```

---

### Task 7: End-to-end verification against the real deployed Worker

This task has no automated test — it's the manual proof that the whole system works together, using the real Worker from the other plan.

- [ ] **Step 1: Set queue_api_url/token in this machine's config**

Edit `~/.config/archivore/config.yaml`, add:

```yaml
queue_api_url: https://archivore-queue.<your-subdomain>.workers.dev
queue_api_token: <your-real-token>
```

- [ ] **Step 2: Run archivore for real**

```bash
uv run archivore run --skip-embed
```

Expected: scans history, discovers items, claims them (console shows "Claimed N new/retryable item(s)"), resolves + downloads, writes files into the configured `output_dir`, logs a summary.

- [ ] **Step 3: Confirm dedup works — run it again immediately**

```bash
uv run archivore run --skip-embed
```

Expected: "Discovered N item(s)" may be similar, but "Claimed 0 new/retryable item(s)" (or close to it) — everything from Step 2 is now `done` in D1, so nothing gets re-fetched.

- [ ] **Step 4: Confirm the D1 row survived correctly**

```bash
cd worker && wrangler d1 execute archivore-queue --remote --command \
  "SELECT item_id, source, status, title, filename FROM queue ORDER BY updated_at DESC LIMIT 5"
```

Expected: recent rows show real titles and filenames, not NULLs — this specifically confirms the `/complete` payload's `title`/`is_selfpost` fields are being stored (the D1 schema has these columns, but only `/complete` populates them, since `/claim` doesn't know them yet at claim time).

- [ ] **Step 5: Confirm the Markdown landed in the right place**

```bash
ls "$HOME/Library/Mobile Documents/com~apple~CloudDocs/Todd's Obsidian Vault/Archivore/Raw/"
```

Expected: `.md` files and `index.md` present.

- [ ] **Step 6: No commit for this task** — it's verification only, not a code change.

---

## Self-Review Notes

- **Spec coverage:** batch discover-then-claim (Task 4's `discover_items`/`_pipeline`), two-phase `/complete` flush (Task 4's `_pipeline` calling `queue_api.complete` after phase 1 and after phase 2), configurable `output_dir` default (Task 1), no offline fallback (Task 4 has no try/except around `queue_api` calls — a failure propagates and the run fails loudly, matching the spec's Non-goals), migration steps (Task 5), README documentation (Task 6).
- **Gap found and closed while writing this plan:** the spec's D1 schema includes `title` and `is_selfpost` columns, but the spec's `/complete` payload sketch didn't explicitly say these fields travel with it — without them, `GET /items` could never return a title for `write_index` to use. Task 1's `CompleteItem` and Task 3's `fetcher.fetch_article` both carry `title`/`is_selfpost` through explicitly; Task 7 Step 4 exists specifically to verify this in the running system, since it's the one gap invented during planning rather than the brainstorming phase.
- **Placeholder scan:** every step has complete code; the one legitimately manual, non-automatable task (Task 7) is a verification task, not a "TODO: implement" gap.
- **Type consistency:** `ClaimItem`/`ClaimResult`/`CompleteItem` (Task 1) are used with identical field names throughout `queue_api.py` (Task 2), `fetcher.py` (Task 3), and `run.py` (Task 4) — checked by re-reading each task's code against Task 1's definitions.

---

**Plan complete and saved to `docs/superpowers/plans/2026-08-30-queue-client-integration.md`.**
