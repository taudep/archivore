#!/usr/bin/env python3
"""
Fetch articles read this week from browser history across HN, Reddit, and X.
Converts each linked article to Markdown and produces a combined index.

Phase 1: resolve metadata sequentially (HN Firebase API, Reddit HTML, X HTML).
Phase 2: download linked articles concurrently (aiohttp) with a Rich live TUI.
Downloads are queued in SQLite so runs are fully resumable.
"""

import asyncio
import html
import json
import re
import sqlite3
import ssl
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urljoin

import aiohttp
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich import box

from main import get_chrome_history, get_firefox_history

try:
    import html2text as _html2text
except ImportError:
    sys.exit("pip install html2text")


OUTPUT_DIR  = Path("hn_this_week")
DB_PATH     = OUTPUT_DIR / "queue.db"
DAYS        = 7
HN_DELAY    = 0.5   # seconds between Firebase API calls
META_DELAY  = 1.5   # seconds between Reddit/X page fetches
MAX_RETRIES = 4     # article download retries before giving up
CONCURRENCY = 5     # simultaneous article downloads

console = Console()

_PERMANENT_ERRORS = {400, 401, 403, 404, 405, 410, 451}

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# SSL
# ---------------------------------------------------------------------------

def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


# ---------------------------------------------------------------------------
# Sync HTTP (phase 1 only — sequential metadata fetches)
# ---------------------------------------------------------------------------

def _sync_get(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers=headers or BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as r:
        charset = r.headers.get_content_charset() or "utf-8"
        return r.read().decode(charset, errors="replace")


def _og(html_text, tag):
    """Extract an og: or twitter: meta tag value."""
    for attr in (f'og:{tag}', f'twitter:{tag}'):
        m = re.search(
            rf'<meta[^>]+(?:property|name)="{re.escape(attr)}"[^>]+content="([^"]*)"',
            html_text, re.I
        )
        if not m:
            m = re.search(
                rf'<meta[^>]+content="([^"]*)"[^>]+(?:property|name)="{re.escape(attr)}"',
                html_text, re.I
            )
        if m:
            return html.unescape(m.group(1).strip())
    return None


# ---------------------------------------------------------------------------
# Queue DB
# ---------------------------------------------------------------------------

def open_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Migration: add source column if missing (existing rows are HN)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS queue (
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
        )
    """)
    # Add source column to old DBs that lack it
    cols = {r[1] for r in conn.execute("PRAGMA table_info(queue)").fetchall()}
    if "source" not in cols:
        conn.execute("ALTER TABLE queue ADD COLUMN source TEXT NOT NULL DEFAULT 'hn'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_source ON queue(source)")
    conn.commit()
    return conn


def _queue_insert(conn, item_id, source, comments_url, now):
    cur = conn.execute(
        "INSERT OR IGNORE INTO queue "
        "(item_id, source, comments_url, status, queued_at, updated_at) "
        "VALUES (?, ?, ?, 'pending', ?, ?)",
        (item_id, source, comments_url, now, now),
    )
    return cur.rowcount


def db_mark(conn, item_id, status, now, **fields):
    sets = ["status = ?", "updated_at = ?", "retries = retries + 1"]
    vals = [status, now]
    for col, val in fields.items():
        sets.append(f"{col} = ?")
        vals.append(str(val)[:500] if col == "last_error" else val)
    vals.append(item_id)
    conn.execute(f"UPDATE queue SET {', '.join(sets)} WHERE item_id = ?", vals)
    conn.commit()


# ---------------------------------------------------------------------------
# History extraction — one function per source
# ---------------------------------------------------------------------------

def get_hn_items_from_history(days=DAYS):
    items = {}
    for row in get_chrome_history(days=days) + get_firefox_history(days=days):
        p = urlparse(row["url"])
        if p.netloc != "news.ycombinator.com" or p.path != "/item":
            continue
        qs = parse_qs(p.query)
        if "id" not in qs:
            continue
        iid = qs["id"][0]
        if iid not in items or row["visit_count"] > items[iid]["visit_count"]:
            items[iid] = row
    return items   # item_id → history row


def get_reddit_items_from_history(days=DAYS):
    """Return {post_id: url} for reddit.com/r/.../comments/{id}/... URLs."""
    items = {}
    for row in get_chrome_history(days=days) + get_firefox_history(days=days):
        p = urlparse(row["url"])
        if p.netloc.replace("www.", "").replace("old.", "") != "reddit.com":
            continue
        m = re.match(r"/r/[^/]+/comments/([a-z0-9]+)", p.path, re.I)
        if not m:
            continue
        post_id = m.group(1)
        if post_id not in items or row["visit_count"] > items[post_id]["visit_count"]:
            items[post_id] = row["url"]
    return items   # post_id → canonical url


def get_x_items_from_history(days=DAYS):
    """Return {tweet_or_article_id: (kind, url)} for x.com /status/ and /article/ URLs."""
    items = {}
    skip_paths = {"/i/flow/", "/i/jf/", "/home", "/explore", "/notifications",
                  "/messages", "/i/", "/settings"}
    for row in get_chrome_history(days=days) + get_firefox_history(days=days):
        p = urlparse(row["url"])
        if p.netloc.replace("www.", "") not in ("x.com", "twitter.com"):
            continue
        if any(p.path.startswith(sp) for sp in skip_paths):
            continue

        m_status  = re.match(r"/[^/]+/status/(\d+)", p.path)
        m_article = re.match(r"/(?:[^/]+/)?(?:i/)?article/(\d+)", p.path)

        if m_status:
            xid = m_status.group(1)
            kind = "article" if m_article else "tweet"
        elif m_article:
            xid = m_article.group(1)
            kind = "article"
        else:
            continue

        # Prefer article kind over tweet if same ID seen as both
        if xid not in items or (kind == "article" and items[xid][0] == "tweet"):
            items[xid] = (kind, row["url"])

    return items   # xid → (kind, url)


# ---------------------------------------------------------------------------
# Phase 1: metadata resolution (sequential, source-specific)
# ---------------------------------------------------------------------------

def resolve_hn_metadata(item_id):
    data = json.loads(_sync_get(
        f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json",
        headers={"User-Agent": "this_week/1.0", "Accept": "application/json"},
    ))
    if not data or data.get("type") not in ("story", "job"):
        return None, None, False
    title = data.get("title", "").strip()
    if not title:
        return None, None, False
    url = data.get("url", "")
    if not url:
        return title, f"https://news.ycombinator.com/item?id={item_id}", True
    return title, url, False


def resolve_reddit_metadata(post_id, original_url):
    """Fetch old.reddit.com for the post. Returns (title, article_url, is_selfpost)."""
    # Rebuild a clean old.reddit.com URL from the post_id
    m = re.match(r".*?/r/([^/]+)/comments/" + re.escape(post_id), original_url, re.I)
    subreddit = m.group(1) if m else "all"
    url = f"https://old.reddit.com/r/{subreddit}/comments/{post_id}/"

    page = _sync_get(url)

    title = _og(page, "title")
    if not title:
        t = re.search(r"<title>([^<]+)</title>", page, re.I)
        title = t.group(1).split(":")[0].strip() if t else f"Reddit post {post_id}"

    # Detect link post: title <a> pointing to an external https:// URL
    link_m = re.search(
        r'class="title[^"]*"\s+href="(https?://(?!(?:www\.)?reddit\.com)[^"]+)"',
        page, re.I
    )
    comments_url = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/"

    if link_m:
        return title, link_m.group(1), False   # link post → fetch article in phase 2
    else:
        # Self-post or cross-post: save the reddit page content itself
        og_desc = _og(page, "description") or ""
        # Grab selftext if present
        selftext_m = re.search(
            r'class="usertext-body[^"]*"[^>]*>\s*<div class="md">([\s\S]+?)</div>\s*</div>',
            page, re.I
        )
        selftext_html = selftext_m.group(1) if selftext_m else ""
        selftext_md   = html_to_markdown(selftext_html) if selftext_html.strip() else og_desc
        return title, comments_url, True, selftext_md  # 4-tuple for self-posts


def resolve_x_metadata(xid, kind, original_url):
    """Fetch x.com and extract whatever is available from meta tags."""
    # Normalise to a clean URL
    m = re.match(r"(https?://(?:www\.)?(?:x|twitter)\.com/[^/]+/(?:status|article)/\d+)",
                 original_url, re.I)
    url = m.group(1) if m else original_url

    try:
        page = _sync_get(url)
    except Exception as e:
        return None, url, False, f"_Could not fetch X page: {e}_"

    og_title = _og(page, "title") or ""
    og_desc  = _og(page, "description") or ""

    # X articles: try the page <title> tag
    if kind == "article":
        t = re.search(r"<title>([^<]{10,})</title>", page, re.I)
        if t and "X (" not in t.group(1) and "Twitter" not in t.group(1):
            og_title = t.group(1).strip()

    # Derive a useful title:
    # - Prefer og:title when it's not the generic "Author on X" boilerplate
    # - Fall back to first 80 chars of the tweet/description content
    # - Last resort: extract @handle from the URL
    content = og_desc if og_desc and not og_desc.startswith("http") else ""
    if og_title and "on X" not in og_title and "Twitter" not in og_title:
        title = og_title
    elif content:
        title = content[:80].rstrip() + ("…" if len(content) > 80 else "")
    else:
        handle_m = re.search(r"x\.com/([^/]+)/", url, re.I)
        handle   = f"@{handle_m.group(1)}" if handle_m else "X user"
        title    = f"{handle} — {kind}"

    note = content or "_Content requires JavaScript or X login — visit the link above._"
    return title, url, True, note   # always selfpost (we don't fetch X pages in phase 2)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def html_to_markdown(html_text):
    h = _html2text.HTML2Text()
    h.ignore_links  = False
    h.ignore_images = True
    h.body_width    = 0
    h.unicode_snob  = True
    return h.handle(html_text)


def safe_slug(title, item_id):
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")[:60]
    return f"{item_id}-{slug}.md"


def write_article_file(item_id, title, article_url, comments_url,
                       is_selfpost, fetch_note, md_body):
    lines = [
        f"# {title}", "",
        f"- **Article:** [{article_url}]({article_url})",
        f"- **Comments:** [{comments_url}]({comments_url})",
        "",
    ]
    if fetch_note:
        lines += [fetch_note.strip(), ""]
    if md_body:
        lines += ["---", "", md_body]
    filename = safe_slug(title, item_id)
    (OUTPUT_DIR / filename).write_text("\n".join(lines), encoding="utf-8")
    return filename


# ---------------------------------------------------------------------------
# Phase 1 dispatcher
# ---------------------------------------------------------------------------

def phase1_resolve(conn, now):
    unresolved = conn.execute(
        "SELECT item_id, source, comments_url, article_url "
        "FROM queue WHERE title IS NULL AND status NOT IN ('skipped')"
    ).fetchall()

    if not unresolved:
        return

    console.print(f"\n[bold]Phase 1[/bold] — resolving {len(unresolved)} item(s)…")

    for row in unresolved:
        item_id      = row["item_id"]
        source       = row["source"]
        comments_url = row["comments_url"]
        console.print(f"  [{source}:{item_id}] ", end="")

        try:
            if source == "hn":
                result = resolve_hn_metadata(item_id)
                selftext = None
                if len(result) == 3:
                    title, article_url, is_selfpost = result
                time.sleep(HN_DELAY)

            elif source == "reddit":
                # We stored original URL in comments_url for reddit items
                result = resolve_reddit_metadata(item_id, comments_url)
                selftext = None
                if len(result) == 4:
                    title, article_url, is_selfpost, selftext = result
                else:
                    title, article_url, is_selfpost = result
                time.sleep(META_DELAY)

            elif source == "x":
                kind, orig_url = row["article_url"].split("|", 1) if row["article_url"] else ("tweet", comments_url)
                result = resolve_x_metadata(item_id, kind, orig_url)
                title, article_url, is_selfpost, selftext = result
                time.sleep(META_DELAY)

            else:
                console.print(f"[yellow]unknown source '{source}', skipping[/yellow]")
                db_mark(conn, item_id, "skipped", now, last_error=f"unknown source")
                continue

            if not title:
                console.print("[yellow]no title found, skipping[/yellow]")
                db_mark(conn, item_id, "skipped", now, last_error="no title")
                continue

            console.print(title[:70])

            if is_selfpost and selftext is not None:
                # Write the file now; no phase-2 fetch needed
                filename = write_article_file(
                    item_id, title, article_url, comments_url,
                    True, selftext, ""
                )
                db_mark(conn, item_id, "done", now,
                        title=title, article_url=article_url,
                        is_selfpost=1, filename=filename)
            else:
                conn.execute(
                    "UPDATE queue SET title=?, article_url=?, is_selfpost=?, updated_at=? "
                    "WHERE item_id=?",
                    (title, article_url, int(is_selfpost), now, item_id),
                )
                conn.commit()

        except Exception as e:
            console.print(f"[red]error: {e}[/red]")
            db_mark(conn, item_id, "failed", now, last_error=str(e))


# ---------------------------------------------------------------------------
# Phase 2: async article downloads (for link posts only)
# ---------------------------------------------------------------------------

async def fetch_article(session, row, state, db_lock, conn, now):
    item_id      = row["item_id"]
    title        = row["title"]
    article_url  = row["article_url"]
    comments_url = row["comments_url"]
    is_selfpost  = bool(row["is_selfpost"])

    state[item_id] = ("⏳", "fetching…")

    if is_selfpost:
        # Should have been written in phase 1; mark done if file exists
        existing = list(OUTPUT_DIR.glob(f"{item_id}-*.md"))
        if existing:
            async with db_lock:
                db_mark(conn, item_id, "done", now, filename=existing[0].name)
            state[item_id] = ("✅", "already saved")
        else:
            state[item_id] = ("⚠", "selfpost but no file?")
        return

    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            async with session.get(
                article_url, headers=BROWSER_HEADERS,
                ssl=_ssl_ctx(), timeout=aiohttp.ClientTimeout(total=25),
                max_line_size=65536, max_field_size=65536,
            ) as resp:
                if resp.status in _PERMANENT_ERRORS:
                    note = f"_Article unavailable (HTTP {resp.status})._\n"
                    filename = write_article_file(
                        item_id, title, article_url, comments_url, False, note, ""
                    )
                    async with db_lock:
                        db_mark(conn, item_id, "skipped", now,
                                filename=filename, last_error=f"HTTP {resp.status}")
                    state[item_id] = ("⛔", f"HTTP {resp.status}")
                    return

                if resp.status == 429 or resp.status >= 500:
                    raise aiohttp.ClientResponseError(
                        resp.request_info, resp.history, status=resp.status
                    )

                content_type = resp.headers.get("Content-Type", "")
                if "text" not in content_type and "html" not in content_type:
                    note = f"_Skipped: non-HTML content ({content_type.split(';')[0]})._\n"
                    filename = write_article_file(
                        item_id, title, article_url, comments_url, False, note, ""
                    )
                    async with db_lock:
                        db_mark(conn, item_id, "skipped", now,
                                filename=filename, last_error="non-HTML")
                    state[item_id] = ("⛔", f"non-HTML")
                    return

                page = await resp.text(errors="replace")
                md_body  = html_to_markdown(page)
                filename = write_article_file(
                    item_id, title, article_url, comments_url, False, "", md_body
                )
                async with db_lock:
                    db_mark(conn, item_id, "done", now, filename=filename)
                state[item_id] = ("✅", "saved")
                return

        except (aiohttp.ClientError, asyncio.TimeoutError, ssl.SSLError) as e:
            last_err = e
            wait = 2 ** attempt
            state[item_id] = ("🔄", f"retry {attempt + 1}/{MAX_RETRIES}")
            await asyncio.sleep(wait)

    state[item_id] = ("❌", f"failed: {str(last_err)[:40]}")
    async with db_lock:
        db_mark(conn, item_id, "failed", now, last_error=str(last_err))


def _build_table(state, rows_by_id):
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan",
                  expand=True, min_width=80)
    table.add_column("", width=2, no_wrap=True)
    table.add_column("Source", width=8, no_wrap=True)
    table.add_column("Title", ratio=3)
    table.add_column("Status", ratio=2)
    color_map = {"✅": "green", "❌": "red", "⛔": "yellow",
                 "⏳": "cyan",  "🔄": "blue", "⚠": "yellow"}
    for item_id, (icon, status) in state.items():
        r      = rows_by_id.get(item_id, {})
        title  = (r.get("title") or "")[:50]
        source = r.get("source", "")
        color  = color_map.get(icon, "white")
        table.add_row(icon, source, title, f"[{color}]{status}[/{color}]")
    return table


async def phase2_download(conn, now):
    rows = conn.execute(
        "SELECT * FROM queue "
        "WHERE title IS NOT NULL AND is_selfpost = 0 "
        "AND status NOT IN ('skipped') "
        "AND (status = 'pending' OR (status = 'failed' AND retries < ?)) "
        "ORDER BY source, item_id DESC",
        (MAX_RETRIES,),
    ).fetchall()

    if not rows:
        console.print("\n[green]Nothing to download in phase 2.[/green]")
        return

    rows_by_id = {r["item_id"]: dict(r) for r in rows}
    state      = {r["item_id"]: ("⏸", "queued") for r in rows}
    db_lock    = asyncio.Lock()

    console.print(f"\n[bold]Phase 2[/bold] — downloading {len(rows)} article(s) "
                  f"({CONCURRENCY} concurrent)…\n")

    connector = aiohttp.TCPConnector(limit=CONCURRENCY, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        sem = asyncio.Semaphore(CONCURRENCY)

        async def bounded(row):
            async with sem:
                await fetch_article(session, row, state, db_lock, conn, now)

        with Live(
            _build_table(state, rows_by_id),
            refresh_per_second=4, console=console
        ) as live:
            tasks = [asyncio.create_task(bounded(r)) for r in rows]
            while not all(t.done() for t in tasks):
                live.update(_build_table(state, rows_by_id))
                await asyncio.sleep(0.25)
            await asyncio.gather(*tasks)
            live.update(_build_table(state, rows_by_id))


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

def write_index(conn):
    rows = conn.execute(
        "SELECT source, title, article_url, comments_url, filename, is_selfpost "
        "FROM queue "
        "WHERE status IN ('done', 'skipped') AND filename IS NOT NULL "
        "ORDER BY source, item_id DESC"
    ).fetchall()

    now   = datetime.now(timezone.utc)
    since = (now - timedelta(days=DAYS)).strftime("%Y-%m-%d")
    until = now.strftime("%Y-%m-%d")

    SOURCE_LABELS = {
        "hn":     "Hacker News",
        "reddit": "Reddit",
        "x":      "X / Twitter",
    }

    by_source = {}
    for r in rows:
        by_source.setdefault(r["source"], []).append(r)

    lines = [
        "# Articles Read This Week", "",
        f"_{since} – {until} · {len(rows)} article(s)_", "",
    ]

    for source in ("hn", "reddit", "x"):
        entries = by_source.get(source, [])
        if not entries:
            continue
        lines += [f"## {SOURCE_LABELS[source]} ({len(entries)})", ""]
        for e in entries:
            tag = " _(self-post)_" if e["is_selfpost"] else ""
            lines.append(
                f"- [{e['title']}]({e['filename']}){tag}"
                f" — [comments]({e['comments_url']})"
            )
        lines.append("")

    index_path = OUTPUT_DIR / "index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path, len(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def sync_all_sources(conn, now):
    new = 0

    hn_items = get_hn_items_from_history()
    console.print(f"  HN:     [cyan]{len(hn_items)}[/cyan] item(s)")
    for item_id in hn_items:
        comments_url = f"https://news.ycombinator.com/item?id={item_id}"
        new += _queue_insert(conn, item_id, "hn", comments_url, now)

    reddit_items = get_reddit_items_from_history()
    console.print(f"  Reddit: [cyan]{len(reddit_items)}[/cyan] item(s)")
    for post_id, orig_url in reddit_items.items():
        # Store orig_url in comments_url so phase1 can build the old.reddit URL
        new += _queue_insert(conn, post_id, "reddit", orig_url, now)

    x_items = get_x_items_from_history()
    console.print(f"  X:      [cyan]{len(x_items)}[/cyan] item(s)")
    for xid, (kind, orig_url) in x_items.items():
        item_id      = f"x_{xid}"
        comments_url = orig_url
        # Store kind|orig_url in article_url field so phase1 can read it
        cur = conn.execute(
            "INSERT OR IGNORE INTO queue "
            "(item_id, source, comments_url, article_url, status, queued_at, updated_at) "
            "VALUES (?, 'x', ?, ?, 'pending', ?, ?)",
            (item_id, comments_url, f"{kind}|{orig_url}", now, now),
        )
        new += cur.rowcount

    conn.commit()
    return new


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    conn = open_db()
    now  = datetime.now(timezone.utc).isoformat()

    console.print(f"[bold]Scanning browser history[/bold] (last {DAYS} days)…")
    new = sync_all_sources(conn, now)
    console.print(f"Queued [cyan]{new}[/cyan] new item(s)\n")

    phase1_resolve(conn, now)
    asyncio.run(phase2_download(conn, now))

    index_path, n = write_index(conn)

    done      = conn.execute("SELECT COUNT(*) FROM queue WHERE status='done'").fetchone()[0]
    skipped   = conn.execute("SELECT COUNT(*) FROM queue WHERE status='skipped'").fetchone()[0]
    failed    = conn.execute("SELECT COUNT(*) FROM queue WHERE status='failed'").fetchone()[0]
    retryable = conn.execute(
        "SELECT COUNT(*) FROM queue WHERE status='failed' AND retries < ?", (MAX_RETRIES,)
    ).fetchone()[0]

    console.print(f"\n[bold green]Done[/bold green] — {done} saved, {skipped} skipped, {failed} failed")
    if retryable:
        console.print(f"[yellow]{retryable} item(s) retryable — run again[/yellow]")
    console.print(f"Index: [cyan]{index_path}[/cyan]  ({n} articles)")
    conn.close()


if __name__ == "__main__":
    main()
