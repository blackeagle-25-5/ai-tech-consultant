"""Agent 5 - Proposal & Review: narrative by the model, numbers and tables by code, then a consistency review."""

from __future__ import annotations

import re

from common.llm import LLM
from task3_meeting_to_scope.agents.base import call, dump
from task3_meeting_to_scope.agents.estimator import EstimateSummary, Pricing, deliverable_price
from task3_meeting_to_scope.schemas import (
    FeasibilityResult, ProjectBrief, ProposalNarrative, ReviewFinding, ReviewReport, ScopeOfWork,
)

FEAS_LABEL = {
    "existing_configuration": "Existing setting",
    "existing_feature_with_limitation": "Existing feature (with limits)",
    "custom_development": "Custom build",
    "needs_clarification": "To be confirmed",
}
MONEY = re.compile(r"[$€£₹]\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s?(?:usd|dollars|eur|euros|inr)\b", re.I)


def write_narrative(llm: LLM, client: str, brief: ProjectBrief, scope: ScopeOfWork,
                    feasibility: list[FeasibilityResult], model: str) -> tuple[ProposalNarrative, float]:
    user = (f"Client: {client}\n<project_brief>\n{dump(brief)}\n</project_brief>\n"
            f"<scope_of_work>\n{dump(scope)}\n</scope_of_work>\n<feasibility>\n{dump(feasibility)}\n</feasibility>")
    res = call(llm, "proposal", ProposalNarrative, user, model, effort="medium")
    return res.value, res.record.cost_usd


def deliverable_feasibility(scope: ScopeOfWork, feasibility: list[FeasibilityResult]) -> dict[str, str]:
    """A deliverable is only as certain as its least certain requirement."""
    order = ["needs_clarification", "custom_development", "existing_feature_with_limitation", "existing_configuration"]
    by_req = {f.req_id: f.classification for f in feasibility}
    out = {}
    for d in scope.deliverables:
        classes = [by_req.get(r, "needs_clarification") for r in d.req_ids] or ["needs_clarification"]
        out[d.id] = min(classes, key=order.index)
    return out


def render(client: str, brief: ProjectBrief, scope: ScopeOfWork, feasibility: list[FeasibilityResult],
           est: EstimateSummary, narrative: ProposalNarrative, pricing: Pricing, version_label: str) -> str:
    feas = deliverable_feasibility(scope, feasibility)
    cur = est.currency
    lines = [f"# Proposal for {client}", f"*{version_label} - DRAFT until approved by our team*", "",
             "## Executive summary", narrative.executive_summary, "",
             "## The problem and our solution", narrative.problem_and_solution, "",
             "## Scope and deliverables", ""]
    for d in scope.deliverables:
        tag = " (optional)" if d.optional else ""
        lines.append(f"### {d.id}. {d.title}{tag} - {FEAS_LABEL[feas[d.id]]}")
        lines.append(d.solution)
        lines.append("Acceptance criteria:")
        lines += [f"- {c}" for c in d.acceptance_criteria]
        lines.append("")
    lines += ["## Estimated effort and price", "",
              f"| Deliverable | Low h | Likely h | High h | Price ({cur}) |", "|---|---|---|---|---|"]
    for d in scope.deliverables:
        row = est.by_deliverable.get(d.id, {"low": 0, "likely": 0, "high": 0})
        tag = " (optional)" if d.optional else ""
        lines.append(f"| {d.id} {d.title}{tag} | {row['low']:g} | {row['likely']:g} | {row['high']:g} | "
                     f"{deliverable_price(est, d.id, pricing):,.2f} |")
    lines += ["", f"**Core scope (excluding optional items):** {est.low:g}-{est.high:g} hours, "
                  f"expected {est.expected:g} h + {pricing.contingency_pct:g}% contingency = {est.with_contingency:g} h.",
              f"**Draft price:** {cur} {est.price_before_tax:,.2f} before tax"
              + (f", {cur} {est.price_with_tax:,.2f} including tax." if pricing.tax_rate else "."),
              f"Pricing assumptions: {cur} {pricing.hourly_rate:g}/hour. Items marked *To be confirmed* may change "
              "after investigation. This is an estimate, not a fixed quote.", "",
              "## Timeline and milestones", narrative.timeline_and_milestones, "",
              "## Client responsibilities"] + [f"- {x}" for x in scope.client_responsibilities] + [
              "", "## Exclusions"] + [f"- {x}" for x in scope.exclusions] + [
              "", "## Assumptions and dependencies"] + [f"- {x}" for x in scope.assumptions + scope.dependencies] + [
              "", "## Risks and open questions", narrative.risks_and_open_questions] + [
              f"- {q}" for q in scope.open_questions] + [
              "", "## Approval and next steps", narrative.next_steps]
    return "\n".join(lines)


def review(llm: LLM, brief: ProjectBrief, scope: ScopeOfWork, feasibility: list[FeasibilityResult],
           narrative: ProposalNarrative, est: EstimateSummary, model: str) -> tuple[ReviewReport, float]:
    findings = deterministic_checks(narrative, scope, feasibility, est)
    user = (f"<project_brief>\n{dump(brief)}\n</project_brief>\n<scope_of_work>\n{dump(scope)}\n</scope_of_work>\n"
            f"<feasibility>\n{dump(feasibility)}\n</feasibility>\n<proposal_narrative>\n{dump(narrative)}\n"
            "</proposal_narrative>")
    res = call(llm, "reviewer", ReviewReport, user, model, effort="medium")
    return ReviewReport(findings=findings + res.value.findings), res.record.cost_usd


def deterministic_checks(narrative: ProposalNarrative, scope: ScopeOfWork, feasibility: list[FeasibilityResult],
                         est: EstimateSummary) -> list[ReviewFinding]:
    findings = []
    text = " ".join(narrative.model_dump().values())
    if MONEY.search(text):
        findings.append(ReviewFinding(severity="blocker",
                                      issue=f"Narrative states a money amount ('{MONEY.search(text).group(0)}'); "
                                            "prices must come only from the estimate table."))
    for d in scope.deliverables:
        if d.id not in est.by_deliverable:
            findings.append(ReviewFinding(severity="blocker", issue=f"{d.id} has no estimate."))
    unclear = [f.req_id for f in feasibility if f.classification == "needs_clarification"]
    if unclear:
        findings.append(ReviewFinding(severity="warning",
                                      issue=f"Requirements needing clarification before commitment: {', '.join(unclear)}"))
    return findings
