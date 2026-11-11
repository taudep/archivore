"""Save one URL on demand (``archivore save``) through the same pipeline
``archivore run`` uses for history items: claim it against the shared
coordination API, download it with the phase-2 fetcher, write it to
``cfg.output_dir``, and report the outcome — one /claim and at most one
/complete call.

Meant to be called by other tools (e.g. a browser-hotkey service), so it
never raises on network trouble: every outcome is a ``SaveResult`` with a
status of saved, duplicate or failed. It deliberately leaves the index, the
qmd embed and the last-run watermark alone; the next ``archivore run``
picks the item up in the index from global queue state.
"""

import asyncio
import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from rich.console import Console

from archivore.clients import fetcher, queue_api
from archivore.commands.run import partition_claims
from archivore.config import Config
from archivore.models import ClaimItem, CompleteItem
from archivore.render import MANUAL_SOURCE

_TRACKING_PARAMS = {"fbclid", "gclid"}


def _is_tracking_param(segment: str) -> bool:
    key = segment.split("=", 1)[0].lower()
    return key.startswith("utm_") or key in _TRACKING_PARAMS


def normalize_url(url: str) -> str:
    """Canonical form of ``url`` for dedupe: lowercase scheme and host, no
    fragment, no utm_*/fbclid/gclid params, no trailing slash except the
    root path. Remaining query params keep their order and encoding."""
    parts = urlsplit(url.strip())
    netloc = parts.netloc
    userinfo, sep, host = netloc.rpartition("@")
    netloc = f"{userinfo}{sep}{host.lower()}"

    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"

    query = "&".join(
        seg for seg in parts.query.split("&") if seg and not _is_tracking_param(seg)
    )
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def manual_item_id(url: str) -> str:
    """Queue item_id for a manually saved URL: ``m_`` + 16 hex chars of the
    SHA-1 of its normalized form, so equivalent URLs dedupe."""
    return "m_" + hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:16]


@dataclass
class SaveResult:
    """Outcome of one ``archivore save``; ``asdict`` is the --json shape."""

    status: str  # "saved" | "duplicate" | "failed"
    item_id: str
    filename: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _error_text(e: BaseException) -> str:
    return str(e) or type(e).__name__


async def _download(item: dict, cfg: Config) -> CompleteItem:
    """Phase-2 download of a single item, same session setup as run."""
    connector = aiohttp.TCPConnector(limit=1, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        state: fetcher.StateMap = {}
        return await fetcher.fetch_article(
            session, item, state, cfg.output_dir, cfg.max_retries
        )


def save(
    cfg: Config,
    url: str,
    title: str | None = None,
    note: str | None = None,
    console: Console | None = None,
) -> SaveResult:
    """Claim, fetch, write and report one URL. Never raises for network,
    queue or fetch failures — those come back as status "failed"."""
    console = console or Console(stderr=True)
    item_id = manual_item_id(url)

    claim_item = ClaimItem(
        item_id=item_id,
        source=MANUAL_SOURCE,
        comments_url=url,
        article_url=url,
    )
    try:
        results = queue_api.claim(cfg, [claim_item])
    except Exception as e:
        return SaveResult("failed", item_id, error=f"claim failed: {_error_text(e)}")

    result = next((r for r in results if r["item_id"] == item_id), None)
    if result is None:
        return SaveResult("failed", item_id, error="claim returned no result")

    if not partition_claims([result], cfg.max_retries):
        if result["status"] == "failed":
            return SaveResult(
                "failed",
                item_id,
                error=(
                    f"retry limit reached ({result['retries']}/{cfg.max_retries}) "
                    "for this URL"
                ),
            )
        # done/skipped, or pending (claimed by another machine, in progress)
        return SaveResult("duplicate", item_id)

    item = {
        "item_id": item_id,
        "source": MANUAL_SOURCE,
        "title": title,
        "article_url": url,
        "comments_url": url,
        "visited_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": note,
    }
    console.print(f"Fetching [cyan]{url}[/cyan]…")
    try:
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        completion = asyncio.run(_download(item, cfg))
    except Exception as e:
        completion = CompleteItem(
            item_id=item_id,
            status="failed",
            title=title,
            is_selfpost=False,
            filename=None,
            last_error=_error_text(e),
            article_url=url,
        )

    try:
        queue_api.complete(cfg, [completion])
    except Exception as e:
        return SaveResult(
            "failed",
            item_id,
            filename=completion["filename"],
            error=f"complete failed: {_error_text(e)}",
        )

    if completion["status"] == "failed":
        return SaveResult("failed", item_id, error=completion["last_error"])
    # "skipped" (e.g. HTTP 404, non-HTML) still writes a stub file holding the
    # URL and note, and the queue now marks it handled — report it as saved,
    # with the reason in ``error``, so a caller doesn't retry it forever.
    return SaveResult(
        "saved",
        item_id,
        filename=completion["filename"],
        error=completion["last_error"],
    )
