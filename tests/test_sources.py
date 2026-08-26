"""Tests for the pure history-extraction and dedup transforms."""

from archivore.models import HistoryRow
from archivore.sources import (
    dedupe_by_domain,
    extract_hn_items,
    extract_reddit_items,
    extract_x_items,
)


def row(url: str, visits: int = 1, title: str = "t") -> HistoryRow:
    return HistoryRow(
        url=url, title=title, visit_count=visits, last_visited_at="2026-07-17"
    )


class TestDedupeByDomain:
    def test_strips_www_and_aggregates(self):
        rows = [
            row("https://www.example.com/a", 3),
            row("https://example.com/b", 5),
        ]
        result = dedupe_by_domain(rows, set())
        assert len(result) == 1
        assert result[0]["domain"] == "example.com"
        assert result[0]["visit_count"] == 8
        assert result[0]["url"] == "https://example.com/b"  # most visited

    def test_filters_ignored_domains_and_non_http(self):
        rows = [
            row("https://gmail.com/inbox"),
            row("ftp://example.com/file"),
            row("https://keep.me/page"),
        ]
        result = dedupe_by_domain(rows, {"gmail.com"})
        assert [r["domain"] for r in result] == ["keep.me"]

    def test_sorted_by_total_visits_descending(self):
        rows = [row("https://low.com/", 1), row("https://high.com/", 9)]
        result = dedupe_by_domain(rows, set())
        assert [r["domain"] for r in result] == ["high.com", "low.com"]


class TestExtractHn:
    def test_extracts_item_ids(self):
        rows = [
            row("https://news.ycombinator.com/item?id=123"),
            row("https://news.ycombinator.com/newest"),
            row("https://example.com/item?id=999"),
        ]
        assert set(extract_hn_items(rows)) == {"123"}

    def test_keeps_most_visited_row_per_id(self):
        rows = [
            row("https://news.ycombinator.com/item?id=1", 2),
            row("https://news.ycombinator.com/item?id=1", 7),
        ]
        assert extract_hn_items(rows)["1"]["visit_count"] == 7


class TestExtractReddit:
    def test_matches_comment_threads_incl_old_and_www(self):
        rows = [
            row("https://www.reddit.com/r/python/comments/abc123/title/"),
            row("https://old.reddit.com/r/golang/comments/xyz789/other/"),
            row("https://www.reddit.com/r/python/"),
        ]
        items = extract_reddit_items(rows)
        assert set(items) == {"abc123", "xyz789"}

    def test_no_allowlist_keeps_everything(self):
        rows = [row("https://www.reddit.com/r/python/comments/abc123/title/")]
        assert set(extract_reddit_items(rows, None)) == {"abc123"}
        assert set(extract_reddit_items(rows, set())) == {"abc123"}

    def test_allowlist_filters_out_other_subreddits(self):
        rows = [
            row("https://www.reddit.com/r/LocalLLaMA/comments/abc123/title/"),
            row("https://www.reddit.com/r/wallstreetbets/comments/xyz789/yolo/"),
        ]
        items = extract_reddit_items(rows, {"localllama"})
        assert set(items) == {"abc123"}

    def test_allowlist_is_case_insensitive(self):
        rows = [row("https://www.reddit.com/r/DoomEmacs/comments/abc123/title/")]
        items = extract_reddit_items(rows, {"doomemacs"})
        assert set(items) == {"abc123"}


class TestExtractX:
    def test_extracts_status_and_article_ids(self):
        rows = [
            row("https://x.com/karpathy/status/111"),
            row("https://x.com/someone/article/222"),
            row("https://x.com/home"),
            row("https://x.com/i/flow/login"),
        ]
        items = extract_x_items(rows)
        assert items["111"][0] == "tweet"
        assert items["222"][0] == "article"
        assert len(items) == 2

    def test_article_kind_wins_over_tweet(self):
        rows = [
            row("https://x.com/a/status/5"),
            row("https://x.com/a/article/5"),
        ]
        assert extract_x_items(rows)["5"][0] == "article"
