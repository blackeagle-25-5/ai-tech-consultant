"""Agent 4 - Estimation: the model proposes task hours; all arithmetic and pricing happen in code."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from common.llm import LLM
from task3_meeting_to_scope.agents.base import call, dump
from task3_meeting_to_scope.schemas import EstimateDraft, EstimateTask, FeasibilityResult, ScopeOfWork

PRICING_PATH = Path(__file__).resolve().parent.parent / "pricing.yaml"


@dataclass
class Pricing:
    hourly_rate: float = 40.0
    currency: str = "USD"
    tax_rate: float = 0.0
    contingency_pct: float = 10.0

    @classmethod
    def load(cls, path: Path = PRICING_PATH) -> "Pricing":
        return cls(**yaml.safe_load(path.read_text(encoding="utf-8")))


@dataclass
class EstimateSummary:
    tasks: list[EstimateTask]
    by_deliverable: dict[str, dict[str, float]] = field(default_factory=dict)
    low: float = 0
    likely: float = 0
    high: float = 0
    expected: float = 0  # PERT: (low + 4*likely + high) / 6
    with_contingency: float = 0
    price_before_tax: float = 0
    price_with_tax: float = 0
    currency: str = "USD"
    hourly_rate: float = 0
    optional_deliverables: list[str] = field(default_factory=list)
    notes: str = ""


def estimate(llm: LLM, scope: ScopeOfWork, feasibility: list[FeasibilityResult], model: str,
             ) -> tuple[EstimateDraft, list[str], float]:
    user = f"<scope_of_work>\n{dump(scope)}\n</scope_of_work>\n<feasibility>\n{dump(feasibility)}\n</feasibility>"
    res = call(llm, "estimator", EstimateDraft, user, model, effort="medium")
    draft = res.value
    issues = []
    known = {d.id for d in scope.deliverables}
    for t in draft.tasks:
        if t.deliverable_id not in known:
            issues.append(f"Task '{t.name}' refers to unknown deliverable {t.deliverable_id}")
        ordered = sorted([t.low_hours, t.likely_hours, t.high_hours])
        if ordered != [t.low_hours, t.likely_hours, t.high_hours]:
            issues.append(f"Task '{t.name}' had low/likely/high out of order; sorted")
            t.low_hours, t.likely_hours, t.high_hours = ordered
    for d in known - {t.deliverable_id for t in draft.tasks}:
        issues.append(f"Deliverable {d} has no estimate tasks")
    return draft, issues, res.record.cost_usd


def summarize(draft: EstimateDraft, scope: ScopeOfWork, pricing: Pricing) -> EstimateSummary:
    optional = {d.id for d in scope.deliverables if d.optional}
    s = EstimateSummary(tasks=draft.tasks, currency=pricing.currency, hourly_rate=pricing.hourly_rate,
                        optional_deliverables=sorted(optional), notes=draft.notes)
    for t in draft.tasks:
        row = s.by_deliverable.setdefault(t.deliverable_id, {"low": 0, "likely": 0, "high": 0, "expected": 0})
        row["low"] += t.low_hours
        row["likely"] += t.likely_hours
        row["high"] += t.high_hours
        row["expected"] += (t.low_hours + 4 * t.likely_hours + t.high_hours) / 6
    core = {k: v for k, v in s.by_deliverable.items() if k not in optional}
    s.low = sum(v["low"] for v in core.values())
    s.likely = sum(v["likely"] for v in core.values())
    s.high = sum(v["high"] for v in core.values())
    s.expected = round(sum(v["expected"] for v in core.values()), 1)
    s.with_contingency = round(s.expected * (1 + pricing.contingency_pct / 100), 1)
    s.price_before_tax = round(s.with_contingency * pricing.hourly_rate, 2)
    s.price_with_tax = round(s.price_before_tax * (1 + pricing.tax_rate), 2)
    return s


def deliverable_price(summary: EstimateSummary, deliverable_id: str, pricing: Pricing) -> float:
    row = summary.by_deliverable.get(deliverable_id, {"expected": 0})
    return round(row["expected"] * (1 + pricing.contingency_pct / 100) * pricing.hourly_rate, 2)
