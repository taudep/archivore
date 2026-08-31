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

    ``author``/``published`` are only populated here for self-posts, where
    the platform itself is the source of truth (the submitter, the post
    time). For link posts they're left ``None`` and phase 2 fills them in
    from the fetched article's own metadata instead — the HN/Reddit
    submitter is not the article's author.
    """

    title: str | None
    article_url: str
    is_selfpost: bool
    selftext: str | None = None
    author: str | None = None
    published: str | None = None


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


class ClaimItem(TypedDict):
    """One item sent to the coordination API's /claim endpoint."""

    item_id: str
    source: str
    comments_url: str
    article_url: str | None


class ClaimResult(TypedDict):
    """One result from the coordination API's /claim endpoint."""

    item_id: str
    claimed: bool
    status: str
    retries: int


class CompleteItem(TypedDict):
    """One outcome sent to the coordination API's /complete endpoint."""

    item_id: str
    status: str
    title: str | None
    is_selfpost: bool | None
    filename: str | None
    last_error: str | None
