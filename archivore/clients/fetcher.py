"""Async article downloader used by phase 2 of the reading digest."""

import asyncio
import sqlite3
import ssl
from pathlib import Path

import aiohttp

from archivore.clients.http import BROWSER_HEADERS, aiohttp_ssl_ctx
from archivore.render import html_to_markdown, write_article_file
from archivore.repository import queue

_PERMANENT_ERRORS = {400, 401, 403, 404, 405, 410, 451}

# aiohttp's default 8 KB header-line limit crashes on X.com's giant CSP header
_HEADER_LIMIT = 65536

StateMap = dict[str, tuple[str, str]]


async def fetch_article(
    session: aiohttp.ClientSession,
    row: sqlite3.Row,
    state: StateMap,
    db_lock: asyncio.Lock,
    conn: sqlite3.Connection,
    now: str,
    output_dir: Path,
    max_retries: int,
) -> None:
    """Download one article, convert to Markdown, and update the queue."""
    item_id = row["item_id"]
    title = row["title"]
    article_url = row["article_url"]
    comments_url = row["comments_url"]

    state[item_id] = ("⏳", "fetching…")

    if row["is_selfpost"]:
        # Should have been written in phase 1; mark done if file exists
        existing = list(output_dir.glob(f"{item_id}-*.md"))
        if existing:
            async with db_lock:
                queue.mark(conn, item_id, "done", now, filename=existing[0].name)
            state[item_id] = ("✅", "already saved")
        else:
            state[item_id] = ("⚠", "selfpost but no file?")
        return

    async def _skip(note: str, error: str, icon_status: str) -> None:
        filename = write_article_file(
            output_dir, item_id, title, article_url, comments_url, note, ""
        )
        async with db_lock:
            queue.mark(
                conn, item_id, "skipped", now, filename=filename, last_error=error
            )
        state[item_id] = ("⛔", icon_status)

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
                    await _skip(
                        f"_Article unavailable (HTTP {resp.status})._\n",
                        f"HTTP {resp.status}",
                        f"HTTP {resp.status}",
                    )
                    return

                if resp.status == 429 or resp.status >= 500:
                    raise aiohttp.ClientResponseError(
                        resp.request_info, resp.history, status=resp.status
                    )

                content_type = resp.headers.get("Content-Type", "")
                if "text" not in content_type and "html" not in content_type:
                    short_type = content_type.split(";")[0]
                    await _skip(
                        f"_Skipped: non-HTML content ({short_type})._\n",
                        "non-HTML",
                        "non-HTML",
                    )
                    return

                page = await resp.text(errors="replace")
                md_body = html_to_markdown(page)
                filename = write_article_file(
                    output_dir,
                    item_id,
                    title,
                    article_url,
                    comments_url,
                    "",
                    md_body,
                )
                async with db_lock:
                    queue.mark(conn, item_id, "done", now, filename=filename)
                state[item_id] = ("✅", "saved")
                return

        except (aiohttp.ClientError, asyncio.TimeoutError, ssl.SSLError) as e:
            last_err = e
            state[item_id] = ("🔄", f"retry {attempt + 1}/{max_retries}")
            await asyncio.sleep(2**attempt)

    state[item_id] = ("❌", f"failed: {str(last_err)[:40]}")
    async with db_lock:
        queue.mark(conn, item_id, "failed", now, last_error=str(last_err))
