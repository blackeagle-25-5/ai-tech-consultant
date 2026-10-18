"""Retrieval-augmented answering with citations and abstention."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from common.llm import LLM, LLMError, LLMResult
from common.prompts import PromptRegistry
from task1_knowledge_brain.retrieval.index import SearchHit

PROMPTS = PromptRegistry(Path(__file__).resolve().parent.parent / "prompts")


class AnswerDraft(BaseModel):
    status: Literal["answered", "insufficient_evidence"]
    answer: str = Field(description="Answer for the user, or a short note on what is missing.")
    cited_evidence: list[str] = Field(description="IDs like E1, E3 of the evidence used.")
    confidence: Literal["high", "medium", "low"]


def _sanitize(text: str) -> str:
    # Evidence must not be able to close our <evidence> wrapper and pose as instructions.
    return re.sub(r"</?\s*(evidence|document|system|instructions?)[^>]*>", "", text, flags=re.I)


def format_evidence(hits: list[SearchHit]) -> tuple[str, dict[str, SearchHit]]:
    labels: dict[str, SearchHit] = {}
    blocks = []
    for i, h in enumerate(hits, start=1):
        label = f"E{i}"
        labels[label] = h
        kind = {"image": "screenshot description", "expert": "expert answer"}.get(h.source_type, "article text")
        blocks.append(
            f'<evidence id="{label}" type="{kind}" title="{_sanitize(h.title)}" '
            f'section="{_sanitize(h.section)}">\n{_sanitize(h.text)}\n</evidence>')
    return "\n\n".join(blocks), labels


def generate_answer(llm: LLM, question: str, hits: list[SearchHit], model: str, *, omitted: list[str] | None = None,
                    ) -> tuple[LLMResult[AnswerDraft], dict[str, SearchHit], str]:
    prompt = PROMPTS.get("answer")
    evidence, labels = format_evidence(hits)
    user = f"<question>\n{_sanitize(question)}\n</question>\n\n{evidence}"
    if omitted:
        user += ("\n\n<review_note>A previous answer to this question left out these items that the evidence lists: "
                 + ", ".join(omitted) + ". Include each of them if it answers the question.</review_note>")
    result = llm.structured(schema=AnswerDraft, system=prompt.system, user=user, model=model,
                            purpose="kb_answer", prompt_version=prompt.id, max_tokens=3000, effort="low")
    return result, labels, prompt.id


def validated_citations(draft: AnswerDraft, labels: dict[str, SearchHit]) -> list[SearchHit]:
    """Keep only citations that point at evidence we actually supplied."""
    seen: set[str] = set()
    cited = []
    for label in draft.cited_evidence:
        label = label.strip().upper()
        if label in labels and label not in seen:
            seen.add(label)
            cited.append(labels[label])
    return cited


__all__ = ["AnswerDraft", "LLMError", "generate_answer", "validated_citations", "format_evidence"]
