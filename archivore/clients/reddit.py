"""Reddit metadata by scraping old.reddit.com.

The JSON API now requires OAuth, but old.reddit.com still serves plain HTML
with og: meta tags and the post body inline.
"""

import re

from archivore.clients.http import extract_og, get_text
from archivore.models import ResolvedItem
from archivore.render import html_to_markdown


def resolve(post_id: str, original_url: str) -> ResolvedItem:
    """Resolve a Reddit post to a title plus either an external article URL
    (link post) or the post body as Markdown (self-post)."""
    m = re.match(r".*?/r/([^/]+)/comments/" + re.escape(post_id), original_url, re.I)
    subreddit = m.group(1) if m else "all"
    page = get_text(f"https://old.reddit.com/r/{subreddit}/comments/{post_id}/")

    title = extract_og(page, "title")
    if not title:
        t = re.search(r"<title>([^<]+)</title>", page, re.I)
        title = t.group(1).split(":")[0].strip() if t else f"Reddit post {post_id}"

    comments_url = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/"

    # Link post: the title anchor points at an external URL
    link_m = re.search(
        r'class="title[^"]*"\s+href="(https?://(?!(?:www\.)?reddit\.com)[^"]+)"',
        page,
        re.I,
    )
    if link_m:
        return ResolvedItem(title=title, article_url=link_m.group(1), is_selfpost=False)

    # Self-post or cross-post: capture the body content itself
    og_desc = extract_og(page, "description") or ""
    selftext_m = re.search(
        r'class="usertext-body[^"]*"[^>]*>\s*<div class="md">'
        r"([\s\S]+?)</div>\s*</div>",
        page,
        re.I,
    )
    selftext_html = selftext_m.group(1) if selftext_m else ""
    selftext_md = html_to_markdown(selftext_html) if selftext_html.strip() else og_desc

    author_m = re.search(r'class="author"[^>]*>([^<]+)<', page, re.I)
    time_m = re.search(r'<time[^>]+datetime="([^"]+)"', page, re.I)
    return ResolvedItem(
        title=title,
        article_url=comments_url,
        is_selfpost=True,
        selftext=selftext_md,
        author=author_m.group(1).strip() if author_m else None,
        published=time_m.group(1)[:10] if time_m else None,
    )
