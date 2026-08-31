"""Tests for Config defaults."""

from pathlib import Path
from unittest.mock import patch

from archivore.config import Config, config_summary, load_config


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


class TestQueueApiTokenEnvFallback:
    def test_falls_back_to_env_var_when_no_config_file_sets_it(self, monkeypatch):
        monkeypatch.setenv("ARCHIVORE_QUEUE_API_TOKEN", "env-token")
        with patch("archivore.config.config_files", return_value=[]):
            cfg = load_config()
        assert cfg.queue_api_token == "env-token"

    def test_config_file_value_wins_over_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ARCHIVORE_QUEUE_API_TOKEN", "env-token")
        config_yaml = tmp_path / "config.yaml"
        config_yaml.write_text("queue_api_token: file-token\n")
        with patch("archivore.config.config_files", return_value=[config_yaml]):
            cfg = load_config()
        assert cfg.queue_api_token == "file-token"

    def test_none_when_neither_env_var_nor_config_set(self, monkeypatch):
        monkeypatch.delenv("ARCHIVORE_QUEUE_API_TOKEN", raising=False)
        with patch("archivore.config.config_files", return_value=[]):
            cfg = load_config()
        assert cfg.queue_api_token is None


class TestConfigSummary:
    def test_redacts_unset_secrets_as_not_set(self):
        cfg = Config()
        summary = config_summary(cfg)
        assert summary["queue_api_token"] == "<not set>"
        assert summary["smtp_password"] == "<not set>"

    def test_redacts_set_secret_to_last_four_chars_only(self):
        cfg = Config()
        cfg.queue_api_token = "abcdefgh12345678"
        summary = config_summary(cfg)
        assert summary["queue_api_token"] == "<set: ...5678>"
        assert "abcdefgh" not in summary["queue_api_token"]

    def test_non_secret_fields_shown_in_full(self):
        cfg = Config()
        summary = config_summary(cfg)
        assert summary["digest_days"] == "7"
        assert summary["concurrency"] == "5"
