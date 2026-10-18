"""Public API contract of the Knowledge Brain. Documented in API.md."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AnswerStatus = Literal["answered", "insufficient_evidence"]


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    top_k: int = Field(default=8, ge=1, le=20)
    requester: str | None = Field(default=None, max_length=100,
                                  description="Who is asking, e.g. 'inbox-agent'. Stored on escalations.")
    escalate: bool = Field(default=True, description="Create a human task when evidence is insufficient.")
    model: Literal["haiku", "sonnet"] | None = Field(default=None, description="Override the answer model.")


class Source(BaseModel):
    title: str
    url: str
    section: str = ""
    source_type: Literal["article", "image", "expert"]
    relevance: float = Field(description="Cosine similarity between question and passage, 0-1.")
    image_urls: list[str] = []


class QueryResponse(BaseModel):
    request_id: str
    status: AnswerStatus
    answer: str
    sources: list[Source]
    confidence: Literal["high", "medium", "low"] = Field(
        description="Model's self-reported support level; see EVALS.md for how well it tracks accuracy.")
    needs_human: bool
    escalation_id: int | None = None
    model: str | None = None
    prompt_version: str | None = None
    safety_notes: list[str] = Field(default_factory=list,
        description="Why an answer was declined or downgraded by the deterministic safety checks.")
    latency_ms: int
    cost_usd: float


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    k: int = Field(default=8, ge=1, le=50)
    mode: Literal["hybrid", "vector", "keyword"] = "hybrid"


class SearchResult(BaseModel):
    chunk_id: str
    title: str
    url: str
    section: str
    source_type: str
    text: str
    image_urls: list[str]
    similarity: float


class IngestionRunRequest(BaseModel):
    mode: Literal["incremental", "full"] = "incremental"
    limit: int | None = Field(default=None, ge=1, le=5000)
    describe_images: bool = True


class EscalationCreate(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    reason: str = Field(default="manual", max_length=500)
    requester: str | None = Field(default=None, max_length=100)


class EscalationResolve(BaseModel):
    answer: str = Field(min_length=3, max_length=5000)
    expert: str = Field(min_length=1, max_length=100)
    scope: Literal["general", "customer_specific"] = Field(
        default="general",
        description="Only 'general' answers are added to the searchable knowledge.")
    source_url: str | None = None
    expires_at: str | None = Field(default=None, description="ISO date after which the answer is ignored.")


class Escalation(BaseModel):
    id: int
    question: str
    reason: str
    requester: str | None
    status: Literal["open", "resolved"]
    created_at: str
    resolved_at: str | None
    evidence: list[dict]
    answer: str | None = None
