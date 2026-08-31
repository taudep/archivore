"""Tests for HTML meta-tag extraction helpers."""

from archivore.clients.http import (
    extract_meta_tag,
    extract_og,
    extract_page_author,
    extract_page_published,
)


def test_extract_meta_tag_property_then_content():
    html = '<meta property="article:author" content="Jane Doe">'
    assert extract_meta_tag(html, "article:author") == "Jane Doe"


def test_extract_meta_tag_content_then_property():
    html = '<meta content="Jane Doe" property="article:author">'
    assert extract_meta_tag(html, "article:author") == "Jane Doe"


def test_extract_meta_tag_missing_returns_none():
    assert extract_meta_tag("<html></html>", "article:author") is None


def test_extract_meta_tag_unescapes_entities():
    html = '<meta name="author" content="Q&amp;A Author">'
    assert extract_meta_tag(html, "author") == "Q&A Author"


def test_extract_og_still_works():
    html = '<meta property="og:title" content="Hello">'
    assert extract_og(html, "title") == "Hello"


class TestExtractPageAuthor:
    def test_prefers_article_author_over_generic_author(self):
        html = (
            '<meta name="author" content="Wrong Person">'
            '<meta property="article:author" content="Right Person">'
        )
        assert extract_page_author(html) == "Right Person"

    def test_falls_back_to_generic_author_meta(self):
        html = '<meta name="author" content="Jane Doe">'
        assert extract_page_author(html) == "Jane Doe"

    def test_none_when_nothing_present(self):
        assert extract_page_author("<html></html>") is None


class TestExtractPagePublished:
    def test_prefers_article_published_time(self):
        html = (
            '<meta name="date" content="2020-01-01">'
            '<meta property="article:published_time" content="2020-06-01T00:00:00Z">'
        )
        assert extract_page_published(html) == "2020-06-01T00:00:00Z"

    def test_falls_back_to_date_meta(self):
        html = '<meta name="date" content="2020-06-01">'
        assert extract_page_published(html) == "2020-06-01"

    def test_none_when_nothing_present(self):
        assert extract_page_published("<html></html>") is None
