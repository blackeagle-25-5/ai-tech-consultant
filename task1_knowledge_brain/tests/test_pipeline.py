"""End-to-end ingestion against a mocked help center: add, update, remove, resume."""

import httpx
import respx

from task1_knowledge_brain.ingestion.crawler import PoliteFetcher
from task1_knowledge_brain.ingestion.pipeline import rollback_article, run_ingestion

from .conftest import article_html, sitemap_xml

HOST = "https://help.example.com"
SITEMAP = f"{HOST}/support/sitemap.xml"
A1 = f"{HOST}/support/solutions/articles/1-refunds"
A2 = f"{HOST}/support/solutions/articles/2-logo"


async def _no_sleep(_: float) -> None:
    return None


async def run(index, settings):
    async with httpx.AsyncClient() as c:
        f = PoliteFetcher(c, concurrency=2, delay_s=0, max_attempts=2, sleep=_no_sleep)
        return await run_ingestion(index.conn, index, f, settings, describe_images=False)


def mock_site(entries, pages):
    respx.get(f"{HOST}/robots.txt").respond(404)
    respx.get(SITEMAP).respond(200, text=sitemap_xml(entries))
    for url, resp in pages.items():
        respx.get(url).mock(return_value=resp)


REFUND_V1 = article_html("How refunds work", "<p>Go to Finance and click Issue Refund on the invoice.</p>")
REFUND_V2 = article_html("How refunds work", "<p>Refunds now live under Billing then Refund Payment.</p>")
LOGO = article_html("Change your logo", "<p>Open Design Settings and upload a new logo image.</p>",
                    category="Design", folder="Branding")


@respx.mock
async def test_first_run_adds_and_second_run_is_unchanged(index, settings):
    mock_site([(A1, "d1"), (A2, "d1")], {A1: httpx.Response(200, text=REFUND_V1),
                                         A2: httpx.Response(200, text=LOGO)})
    r1 = await run(index, settings)
    assert (r1.added, r1.failed) == (2, 0)
    chunks_after_first = index.count()

    r2 = await run(index, settings)
    assert (r2.added, r2.updated, r2.unchanged) == (0, 0, 2)
    assert index.count() == chunks_after_first  # nothing duplicated


@respx.mock
async def test_changed_article_is_reprocessed_and_versioned(index, settings):
    mock_site([(A1, "d1")], {A1: httpx.Response(200, text=REFUND_V1)})
    await run(index, settings)
    respx.reset()
    mock_site([(A1, "d2")], {A1: httpx.Response(200, text=REFUND_V2)})
    r = await run(index, settings)
    assert r.updated == 1
    top = index.search("refund payment billing", k=1)[0]
    assert "Billing" in top.text
    row = index.conn.execute("SELECT version FROM articles WHERE article_id='1'").fetchone()
    assert row["version"] == 2

    rollback_article(index.conn, index, "1", 1)
    assert "Issue Refund" in index.search("issue refund finance", k=1)[0].text


@respx.mock
async def test_removed_article_disappears_from_search(index, settings):
    mock_site([(A1, "d1"), (A2, "d1")], {A1: httpx.Response(200, text=REFUND_V1),
                                         A2: httpx.Response(200, text=LOGO)})
    await run(index, settings)
    respx.reset()
    mock_site([(A2, "d1")], {A2: httpx.Response(200, text=LOGO)})
    r = await run(index, settings)
    assert r.removed == 1
    assert all(h.article_id != "1" for h in index.search("refund invoice finance", k=10))


@respx.mock
async def test_interrupted_page_is_retried_on_next_run(index, settings):
    mock_site([(A1, "d1"), (A2, "d1")], {A1: httpx.Response(503), A2: httpx.Response(200, text=LOGO)})
    r1 = await run(index, settings)
    assert (r1.added, r1.failed) == (1, 1)
    status = index.conn.execute("SELECT status FROM pages WHERE url=?", (A1,)).fetchone()[0]
    assert status == "failed"

    respx.reset()
    mock_site([(A1, "d1"), (A2, "d1")], {A1: httpx.Response(200, text=REFUND_V1),
                                         A2: httpx.Response(200, text=LOGO)})
    r2 = await run(index, settings)
    assert (r2.added, r2.unchanged, r2.failed) == (1, 1, 0)


@respx.mock
async def test_bad_html_does_not_stop_the_job(index, settings):
    mock_site([(A1, "d1"), (A2, "d1")], {A1: httpx.Response(200, text="<html>no article</html>"),
                                         A2: httpx.Response(200, text=LOGO)})
    r = await run(index, settings)
    assert (r.added, r.failed) == (1, 1)
