"""Agent 2 - Product Feasibility: checks each requirement against the Knowledge Brain (Task 1) over HTTP."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from common.kb_client import KnowledgeClient, KnowledgeUnavailable
from common.llm import LLM, LLMError
from task3_meeting_to_scope.agents.base import call
from task3_meeting_to_scope.schemas import FeasibilityJudgement, FeasibilityResult, Requirement


def kb_question(req: Requirement) -> str:
    return (f"Can Brilliant Directories do this, and how is it set up? {req.title}: {req.description} "
            "Mention any limitations.")


def check(llm: LLM, kb: KnowledgeClient, req: Requirement, model: str) -> tuple[FeasibilityResult, float]:
    question = kb_question(req)
    try:
        answer = kb.query(question, escalate=False)
    except KnowledgeUnavailable as exc:
        return FeasibilityResult(req_id=req.id, classification="needs_clarification",
                                 explanation=f"Knowledge service unavailable ({exc}); check manually.",
                                 kb_status="unavailable", kb_question=question), 0.0
    sources = [{"title": s.title, "url": s.url} for s in answer.sources]
    user = (f"<requirement id=\"{req.id}\">\n{req.title}: {req.description}\n</requirement>\n"
            f"<help_center status=\"{answer.status}\" confidence=\"{answer.confidence}\">\n{answer.answer}\n"
            + "\n".join(f"Source: {s['title']} - {s['url']}" for s in sources) + "\n</help_center>")
    try:
        res = call(llm, "feasibility", FeasibilityJudgement, user, model)
    except LLMError as exc:  # one failed check must not stop the whole proposal
        return FeasibilityResult(req_id=req.id, classification="needs_clarification",
                                 explanation=f"Automatic check failed ({str(exc)[:120]}); check manually.",
                                 kb_status=answer.status, kb_question=question, sources=sources), answer.cost_usd
    j = res.value
    classification = j.classification
    # Missing evidence is not proof of absence: only the evidence can justify "existing", and only an
    # inherently bespoke need can be called custom without evidence.
    if answer.status != "answered" and classification in ("existing_configuration", "existing_feature_with_limitation"):
        classification = "needs_clarification"
    if answer.status != "answered" and classification == "custom_development" and not j.inherently_custom:
        classification = "needs_clarification"
    return FeasibilityResult(req_id=req.id, classification=classification, explanation=j.explanation,
                             kb_status=answer.status, kb_question=question, sources=sources), \
        res.record.cost_usd + answer.cost_usd


def check_all(llm: LLM, kb: KnowledgeClient, reqs: list[Requirement], model: str,
              workers: int = int(os.getenv("SCOPE_FEASIBILITY_WORKERS", "2"))) -> tuple[list[FeasibilityResult], float]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda r: check(llm, kb, r, model), reqs))
    return [r for r, _ in results], sum(c for _, c in results)
