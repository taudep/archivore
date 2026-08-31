"""Tests for Markdown rendering helpers."""

from datetime import datetime, timedelta, timezone

from archivore.render import (
    build_filename,
    html_to_markdown,
    md_escape,
    render_frontmatter,
    sanitize_title_for_filename,
    write_article_file,
    write_index,
)


def test_sanitize_title_strips_unsafe_chars_and_truncates():
    cleaned = sanitize_title_for_filename('A: Very/Bad*Title?"<With>|Junk' + " x" * 60)
    assert "/" not in cleaned and ":" not in cleaned and "*" not in cleaned
    assert '"' not in cleaned and "<" not in cleaned and ">" not in cleaned
    assert len(cleaned) <= 80


def test_sanitize_title_preserves_spaces_and_case():
    assert sanitize_title_for_filename("SQLite as a Document Database") == (
        "SQLite as a Document Database"
    )


def test_md_escape_pipes():
    assert md_escape("a|b") == "a\\|b"


def test_html_to_markdown_basic():
    md = html_to_markdown("<h1>Title</h1><p>Some <b>bold</b> text.</p>")
    assert "# Title" in md
    assert "**bold**" in md


class TestBuildFilename:
    def test_timestamp_comes_first_as_yyyymmdd(self):
        name = build_filename("My Title", "2026-08-30T14:22:00+00:00")
        assert name.startswith("20260830-")

    def test_matches_same_date_as_created(self):
        # created is visited_at[:10]; filename must use the same date, not
        # a locally-converted one, so the two never disagree near midnight.
        name = build_filename("My Title", "2026-08-30T23:58:00+00:00")
        assert name.startswith("20260830-")

    def test_title_is_human_readable_not_slugified(self):
        name = build_filename("My Cool Title", "2026-08-30T14:22:00+00:00")
        assert "My Cool Title" in name
        assert "my-cool-title" not in name

    def test_disambiguator_appended_on_collision(self):
        plain = build_filename("Same Title", "2026-08-30T14:22:00+00:00")
        disambiguated = build_filename(
            "Same Title", "2026-08-30T14:22:00+00:00", disambiguator="12345"
        )
        assert disambiguated != plain
        assert "(12345)" in disambiguated


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
    assert filename.startswith("20260830-")
    assert "My Title" in filename
    assert not filename.startswith("1-")

    content = (tmp_path / filename).read_text()
    assert content.startswith("---\n")
    assert "# My Title" in content
    assert "https://a.example" in content
    assert "body" in content
    assert 'id: "1"' in content
    assert 'source: "https://a.example"' in content
    assert '- "[[Jane Doe]]"' in content
    assert "created: 2026-08-30" in content
    assert "published: 2026-08-01" in content
    assert "hackernews-discussion: https://c.example" in content


def test_write_article_file_retry_overwrites_same_item(tmp_path):
    """A re-fetch of the same item_id lands on the same filename instead of
    piling up disambiguated copies."""
    first = write_article_file(
        tmp_path,
        "1",
        "My Title",
        "https://a.example",
        "https://c.example",
        "",
        "first body",
        source="hn",
        visited_at="2026-08-30T12:00:00+00:00",
    )
    second = write_article_file(
        tmp_path,
        "1",
        "My Title",
        "https://a.example",
        "https://c.example",
        "",
        "second body",
        source="hn",
        visited_at="2026-08-30T12:00:00+00:00",
    )
    assert first == second
    assert "second body" in (tmp_path / second).read_text()


def test_write_article_file_disambiguates_different_items(tmp_path):
    """Two different items landing on the same timestamp+title don't
    clobber each other."""
    first = write_article_file(
        tmp_path,
        "1",
        "Same Title",
        "https://a.example",
        "https://c.example",
        "",
        "body one",
        source="hn",
        visited_at="2026-08-30T12:00:00+00:00",
    )
    second = write_article_file(
        tmp_path,
        "2",
        "Same Title",
        "https://a.example",
        "https://c.example",
        "",
        "body two",
        source="hn",
        visited_at="2026-08-30T12:00:00+00:00",
    )
    assert first != second
    assert "body one" in (tmp_path / first).read_text()
    assert "body two" in (tmp_path / second).read_text()


class TestRenderFrontmatter:
    def test_created_is_visited_at_date_not_published(self):
        fm = render_frontmatter(
            "T",
            "1",
            "https://a.example",
            "https://c.example",
            "hn",
            "2026-08-30T12:00:00+00:00",
            None,
            "2020-06-01",
        )
        assert "created: 2026-08-30" in fm
        assert "published: 2020-06-01" in fm

    def test_id_field_present(self):
        fm = render_frontmatter(
            "T",
            "49426995",
            "https://a.example",
            "https://c.example",
            "hn",
            "2026-08-30",
            None,
            None,
        )
        assert 'id: "49426995"' in fm

    def test_multiple_authors_become_separate_wikilinks(self):
        fm = render_frontmatter(
            "T",
            "1",
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
            "1",
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
            "1",
            "https://a.example",
            "https://c.example",
            "hn",
            "2026-08-30",
            None,
            None,
        )
        reddit_fm = render_frontmatter(
            "T",
            "1",
            "https://a.example",
            "https://c.example",
            "reddit",
            "2026-08-30",
            None,
            None,
        )
        x_fm = render_frontmatter(
            "T",
            "1",
            "https://a.example",
            "https://c.example",
            "x",
            "2026-08-30",
            None,
            None,
        )
        assert "hackernews-discussion: https://c.example" in hn_fm
        assert "reddit-discussion: https://c.example" in reddit_fm
        assert "discussion" not in x_fm

    def test_tags_always_include_clippings(self):
        fm = render_frontmatter(
            "T",
            "1",
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
