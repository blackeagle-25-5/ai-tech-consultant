"""Shared helpers for the Meeting-to-Scope agents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from common.llm import LLM, LLMResult
from common.prompts import PromptRegistry

PROMPTS = PromptRegistry(Path(__file__).resolve().parent.parent / "prompts")
T = TypeVar("T", bound=BaseModel)


def call(llm: LLM, prompt_name: str, schema: type[T], user: str, model: str, *, effort: str = "low",
         max_tokens: int = 8000) -> LLMResult[T]:
    prompt = PROMPTS.get(prompt_name)
    return llm.structured(schema=schema, system=prompt.system, user=user, model=model,
                          purpose=f"scope_{prompt_name}", prompt_version=prompt.id, max_tokens=max_tokens,
                          effort=effort, schema_retries=1)


def dump(obj) -> str:
    """Compact JSON for passing one agent's validated output to the next."""
    if isinstance(obj, BaseModel):
        return obj.model_dump_json(indent=1)
    return json.dumps(obj, indent=1, default=lambda o: o.model_dump() if isinstance(o, BaseModel) else str(o))


def compact_requirements(brief) -> str:
    """Requirements without source quotes: agents after the Analyst do not need them (they stay in the stored brief)."""
    data = {"summary": brief.summary, "budget": brief.budget, "deadline": brief.deadline,
            "requirements": [{"id": r.id, "title": r.title, "description": r.description, "priority": r.priority,
                              "status": r.status, "origin": r.origin} for r in brief.requirements],
            "decisions": [x.text for x in brief.decisions], "constraints": [x.text for x in brief.constraints],
            "open_questions": [x.text for x in brief.open_questions],
            "client_commitments": [x.text for x in brief.client_commitments],
            "exclusions": [x.text for x in brief.exclusions]}
    return json.dumps(data, separators=(",", ":"))


def compact_feasibility(feasibility) -> str:
    return json.dumps([{"req_id": f.req_id, "classification": f.classification, "explanation": f.explanation}
                       for f in feasibility], separators=(",", ":"))
