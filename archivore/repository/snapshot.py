"""Persistence for browser snapshots: open tabs and domain-deduped history."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from archivore.models import DomainEntry, Tab


def _setup(conn: sqlite3.Connection) -> None:
    # Migrate tabs: old schema had snapshot_id, no UNIQUE constraint
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='tabs'"
    ).fetchone()
    if row:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tabs)").fetchall()}
        if "snapshot_id" in cols:
            conn.execute("DROP TABLE tabs")

    # Migrate history: old schema was snapshot-scoped per raw URL
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='history'"
    ).fetchone()
    if row:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(history)").fetchall()}
        if "snapshot_id" in cols or "domain" not in cols:
            conn.execute("DROP TABLE history")

    conn.execute("DROP TABLE IF EXISTS snapshots")
    conn.commit()

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tabs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            browser       TEXT NOT NULL,
            window        INTEGER NOT NULL DEFAULT 1,
            url           TEXT NOT NULL,
            title         TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at  TEXT NOT NULL,
            UNIQUE(browser, url)
        );

        CREATE TABLE IF NOT EXISTS history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            domain          TEXT NOT NULL,
            url             TEXT NOT NULL,
            title           TEXT,
            visit_count     INTEGER NOT NULL DEFAULT 1,
            last_visited_at TEXT NOT NULL,
            first_seen_at   TEXT NOT NULL,
            UNIQUE(domain)
        );

        CREATE INDEX IF NOT EXISTS idx_tabs_browser   ON tabs(browser);
        CREATE INDEX IF NOT EXISTS idx_tabs_last_seen ON tabs(last_seen_at);
        CREATE INDEX IF NOT EXISTS idx_history_visits ON history(visit_count);
    """)


def save_snapshot(
    db_path: Path,
    chrome_tabs: list[Tab],
    firefox_tabs: list[Tab],
    deduped_history: list[DomainEntry],
) -> tuple[int, int, int, int]:
    """Upsert tabs and history; return (new tabs, updated tabs,
    new history domains, updated history domains)."""
    conn = sqlite3.connect(db_path)
    _setup(conn)

    now = datetime.now(timezone.utc).isoformat()

    tab_rows = [
        ("chrome", t["window"], t["url"], t["title"], now, now) for t in chrome_tabs
    ] + [("firefox", t["window"], t["url"], t["title"], now, now) for t in firefox_tabs]
    n_tabs_before = conn.execute("SELECT COUNT(*) FROM tabs").fetchone()[0]
    conn.executemany(
        """
        INSERT INTO tabs (browser, window, url, title, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(browser, url) DO UPDATE SET
            window       = excluded.window,
            title        = excluded.title,
            last_seen_at = excluded.last_seen_at
        """,
        tab_rows,
    )
    n_tabs_after = conn.execute("SELECT COUNT(*) FROM tabs").fetchone()[0]
    n_new_tabs = n_tabs_after - n_tabs_before

    history_rows = [
        (
            h["domain"],
            h["url"],
            h["title"],
            h["visit_count"],
            h["last_visited_at"],
            now,
        )
        for h in deduped_history
    ]
    n_hist_before = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    conn.executemany(
        """
        INSERT INTO history
            (domain, url, title, visit_count, last_visited_at, first_seen_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(domain) DO UPDATE SET
            url             = excluded.url,
            title           = excluded.title,
            visit_count     = excluded.visit_count,
            last_visited_at = excluded.last_visited_at
        """,
        history_rows,
    )
    n_hist_after = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    n_new_hist = n_hist_after - n_hist_before

    conn.commit()
    conn.close()
    return (
        n_new_tabs,
        len(tab_rows) - n_new_tabs,
        n_new_hist,
        len(history_rows) - n_new_hist,
    )
