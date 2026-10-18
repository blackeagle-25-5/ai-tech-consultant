"""Agent 3 - Scope: deliverables, exclusions, dependencies and testable acceptance criteria."""

from __future__ import annotations

from common.llm import LLM
from task3_meeting_to_scope.agents.base import call, compact_feasibility, compact_requirements
from task3_meeting_to_scope.schemas import FeasibilityResult, ProjectBrief, ScopeOfWork


def build(llm: LLM, brief: ProjectBrief, feasibility: list[FeasibilityResult], model: str,
          ) -> tuple[ScopeOfWork, list[str], float]:
    user = (f"<project_brief>\n{compact_requirements(brief)}\n</project_brief>\n"
            f"<feasibility>\n{compact_feasibility(feasibility)}\n</feasibility>")
    res = call(llm, "scope", ScopeOfWork, user, model, effort="medium")
    return res.value, validate(res.value, brief), res.record.cost_usd


def validate(scope: ScopeOfWork, brief: ProjectBrief) -> list[str]:
    """Every requirement must be covered by exactly one deliverable, and ids must exist."""
    issues = []
    known = {r.id for r in brief.requirements}
    seen: dict[str, str] = {}
    for d in scope.deliverables:
        if not d.acceptance_criteria:
            issues.append(f"{d.id} has no acceptance criteria")
        for rid in d.req_ids:
            if rid not in known:
                issues.append(f"{d.id} references unknown requirement {rid}")
            elif rid in seen:
                issues.append(f"{rid} appears in both {seen[rid]} and {d.id}")
            seen[rid] = d.id
    for rid in sorted(known - set(seen)):
        issues.append(f"{rid} is not covered by any deliverable")
    return issues
