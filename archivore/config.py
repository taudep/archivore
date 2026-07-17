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

_PATH_FIELDS = {"db_path", "md_path", "output_dir"}


@dataclass
class Config:
    """Runtime settings for all archivore commands."""

    db_path: Path = field(default_factory=lambda: Path.home() / "tabs.db")
    md_path: Path = field(default_factory=lambda: Path.home() / "tabs.md")
    history_days: int = 90
    ignore_domains: set[str] = field(
        default_factory=lambda: set(DEFAULT_IGNORE_DOMAINS)
    )
    output_dir: Path = field(default_factory=lambda: Path("hn_this_week"))
    digest_days: int = 7
    hn_delay: float = 0.5
    meta_delay: float = 1.5
    max_retries: int = 4
    concurrency: int = 5


def config_files() -> list[Path]:
    """Return candidate config paths, lowest precedence first."""
    xdg = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return [xdg / "archivore" / "config.yaml", Path("archivore.yaml")]


def load_config() -> Config:
    """Load configuration, applying overrides from any config files found."""
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
            elif f.name == "ignore_domains":
                value = set(value)
            setattr(cfg, f.name, value)
    return cfg
