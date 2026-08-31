"""Async article downloader used by phase 2 of the reading digest.

Every call returns a CompleteItem for the caller to report back to the
coordination API — this module never talks to that API directly, and
never handles self-posts (phase 1 resolves and completes those before
anything reaches here)."""

import asyncio
import ssl
from pathlib import Path

import aiohttp

from archivore.clients.http import (
    BROWSER_HEADERS,
    aiohttp_ssl_ctx,
    extract_page_author,
    extract_page_published,
)
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

    ``item`` needs keys: item_id, title, article_url, comments_url, source,
    visited_at, and optionally author/published (a self-post fallback from
    resolve() — always None here in practice, since phase 2 only handles
    link posts).
    """
    item_id = item["item_id"]
    title = item["title"]
    article_url = item["article_url"]
    comments_url = item["comments_url"]
    source = item["source"]
    visited_at = item["visited_at"]
    resolved_author = item.get("author")
    resolved_published = item.get("published")

    state[item_id] = ("⏳", "fetching…")

    def _skip(note: str, error: str, icon_status: str) -> CompleteItem:
        filename = write_article_file(
            output_dir,
            item_id,
            title,
            article_url,
            comments_url,
            note,
            "",
            source=source,
            visited_at=visited_at,
            author=resolved_author,
            published=resolved_published,
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
                    output_dir,
                    item_id,
                    title,
                    article_url,
                    comments_url,
                    "",
                    md_body,
                    source=source,
                    visited_at=visited_at,
                    author=extract_page_author(page) or resolved_author,
                    published=extract_page_published(page) or resolved_published,
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
