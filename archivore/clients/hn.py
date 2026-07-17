"""Hacker News metadata via the Firebase API (no rate limits)."""

from archivore.clients.http import session
from archivore.models import ResolvedItem

API_URL = "https://hacker-news.firebaseio.com/v0/item/{item_id}.json"


def resolve(item_id: str) -> ResolvedItem | None:
    """Resolve an HN item to its title and linked article URL.

    Returns ``None`` for deleted items, comments, and other non-story types.
    Text posts (Ask HN etc.) come back as self-posts pointing at the thread.
    """
    resp = session().get(
        API_URL.format(item_id=item_id),
        headers={"User-Agent": "archivore/0.2", "Accept": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    if not data or data.get("type") not in ("story", "job"):
        return None
    title = data.get("title", "").strip()
    if not title:
        return None

    comments_url = f"https://news.ycombinator.com/item?id={item_id}"
    url = data.get("url", "")
    if not url:
        text = data.get("text", "")
        note = text or "_Self-post — discussion is at the comments link above._"
        return ResolvedItem(
            title=title, article_url=comments_url, is_selfpost=True, selftext=note
        )
    return ResolvedItem(title=title, article_url=url, is_selfpost=False)
