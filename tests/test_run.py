"""Tests for the pure claim-partitioning logic in commands/run.py."""

from archivore.commands.run import partition_claims


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
