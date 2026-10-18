"""Projects, scope versions, approvals and delivery log. Nothing is delivered without an approved version."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(os.getenv("SCOPE_DB", Path(__file__).resolve().parent.parent / "data" / "task3" / "scope.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id      TEXT PRIMARY KEY,
    client  TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meetings (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    date       TEXT,
    kind       TEXT NOT NULL,      -- initial | follow_up
    transcript TEXT NOT NULL,      -- original text, preserved
    placeholder INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT NOT NULL REFERENCES projects(id),
    number      INTEGER NOT NULL,
    status      TEXT NOT NULL,     -- draft | approved | rejected | superseded
    based_on    INTEGER,           -- approved baseline version id (for follow-ups)
    meeting_ids TEXT NOT NULL,     -- JSON list
    payload     TEXT NOT NULL,     -- JSON: brief, feasibility, scope, estimate, proposal, review, change_report
    created_at  TEXT NOT NULL,
    reviewer    TEXT,
    review_note TEXT,
    reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS deliveries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id INTEGER NOT NULL REFERENCES versions(id),
    actor      TEXT NOT NULL,
    delivered_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,
    actor  TEXT NOT NULL,
    action TEXT NOT NULL,
    project_id TEXT,
    version_id INTEGER,
    details TEXT
);
"""


class NotApproved(PermissionError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScopeStore:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def audit(self, actor: str, action: str, project_id: str | None, version_id: int | None, details: str = "") -> None:
        with self.conn:
            self.conn.execute("INSERT INTO audit_log(ts, actor, action, project_id, version_id, details) "
                              "VALUES (?, ?, ?, ?, ?, ?)", (now(), actor, action, project_id, version_id, details))

    def ensure_project(self, project_id: str, client: str) -> None:
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO projects(id, client, created_at) VALUES (?, ?, ?)",
                              (project_id, client, now()))

    def add_meeting(self, meeting_id: str, project_id: str, date: str, kind: str, transcript: str,
                    placeholder: bool) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO meetings(id, project_id, date, kind, transcript, placeholder) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET transcript = excluded.transcript, date = excluded.date,
                       placeholder = excluded.placeholder""",
                (meeting_id, project_id, date, kind, transcript, int(placeholder)))

    def add_version(self, project_id: str, payload: dict, meeting_ids: list[str], based_on: int | None) -> int:
        number = self.conn.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM versions WHERE project_id = ?",
                                   (project_id,)).fetchone()[0]
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO versions(project_id, number, status, based_on, meeting_ids, payload, created_at)
                   VALUES (?, ?, 'draft', ?, ?, ?, ?)""",
                (project_id, number, based_on, json.dumps(meeting_ids), json.dumps(payload), now()))
        vid = int(cur.lastrowid)
        self.audit("system", "version_created", project_id, vid, f"v{number} (draft)")
        return vid

    def version(self, version_id: int) -> dict:
        row = self.conn.execute("SELECT * FROM versions WHERE id = ?", (version_id,)).fetchone()
        if row is None:
            raise KeyError(version_id)
        return {**dict(row), "payload": json.loads(row["payload"]), "meeting_ids": json.loads(row["meeting_ids"])}

    def versions(self, project_id: str) -> list[dict]:
        rows = self.conn.execute("SELECT id FROM versions WHERE project_id = ? ORDER BY number", (project_id,))
        return [self.version(r[0]) for r in rows]

    def approved_baseline(self, project_id: str) -> dict | None:
        row = self.conn.execute("SELECT id FROM versions WHERE project_id = ? AND status = 'approved' "
                                "ORDER BY number DESC LIMIT 1", (project_id,)).fetchone()
        return self.version(row[0]) if row else None

    # ---------- human approval ----------
    def approve(self, version_id: int, reviewer: str, note: str = "") -> None:
        v = self.version(version_id)
        if v["status"] != "draft":
            raise ValueError(f"Only drafts can be approved (this one is {v['status']}).")
        if not reviewer.strip():
            raise ValueError("Reviewer name is required.")
        blockers = [f for f in v["payload"].get("review", {}).get("findings", []) if f["severity"] == "blocker"]
        if blockers and not note.strip():
            raise ValueError("This draft has blocker findings; add a note explaining why it is approved anyway.")
        with self.conn:
            self.conn.execute("UPDATE versions SET status = 'superseded' WHERE project_id = ? AND status = 'approved'",
                              (v["project_id"],))
            self.conn.execute("UPDATE versions SET status = 'approved', reviewer = ?, review_note = ?, reviewed_at = ? "
                              "WHERE id = ?", (reviewer, note, now(), version_id))
        self.audit(reviewer, "approved", v["project_id"], version_id, note)

    def reject(self, version_id: int, reviewer: str, note: str) -> None:
        v = self.version(version_id)
        if v["status"] != "draft":
            raise ValueError(f"Only drafts can be rejected (this one is {v['status']}).")
        with self.conn:
            self.conn.execute("UPDATE versions SET status = 'rejected', reviewer = ?, review_note = ?, reviewed_at = ? "
                              "WHERE id = ?", (reviewer, note, now(), version_id))
        self.audit(reviewer, "rejected", v["project_id"], version_id, note)

    def deliver(self, version_id: int, actor: str) -> str:
        """Release the proposal to the client (simulated). Refuses anything not approved."""
        v = self.version(version_id)
        if v["status"] != "approved":
            self.audit(actor, "delivery_blocked", v["project_id"], version_id, f"status {v['status']}")
            raise NotApproved(f"Version {v['number']} is {v['status']}; only an approved version can be sent.")
        with self.conn:
            self.conn.execute("INSERT INTO deliveries(version_id, actor, delivered_at) VALUES (?, ?, ?)",
                              (version_id, actor, now()))
        self.audit(actor, "delivered", v["project_id"], version_id)
        return v["payload"]["proposal_markdown"]
