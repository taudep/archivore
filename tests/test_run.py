"""Tests for the pure claim-partitioning logic in commands/run.py."""

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from archivore.commands.run import (
    _run_post_run_cmd,
    discover_items,
    indexable_rows,
    partition_claims,
    phase1_resolve,
    run,
)
from archivore.config import Config
from archivore.models import ClaimItem, HistoryRow, ResolvedItem, RunResult


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


class TestPhase1ResolveArticleUrl:
    """Regression coverage: CompleteItem must carry the resolved
    article_url. It previously didn't, so the /complete API call never
    updated D1's article_url column, leaving it permanently NULL for
    every HN/Reddit item (set to NULL at /claim time, since the real URL
    isn't known until resolve() runs here in phase 1)."""

    def _cfg(self, tmp_path):
        cfg = Config()
        cfg.output_dir = tmp_path
        cfg.hn_delay = 0
        return cfg

    def test_self_post_completion_includes_resolved_article_url(self, tmp_path):
        claim_item = ClaimItem(
            item_id="1",
            source="hn",
            comments_url="https://news.ycombinator.com/item?id=1",
            article_url=None,
        )
        resolved = ResolvedItem(
            title="Ask HN: something",
            article_url="https://news.ycombinator.com/item?id=1",
            is_selfpost=True,
            selftext="body",
        )
        with (
            patch("archivore.commands.run.hn.resolve", return_value=resolved),
            patch("archivore.commands.run.write_article_file", return_value="1.md"),
        ):
            completions, to_download = phase1_resolve(
                [claim_item], self._cfg(tmp_path), {"1": "2026-01-01T00:00:00+00:00"}
            )

        assert to_download == []
        assert len(completions) == 1
        assert completions[0]["article_url"] == "https://news.ycombinator.com/item?id=1"

    def test_link_post_carries_article_url_into_to_download(self, tmp_path):
        claim_item = ClaimItem(
            item_id="2",
            source="hn",
            comments_url="https://news.ycombinator.com/item?id=2",
            article_url=None,
        )
        resolved = ResolvedItem(
            title="A link post",
            article_url="https://example.com/article",
            is_selfpost=False,
        )
        with patch("archivore.commands.run.hn.resolve", return_value=resolved):
            completions, to_download = phase1_resolve(
                [claim_item], self._cfg(tmp_path), {"2": "2026-01-01T00:00:00+00:00"}
            )

        assert completions == []
        assert len(to_download) == 1
        assert to_download[0]["article_url"] == "https://example.com/article"

    def test_resolve_failure_reports_no_article_url(self, tmp_path):
        claim_item = ClaimItem(
            item_id="3",
            source="hn",
            comments_url="https://news.ycombinator.com/item?id=3",
            article_url=None,
        )
        with patch(
            "archivore.commands.run.hn.resolve", side_effect=RuntimeError("boom")
        ):
            completions, to_download = phase1_resolve(
                [claim_item], self._cfg(tmp_path), {"3": "2026-01-01T00:00:00+00:00"}
            )

        assert to_download == []
        assert len(completions) == 1
        assert completions[0]["status"] == "failed"
        assert completions[0]["article_url"] is None


class TestRunPostRunCmd:
    def test_noop_when_unset(self):
        cfg = Config()
        with patch("archivore.commands.run.subprocess.run") as mock_run:
            _run_post_run_cmd(cfg)
        mock_run.assert_not_called()

    def test_invokes_configured_command(self):
        cfg = Config()
        cfg.post_run_cmd = 'claude -p "/taude ingest"'
        with patch("archivore.commands.run.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                cfg.post_run_cmd, 0, "", ""
            )
            _run_post_run_cmd(cfg)
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == 'claude -p "/taude ingest"'
        assert kwargs.get("shell") is True

    def test_nonzero_returncode_is_logged_not_raised(self):
        cfg = Config()
        cfg.post_run_cmd = "false"
        with patch("archivore.commands.run.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                cfg.post_run_cmd, 1, "", "boom"
            )
            _run_post_run_cmd(cfg)  # must not raise

    def test_timeout_is_swallowed_not_raised(self):
        cfg = Config()
        cfg.post_run_cmd = "sleep 1000"
        with patch(
            "archivore.commands.run.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cfg.post_run_cmd, 600),
        ):
            _run_post_run_cmd(cfg)  # must not raise

    def test_failure_is_swallowed_not_raised(self):
        cfg = Config()
        cfg.post_run_cmd = "false"
        with patch(
            "archivore.commands.run.subprocess.run",
            side_effect=OSError("boom"),
        ):
            _run_post_run_cmd(cfg)  # must not raise


class TestRunGatesPostRunCmd:
    """The post-run hook is spec'd to fire only after a scrape that produced
    new files, not on every run — a no-op run shouldn't shell out."""

    def _run_result(self, new_items):
        return RunResult(
            new_queued=0,
            done=0,
            skipped=0,
            failed=0,
            retryable=0,
            index_path=Path("/tmp/index.md"),
            article_count=0,
            new_items=new_items,
        )

    def test_called_when_run_produced_new_items(self, tmp_path):
        cfg = Config()
        cfg.log_path = tmp_path / "run.log"
        cfg.notify_macos = False
        result = self._run_result([{"source": "hn", "title": "t"}])
        with (
            patch("archivore.commands.run._pipeline", return_value=result),
            patch("archivore.commands.run._run_post_run_cmd") as mock_hook,
        ):
            run(cfg)
        mock_hook.assert_called_once_with(cfg)

    def test_not_called_when_run_produced_no_new_items(self, tmp_path):
        cfg = Config()
        cfg.log_path = tmp_path / "run.log"
        cfg.notify_macos = False
        result = self._run_result([])
        with (
            patch("archivore.commands.run._pipeline", return_value=result),
            patch("archivore.commands.run._run_post_run_cmd") as mock_hook,
        ):
            run(cfg)
        mock_hook.assert_not_called()
