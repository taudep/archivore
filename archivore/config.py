"""Configuration with an XDG-style hierarchy.

Values are merged in order: built-in defaults, then
``$XDG_CONFIG_HOME/archivore/config.yaml`` (default ``~/.config``), then
``./archivore.yaml`` in the working directory. Later files win.
"""

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

DEFAULT_IGNORE_DOMAINS = {
    "gmail.com",
    "mail.google.com",
    "amazon.com",
    "facebook.com",
}

# Subreddits worth ingesting. Case-insensitive; an empty set disables the
# filter entirely (every subreddit is ingested).
DEFAULT_REDDIT_SUBREDDITS = {
    "localllm",
    "LocalLLaMA",
    "DoomEmacs",
    "snowflake",
    "git",
    "dotfiles",
    "dotnet",
}

_PATH_FIELDS = {"db_path", "md_path", "output_dir", "log_path"}
_SET_FIELDS = {"ignore_domains", "reddit_subreddits"}
_SECRET_FIELDS = {"queue_api_token", "smtp_password"}

# Environment variable fallback for queue_api_token, used only when the
# config files don't set it — lets it come from a secret manager / CI
# environment instead of a file on disk.
_QUEUE_API_TOKEN_ENV_VAR = "ARCHIVORE_QUEUE_API_TOKEN"


@dataclass
class Config:
    """Runtime settings for all archivore commands."""

    db_path: Path = field(default_factory=lambda: Path.home() / "tabs.db")
    md_path: Path = field(default_factory=lambda: Path.home() / "tabs.md")
    history_days: int = 90
    ignore_domains: set[str] = field(
        default_factory=lambda: set(DEFAULT_IGNORE_DOMAINS)
    )
    output_dir: Path = field(
        default_factory=lambda: (
            Path.home()
            / "Library/Mobile Documents/com~apple~CloudDocs"
            / "Todd's Obsidian Vault/Archivore/Raw"
        )
    )
    reddit_subreddits: set[str] = field(
        default_factory=lambda: set(DEFAULT_REDDIT_SUBREDDITS)
    )
    digest_days: int = 7
    hn_delay: float = 0.5
    meta_delay: float = 1.5
    max_retries: int = 4
    concurrency: int = 5
    queue_api_url: str | None = None
    queue_api_token: str | None = None
    last_run: str | None = None
    log_path: Path = field(
        default_factory=lambda: Path.home() / "Library/Logs/archivore/run.log"
    )
    notify_macos: bool = True
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    email_to: str | None = None
    email_from: str | None = None


def xdg_config_path() -> Path:
    """Path to the user-level config file — where ``last_run`` is persisted,
    since it must survive regardless of the working directory."""
    xdg = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return xdg / "archivore" / "config.yaml"


def config_files() -> list[Path]:
    """Return candidate config paths, lowest precedence first."""
    return [xdg_config_path(), Path("archivore.yaml")]


def load_config() -> Config:
    """Load configuration, applying overrides from any config files found.

    ``queue_api_token`` additionally falls back to the
    ``ARCHIVORE_QUEUE_API_TOKEN`` environment variable if no config file sets
    it, so it can come from a secret manager / CI environment instead of a
    file on disk. A config file always wins over the environment variable.
    """
    cfg = Config()
    for path in config_files():
        if not path.is_file():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for f in fields(cfg):
            if f.name not in data:
                continue
            value = data[f.name]
            if f.name in _PATH_FIELDS:
                value = Path(value).expanduser()
            elif f.name in _SET_FIELDS:
                value = set(value)
            setattr(cfg, f.name, value)
    if cfg.queue_api_token is None:
        cfg.queue_api_token = os.environ.get(_QUEUE_API_TOKEN_ENV_VAR)
    return cfg


def config_summary(cfg: Config) -> dict[str, str]:
    """Return a loggable view of every config field, redacting secrets.

    Secret fields (``queue_api_token``, ``smtp_password``) never appear in
    full — only whether they're set, plus the last 4 characters as a sanity
    check that the intended value is active.
    """
    summary: dict[str, str] = {}
    for f in fields(cfg):
        value = getattr(cfg, f.name)
        if f.name in _SECRET_FIELDS:
            if value is None:
                summary[f.name] = "<not set>"
            elif len(value) >= 4:
                summary[f.name] = f"<set: ...{value[-4:]}>"
            else:
                summary[f.name] = "<set>"
        else:
            summary[f.name] = str(value)
    return summary


def save_last_run(timestamp: str) -> None:
    """Persist the watermark timestamp into the user-level config file,
    preserving any other keys already there."""
    path = xdg_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if path.is_file():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data["last_run"] = timestamp
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
