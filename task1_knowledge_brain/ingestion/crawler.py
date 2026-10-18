"""Polite HTTP fetching: limited concurrency, a delay per request, robots.txt,
and retries with exponential backoff for temporary failures only."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
GONE_STATUS = {404, 410}


@dataclass
class FetchResult:
    url: str
    status: str  # ok | gone | failed | blocked
    text: str = ""
    content: bytes = b""
    content_type: str = ""
    error: str = ""
    attempts: int = 0


class PoliteFetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        concurrency: int = 2,
        delay_s: float = 1.0,
        max_attempts: int = 4,
        backoff_base_s: float = 2.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.client = client
        self.semaphore = asyncio.Semaphore(concurrency)
        self.delay_s = delay_s
        self.max_attempts = max_attempts
        self.backoff_base_s = backoff_base_s
        self.sleep = sleep
        self._robots: dict[str, RobotFileParser | None] = {}

    async def _robots_for(self, url: str) -> RobotFileParser | None:
        parsed = urlparse(url)
        host = f"{parsed.scheme}://{parsed.netloc}"
        if host not in self._robots:
            parser: RobotFileParser | None = None
            try:
                resp = await self.client.get(f"{host}/robots.txt")
                if resp.status_code == 200:
                    parser = RobotFileParser()
                    parser.parse(resp.text.splitlines())
            except httpx.HTTPError:
                parser = None  # No robots file reachable: fall back to allowing.
            self._robots[host] = parser
        return self._robots[host]

    async def allowed(self, url: str, user_agent: str = "*") -> bool:
        robots = await self._robots_for(url)
        return True if robots is None else robots.can_fetch(user_agent, url)

    async def fetch(self, url: str, *, check_robots: bool = True) -> FetchResult:
        if check_robots and not await self.allowed(url):
            return FetchResult(url=url, status="blocked", error="disallowed by robots.txt")
        last_error = ""
        for attempt in range(1, self.max_attempts + 1):
            async with self.semaphore:
                try:
                    resp = await self.client.get(url)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    resp = None
                    last_error = f"{type(exc).__name__}: {exc}"
                finally:
                    await self.sleep(self.delay_s)  # politeness gap, held inside the slot

            if resp is not None:
                if resp.status_code == 200:
                    return FetchResult(url=url, status="ok", text=resp.text, content=resp.content,
                                       content_type=resp.headers.get("content-type", ""),
                                       attempts=attempt)
                if resp.status_code in GONE_STATUS:
                    return FetchResult(url=url, status="gone", error=f"HTTP {resp.status_code}",
                                       attempts=attempt)
                last_error = f"HTTP {resp.status_code}"
                if resp.status_code not in RETRYABLE_STATUS:
                    # Permanent client error: do not hammer the server with retries.
                    return FetchResult(url=url, status="blocked", error=last_error, attempts=attempt)

            if attempt < self.max_attempts:
                await self.sleep(self.backoff_base_s * (2 ** (attempt - 1)))
        return FetchResult(url=url, status="failed", error=last_error, attempts=self.max_attempts)


def make_client(user_agent: str, timeout_s: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": user_agent},
        timeout=timeout_s,
        follow_redirects=True,
    )
