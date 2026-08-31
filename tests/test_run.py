"""Tests for the pure claim-partitioning logic in commands/run.py."""

from datetime import datetime, timezone
from unittest.mock import patch

from archivore.commands.run import discover_items, indexable_rows, partition_claims
from archivore.config import Config
from archivore.models import HistoryRow


def test_claimed_items_are_fetched():
    results = [{"item_id": "1", "claimed": True, "status": "pending", "retries": 0}]
    assert partition_claims(results, max_retries=4) == ["1"]


def test_done_items_are_not_refetched():
    results = [{"item_id": "1", "claimed": False, "status": "done", "retries": 0}]
    assert partition_claims(results, max_retries=4) == []


def test_skipped_items_are_not_refetched():
    results = [{"item_id": "1", "claimed": False, "status": "skipped", "retries": 0}]
    assert partition_claims(results, max_retries=4) == []


def test_failed_under_max_retries_is_retried():
    results = [{"item_id": "1", "claimed": False, "status": "failed", "retries": 2}]
    assert partition_claims(results, max_retries=4) == ["1"]


def test_failed_at_max_retries_is_not_retried():
    results = [{"item_id": "1", "claimed": False, "status": "failed", "retries": 4}]
    assert partition_claims(results, max_retries=4) == []


def test_mixed_batch_partitions_correctly():
    results = [
        {"item_id": "new", "claimed": True, "status": "pending", "retries": 0},
        {"item_id": "done", "claimed": False, "status": "done", "retries": 0},
        {"item_id": "retry", "claimed": False, "status": "failed", "retries": 1},
        {"item_id": "exhausted", "claimed": False, "status": "failed", "retries": 4},
    ]
    assert partition_claims(results, max_retries=4) == ["new", "retry"]


_REDDIT_ROW = HistoryRow(
    url="https://www.reddit.com/r/git/comments/abc123/title/",
    title="t",
    visit_count=1,
    last_visited_at="2026-08-30T12:00:00+00:00",
)


class TestIndexableRows:
    def test_excludes_done_item_whose_file_was_deleted(self, tmp_path):
        (tmp_path / "present.md").write_text("x")
        all_items = [
            {"status": "done", "filename": "present.md"},
            {"status": "done", "filename": "deleted.md"},
        ]
        result = indexable_rows(all_items, tmp_path)
        assert [r["filename"] for r in result] == ["present.md"]

    def test_excludes_items_with_no_filename_or_wrong_status(self, tmp_path):
        (tmp_path / "present.md").write_text("x")
        all_items = [
            {"status": "done", "filename": None},
            {"status": "pending", "filename": "present.md"},
            {"status": "failed", "filename": "present.md"},
        ]
        assert indexable_rows(all_items, tmp_path) == []

    def test_includes_skipped_with_existing_file(self, tmp_path):
        (tmp_path / "present.md").write_text("x")
        all_items = [{"status": "skipped", "filename": "present.md"}]
        assert len(indexable_rows(all_items, tmp_path)) == 1


class TestDiscoverItemsRedditToggle:
    def test_reddit_excluded_by_default(self):
        cfg = Config()
        assert cfg.enable_reddit is False
        with patch(
            "archivore.commands.run.get_all_history", return_value=[_REDDIT_ROW]
        ):
            items, _ = discover_items(cfg, datetime.now(timezone.utc))
        assert items == []

    def test_reddit_included_when_enabled(self):
        cfg = Config()
        cfg.enable_reddit = True
        with patch(
            "archivore.commands.run.get_all_history", return_value=[_REDDIT_ROW]
        ):
            items, visited_at = discover_items(cfg, datetime.now(timezone.utc))
        assert len(items) == 1
        assert items[0]["source"] == "reddit"
        assert visited_at["abc123"] == "2026-08-30T12:00:00+00:00"
