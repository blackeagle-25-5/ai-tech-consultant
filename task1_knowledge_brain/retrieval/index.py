"""Chunk storage and search: vector (fastembed), keyword (SQLite FTS5 / BM25) and hybrid.

Vectors live in the `chunks.embedding` column as float32 bytes and are loaded into
a numpy matrix for search. At ~15k chunks this is milliseconds, so a separate
vector database is not needed (see DESIGN.md).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from task1_knowledge_brain.retrieval.chunker import Chunk

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
RRF_K = 60


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...


class FastEmbedder:
    """Local embedding model (~130 MB download on first use, runs on CPU)."""

    def __init__(self, model_name: str = EMBED_MODEL) -> None:
        from fastembed import TextEmbedding

        cache_dir = Path(os.getenv("FASTEMBED_CACHE", Path(__file__).resolve().parents[2] / "data" / "models"))
        self.model = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir), threads=1, lazy_load=True)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return _normalize(np.array(list(self.model.embed(texts, batch_size=64)), dtype=np.float32))

    def embed_query(self, text: str) -> np.ndarray:
        return _normalize(np.array(list(self.model.query_embed(text)), dtype=np.float32))[0]


def _normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=-1, keepdims=True)
    return m / np.where(norms == 0, 1, norms)


@dataclass
class SearchHit:
    chunk_id: str
    article_id: str
    source_type: str
    title: str
    url: str
    section: str
    text: str
    image_urls: list[str]
    score: float  # fused rank score (hybrid) or raw score (single mode)
    similarity: float  # cosine similarity with the query, 0..1 (used for the relevance gate)


class ChunkIndex:
    def __init__(self, conn: sqlite3.Connection, embedder: Embedder) -> None:
        self.conn = conn
        self.embedder = embedder
        self._lock = threading.Lock()
        self._matrix: np.ndarray | None = None
        self._ids: list[str] = []

    # ---------- writes ----------
    def replace_article_chunks(self, article_id: str, chunks: list[Chunk], source_type: str) -> None:
        """Delete this article's chunks of one source type, then insert new ones (idempotent)."""
        vectors = self.embedder.embed_documents([c.text for c in chunks]) if chunks else None
        with self.conn:
            old = [r[0] for r in self.conn.execute(
                "SELECT chunk_id FROM chunks WHERE article_id = ? AND source_type = ?",
                (article_id, source_type))]
            self.conn.executemany("DELETE FROM chunks_fts WHERE chunk_id = ?", [(i,) for i in old])
            self.conn.execute("DELETE FROM chunks WHERE article_id = ? AND source_type = ?",
                              (article_id, source_type))
            for c, vec in zip(chunks, vectors if vectors is not None else []):
                self.conn.execute(
                    """INSERT INTO chunks(chunk_id, article_id, source_type, title, url, section,
                                          text, image_urls, active, embedding)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                    (c.chunk_id, c.article_id, c.source_type, c.title, c.url, c.section, c.text,
                     c.image_urls_json, vec.astype(np.float32).tobytes()))
                self.conn.execute(
                    "INSERT INTO chunks_fts(chunk_id, title, section, text) VALUES (?, ?, ?, ?)",
                    (c.chunk_id, c.title, c.section, c.text))
        self.invalidate()

    def upsert_chunk(self, chunk: Chunk) -> None:
        vec = self.embedder.embed_documents([chunk.text])[0]
        with self.conn:
            self.conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk.chunk_id,))
            self.conn.execute(
                """INSERT INTO chunks(chunk_id, article_id, source_type, title, url, section,
                                      text, image_urls, active, embedding)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(chunk_id) DO UPDATE SET text=excluded.text, section=excluded.section,
                       title=excluded.title, url=excluded.url, image_urls=excluded.image_urls,
                       active=1, embedding=excluded.embedding""",
                (chunk.chunk_id, chunk.article_id, chunk.source_type, chunk.title, chunk.url,
                 chunk.section, chunk.text, chunk.image_urls_json, vec.astype(np.float32).tobytes()))
            self.conn.execute(
                "INSERT INTO chunks_fts(chunk_id, title, section, text) VALUES (?, ?, ?, ?)",
                (chunk.chunk_id, chunk.title, chunk.section, chunk.text))
        self.invalidate()

    def set_article_active(self, article_id: str, active: bool) -> None:
        with self.conn:
            self.conn.execute("UPDATE chunks SET active = ? WHERE article_id = ?",
                              (1 if active else 0, article_id))
        self.invalidate()

    def invalidate(self) -> None:
        with self._lock:
            self._matrix = None

    # ---------- reads ----------
    def _load(self) -> tuple[np.ndarray, list[str]]:
        with self._lock:
            if self._matrix is None:
                rows = self.conn.execute(
                    "SELECT chunk_id, embedding FROM chunks WHERE active = 1 AND embedding IS NOT NULL"
                ).fetchall()
                self._ids = [r[0] for r in rows]
                self._matrix = (np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
                                if rows else np.zeros((0, 384), dtype=np.float32))
            return self._matrix, self._ids

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM chunks WHERE active = 1").fetchone()[0]

    def vector_search(self, query_vec: np.ndarray, k: int) -> list[tuple[str, float]]:
        matrix, ids = self._load()
        if not ids:
            return []
        sims = matrix @ query_vec
        top = np.argsort(-sims)[:k]
        return [(ids[i], float(sims[i])) for i in top]

    def keyword_search(self, query: str, k: int) -> list[tuple[str, float]]:
        terms = [t for t in re.findall(r"[A-Za-z0-9]+", query.lower()) if len(t) > 1]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        rows = self.conn.execute(
            """SELECT f.chunk_id, bm25(chunks_fts, 2.0, 1.5, 1.0) AS rank
               FROM chunks_fts f JOIN chunks c ON c.chunk_id = f.chunk_id
               WHERE chunks_fts MATCH ? AND c.active = 1
               ORDER BY rank LIMIT ?""",
            (match, k)).fetchall()
        return [(r[0], -float(r[1])) for r in rows]  # bm25(): lower is better

    def search(self, query: str, k: int = 8, mode: str = "hybrid") -> list[SearchHit]:
        query_vec = self.embedder.embed_query(query)
        vec_hits = self.vector_search(query_vec, 50) if mode in ("vector", "hybrid") else []
        kw_hits = self.keyword_search(query, 50) if mode in ("keyword", "hybrid") else []

        if mode == "vector":
            ranked = vec_hits[:k]
        elif mode == "keyword":
            ranked = kw_hits[:k]
        else:  # Reciprocal Rank Fusion
            fused: dict[str, float] = {}
            for hits in (vec_hits, kw_hits):
                for rank, (cid, _) in enumerate(hits):
                    fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            ranked = sorted(fused.items(), key=lambda x: -x[1])[:k]
        return self._hydrate(ranked, query_vec)

    def _hydrate(self, ranked: list[tuple[str, float]], query_vec: np.ndarray) -> list[SearchHit]:
        if not ranked:
            return []
        ids = [cid for cid, _ in ranked]
        placeholders = ",".join("?" * len(ids))
        rows = {r["chunk_id"]: r for r in self.conn.execute(
            f"SELECT * FROM chunks WHERE chunk_id IN ({placeholders})", ids)}
        hits = []
        for cid, score in ranked:
            r = rows.get(cid)
            if r is None:
                continue
            vec = np.frombuffer(r["embedding"], dtype=np.float32)
            hits.append(SearchHit(
                chunk_id=cid, article_id=r["article_id"], source_type=r["source_type"],
                title=r["title"], url=r["url"], section=r["section"] or "", text=r["text"],
                image_urls=json.loads(r["image_urls"]), score=score,
                similarity=float(vec @ query_vec),
            ))
        return hits
