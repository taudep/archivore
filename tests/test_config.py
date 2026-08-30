"""Tests for Config defaults."""

from pathlib import Path

from archivore.config import Config


def test_output_dir_default_points_at_obsidian_vault():
    cfg = Config()
    assert cfg.output_dir == (
        Path.home()
        / "Library/Mobile Documents/com~apple~CloudDocs"
        / "Todd's Obsidian Vault/Archivore/Raw"
    )


def test_queue_api_fields_default_to_none():
    cfg = Config()
    assert cfg.queue_api_url is None
    assert cfg.queue_api_token is None
