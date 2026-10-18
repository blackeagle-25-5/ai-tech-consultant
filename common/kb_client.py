"""Typed HTTP client for the Knowledge Brain API (Task 1).

Tasks 2 and 3 use only this client. It mirrors the documented API contract and
does not import any Task 1 code, so the Knowledge Brain can be swapped out.
"""

from __future__ import annotations

import os
import time
from typing import Literal

import httpx
from pydantic import BaseModel, ValidationError


class KnowledgeUnavailable(RuntimeError):
    """The Knowledge Brain could not be reached or returned an error. Never draft facts without it."""


class KBSource(BaseModel):
    title: str
    url: str
    section: str = ""
    source_type: str
    relevance: float
    image_urls: list[str] = []


class KBAnswer(BaseModel):
    request_id: str
    status: Literal["answered", "insufficient_evidence"]
    answer: str
    sources: list[KBSource]
    confidence: Literal["high", "medium", "low"]
    needs_human: bool
    escalation_id: int | None = None
    model: str | None = None
    latency_ms: int
    cost_usd: float


class KBSearchHit(BaseModel):
    chunk_id: str
    title: str
    url: str
    section: str
    source_type: str
    text: str
    image_urls: list[str]
    similarity: float


class KnowledgeClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout_s: float = 75.0,
                 retries: int = 2, transport: httpx.BaseTransport | None = None,
                 requester: str = "client") -> None:
        self.base_url = (base_url or os.getenv("KB_API_URL", "http://127.0.0.1:8000")).rstrip("/")
        headers = {}
        key = api_key if api_key is not None else os.getenv("KB_API_KEY", "")
        if key:
            headers["X-API-Key"] = key
        self.retries = retries
        self.requester = requester
        self.http = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout_s,
                                 transport=transport)

    def _post(self, path: str, payload: dict) -> dict:
        last = ""
        for attempt in range(self.retries + 1):
            try:
                r = self.http.post(path, json=payload)
            except httpx.HTTPError as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                if r.status_code < 400:
                    return r.json()
                last = f"HTTP {r.status_code}: {r.text[:200]}"
                if r.status_code < 500 and r.status_code != 429:
                    break  # client error: retrying will not help
            if attempt < self.retries:
                time.sleep(0.5 * (2 ** attempt))
        raise KnowledgeUnavailable(f"Knowledge Brain request to {path} failed: {last}")

    def query(self, question: str, *, escalate: bool = True, model: str | None = None) -> KBAnswer:
        payload: dict = {"question": question[:2000], "requester": self.requester, "escalate": escalate}
        if model:
            payload["model"] = model
        data = self._post("/query", payload)
        try:
            return KBAnswer.model_validate(data)
        except ValidationError as exc:
            raise KnowledgeUnavailable(f"Unexpected /query response: {exc}") from exc

    def search(self, query: str, k: int = 5) -> list[KBSearchHit]:
        data = self._post("/search", {"query": query[:2000], "k": k})
        return [KBSearchHit.model_validate(h) for h in data]

    def escalate(self, question: str, reason: str) -> int:
        data = self._post("/escalations", {"question": question[:2000], "reason": reason[:500],
                                           "requester": self.requester})
        return int(data["id"])

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        try:
            r = self.http.get(path, params=params)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as exc:
            raise KnowledgeUnavailable(f"Knowledge Brain request to {path} failed: {exc}") from exc

    def list_escalations(self, status: str | None = None) -> list[dict]:
        return self._get("/escalations", {"status": status} if status else None)  # type: ignore[return-value]

    def resolve_escalation(self, escalation_id: int, answer: str, expert: str, scope: str = "general",
                           source_url: str | None = None, expires_at: str | None = None) -> dict:
        return self._post(f"/escalations/{escalation_id}/resolve",
                          {"answer": answer, "expert": expert, "scope": scope, "source_url": source_url,
                           "expires_at": expires_at})

    def ingestion_status(self) -> dict:
        return self._get("/ingestion/status")  # type: ignore[return-value]

    def run_ingestion(self, mode: str = "incremental", limit: int | None = None,
                      describe_images: bool = True) -> dict:
        return self._post("/ingestion/run", {"mode": mode, "limit": limit, "describe_images": describe_images})

    def health(self) -> dict:
        try:
            r = self.http.get("/health", timeout=5)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as exc:
            raise KnowledgeUnavailable(f"Knowledge Brain health check failed: {exc}") from exc
