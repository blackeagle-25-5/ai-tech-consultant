import httpx
import pytest
import respx

from task1_knowledge_brain.ingestion.crawler import PoliteFetcher

URL = "https://help.example.com/support/solutions/articles/1-a"
ROBOTS = "https://help.example.com/robots.txt"


async def _no_sleep(_: float) -> None:
    return None


def fetcher(client: httpx.AsyncClient) -> PoliteFetcher:
    return PoliteFetcher(client, concurrency=1, delay_s=0, max_attempts=3, sleep=_no_sleep)


@respx.mock
async def test_retries_server_errors_then_succeeds():
    respx.get(ROBOTS).respond(404)
    route = respx.get(URL).mock(side_effect=[httpx.Response(503), httpx.Response(200, text="ok")])
    async with httpx.AsyncClient() as c:
        r = await fetcher(c).fetch(URL)
    assert r.status == "ok" and r.attempts == 2 and route.call_count == 2


@respx.mock
async def test_404_means_gone_without_retry():
    respx.get(ROBOTS).respond(404)
    route = respx.get(URL).respond(404)
    async with httpx.AsyncClient() as c:
        r = await fetcher(c).fetch(URL)
    assert r.status == "gone" and route.call_count == 1


@respx.mock
async def test_permanent_client_error_is_not_retried():
    respx.get(ROBOTS).respond(404)
    route = respx.get(URL).respond(403)
    async with httpx.AsyncClient() as c:
        r = await fetcher(c).fetch(URL)
    assert r.status == "blocked" and route.call_count == 1


@respx.mock
async def test_timeouts_give_up_after_max_attempts():
    respx.get(ROBOTS).respond(404)
    route = respx.get(URL).mock(side_effect=httpx.ConnectTimeout("slow"))
    async with httpx.AsyncClient() as c:
        r = await fetcher(c).fetch(URL)
    assert r.status == "failed" and route.call_count == 3 and "ConnectTimeout" in r.error


@respx.mock
async def test_robots_disallow_is_respected():
    respx.get(ROBOTS).respond(200, text="User-agent: *\nDisallow: /support/solutions/articles/")
    route = respx.get(URL).respond(200, text="ok")
    async with httpx.AsyncClient() as c:
        r = await fetcher(c).fetch(URL)
    assert r.status == "blocked" and route.call_count == 0


@pytest.mark.parametrize("delay", [0.5])
@respx.mock
async def test_politeness_delay_is_applied_per_request(delay):
    respx.get(ROBOTS).respond(404)
    respx.get(URL).respond(200, text="ok")
    sleeps: list[float] = []

    async def record(s: float) -> None:
        sleeps.append(s)

    async with httpx.AsyncClient() as c:
        f = PoliteFetcher(c, concurrency=1, delay_s=delay, sleep=record)
        await f.fetch(URL)
        await f.fetch(URL)
    assert sleeps == [delay, delay]
