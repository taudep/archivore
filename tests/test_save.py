"""Tests for `archivore save`: URL normalization, claim/fetch/complete flow,
JSON output, and exit codes. queue_api and the fetcher are mocked."""

import hashlib
import json
from unittest.mock import patch

import pytest
import requests
from click.testing import CliRunner

from archivore.cli import cli
from archivore.commands.save import manual_item_id, normalize_url, save
from archivore.config import Config
from archivore.models import CompleteItem
from archivore.render import write_article_file

URL = "https://Example.com/post/1?utm_source=hn&id=7#comments"
NORMALIZED = "https://example.com/post/1?id=7"


# --- URL normalization -----------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("HTTPS://EXAMPLE.COM/Path", "https://example.com/Path"),
        ("https://example.com/a#frag", "https://example.com/a"),
        ("https://example.com/a/", "https://example.com/a"),
        ("https://example.com/", "https://example.com/"),
        ("https://example.com", "https://example.com/"),
        (
            "https://example.com/a?utm_source=x&utm_medium=y&q=1",
            "https://example.com/a?q=1",
        ),
        ("https://example.com/a?fbclid=abc&gclid=def", "https://example.com/a"),
        ("https://example.com/a?b=2&a=1", "https://example.com/a?b=2&a=1"),
        (
            "https://example.com/a?q=hello%20world",
            "https://example.com/a?q=hello%20world",
        ),
        ("  https://example.com/a  ", "https://example.com/a"),
        ("https://Example.com:8080/a/", "https://example.com:8080/a"),
        (URL, NORMALIZED),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


def test_manual_item_id_is_prefixed_sha1_of_normalized_url():
    expected = "m_" + hashlib.sha1(NORMALIZED.encode()).hexdigest()[:16]
    assert manual_item_id(URL) == expected
    assert len(manual_item_id(URL)) == 18


def test_equivalent_urls_share_an_item_id():
    assert manual_item_id("https://example.com/a/#x") == manual_item_id(
        "HTTPS://example.com/a?utm_campaign=z"
    )


# --- save() flow -----------------------------------------------------------


def _cfg(tmp_path) -> Config:
    cfg = Config()
    cfg.output_dir = tmp_path
    cfg.queue_api_url = "https://queue.invalid"
    cfg.queue_api_token = "secret-token-value"
    return cfg


def _claimed(item_id, claimed=True, status="pending", retries=0):
    return [
        {"item_id": item_id, "claimed": claimed, "status": status, "retries": retries}
    ]


async def _fake_fetch_writes_file(session, item, state, output_dir, max_retries):
    """Stand-in for fetcher.fetch_article that writes a real article file
    through the normal render path, so frontmatter can be asserted on."""
    filename = write_article_file(
        output_dir,
        item["item_id"],
        item["title"] or "Fetched Title",
        item["article_url"],
        item["comments_url"],
        "",
        "body text",
        source=item["source"],
        visited_at=item["visited_at"],
        note=item.get("note"),
    )
    return CompleteItem(
        item_id=item["item_id"],
        status="done",
        title=item["title"] or "Fetched Title",
        is_selfpost=False,
        filename=filename,
        last_error=None,
        article_url=item["article_url"],
    )


async def _fake_fetch_fails(session, item, state, output_dir, max_retries):
    return CompleteItem(
        item_id=item["item_id"],
        status="failed",
        title=item["title"],
        is_selfpost=False,
        filename=None,
        last_error="Cannot connect to host",
        article_url=item["article_url"],
    )


class TestSave:
    def test_new_claim_is_saved_with_note_in_frontmatter(self, tmp_path):
        cfg = _cfg(tmp_path)
        item_id = manual_item_id(URL)
        with (
            patch(
                "archivore.commands.save.queue_api.claim",
                return_value=_claimed(item_id),
            ) as mock_claim,
            patch("archivore.commands.save.queue_api.complete") as mock_complete,
            patch(
                "archivore.commands.save.fetcher.fetch_article",
                side_effect=_fake_fetch_writes_file,
            ),
        ):
            result = save(cfg, URL, note='read "later"')

        assert result.status == "saved"
        assert result.item_id == item_id
        assert result.error is None

        claim_items = mock_claim.call_args.args[1]
        assert claim_items == [
            {
                "item_id": item_id,
                "source": "manual",
                "comments_url": URL,
                "article_url": URL,
            }
        ]
        mock_complete.assert_called_once()
        completions = mock_complete.call_args.args[1]
        assert len(completions) == 1
        assert completions[0]["status"] == "done"
        assert completions[0]["filename"] == result.filename

        content = (tmp_path / result.filename).read_text()
        assert "via: manual" in content
        assert f'source: "{URL}"' in content
        assert 'note: "read \\"later\\""' in content
        assert "visited_at: " in content
        assert "body text" in content

    def test_title_option_overrides_extracted_title(self, tmp_path):
        cfg = _cfg(tmp_path)
        item_id = manual_item_id(URL)
        with (
            patch(
                "archivore.commands.save.queue_api.claim",
                return_value=_claimed(item_id),
            ),
            patch("archivore.commands.save.queue_api.complete"),
            patch(
                "archivore.commands.save.fetcher.fetch_article",
                side_effect=_fake_fetch_writes_file,
            ),
        ):
            result = save(cfg, URL, title="My Own Title")

        content = (tmp_path / result.filename).read_text()
        assert 'title: "My Own Title"' in content
        assert "note:" not in content

    @pytest.mark.parametrize("status", ["done", "skipped", "pending"])
    def test_already_handled_item_is_duplicate_without_fetch(self, tmp_path, status):
        cfg = _cfg(tmp_path)
        item_id = manual_item_id(URL)
        with (
            patch(
                "archivore.commands.save.queue_api.claim",
                return_value=_claimed(item_id, claimed=False, status=status),
            ),
            patch("archivore.commands.save.queue_api.complete") as mock_complete,
            patch("archivore.commands.save.fetcher.fetch_article") as mock_fetch,
        ):
            result = save(cfg, URL)

        assert result.status == "duplicate"
        assert result.filename is None
        mock_fetch.assert_not_called()
        mock_complete.assert_not_called()

    def test_failed_under_max_retries_is_refetched(self, tmp_path):
        cfg = _cfg(tmp_path)
        item_id = manual_item_id(URL)
        with (
            patch(
                "archivore.commands.save.queue_api.claim",
                return_value=_claimed(item_id, False, "failed", 1),
            ),
            patch("archivore.commands.save.queue_api.complete"),
            patch(
                "archivore.commands.save.fetcher.fetch_article",
                side_effect=_fake_fetch_writes_file,
            ),
        ):
            result = save(cfg, URL)
        assert result.status == "saved"

    def test_failed_at_max_retries_is_reported_failed(self, tmp_path):
        cfg = _cfg(tmp_path)
        item_id = manual_item_id(URL)
        with (
            patch(
                "archivore.commands.save.queue_api.claim",
                return_value=_claimed(item_id, False, "failed", cfg.max_retries),
            ),
            patch("archivore.commands.save.queue_api.complete") as mock_complete,
            patch("archivore.commands.save.fetcher.fetch_article") as mock_fetch,
        ):
            result = save(cfg, URL)
        assert result.status == "failed"
        assert "retry limit" in result.error
        mock_fetch.assert_not_called()
        mock_complete.assert_not_called()

    def test_claim_network_error_is_failed_not_raised(self, tmp_path):
        cfg = _cfg(tmp_path)
        with (
            patch(
                "archivore.commands.save.queue_api.claim",
                side_effect=requests.ConnectionError("no route to host"),
            ),
            patch("archivore.commands.save.fetcher.fetch_article") as mock_fetch,
        ):
            result = save(cfg, URL)
        assert result.status == "failed"
        assert "no route to host" in result.error
        assert cfg.queue_api_token not in result.error
        mock_fetch.assert_not_called()

    def test_fetch_failure_reports_failed_via_complete(self, tmp_path):
        cfg = _cfg(tmp_path)
        item_id = manual_item_id(URL)
        with (
            patch(
                "archivore.commands.save.queue_api.claim",
                return_value=_claimed(item_id),
            ),
            patch("archivore.commands.save.queue_api.complete") as mock_complete,
            patch(
                "archivore.commands.save.fetcher.fetch_article",
                side_effect=_fake_fetch_fails,
            ),
        ):
            result = save(cfg, URL)

        assert result.status == "failed"
        assert result.error == "Cannot connect to host"
        mock_complete.assert_called_once()
        assert mock_complete.call_args.args[1][0]["status"] == "failed"

    def test_unexpected_fetch_exception_still_reports_failed(self, tmp_path):
        cfg = _cfg(tmp_path)
        item_id = manual_item_id(URL)
        with (
            patch(
                "archivore.commands.save.queue_api.claim",
                return_value=_claimed(item_id),
            ),
            patch("archivore.commands.save.queue_api.complete") as mock_complete,
            patch(
                "archivore.commands.save.fetcher.fetch_article",
                side_effect=RuntimeError("kaboom"),
            ),
        ):
            result = save(cfg, URL)
        assert result.status == "failed"
        assert "kaboom" in result.error
        completions = mock_complete.call_args.args[1]
        assert completions[0]["status"] == "failed"

    def test_complete_network_error_is_failed(self, tmp_path):
        cfg = _cfg(tmp_path)
        item_id = manual_item_id(URL)
        with (
            patch(
                "archivore.commands.save.queue_api.claim",
                return_value=_claimed(item_id),
            ),
            patch(
                "archivore.commands.save.queue_api.complete",
                side_effect=requests.Timeout("timed out"),
            ),
            patch(
                "archivore.commands.save.fetcher.fetch_article",
                side_effect=_fake_fetch_writes_file,
            ),
        ):
            result = save(cfg, URL)
        assert result.status == "failed"
        assert "timed out" in result.error
        # The article was still written; a retry re-saves over the same file.
        assert result.filename is not None
        assert (tmp_path / result.filename).is_file()

    def test_save_does_not_touch_index_or_watermark(self, tmp_path):
        cfg = _cfg(tmp_path)
        item_id = manual_item_id(URL)
        with (
            patch(
                "archivore.commands.save.queue_api.claim",
                return_value=_claimed(item_id),
            ),
            patch("archivore.commands.save.queue_api.complete"),
            patch(
                "archivore.commands.save.fetcher.fetch_article",
                side_effect=_fake_fetch_writes_file,
            ),
            patch("archivore.config.save_last_run") as mock_watermark,
        ):
            save(cfg, URL)
        mock_watermark.assert_not_called()
        assert not (tmp_path / "index.md").exists()


# --- CLI: output and exit codes -------------------------------------------


def _invoke(tmp_path, args):
    cfg = _cfg(tmp_path)
    with patch("archivore.cli.load_config", return_value=cfg):
        return CliRunner().invoke(cli, ["save", *args])


class TestSaveCli:
    def test_json_saved_emits_exactly_one_line_and_exit_0(self, tmp_path):
        item_id = manual_item_id(URL)
        with (
            patch(
                "archivore.commands.save.queue_api.claim",
                return_value=_claimed(item_id),
            ),
            patch("archivore.commands.save.queue_api.complete"),
            patch(
                "archivore.commands.save.fetcher.fetch_article",
                side_effect=_fake_fetch_writes_file,
            ),
        ):
            res = _invoke(tmp_path, [URL, "--note", "hi", "--json"])

        assert res.exit_code == 0, res.output
        lines = res.stdout.splitlines()
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["status"] == "saved"
        assert payload["item_id"] == item_id
        assert payload["filename"].endswith(".md")
        assert payload["error"] is None
        assert set(payload) == {"status", "item_id", "filename", "error"}

    def test_json_duplicate_exit_0(self, tmp_path):
        item_id = manual_item_id(URL)
        with (
            patch(
                "archivore.commands.save.queue_api.claim",
                return_value=_claimed(item_id, claimed=False, status="done"),
            ),
            patch("archivore.commands.save.fetcher.fetch_article") as mock_fetch,
        ):
            res = _invoke(tmp_path, [URL, "--json"])
        assert res.exit_code == 0
        payload = json.loads(res.stdout)
        assert payload == {
            "status": "duplicate",
            "item_id": item_id,
            "filename": None,
            "error": None,
        }
        mock_fetch.assert_not_called()

    def test_json_claim_network_error_exit_1(self, tmp_path):
        with patch(
            "archivore.commands.save.queue_api.claim",
            side_effect=requests.ConnectionError("connection refused"),
        ):
            res = _invoke(tmp_path, [URL, "--json"])
        assert res.exit_code == 1
        lines = res.stdout.splitlines()
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["status"] == "failed"
        assert payload["filename"] is None
        assert "connection refused" in payload["error"]
        assert "secret-token-value" not in res.output

    def test_human_output_on_fetch_failure_exit_1(self, tmp_path):
        item_id = manual_item_id(URL)
        with (
            patch(
                "archivore.commands.save.queue_api.claim",
                return_value=_claimed(item_id),
            ),
            patch("archivore.commands.save.queue_api.complete") as mock_complete,
            patch(
                "archivore.commands.save.fetcher.fetch_article",
                side_effect=_fake_fetch_fails,
            ),
        ):
            res = _invoke(tmp_path, [URL])
        assert res.exit_code == 1
        assert "failed" in res.stdout.lower()
        mock_complete.assert_called_once()

    def test_human_output_saved(self, tmp_path):
        item_id = manual_item_id(URL)
        with (
            patch(
                "archivore.commands.save.queue_api.claim",
                return_value=_claimed(item_id),
            ),
            patch("archivore.commands.save.queue_api.complete"),
            patch(
                "archivore.commands.save.fetcher.fetch_article",
                side_effect=_fake_fetch_writes_file,
            ),
        ):
            res = _invoke(tmp_path, [URL])
        assert res.exit_code == 0
        assert "saved" in res.stdout.lower()

    def test_missing_url_is_usage_error(self, tmp_path):
        res = _invoke(tmp_path, [])
        assert res.exit_code == 2

    def test_non_http_url_is_usage_error(self, tmp_path):
        with patch("archivore.commands.save.queue_api.claim") as mock_claim:
            res = _invoke(tmp_path, ["ftp://example.com/file", "--json"])
        assert res.exit_code == 2
        mock_claim.assert_not_called()
