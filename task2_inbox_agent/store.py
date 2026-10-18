"""SQLite storage for emails, every draft/decision, the (simulated) outbox and the autonomy audit log."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    id          TEXT PRIMARY KEY,
    from_name   TEXT NOT NULL,
    from_email  TEXT NOT NULL,
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,
    thread_key  TEXT NOT NULL,
    received_at TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'new'   -- new | awaiting_review | sent | auto_sent | escalated | error
);
CREATE TABLE IF NOT EXISTS interactions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id           TEXT NOT NULL REFERENCES emails(id),
    created_at         TEXT NOT NULL,
    kind               TEXT,
    classification     TEXT,                 -- JSON
    kb_status          TEXT,                 -- answered | insufficient_evidence | unavailable | not_needed
    kb_response        TEXT,                 -- JSON
    draft_text         TEXT,
    draft_meta         TEXT,                 -- JSON (requires_human, next_action, follow-up)
    violations         TEXT NOT NULL DEFAULT '[]',
    decision           TEXT NOT NULL,        -- queued | auto_sent | held_for_audit | kb_unavailable | error
    decision_reason    TEXT,
    final_text         TEXT,
    outcome            TEXT NOT NULL DEFAULT 'pending', -- pending | sent_unchanged | edited | rejected | escalated | auto_sent
    reviewer           TEXT,
    correction_reason  TEXT,
    reviewed_at        TEXT,
    similarity         REAL,                 -- difflib ratio draft vs final (1.0 = identical)
    models             TEXT,                 -- JSON {classifier, drafter}
    prompt_versions    TEXT,                 -- JSON {classifier, drafter}
    cost_usd           REAL NOT NULL DEFAULT 0,
    latency_ms         INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS outbox (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    email_id        TEXT NOT NULL,
    interaction_id  INTEGER NOT NULL,
    to_addr         TEXT NOT NULL,
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    mode            TEXT NOT NULL,           -- human | autonomous
    sent_at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS autonomy (
    kind          TEXT PRIMARY KEY,
    streak        INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'learning',  -- learning | autonomous | eligible_blocked
    auto_sent     INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    actor          TEXT NOT NULL,
    action         TEXT NOT NULL,
    kind           TEXT,
    interaction_id INTEGER,
    details        TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InboxStore:
    def __init__(self, db_path: Path) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    # ---------- settings ----------
    def get_setting(self, key: str, default: str) -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) "
                              "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    # ---------- audit ----------
    def audit(self, actor: str, action: str, kind: str | None = None, interaction_id: int | None = None,
              details: str = "") -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO audit_log(ts, actor, action, kind, interaction_id, details) VALUES (?, ?, ?, ?, ?, ?)",
                (now(), actor, action, kind, interaction_id, details))

    # ---------- emails ----------
    def add_email(self, email) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT OR IGNORE INTO emails(id, from_name, from_email, subject, body, thread_key, received_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (email.id, email.from_name, email.from_email, email.subject, email.body, email.thread_key,
                 email.received_at or now()))

    def set_email_status(self, email_id: str, status: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE emails SET status = ? WHERE id = ?", (status, email_id))

    def thread_history(self, thread_key: str, exclude_email_id: str) -> list[dict]:
        """Earlier customer emails in this thread and what we sent back."""
        rows = self.conn.execute(
            """SELECT e.id, e.subject, e.body, e.received_at,
                      (SELECT body FROM outbox o WHERE o.email_id = e.id ORDER BY o.id DESC LIMIT 1) AS our_reply,
                      (SELECT mode FROM outbox o WHERE o.email_id = e.id ORDER BY o.id DESC LIMIT 1) AS reply_mode
               FROM emails e WHERE e.thread_key = ? AND e.id != ? ORDER BY e.received_at""",
            (thread_key, exclude_email_id)).fetchall()
        return [dict(r) for r in rows]

    # ---------- interactions ----------
    def add_interaction(self, **fields) -> int:
        for key in ("classification", "kb_response", "draft_meta", "violations", "models", "prompt_versions"):
            if key in fields and not isinstance(fields[key], str) and fields[key] is not None:
                fields[key] = json.dumps(fields[key])
        fields.setdefault("created_at", now())
        cols = ", ".join(fields)
        marks = ", ".join("?" * len(fields))
        with self.conn:
            cur = self.conn.execute(f"INSERT INTO interactions({cols}) VALUES ({marks})", tuple(fields.values()))
        return int(cur.lastrowid)

    def update_interaction(self, interaction_id: int, **fields) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.conn:
            self.conn.execute(f"UPDATE interactions SET {sets} WHERE id = ?", (*fields.values(), interaction_id))

    def interaction(self, interaction_id: int) -> sqlite3.Row:
        row = self.conn.execute(
            """SELECT i.*, e.from_name, e.from_email, e.subject, e.body, e.thread_key
               FROM interactions i JOIN emails e ON e.id = i.email_id WHERE i.id = ?""",
            (interaction_id,)).fetchone()
        if row is None:
            raise KeyError(interaction_id)
        return row

    def approved_examples(self, kind: str, limit: int = 2) -> list[str]:
        """Most recent replies a human sent (unchanged or edited) for this kind: style examples."""
        rows = self.conn.execute(
            """SELECT final_text FROM interactions
               WHERE kind = ? AND outcome IN ('sent_unchanged', 'edited') AND final_text IS NOT NULL
               ORDER BY reviewed_at DESC LIMIT ?""", (kind, limit)).fetchall()
        return [r[0] for r in rows]

    # ---------- outbox (simulated sending) ----------
    def send(self, *, idempotency_key: str, email_id: str, interaction_id: int, to_addr: str, subject: str,
             body: str, mode: str) -> bool:
        """Record a send. Returns False if this key was already sent (protects against double sends)."""
        try:
            with self.conn:
                self.conn.execute(
                    """INSERT INTO outbox(idempotency_key, email_id, interaction_id, to_addr, subject, body, mode,
                                          sent_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (idempotency_key, email_id, interaction_id, to_addr, subject, body, mode, now()))
            return True
        except sqlite3.IntegrityError:
            return False
