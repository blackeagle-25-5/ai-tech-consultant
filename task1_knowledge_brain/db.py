"""SQLite storage for the Knowledge Brain.

One file holds the crawl manifest, articles (with version history), image
descriptions, searchable chunks (vector + full-text), escalations and run reports.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    url          TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,            -- article | folder | other
    article_id   TEXT,
    lastmod      TEXT,
    status       TEXT NOT NULL DEFAULT 'pending', -- pending | done | failed | removed | skipped
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    content_hash TEXT,
    fetched_at   TEXT,
    in_sitemap   INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS articles (
    article_id   TEXT PRIMARY KEY,
    url          TEXT NOT NULL,
    title        TEXT NOT NULL,
    category     TEXT,
    folder       TEXT,
    body_md      TEXT NOT NULL,
    sections_json TEXT NOT NULL DEFAULT '[]',
    lastmod      TEXT,
    content_hash TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    active       INTEGER NOT NULL DEFAULT 1,
    ingested_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS article_versions (
    article_id   TEXT NOT NULL,
    version      INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    body_md      TEXT NOT NULL,
    sections_json TEXT NOT NULL DEFAULT '[]',
    ingested_at  TEXT NOT NULL,
    PRIMARY KEY (article_id, version)
);
CREATE TABLE IF NOT EXISTS images (
    image_url    TEXT NOT NULL,
    article_id   TEXT NOT NULL,
    position     INTEGER NOT NULL,
    alt          TEXT,
    context      TEXT,                     -- text right before the image
    section      TEXT,
    description  TEXT,                     -- JSON from the vision model
    status       TEXT NOT NULL DEFAULT 'pending', -- pending | done | failed | skipped
    model        TEXT,
    PRIMARY KEY (article_id, image_url)
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    article_id   TEXT NOT NULL,
    source_type  TEXT NOT NULL,            -- article | image | expert
    title        TEXT NOT NULL,
    url          TEXT NOT NULL,
    section      TEXT,
    text         TEXT NOT NULL,
    image_urls   TEXT NOT NULL DEFAULT '[]',
    active       INTEGER NOT NULL DEFAULT 1,
    embedding    BLOB
);
CREATE INDEX IF NOT EXISTS idx_chunks_article ON chunks(article_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED, title, section, text, tokenize = 'porter unicode61'
);
CREATE TABLE IF NOT EXISTS escalations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    question     TEXT NOT NULL,
    reason       TEXT NOT NULL,
    evidence     TEXT NOT NULL DEFAULT '[]',
    requester    TEXT,
    status       TEXT NOT NULL DEFAULT 'open', -- open | resolved
    created_at   TEXT NOT NULL,
    resolved_at  TEXT
);
CREATE TABLE IF NOT EXISTS expert_answers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    escalation_id INTEGER NOT NULL,
    question      TEXT NOT NULL,
    answer        TEXT NOT NULL,
    expert        TEXT NOT NULL,
    scope         TEXT NOT NULL,           -- general | customer_specific
    source_url    TEXT,
    version       INTEGER NOT NULL DEFAULT 1,
    expires_at    TEXT,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    mode         TEXT NOT NULL,
    status       TEXT NOT NULL,            -- running | finished | failed
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    added        INTEGER NOT NULL DEFAULT 0,
    updated      INTEGER NOT NULL DEFAULT 0,
    removed      INTEGER NOT NULL DEFAULT 0,
    unchanged    INTEGER NOT NULL DEFAULT 0,
    failed       INTEGER NOT NULL DEFAULT 0,
    images_described INTEGER NOT NULL DEFAULT 0,
    message      TEXT
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn
