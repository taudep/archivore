"""X/Twitter metadata from og: tags.

X pages are fully JS-rendered, so meta tags are all the server gives us:
og:description carries tweet text; X articles expose only their title. The
result is always a self-post — phase 2 never fetches X pages.
"""

import re

from archivore.clients.http import extract_og, get_text
from archivore.models import ResolvedItem

_JS_NOTE = "_Content requires JavaScript or X login — visit the link above._"


def resolve(xid: str, kind: str, original_url: str) -> ResolvedItem:
    """Resolve an X status or article to whatever metadata is available."""
    m = re.match(
        r"(https?://(?:www\.)?(?:x|twitter)\.com/[^/]+/(?:status|article)/\d+)",
        original_url,
        re.I,
    )
    url = m.group(1) if m else original_url

    try:
        page = get_text(url)
    except Exception as e:
        return ResolvedItem(
            title=None,
            article_url=url,
            is_selfpost=False,
            selftext=f"_Could not fetch X page: {e}_",
        )

    og_title = extract_og(page, "title") or ""
    og_desc = extract_og(page, "description") or ""

    handle_m = re.search(r"(?:x|twitter)\.com/([^/]+)/", url, re.I)
    author = f"@{handle_m.group(1)}" if handle_m else None

    # X articles: the page <title> tag is more useful than og:title
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
        title = f"{author or 'X user'} — {kind}"

    return ResolvedItem(
        title=title,
        article_url=url,
        is_selfpost=True,
        selftext=content or _JS_NOTE,
        author=author,
    )
