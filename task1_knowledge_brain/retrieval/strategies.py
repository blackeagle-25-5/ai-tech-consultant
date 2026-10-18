"""Evidence selection strategies on top of ChunkIndex.search, compared in evals/retrieval_experiments.py.

- baseline:  hybrid search, top k passages.
- siblings:  baseline, plus the other passages of the top-ranked articles (so a list split across
             passages, e.g. four mandatory import fields, reaches the model complete).
- rerank:    hybrid top-`pool` candidates re-ordered by a local cross-encoder, then top k.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from task1_knowledge_brain.retrieval.index import ChunkIndex, SearchHit

RERANK_MODEL = os.getenv("KB_RERANK_MODEL", "Xenova/ms-marco-MiniLM-L-12-v2")


def baseline(index: ChunkIndex, query: str, k: int = 8) -> list[SearchHit]:
    return index.search(query, k=k)


def add_siblings(index: ChunkIndex, query: str, hits: list[SearchHit], *, top_articles: int = 2,
                 max_article_chunks: int = 8, max_total: int = 12) -> list[SearchHit]:
    """Append missing passages of the first `top_articles` distinct articles, in document order."""
    seen = {h.chunk_id for h in hits}
    articles: list[str] = []
    for h in hits:
        if h.source_type == "article" and h.article_id not in articles:
            articles.append(h.article_id)
        if len(articles) == top_articles:
            break
    extra_ids: list[str] = []
    for aid in articles:
        rows = index.conn.execute(
            "SELECT chunk_id FROM chunks WHERE article_id = ? AND source_type = 'article' AND active = 1 ORDER BY rowid",
            (aid,)).fetchall()
        if len(rows) > max_article_chunks:
            continue  # long articles: the ranked passages are a better choice than the whole article
        extra_ids += [r[0] for r in rows if r[0] not in seen]
    room = max_total - len(hits)
    if room <= 0 or not extra_ids:
        return hits
    query_vec = index.embedder.embed_query(query)
    extra = index._hydrate([(cid, 0.0) for cid in extra_ids[:room]], query_vec)
    return hits + extra


class Reranker:
    """Local cross-encoder (fastembed, CPU). No API calls."""

    def __init__(self, model_name: str = RERANK_MODEL) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        cache_dir = Path(os.getenv("FASTEMBED_CACHE", Path(__file__).resolve().parents[2] / "data" / "models"))
        self.model_name = model_name
        self.model = TextCrossEncoder(model_name=model_name, cache_dir=str(cache_dir))

    def rerank(self, query: str, hits: list[SearchHit]) -> list[SearchHit]:
        if not hits:
            return hits
        scores = np.array(list(self.model.rerank(query, [h.text for h in hits])), dtype=np.float32)
        order = np.argsort(-scores)
        return [hits[i] for i in order]


def reranked(index: ChunkIndex, reranker: Reranker, query: str, k: int = 8, pool: int = 40) -> list[SearchHit]:
    return reranker.rerank(query, index.search(query, k=pool))[:k]
