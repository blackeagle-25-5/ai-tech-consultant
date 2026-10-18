"""KnowledgeService: wires storage, search, crawler and Claude together.

The API (app/api.py), the CLI and the evals all use this one object.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass

from common.llm import HAIKU, LLM, SONNET, LLMError, llm_configured
from task1_knowledge_brain.app import completeness, escalations, safety
from task1_knowledge_brain.app.qa import generate_answer, validated_citations
from task1_knowledge_brain.app.schemas import QueryResponse, Source
from task1_knowledge_brain.config import Settings, get_settings
from task1_knowledge_brain.db import connect
from task1_knowledge_brain.ingestion.crawler import PoliteFetcher, make_client
from task1_knowledge_brain.ingestion.pipeline import RunReport, run_ingestion
from task1_knowledge_brain.retrieval.index import ChunkIndex, Embedder, FastEmbedder, SearchHit

PERSONAL_INFO_MESSAGE = ("I can't share personal information about individuals, and the help center is not a source "
                         "for it. For company contact details, please use the official support channels.")
UNSUPPORTED_MESSAGE = ("I couldn't verify a complete answer from the help center, so I've passed this to a "
                       "human expert.")
MODEL_ALIASES = {"haiku": HAIKU, "sonnet": SONNET}


class IngestionBusy(RuntimeError):
    pass


class LLMUnavailable(RuntimeError):
    pass


@dataclass
class KnowledgeService:
    settings: Settings
    index: ChunkIndex
    llm: LLM | None

    def __post_init__(self) -> None:
        self.conn = self.index.conn
        self._ingest_lock = asyncio.Lock()

    # ------------------------------------------------------------- ingestion
    @property
    def ingestion_running(self) -> bool:
        return self._ingest_lock.locked()

    async def run_ingestion(self, mode: str = "incremental", limit: int | None = None,
                            describe_images: bool = True) -> RunReport:
        if self._ingest_lock.locked():
            raise IngestionBusy("An ingestion run is already in progress.")
        async with self._ingest_lock:
            s = self.settings
            async with make_client(s.user_agent, s.request_timeout_s) as page_client, \
                    make_client(s.user_agent, s.request_timeout_s) as image_client:
                fetcher = PoliteFetcher(page_client, concurrency=s.crawl_concurrency,
                                        delay_s=s.crawl_delay_s, max_attempts=s.max_attempts)
                image_fetcher = PoliteFetcher(image_client, concurrency=4, delay_s=0.2,
                                              max_attempts=s.max_attempts)
                report = await run_ingestion(
                    self.conn, self.index, fetcher, s, mode=mode, limit=limit,
                    describe_images=describe_images, image_fetcher=image_fetcher, llm=self.llm)
            escalations.expire_answers(self.conn, self.index)
            return report

    def status(self) -> dict:
        c = self.conn
        pages = {r[0]: r[1] for r in c.execute(
            "SELECT status, COUNT(*) FROM pages WHERE kind = 'article' GROUP BY status")}
        images = {r[0]: r[1] for r in c.execute("SELECT status, COUNT(*) FROM images GROUP BY status")}
        last = c.execute("SELECT * FROM ingestion_runs ORDER BY id DESC LIMIT 1").fetchone()
        return {
            "running": self.ingestion_running,
            "article_pages": pages,
            "articles_active": c.execute("SELECT COUNT(*) FROM articles WHERE active = 1").fetchone()[0],
            "images": images,
            "chunks_active": self.index.count(),
            "last_run": dict(last) if last else None,
        }

    # ------------------------------------------------------------- search / answer
    def search(self, query: str, k: int = 8, mode: str = "hybrid") -> list[SearchHit]:
        return self.index.search(query, k=k, mode=mode)

    def answer(self, question: str, *, top_k: int | None = None, requester: str | None = None,
               escalate: bool = True, model: str | None = None) -> QueryResponse:
        start = time.perf_counter()
        request_id = uuid.uuid4().hex[:12]
        model_id = MODEL_ALIASES.get(model or "", model) or self.settings.answer_model
        hits = self.search(question, k=top_k or self.settings.top_k)
        best = max((h.similarity for h in hits), default=0.0)

        def respond(status: str, answer: str, cited: list[SearchHit], confidence: str,
                    reason: str | None, cost: float = 0.0, prompt_version: str | None = None,
                    used_model: str | None = None, notes: list[str] | None = None,
                    allow_escalation: bool = True) -> QueryResponse:
            escalation_id = None
            if status == "insufficient_evidence" and escalate and allow_escalation:
                evidence = [{"title": h.title, "url": h.url, "similarity": round(h.similarity, 3)}
                            for h in hits[:5]]
                escalation_id = escalations.create(self.conn, question, reason or "insufficient evidence",
                                                   evidence, requester)
            return QueryResponse(
                request_id=request_id, status=status, answer=answer,
                sources=merge_sources(cited),
                confidence=confidence, needs_human=status != "answered", escalation_id=escalation_id,
                model=used_model, prompt_version=prompt_version, safety_notes=notes or [],
                latency_ms=int((time.perf_counter() - start) * 1000), cost_usd=round(cost, 6))

        if safety.is_personal_info_request(question):
            # Not escalated: an expert should not answer this either.
            return respond("insufficient_evidence", PERSONAL_INFO_MESSAGE, [], "low", "personal information request",
                           notes=["personal information request declined"], allow_escalation=False)
        if not hits or best < self.settings.min_relevance:
            return respond("insufficient_evidence",
                           "I could not find this in the help center, so I've passed it to a human expert.",
                           [], "low", f"low retrieval relevance ({best:.2f})")
        if self.llm is None:
            raise LLMUnavailable("No model key configured (set ANTHROPIC_API_KEY, or GROQ_API_KEY for the dev fallback).")

        result, labels, prompt_version = generate_answer(self.llm, question, hits, model_id)
        draft = result.value
        cited = validated_citations(draft, labels)
        if draft.status == "answered" and not cited:
            # An answer that cites nothing we supplied is treated as unsupported; its text is not passed on.
            return respond("insufficient_evidence", UNSUPPORTED_MESSAGE, [], "low",
                           "model answer had no valid citation", result.record.cost_usd,
                           prompt_version, result.record.model, notes=["answer had no valid citation"])
        notes: list[str] = []
        cost = result.record.cost_usd
        if draft.status == "answered":
            omitted = completeness.missing_items(question, draft.answer, hits, cited)
            if omitted:
                # One repair attempt that names what the evidence lists but the answer left out.
                retry, labels, prompt_version = generate_answer(self.llm, question, hits, model_id, omitted=omitted)
                cost += retry.record.cost_usd
                retry_cited = validated_citations(retry.value, labels)
                if retry.value.status == "answered" and retry_cited:
                    result, draft, cited = retry, retry.value, retry_cited
                still = completeness.missing_items(question, draft.answer, hits, cited)
                notes.append(f"completeness repair for {', '.join(omitted)}"
                             + (f"; still missing {', '.join(still)}" if still else "; fixed"))
            problems = safety.check_answer(draft.answer, hits)
            if problems:
                return respond("insufficient_evidence", UNSUPPORTED_MESSAGE, [], "low",
                               "unsupported specifics: " + "; ".join(problems)[:300], cost,
                               prompt_version, result.record.model, notes=notes + problems)
        return respond(draft.status, draft.answer, cited, draft.confidence,
                       "model reported insufficient evidence", cost, prompt_version, result.record.model, notes=notes)


def merge_sources(cited: list[SearchHit]) -> list[Source]:
    """One source per article URL: keep the best relevance and combine screenshot links."""
    merged: dict[str, Source] = {}
    for h in cited:
        relevance = round(max(h.similarity, 0.0), 3)
        s = merged.get(h.url)
        if s is None:
            merged[h.url] = Source(title=h.title, url=h.url, section=h.section, source_type=h.source_type,
                                   relevance=relevance, image_urls=list(h.image_urls))
            continue
        s.relevance = max(s.relevance, relevance)
        s.image_urls += [u for u in h.image_urls if u not in s.image_urls]
        if s.source_type == "image" and h.source_type == "article":
            s.source_type, s.section = "article", h.section
    return list(merged.values())


def build_service(settings: Settings | None = None, embedder: Embedder | None = None,
                  llm: LLM | None = None) -> KnowledgeService:
    settings = settings or get_settings()
    conn = connect(settings.db_path)
    index = ChunkIndex(conn, embedder or FastEmbedder())
    if llm is None and llm_configured():
        llm = LLM()
    return KnowledgeService(settings=settings, index=index, llm=llm)


__all__ = ["KnowledgeService", "build_service", "IngestionBusy", "LLMUnavailable", "LLMError"]
