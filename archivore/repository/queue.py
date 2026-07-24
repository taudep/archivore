"""The resumable download queue backing the reading digest."""

import sqlite3
from pathlib import Path


def open_queue(db_path: Path) -> sqlite3.Connection:
    """Open (and migrate) the queue database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE IF NOT EXISTS queue (
            item_id      TEXT PRIMARY KEY,
            source       TEXT NOT NULL DEFAULT 'hn',
            title        TEXT,
            article_url  TEXT,
            comments_url TEXT NOT NULL,
            is_selfpost  INTEGER NOT NULL DEFAULT 0,
            status       TEXT NOT NULL DEFAULT 'pending',
            retries      INTEGER NOT NULL DEFAULT 0,
            last_error   TEXT,
            filename     TEXT,
            queued_at    TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        )
    """)
    # Add source column to old DBs that lack it (existing rows are HN)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(queue)").fetchall()}
    if "source" not in cols:
        conn.execute("ALTER TABLE queue ADD COLUMN source TEXT NOT NULL DEFAULT 'hn'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_source ON queue(source)")
    conn.commit()
    return conn


def insert(
    conn: sqlite3.Connection,
    item_id: str,
    source: str,
    comments_url: str,
    now: str,
    article_url: str | None = None,
) -> int:
    """Queue an item if not already present; return 1 if newly inserted."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO queue "
        "(item_id, source, comments_url, article_url, status, "
        " queued_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
        (item_id, source, comments_url, article_url, now, now),
    )
    return cur.rowcount


def mark(
    conn: sqlite3.Connection, item_id: str, status: str, now: str, **fields
) -> None:
    """Set an item's status (bumping retries) and any extra columns."""
    sets = ["status = ?", "updated_at = ?", "retries = retries + 1"]
    vals: list = [status, now]
    for col, val in fields.items():
        sets.append(f"{col} = ?")
        vals.append(str(val)[:500] if col == "last_error" else val)
    vals.append(item_id)
    conn.execute(f"UPDATE queue SET {', '.join(sets)} WHERE item_id = ?", vals)
    conn.commit()


def set_metadata(
    conn: sqlite3.Connection,
    item_id: str,
    title: str,
    article_url: str,
    is_selfpost: bool,
    now: str,
) -> None:
    """Store phase-1 metadata without changing status or retries."""
    conn.execute(
        "UPDATE queue SET title=?, article_url=?, is_selfpost=?, updated_at=? "
        "WHERE item_id=?",
        (title, article_url, int(is_selfpost), now, item_id),
    )
    conn.commit()


def unresolved(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Items still needing phase-1 metadata resolution."""
    return conn.execute(
        "SELECT item_id, source, comments_url, article_url "
        "FROM queue WHERE title IS NULL AND status NOT IN ('skipped')"
    ).fetchall()


def downloadable(conn: sqlite3.Connection, max_retries: int) -> list[sqlite3.Row]:
    """Link posts ready for a phase-2 download attempt."""
    return conn.execute(
        "SELECT * FROM queue "
        "WHERE title IS NOT NULL AND is_selfpost = 0 "
        "AND status NOT IN ('skipped') "
        "AND (status = 'pending' OR (status = 'failed' AND retries < ?)) "
        "ORDER BY source, item_id DESC",
        (max_retries,),
    ).fetchall()


def index_rows(conn: sqlite3.Connection) -> list[dict]:
    """Completed items for the index, as plain dicts."""
    rows = conn.execute(
        "SELECT source, title, article_url, comments_url, filename, is_selfpost "
        "FROM queue "
        "WHERE status IN ('done', 'skipped') AND filename IS NOT NULL "
        "ORDER BY source, item_id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def done_this_run(conn: sqlite3.Connection, now: str) -> list[sqlite3.Row]:
    """Items marked done in this run's pass, matched by the shared `now`
    timestamp every write in a run shares."""
    return conn.execute(
        "SELECT source, title, article_url FROM queue "
        "WHERE status = 'done' AND updated_at = ? ORDER BY source",
        (now,),
    ).fetchall()


def status_counts(
    conn: sqlite3.Connection, max_retries: int
) -> tuple[int, int, int, int]:
    """Return (done, skipped, failed, retryable) counts."""

    def count(sql: str, *params) -> int:
        return conn.execute(sql, params).fetchone()[0]

    return (
        count("SELECT COUNT(*) FROM queue WHERE status='done'"),
        count("SELECT COUNT(*) FROM queue WHERE status='skipped'"),
        count("SELECT COUNT(*) FROM queue WHERE status='failed'"),
        count(
            "SELECT COUNT(*) FROM queue WHERE status='failed' AND retries < ?",
            max_retries,
        ),
    )
