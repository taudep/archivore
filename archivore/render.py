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

# Key for the discussion-thread link in front matter, per source. X has no
# separate discussion page — the tweet URL already is the "source" — so it's
# omitted there.
_DISCUSSION_KEYS = {
    "hn": "hackernews-discussion",
    "reddit": "reddit-discussion",
}


def _yaml_str(value: str) -> str:
    """Quote a YAML scalar, escaping backslashes and embedded quotes."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_frontmatter(
    title: str,
    item_id: str,
    article_url: str,
    comments_url: str,
    source: str,
    visited_at: str,
    author: str | None,
    published: str | None,
) -> str:
    """Build an Obsidian-clipper-style YAML front matter block.

    ``created`` is always the date the item was viewed in the browser
    (``visited_at``, a ``HistoryRow.last_visited_at`` timestamp) — not
    today's date and not the article's own publish date, so the vault
    reflects when *you* read something rather than when it ran.

    ``item_id`` lives here (as ``id``) rather than in the filename, since
    filenames are now ``<timestamp>-<title>.md`` for readability and
    chronological sorting on disk.
    """
    authors = [a.strip() for a in author.split(",") if a.strip()] if author else []
    lines = [
        "---",
        f"title: {_yaml_str(title)}",
        f"id: {_yaml_str(item_id)}",
        f"source: {_yaml_str(article_url)}",
        "author:",
    ]
    lines += [f'  - "[[{a}]]"' for a in authors]
    lines += [
        f"published: {published or ''}",
        f"created: {visited_at[:10]}",
        "description:",
        "tags:",
        "  - clippings",
    ]
    discussion_key = _DISCUSSION_KEYS.get(source)
    if discussion_key:
        lines.append(f"{discussion_key}: {comments_url}")
    lines.append("---")
    return "\n".join(lines)


def html_to_markdown(html_text: str) -> str:
    """Convert an HTML document or fragment to Markdown."""
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.body_width = 0
    h.unicode_snob = True
    return h.handle(html_text)


_FILENAME_UNSAFE_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_FRONTMATTER_ID_RE = re.compile(r'^id: "(.*)"$', re.MULTILINE)


def sanitize_title_for_filename(title: str) -> str:
    """Strip characters invalid in filenames while keeping the title
    otherwise human-readable — spaces and normal capitalization stay."""
    cleaned = _FILENAME_UNSAFE_RE.sub("", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:80].rstrip(" .")


def build_filename(
    title: str, visited_at: str, disambiguator: str | None = None
) -> str:
    """``<YYYYMMDD>-<title>.md`` — timestamp first so the vault sorts by
    capture date on disk. Uses the same date as front matter's ``created``
    (``visited_at[:10]``, the browser-history visit date), so filename and
    front matter always agree.

    ``disambiguator`` (an item_id) is only appended on an actual filename
    collision with a *different* item — see ``write_article_file``.
    """
    ts = visited_at[:10].replace("-", "")
    name = f"{ts}-{sanitize_title_for_filename(title)}"
    if disambiguator:
        name += f" ({disambiguator})"
    return f"{name}.md"


def _file_belongs_to(path: Path, item_id: str) -> bool:
    """True if ``path`` doesn't exist yet, or its front matter's ``id``
    matches ``item_id`` (a retry/re-fetch of the same item, safe to
    overwrite) rather than a different item that happens to collide."""
    if not path.is_file():
        return True
    text = path.read_text(encoding="utf-8", errors="replace")
    m = _FRONTMATTER_ID_RE.search(text)
    return m is not None and m.group(1) == item_id


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
    *,
    source: str,
    visited_at: str,
    author: str | None = None,
    published: str | None = None,
) -> str:
    """Write one article file, with Obsidian-clipper-style YAML front
    matter, and return its filename."""
    lines = [
        render_frontmatter(
            title,
            item_id,
            article_url,
            comments_url,
            source,
            visited_at,
            author,
            published,
        ),
        "",
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

    filename = build_filename(title, visited_at)
    if not _file_belongs_to(output_dir / filename, item_id):
        filename = build_filename(title, visited_at, disambiguator=item_id)
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
