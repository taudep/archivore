"""Tests for fetcher.fetch_article's manual-save extensions: a missing title
falls back to the page's own title (then the URL), and an optional user note
is passed through to the article's front matter."""

import asyncio

from archivore.clients import fetcher


class _FakeResponse:
    def __init__(self, status, body, content_type="text/html; charset=utf-8"):
        self.status = status
        self._body = body
        self.headers = {"Content-Type": content_type}

    async def text(self, errors="strict"):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, response):
        self._response = response

    def get(self, url, **kwargs):
        return self._response


def _item(title=None, note=None):
    item = {
        "item_id": "m_0123456789abcdef",
        "source": "manual",
        "title": title,
        "article_url": "https://a.example/page",
        "comments_url": "https://a.example/page",
        "visited_at": "2026-11-11T10:20:30+00:00",
    }
    if note is not None:
        item["note"] = note
    return item


def _fetch(tmp_path, response, item):
    return asyncio.run(
        fetcher.fetch_article(_FakeSession(response), item, {}, tmp_path, 1)
    )


def test_missing_title_is_extracted_from_page(tmp_path):
    page = "<html><head><title>Page Title</title></head><body>hi</body></html>"
    result = _fetch(tmp_path, _FakeResponse(200, page), _item())
    assert result["status"] == "done"
    assert result["title"] == "Page Title"
    content = (tmp_path / result["filename"]).read_text()
    assert 'title: "Page Title"' in content


def test_missing_title_and_no_page_title_falls_back_to_url(tmp_path):
    result = _fetch(tmp_path, _FakeResponse(200, "<p>hi</p>"), _item())
    assert result["title"] == "https://a.example/page"


def test_given_title_wins_over_page_title(tmp_path):
    page = "<title>Page Title</title>"
    result = _fetch(tmp_path, _FakeResponse(200, page), _item(title="Mine"))
    assert result["title"] == "Mine"


def test_note_is_written_to_frontmatter(tmp_path):
    result = _fetch(
        tmp_path, _FakeResponse(200, "<title>T</title>"), _item(note="remember")
    )
    content = (tmp_path / result["filename"]).read_text()
    assert 'note: "remember"' in content


def test_permanent_error_without_title_uses_url_and_keeps_note(tmp_path):
    result = _fetch(tmp_path, _FakeResponse(404, ""), _item(note="remember"))
    assert result["status"] == "skipped"
    assert result["title"] == "https://a.example/page"
    content = (tmp_path / result["filename"]).read_text()
    assert 'note: "remember"' in content
