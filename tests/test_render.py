"""Tests for Markdown rendering helpers."""

from datetime import datetime, timedelta, timezone

from archivore.render import (
    html_to_markdown,
    md_escape,
    render_frontmatter,
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
        source="hn",
        visited_at="2026-08-30T12:00:00+00:00",
        author="Jane Doe",
        published="2026-08-01",
    )
    content = (tmp_path / filename).read_text()
    assert content.startswith("---\n")
    assert "# My Title" in content
    assert "https://a.example" in content
    assert "body" in content
    assert 'source: "https://a.example"' in content
    assert '- "[[Jane Doe]]"' in content
    assert "created: 2026-08-30" in content
    assert "published: 2026-08-01" in content
    assert "hackernews-discussion: https://c.example" in content


class TestRenderFrontmatter:
    def test_created_is_visited_at_date_not_published(self):
        fm = render_frontmatter(
            "T",
            "https://a.example",
            "https://c.example",
            "hn",
            "2026-08-30T12:00:00+00:00",
            None,
            "2020-06-01",
        )
        assert "created: 2026-08-30" in fm
        assert "published: 2020-06-01" in fm

    def test_multiple_authors_become_separate_wikilinks(self):
        fm = render_frontmatter(
            "T",
            "https://a.example",
            "https://c.example",
            "hn",
            "2026-08-30",
            "David Leadbeater, Jane Doe",
            None,
        )
        assert '  - "[[David Leadbeater]]"' in fm
        assert '  - "[[Jane Doe]]"' in fm

    def test_no_author_leaves_author_key_empty(self):
        fm = render_frontmatter(
            "T",
            "https://a.example",
            "https://c.example",
            "hn",
            "2026-08-30",
            None,
            None,
        )
        lines = fm.splitlines()
        author_idx = lines.index("author:")
        assert lines[author_idx + 1].startswith("published:")

    def test_discussion_key_depends_on_source(self):
        hn_fm = render_frontmatter(
            "T",
            "https://a.example",
            "https://c.example",
            "hn",
            "2026-08-30",
            None,
            None,
        )
        reddit_fm = render_frontmatter(
            "T",
            "https://a.example",
            "https://c.example",
            "reddit",
            "2026-08-30",
            None,
            None,
        )
        x_fm = render_frontmatter(
            "T", "https://a.example", "https://c.example", "x", "2026-08-30", None, None
        )
        assert "hackernews-discussion: https://c.example" in hn_fm
        assert "reddit-discussion: https://c.example" in reddit_fm
        assert "discussion" not in x_fm

    def test_tags_always_include_clippings(self):
        fm = render_frontmatter(
            "T",
            "https://a.example",
            "https://c.example",
            "hn",
            "2026-08-30",
            None,
            None,
        )
        assert "tags:" in fm
        assert "  - clippings" in fm


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
