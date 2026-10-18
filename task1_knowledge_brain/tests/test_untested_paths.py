"""Tests for paths that had no coverage: screenshot pipeline, expert-answer expiry, ingestion endpoints,
daily scheduler. Mock-based, no network."""

import asyncio

import httpx
import respx
from fastapi.testclient import TestClient

from common.llm import LLM
from common.testing import FakeAnthropic
from task1_knowledge_brain.app import api, escalations
from task1_knowledge_brain.app.scheduler import start_scheduler
from task1_knowledge_brain.ingestion.crawler import PoliteFetcher
from task1_knowledge_brain.ingestion.parser import parse_article
from task1_knowledge_brain.ingestion.pipeline import RunReport, store_article
from task1_knowledge_brain.multimodal.describe import ImageDescription
from task1_knowledge_brain.multimodal.pipeline import describe_pending_images

from .conftest import article_html, make_service

IMG = "https://img.example.com/shot.png"
PNG = b"\x89PNG" + b"0" * 5000


def seed_article_with_image(index):
    html = article_html("Change your logo", f'<p>Open Brand Images.</p><img src="{IMG}"><p>Click Save.</p>')
    store_article(index.conn, index, parse_article(html, "https://help.example.com/a/1", "1"), "d1")


async def _no_sleep(_):
    return None


@respx.mock
async def test_screenshot_pipeline_describes_indexes_and_records_actual_model(settings, index):
    seed_article_with_image(index)
    respx.get(IMG).respond(200, content=PNG, headers={"content-type": "image/png"})
    fake = FakeAnthropic(lambda kw: ImageDescription(description="Brand Images tab with Upload Logo button",
                                                     ui_labels=["Upload Logo"], steps=[], uncertain=False,
                                                     is_informative=True))
    async with httpx.AsyncClient() as c:
        n = await describe_pending_images(index.conn, index, LLM(client=fake), PoliteFetcher(c, delay_s=0, sleep=_no_sleep),
                                          settings, RunReport())
    assert n == 1
    row = index.conn.execute("SELECT status, model FROM images").fetchone()
    assert row["status"] == "done" and row["model"] == settings.vision_model
    hit = index.search("upload logo button brand images", k=3)
    assert any(h.source_type == "image" and h.image_urls == [IMG] for h in hit)
    # Re-running does not describe the same screenshot again.
    async with httpx.AsyncClient() as c:
        again = await describe_pending_images(index.conn, index, LLM(client=fake),
                                              PoliteFetcher(c, delay_s=0, sleep=_no_sleep), settings, RunReport())
    assert again == 0 and len(fake.calls) == 1


@respx.mock
async def test_decorative_and_tiny_images_are_skipped(settings, index):
    seed_article_with_image(index)
    respx.get(IMG).respond(200, content=b"tiny", headers={"content-type": "image/png"})
    async with httpx.AsyncClient() as c:
        n = await describe_pending_images(index.conn, index, LLM(client=FakeAnthropic(lambda kw: None)),
                                          PoliteFetcher(c, delay_s=0, sleep=_no_sleep), settings, RunReport())
    assert n == 0 and index.conn.execute("SELECT status FROM images").fetchone()[0] == "skipped"


def test_expired_expert_answer_stops_being_searchable(settings, index):
    esc = escalations.create(index.conn, "Is the spring promo still running?", "manual", [], None)
    escalations.resolve(index.conn, index, esc, answer="Yes, the spring promo runs all season.", expert="a",
                        scope="general", source_url=None, expires_at="2020-01-01")
    assert index.search("spring promo running", k=1)[0].source_type == "expert"
    assert escalations.expire_answers(index.conn, index) == 1
    assert all(h.source_type != "expert" for h in index.search("spring promo running", k=5))


def test_ingestion_endpoints(settings, index, monkeypatch):
    svc = make_service(settings, index)
    started = []

    async def fake_run(**kwargs):
        started.append(kwargs)

    monkeypatch.setattr(svc, "run_ingestion", fake_run)
    api.set_service(svc)
    try:
        c = TestClient(api.app)
        r = c.post("/ingestion/run", json={"mode": "incremental", "limit": 5, "describe_images": False})
        assert r.status_code == 202 and started == [{"mode": "incremental", "limit": 5, "describe_images": False}]
        status = c.get("/ingestion/status").json()
        assert {"running", "article_pages", "articles_active", "images", "chunks_active", "last_run"} <= status.keys()
        assert c.post("/ingestion/run", json={"mode": "everything"}).status_code == 422
    finally:
        api.set_service(None)


def test_daily_scheduler_registers_one_job_and_skips_when_busy():
    calls = []

    class Busy:
        ingestion_running = True

        async def run_ingestion(self, **kw):
            calls.append(kw)

    async def run():
        scheduler = start_scheduler(lambda: Busy())
        jobs = scheduler.get_jobs()
        assert len(jobs) == 1 and jobs[0].id == "daily_sync" and "hour='3'" in str(jobs[0].trigger)
        await jobs[0].func()
        scheduler.shutdown(wait=False)

    asyncio.run(run())
    assert calls == []


def test_query_expansion_fuses_rewrites_and_keeps_original_similarity(index):
    from task1_knowledge_brain.retrieval import strategies
    from task1_knowledge_brain.retrieval.chunker import Chunk

    index.upsert_chunk(Chunk("s1", "10", "article", "Payment Settings", "u1", "", "Finance Payment Settings add new payment gateway Stripe"))
    index.upsert_chunk(Chunk("s2", "11", "article", "Cancel", "u2", "", "cancel subscription card"))
    fake = FakeAnthropic(lambda kw: strategies.SearchRewrites(queries=["add new payment gateway Finance Payment Settings"]))
    hits, calls = strategies.expanded(index, LLM(client=fake), "connect stripe for cards", "m", k=2)
    assert calls == 1 and hits[0].chunk_id == "s1"
    assert abs(hits[0].similarity - float(index.embedder.embed_query("connect stripe for cards")
                                          @ index.embedder.embed_documents([hits[0].text])[0])) < 1e-4


def test_query_expansion_failure_falls_back_to_plain_search(index):
    from task1_knowledge_brain.retrieval import strategies
    from task1_knowledge_brain.retrieval.chunker import Chunk

    index.upsert_chunk(Chunk("s1", "10", "article", "T", "u1", "", "stripe gateway"))
    broken = FakeAnthropic(lambda kw: RuntimeError("rate limited"))
    hits, calls = strategies.expanded(index, LLM(client=broken), "stripe gateway", "m", k=1)
    assert [h.chunk_id for h in hits] == ["s1"]
