"""All Markdown output: HTML conversion, article files, snapshot, and index."""

import re
from datetime import datetime, timezone
from itertools import groupby
from pathlib import Path

import html2text

from archivore.models import DomainEntry, Tab

SOURCE_LABELS = {
    "hn": "Hacker News",
    "reddit": "Reddit",
    "x": "X / Twitter",
}


def html_to_markdown(html_text: str) -> str:
    """Convert an HTML document or fragment to Markdown."""
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.body_width = 0
    h.unicode_snob = True
    return h.handle(html_text)


def safe_slug(title: str, item_id: str) -> str:
    """Build a filesystem-safe ``<id>-<slug>.md`` filename from a title."""
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")[:60]
    return f"{item_id}-{slug}.md"


def md_escape(text: str) -> str:
    """Escape pipe characters so they don't break Markdown tables."""
    return str(text).replace("|", "\\|")


def write_article_file(
    output_dir: Path,
    item_id: str,
    title: str,
    article_url: str,
    comments_url: str,
    fetch_note: str,
    md_body: str,
) -> str:
    """Write one article file and return its filename."""
    lines = [
        f"# {title}",
        "",
        f"- **Article:** [{article_url}]({article_url})",
        f"- **Comments:** [{comments_url}]({comments_url})",
        "",
    ]
    if fetch_note:
        lines += [fetch_note.strip(), ""]
    if md_body:
        lines += ["---", "", md_body]
    filename = safe_slug(title, item_id)
    (output_dir / filename).write_text("\n".join(lines), encoding="utf-8")
    return filename


def write_snapshot_markdown(
    path: Path,
    chrome_tabs: list[Tab],
    firefox_tabs: list[Tab],
    deduped_history: list[DomainEntry],
    days: int,
    n_raw_urls: int,
    limit: int,
    ignore_domains: set[str],
) -> None:
    """Write the tabs + history snapshot document."""
    now = datetime.now(timezone.utc)
    lines = [f"# Browser Snapshot — {now.strftime('%Y-%m-%d %H:%M UTC')}", ""]

    all_tabs = [("Chrome", t) for t in chrome_tabs] + [
        ("Firefox", t) for t in firefox_tabs
    ]
    lines += [f"## Open Tabs ({len(all_tabs)})", ""]

    if all_tabs:
        for (browser, window), group in groupby(
            all_tabs, key=lambda bt: (bt[0], bt[1]["window"])
        ):
            lines += [f"### {browser} — Window {window}", ""]
            for _, tab in group:
                title = tab["title"] or tab["url"]
                lines.append(f"- [{md_escape(title)}]({tab['url']})")
            lines.append("")
    else:
        lines += ["_No open tabs found._", ""]

    shown = deduped_history[:limit]
    total_domains = len(deduped_history)
    caption = f"{len(shown)} domains"
    if total_domains > len(shown):
        caption += f" of {total_domains:,}"
    caption += f", from {n_raw_urls:,} raw URLs"
    ignored = ", ".join(sorted(ignore_domains))
    lines += [
        f"## History — Last {days} Days ({caption})",
        f"_Ignored: {ignored}_",
        "",
    ]

    if shown:
        lines.append("| Visits | Last Visited | Domain | Top Page |")
        lines.append("|-------:|-------------|--------|---------|")
        for h in shown:
            date = h["last_visited_at"][:10]
            title = md_escape(h["title"] or h["domain"])
            lines.append(
                f"| {h['visit_count']:,} | {date} | {h['domain']} "
                f"| [{title}]({h['url']}) |"
            )
        lines.append("")
    else:
        lines += ["_No history found._", ""]

    path.write_text("\n".join(lines), encoding="utf-8")


def write_index(
    output_dir: Path, rows: list[dict], since: datetime, until: datetime
) -> tuple[Path, int]:
    """Write the reading-digest index and return its path and article count.

    ``rows`` need keys: source, title, filename, comments_url, is_selfpost.
    """
    since_str = since.strftime("%Y-%m-%d")
    until_str = until.strftime("%Y-%m-%d")

    by_source: dict[str, list[dict]] = {}
    for r in rows:
        by_source.setdefault(r["source"], []).append(r)

    lines = [
        "# Articles Read This Week",
        "",
        f"_{since_str} – {until_str} · {len(rows)} article(s)_",
        "",
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

    index_path = output_dir / "index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path, len(rows)
