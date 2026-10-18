"""Compare a follow-up meeting with the APPROVED baseline. The model maps items to baseline ids;
the change report itself is computed here in code so it can be tested and trusted."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from common.llm import LLM
from task3_meeting_to_scope.agents.base import call, dump
from task3_meeting_to_scope.schemas import FollowupMapping, ProjectBrief, Requirement
from task3_meeting_to_scope.transcript import Transcript


@dataclass
class ChangeReport:
    added: list[dict] = field(default_factory=list)       # new requirements -> out of scope until quoted
    modified: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)    # baseline ids carried over
    out_of_scope: list[dict] = field(default_factory=list)
    deadline_change: str | None = None
    budget_change: str | None = None
    hours_delta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def map_followup(llm: LLM, baseline: ProjectBrief, followup: ProjectBrief, transcript: Transcript,
                 model: str) -> tuple[FollowupMapping, float]:
    base_reqs = [{"id": r.id, "title": r.title, "description": r.description} for r in baseline.requirements]
    new_reqs = [{"title": r.title, "description": r.description} for r in followup.requirements]
    user = (f"<approved_baseline_requirements>\n{dump(base_reqs)}\n</approved_baseline_requirements>\n"
            f"Baseline deadline: {baseline.deadline}; baseline budget: {baseline.budget}\n"
            f"<followup_requirements>\n{dump(new_reqs)}\n</followup_requirements>\n"
            f"<followup_transcript>\n{transcript.render()}\n</followup_transcript>")
    res = call(llm, "followup_mapper", FollowupMapping, user, model, effort="medium")
    return res.value, res.record.cost_usd


def apply_mapping(baseline: ProjectBrief, followup: ProjectBrief, mapping: FollowupMapping,
                  ) -> tuple[list[Requirement], ChangeReport]:
    """Build the revised requirement list (stable ids) and the change report."""
    by_id = {r.id: r.model_copy(deep=True) for r in baseline.requirements}
    followup_by_title = {r.title: r for r in followup.requirements}
    report = ChangeReport(deadline_change=mapping.deadline_change, budget_change=mapping.budget_change)
    next_num = max((int(r.id.split("-")[1]) for r in baseline.requirements), default=0) + 1
    touched: set[str] = set()
    new_reqs: list[Requirement] = []

    for removal in mapping.removed:
        if removal.baseline_id in by_id:
            req = by_id.pop(removal.baseline_id)
            touched.add(removal.baseline_id)
            report.removed.append({"id": req.id, "title": req.title, "reason": removal.reason,
                                   "sources": [s.model_dump() for s in removal.sources]})

    for m in mapping.mappings:
        source = followup_by_title.get(m.followup_title)
        if m.relation in ("same", "modified") and m.baseline_id in by_id:
            touched.add(m.baseline_id)
            if m.relation == "modified" and source is not None:
                old = by_id[m.baseline_id]
                updated = old.model_copy(update={"description": source.description, "title": source.title,
                                                 "sources": old.sources + source.sources,
                                                 "status": source.status, "priority": source.priority})
                by_id[m.baseline_id] = updated
                report.modified.append({"id": old.id, "title": updated.title, "change": m.change,
                                        "before": old.description, "after": updated.description})
        elif m.relation == "new" and source is not None:
            req = Requirement(**{**source.model_dump(), "id": f"REQ-{next_num:03d}"})
            next_num += 1
            new_reqs.append(req)
            entry = {"id": req.id, "title": req.title, "description": req.description,
                     "sources": [s.model_dump() for s in req.sources]}
            report.added.append(entry)
            # Anything not in the approved baseline is out of scope until it is quoted and approved.
            report.out_of_scope.append({**entry, "reason": "not in the approved baseline; quote separately"})

    report.unchanged = [rid for rid in by_id if rid not in touched]
    return list(by_id.values()) + new_reqs, report


def hours_delta(baseline_estimate: dict, new_estimate: dict) -> dict:
    keys = ("low", "likely", "high", "expected", "with_contingency", "price_before_tax")
    return {k: round(new_estimate.get(k, 0) - baseline_estimate.get(k, 0), 2) for k in keys}
