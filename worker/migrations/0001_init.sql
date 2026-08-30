CREATE TABLE queue (
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
);
CREATE INDEX idx_queue_status ON queue(status);
CREATE INDEX idx_queue_source ON queue(source);
