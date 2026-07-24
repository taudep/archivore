"""Shared data shapes passed between clients, repositories, and commands."""

from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict


class Tab(TypedDict):
    """One open browser tab."""

    url: str
    title: str
    window: int


class HistoryRow(TypedDict):
    """One URL from browser history."""

    url: str
    title: str
    visit_count: int
    last_visited_at: str


class DomainEntry(TypedDict):
    """History aggregated to one row per domain."""

    domain: str
    url: str
    title: str
    visit_count: int
    last_visited_at: str


@dataclass
class ResolvedItem:
    """Metadata for a queued item after phase-1 resolution.

    ``selftext`` is Markdown body content for self-posts (written directly in
    phase 1); ``None`` means the article URL should be fetched in phase 2.
    """

    title: str | None
    article_url: str
    is_selfpost: bool
    selftext: str | None = None


@dataclass
class RunResult:
    """Outcome of one `archivore run` pipeline pass, for callers that need
    to log or notify rather than just print to the console."""

    new_queued: int
    done: int
    skipped: int
    failed: int
    retryable: int
    index_path: Path
    article_count: int
    new_items: list[dict]
