"""Tests for Markdown rendering helpers."""

from datetime import datetime, timedelta, timezone

from archivore.render import (
    html_to_markdown,
    md_escape,
    safe_slug,
    write_article_file,
    write_index,
)


def test_safe_slug_sanitizes_and_truncates():
    slug = safe_slug("Hello, World! A Very: Long/Title" + " x" * 60, "42")
    assert slug.startswith("42-hello-world")
    assert slug.endswith(".md")
    assert "/" not in slug and ":" not in slug
    # id prefix + max 60 slug chars + extension
    assert len(slug) <= len("42-") + 60 + len(".md")


def test_md_escape_pipes():
    assert md_escape("a|b") == "a\\|b"


def test_html_to_markdown_basic():
    md = html_to_markdown("<h1>Title</h1><p>Some <b>bold</b> text.</p>")
    assert "# Title" in md
    assert "**bold**" in md


def test_write_article_file(tmp_path):
    filename = write_article_file(
        tmp_path,
        "1",
        "My Title",
        "https://a.example",
        "https://c.example",
        "",
        "body",
    )
    content = (tmp_path / filename).read_text()
    assert content.startswith("# My Title")
    assert "https://a.example" in content
    assert "body" in content


def test_write_index_groups_by_source(tmp_path):
    rows = [
        {
            "source": "hn",
            "title": "HN Post",
            "filename": "1-hn.md",
            "comments_url": "https://news.ycombinator.com/item?id=1",
            "is_selfpost": 0,
        },
        {
            "source": "x",
            "title": "Tweet",
            "filename": "x_2-tweet.md",
            "comments_url": "https://x.com/a/status/2",
            "is_selfpost": 1,
        },
    ]
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=7)
    index_path, n = write_index(tmp_path, rows, since, until)
    content = index_path.read_text()
    assert n == 2
    assert "## Hacker News (1)" in content
    assert "## X / Twitter (1)" in content
    assert "_(self-post)_" in content
