"""Human escalations: questions the bot could not answer, and the expert answers that flow back."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from task1_knowledge_brain.retrieval.chunker import Chunk, make_chunk_id
from task1_knowledge_brain.retrieval.index import ChunkIndex

ESCALATED_EVAL_PATH = Path(__file__).resolve().parent.parent / "evals" / "escalated_questions.jsonl"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create(conn: sqlite3.Connection, question: str, reason: str, evidence: list[dict],
           requester: str | None) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO escalations(question, reason, evidence, requester, status, created_at) "
            "VALUES (?, ?, ?, ?, 'open', ?)",
            (question, reason, json.dumps(evidence), requester, now()))
    return int(cur.lastrowid)


def list_all(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    sql = """SELECT e.*, (SELECT answer FROM expert_answers x WHERE x.escalation_id = e.id
                          ORDER BY version DESC LIMIT 1) AS answer
             FROM escalations e"""
    params: tuple = ()
    if status:
        sql += " WHERE e.status = ?"
        params = (status,)
    rows = conn.execute(sql + " ORDER BY e.id DESC", params).fetchall()
    return [{**dict(r), "evidence": json.loads(r["evidence"])} for r in rows]


def get(conn: sqlite3.Connection, escalation_id: int) -> dict | None:
    rows = [e for e in list_all(conn) if e["id"] == escalation_id]
    return rows[0] if rows else None


def resolve(conn: sqlite3.Connection, index: ChunkIndex, escalation_id: int, *, answer: str, expert: str,
            scope: str, source_url: str | None, expires_at: str | None) -> dict:
    esc = get(conn, escalation_id)
    if esc is None:
        raise KeyError(escalation_id)
    version = conn.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM expert_answers WHERE escalation_id = ?",
                           (escalation_id,)).fetchone()[0]
    with conn:
        conn.execute(
            """INSERT INTO expert_answers(escalation_id, question, answer, expert, scope, source_url,
                                          version, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (escalation_id, esc["question"], answer, expert, scope, source_url, version, expires_at, now()))
        conn.execute("UPDATE escalations SET status = 'resolved', resolved_at = ? WHERE id = ?",
                     (now(), escalation_id))

    article_id = f"expert-{escalation_id}"
    if scope == "general":
        # A newer version replaces the older chunk because the chunk id is stable per escalation.
        index.upsert_chunk(Chunk(
            chunk_id=make_chunk_id(article_id, "expert", 0), article_id=article_id, source_type="expert",
            title=f"Expert answer: {esc['question'][:80]}",
            url=source_url or f"expert://escalations/{escalation_id}",
            section=f"v{version} by {expert}",
            text=f"Question: {esc['question']}\nApproved expert answer: {answer}",
        ))
    else:
        index.set_article_active(article_id, False)

    # Unanswered questions become regression tests for the next eval run.
    ESCALATED_EVAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ESCALATED_EVAL_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"escalation_id": escalation_id, "question": esc["question"],
                            "expert_answer": answer, "scope": scope, "version": version}) + "\n")
    return get(conn, escalation_id) or {}


def expire_answers(conn: sqlite3.Connection, index: ChunkIndex) -> int:
    """Deactivate expert answers whose expiry date has passed."""
    today = datetime.now(timezone.utc).date().isoformat()
    rows = conn.execute(
        """SELECT escalation_id FROM expert_answers x
           WHERE expires_at IS NOT NULL AND expires_at < ?
             AND version = (SELECT MAX(version) FROM expert_answers y WHERE y.escalation_id = x.escalation_id)""",
        (today,)).fetchall()
    for r in rows:
        index.set_article_active(f"expert-{r[0]}", False)
    return len(rows)
