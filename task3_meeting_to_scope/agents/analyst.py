"""Agent 1 - Transcript Analyst: staged extraction for long transcripts, merge, and quote verification."""

from __future__ import annotations

from pydantic import BaseModel, Field

from common.llm import LLM
from task3_meeting_to_scope.agents.base import call, dump
from task3_meeting_to_scope.schemas import (
    ChunkExtraction, ExtractedItem, ExtractedRequirement, ProjectBrief, Requirement,
)
from task3_meeting_to_scope.transcript import Transcript, chunk, verify_quote


class MergedBrief(BaseModel):
    summary: str
    requirements: list[ExtractedRequirement]
    decisions: list[ExtractedItem]
    constraints: list[ExtractedItem]
    open_questions: list[ExtractedItem]
    client_commitments: list[ExtractedItem]
    exclusions: list[ExtractedItem]
    budget: str | None = Field(description="As the client stated it, or null")
    deadline: str | None = Field(description="As the client stated it, or null")


def extract(llm: LLM, transcript: Transcript, model: str, max_chars: int = 6000) -> tuple[list[ChunkExtraction], float]:
    """Stage 1: extract from each overlapping section separately (keeps every call small and focused)."""
    results, cost = [], 0.0
    sections = chunk(transcript, max_chars=max_chars)
    for i, turns in enumerate(sections, start=1):
        user = (f"Meeting id: {transcript.meeting_id} (date {transcript.date or 'unknown'}), "
                f"section {i} of {len(sections)}.\n<transcript>\n{transcript.render(turns)}\n</transcript>")
        res = call(llm, "analyst_extract", ChunkExtraction, user, model)
        for item in _all_sources(res.value):
            item.meeting_id = transcript.meeting_id
        results.append(res.value)
        cost += res.record.cost_usd
    return results, cost


def merge(llm: LLM, extractions: list[ChunkExtraction], model: str, *, first_id: int = 1) -> tuple[ProjectBrief, float]:
    """Stage 2: merge section results into one brief; ids are assigned in code so they stay stable."""
    user = "\n\n".join(f"<section_extraction n=\"{i}\">\n{dump(e)}\n</section_extraction>"
                       for i, e in enumerate(extractions, start=1))
    res = call(llm, "analyst_merge", MergedBrief, user, model)
    merged, cost = res.value, res.record.cost_usd
    reqs = [Requirement(id=f"REQ-{first_id + i:03d}", **r.model_dump()) for i, r in enumerate(merged.requirements)]
    brief = ProjectBrief(summary=merged.summary, requirements=reqs, decisions=merged.decisions,
                         constraints=merged.constraints, open_questions=merged.open_questions,
                         client_commitments=merged.client_commitments, exclusions=merged.exclusions,
                         budget=merged.budget, deadline=merged.deadline)
    return brief, cost


def verify(brief: ProjectBrief, transcripts: dict[str, Transcript]) -> list[str]:
    """Stage 3: check every cited quote against the transcript. Unverifiable explicit requirements become 'unclear'."""
    problems = []
    for item in _all_sources(brief):
        t = transcripts.get(item.meeting_id)
        item.verified = bool(t and verify_quote(t, item.line, item.quote))
    for req in brief.requirements:
        if not any(s.verified for s in req.sources):
            problems.append(f"{req.id} '{req.title}' has no verifiable quote")
            if req.origin == "explicit" and req.status == "confirmed":
                req.status = "unclear"
    return problems


def analyse(llm: LLM, transcripts: list[Transcript], model: str, *, first_id: int = 1,
            max_chars: int = 6000) -> tuple[ProjectBrief, list[str], float]:
    extractions, cost = [], 0.0
    for t in transcripts:
        ex, c = extract(llm, t, model, max_chars=max_chars)
        extractions.extend(ex)
        cost += c
    brief, c = merge(llm, extractions, model, first_id=first_id)
    problems = verify(brief, {t.meeting_id: t for t in transcripts})
    return brief, problems, cost + c


def _all_sources(obj):
    """Yield every SourceRef inside an extraction or brief."""
    for field in ("requirements", "decisions", "constraints", "open_questions", "client_commitments", "exclusions"):
        for item in getattr(obj, field, []):
            yield from item.sources
