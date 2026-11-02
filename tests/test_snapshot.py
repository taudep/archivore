"""Tests for snapshot's Markdown output path handling."""

from datetime import date
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from archivore.cli import cli
from archivore.commands.snapshot import dated_markdown_path
from archivore.config import Config


def test_dated_markdown_path_prefixes_filename_with_date_and_host():
    path = Path("/vault/Snapshots/open-tabs.md")
    assert dated_markdown_path(path, date(2026, 11, 2), "laptop") == Path(
        "/vault/Snapshots/2026-11-02-laptop-open-tabs.md"
    )


def test_dated_markdown_path_drops_domain_from_hostname():
    path = Path("/vault/Snapshots/open-tabs.md")
    with patch(
        "archivore.commands.snapshot.socket.gethostname",
        return_value="Todds-MacBook-Neo.local",
    ):
        got = dated_markdown_path(path, date(2026, 11, 2))
    assert got.name == "2026-11-02-Todds-MacBook-Neo-open-tabs.md"


def _invoke_snapshot(args, md_path):
    cfg = Config(md_path=md_path)
    with (
        patch("archivore.cli.load_config", return_value=cfg),
        patch("archivore.commands.snapshot.run") as run,
    ):
        result = CliRunner().invoke(cli, ["snapshot", "--no-db", *args])
    assert result.exit_code == 0, result.output
    return run.call_args.kwargs["markdown_path"]


def test_default_markdown_path_is_date_stamped(tmp_path):
    with (
        patch("archivore.commands.snapshot.date") as fake_date,
        patch("archivore.commands.snapshot.socket.gethostname", return_value="laptop"),
    ):
        fake_date.today.return_value = date(2026, 11, 2)
        got = _invoke_snapshot(["--markdown"], tmp_path / "open-tabs.md")
    assert got == tmp_path / "2026-11-02-laptop-open-tabs.md"


def test_explicit_markdown_path_is_used_as_given(tmp_path):
    explicit = tmp_path / "mine.md"
    got = _invoke_snapshot(["--markdown", str(explicit)], tmp_path / "open-tabs.md")
    assert got == explicit
