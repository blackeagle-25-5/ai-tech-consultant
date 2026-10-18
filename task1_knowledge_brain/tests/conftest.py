from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np
import pytest

import common.llm
from common.llm import LLM
from common.testing import FakeAnthropic
from task1_knowledge_brain.app import escalations
from task1_knowledge_brain.config import Settings
from task1_knowledge_brain.db import connect
from task1_knowledge_brain.retrieval.index import ChunkIndex
from task1_knowledge_brain.service import KnowledgeService

FIXTURES = Path(__file__).parent / "fixtures"


class FakeEmbedder:
    """Bag-of-words hashing embedder: deterministic and good enough for overlap-based tests."""

    dim = 384

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            if len(word) > 2:
                v[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return np.array([self._vec(t) for t in texts], dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._vec(text)


@pytest.fixture(autouse=True)
def _ledger_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(common.llm, "LEDGER_PATH", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(escalations, "ESCALATED_EVAL_PATH", tmp_path / "escalated.jsonl")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(db_path=tmp_path / "kb.db", crawl_delay_s=0, min_relevance=0.2,
                    sitemap_url="https://help.example.com/support/sitemap.xml")


@pytest.fixture
def index(settings) -> ChunkIndex:
    return ChunkIndex(connect(settings.db_path), FakeEmbedder())


def make_service(settings: Settings, index: ChunkIndex, responder=None) -> KnowledgeService:
    llm = LLM(client=FakeAnthropic(responder)) if responder else None
    return KnowledgeService(settings=settings, index=index, llm=llm)


def article_html(title: str, body: str, category: str = "Payments", folder: str = "Refunds") -> str:
    return f"""<html><head><meta property="og:title" content="{title}"></head><body>
    <div class="breadcrumb"><a href="/support/solutions">Solution home</a>
      <a href="/support/solutions/1">Section: {category}</a>
      <a href="/support/solutions/folders/2">{folder}</a></div>
    <article class="article-body" id="article-body"><h1>{title}</h1>{body}</article>
    </body></html>"""


def sitemap_xml(entries: list[tuple[str, str]]) -> str:
    urls = "".join(f"<url><loc>{u}</loc><lastmod>{m}</lastmod></url>" for u, m in entries)
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
