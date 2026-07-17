"""Pure transforms over browser-history rows: per-source item extraction
and domain-level deduplication. No I/O — everything takes rows as input."""

import re
from urllib.parse import parse_qs, urlparse

from archivore.models import DomainEntry, HistoryRow


def dedupe_by_domain(
    rows: list[HistoryRow], ignore_domains: set[str]
) -> list[DomainEntry]:
    """Group history by domain, filter noise, and sort by total visits.

    For each domain the visit counts are summed and the single most-visited
    URL is surfaced as the representative link.
    """
    domains: dict[str, dict] = {}
    for h in rows:
        parsed = urlparse(h["url"])
        if parsed.scheme not in ("http", "https"):
            continue
        host = (parsed.hostname or "").removeprefix("www.")
        if not host or host in ignore_domains:
            continue

        if host not in domains:
            domains[host] = {
                "visit_count": 0,
                "best_url": h["url"],
                "best_title": h["title"],
                "best_count": 0,
                "last_visited_at": h["last_visited_at"],
            }

        d = domains[host]
        d["visit_count"] += h["visit_count"]

        if h["visit_count"] > d["best_count"]:
            d["best_count"] = h["visit_count"]
            d["best_url"] = h["url"]
            d["best_title"] = h["title"]

        if h["last_visited_at"] > d["last_visited_at"]:
            d["last_visited_at"] = h["last_visited_at"]

    result = [
        DomainEntry(
            domain=host,
            url=d["best_url"],
            title=d["best_title"],
            visit_count=d["visit_count"],
            last_visited_at=d["last_visited_at"],
        )
        for host, d in domains.items()
    ]
    result.sort(key=lambda r: r["visit_count"], reverse=True)
    return result


def extract_hn_items(rows: list[HistoryRow]) -> dict[str, HistoryRow]:
    """Return ``{item_id: history_row}`` for news.ycombinator.com item pages."""
    items: dict[str, HistoryRow] = {}
    for row in rows:
        p = urlparse(row["url"])
        if p.netloc != "news.ycombinator.com" or p.path != "/item":
            continue
        qs = parse_qs(p.query)
        if "id" not in qs:
            continue
        iid = qs["id"][0]
        if iid not in items or row["visit_count"] > items[iid]["visit_count"]:
            items[iid] = row
    return items


def extract_reddit_items(rows: list[HistoryRow]) -> dict[str, str]:
    """Return ``{post_id: url}`` for reddit.com comment-thread URLs."""
    items: dict[str, str] = {}
    best_counts: dict[str, int] = {}
    for row in rows:
        p = urlparse(row["url"])
        if p.netloc.replace("www.", "").replace("old.", "") != "reddit.com":
            continue
        m = re.match(r"/r/[^/]+/comments/([a-z0-9]+)", p.path, re.I)
        if not m:
            continue
        post_id = m.group(1)
        if post_id not in items or row["visit_count"] > best_counts[post_id]:
            items[post_id] = row["url"]
            best_counts[post_id] = row["visit_count"]
    return items


_X_SKIP_PATHS = (
    "/i/flow/",
    "/i/jf/",
    "/home",
    "/explore",
    "/notifications",
    "/messages",
    "/i/",
    "/settings",
)


def extract_x_items(rows: list[HistoryRow]) -> dict[str, tuple[str, str]]:
    """Return ``{id: (kind, url)}`` for x.com /status/ and /article/ URLs.

    ``kind`` is ``"tweet"`` or ``"article"``; article wins when the same ID
    appears as both.
    """
    items: dict[str, tuple[str, str]] = {}
    for row in rows:
        p = urlparse(row["url"])
        if p.netloc.replace("www.", "") not in ("x.com", "twitter.com"):
            continue
        if any(p.path.startswith(sp) for sp in _X_SKIP_PATHS):
            continue

        m_status = re.match(r"/[^/]+/status/(\d+)", p.path)
        m_article = re.match(r"/(?:[^/]+/)?(?:i/)?article/(\d+)", p.path)

        if m_status:
            xid = m_status.group(1)
            kind = "article" if m_article else "tweet"
        elif m_article:
            xid = m_article.group(1)
            kind = "article"
        else:
            continue

        if xid not in items or (kind == "article" and items[xid][0] == "tweet"):
            items[xid] = (kind, row["url"])

    return items
