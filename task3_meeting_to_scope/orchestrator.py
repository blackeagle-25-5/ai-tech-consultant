"""Runs the agent team: Analyst -> Feasibility -> Scope -> Estimator -> Proposal & Review.

Each hand-off is a validated Pydantic object; code-level checks (quote verification, coverage, arithmetic)
run between stages and are stored as review findings for the human approver.
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field

from common.kb_client import KnowledgeClient
from common.llm import HAIKU, LLM
from task3_meeting_to_scope import transcript as tx
from task3_meeting_to_scope.agents import analyst, estimator, feasibility, proposal, scope
from task3_meeting_to_scope.agents.base import PROMPTS
from task3_meeting_to_scope.diff import apply_mapping, hours_delta, map_followup
from task3_meeting_to_scope.schemas import FeasibilityResult, ProjectBrief, ReviewFinding
from task3_meeting_to_scope.store import ScopeStore


@dataclass
class Models:
    analyst: str = os.getenv("SCOPE_ANALYST_MODEL", HAIKU)
    feasibility: str = os.getenv("SCOPE_FEASIBILITY_MODEL", HAIKU)
    scope: str = os.getenv("SCOPE_SCOPE_MODEL", HAIKU)
    estimator: str = os.getenv("SCOPE_ESTIMATOR_MODEL", HAIKU)
    proposal: str = os.getenv("SCOPE_PROPOSAL_MODEL", HAIKU)


@dataclass
class RunStats:
    cost_usd: float = 0.0
    seconds: dict[str, float] = field(default_factory=dict)

    def timed(self, stage: str, start: float) -> None:
        self.seconds[stage] = round(time.perf_counter() - start, 2)


class ScopePipeline:
    def __init__(self, store: ScopeStore, llm: LLM, kb: KnowledgeClient, models: Models | None = None,
                 pricing: estimator.Pricing | None = None, max_chunk_chars: int = 6000) -> None:
        self.store = store
        self.llm = llm
        self.kb = kb
        self.models = models or Models()
        self.pricing = pricing or estimator.Pricing.load()
        self.max_chunk_chars = max_chunk_chars

    def _register(self, project_id: str, client: str, transcripts: list[tx.Transcript], kind: str) -> None:
        self.store.ensure_project(project_id, client)
        for t in transcripts:
            self.store.add_meeting(t.meeting_id, project_id, t.date, kind, t.raw, t.placeholder)

    def _downstream(self, client: str, brief: ProjectBrief, feas: list[FeasibilityResult], stats: RunStats,
                    issues: list[str], version_label: str) -> dict:
        m = self.models
        start = time.perf_counter()
        sow, scope_issues, c = scope.build(self.llm, brief, feas, m.scope)
        stats.cost_usd += c
        stats.timed("scope", start)

        start = time.perf_counter()
        draft, est_issues, c = estimator.estimate(self.llm, sow, feas, m.estimator)
        stats.cost_usd += c
        summary = estimator.summarize(draft, sow, self.pricing)
        stats.timed("estimate", start)

        start = time.perf_counter()
        narrative, c = proposal.write_narrative(self.llm, client, brief, sow, feas, m.proposal)
        stats.cost_usd += c
        review, c = proposal.review(self.llm, brief, sow, feas, narrative, summary, m.proposal)
        stats.cost_usd += c
        for issue in issues + scope_issues + est_issues:
            review.findings.append(ReviewFinding(severity="warning", issue=issue))
        markdown = proposal.render(client, brief, sow, feas, summary, narrative, self.pricing, version_label)
        stats.timed("proposal_and_review", start)
        return {
            "scope": sow.model_dump(), "estimate_tasks": [t.model_dump() for t in draft.tasks],
            "estimate": {k: v for k, v in asdict(summary).items() if k != "tasks"},
            "narrative": narrative.model_dump(), "review": review.model_dump(), "proposal_markdown": markdown,
        }

    def _payload(self, brief: ProjectBrief, feas: list[FeasibilityResult], rest: dict, stats: RunStats,
                 placeholder: bool, change_report: dict | None = None) -> dict:
        return {
            "brief": brief.model_dump(), "feasibility": [f.model_dump() for f in feas], **rest,
            "change_report": change_report, "placeholder_transcripts": placeholder,
            "models": asdict(self.models),
            "prompt_versions": {n: PROMPTS.active_version(n) for n in
                                ("analyst_extract", "analyst_merge", "feasibility", "scope", "estimator",
                                 "proposal", "reviewer", "followup_mapper")},
            "stats": asdict(stats),
        }

    # ------------------------------------------------------------------ initial meeting(s)
    def run_initial(self, project_id: str, client: str, transcripts: list[tx.Transcript]) -> int:
        stats = RunStats()
        self._register(project_id, client, transcripts, "initial")
        start = time.perf_counter()
        brief, issues, c = analyst.analyse(self.llm, transcripts, self.models.analyst,
                                           max_chars=self.max_chunk_chars)
        stats.cost_usd += c
        stats.timed("analyst", start)

        start = time.perf_counter()
        feas, c = feasibility.check_all(self.llm, self.kb, brief.requirements, self.models.feasibility)
        stats.cost_usd += c
        stats.timed("feasibility", start)

        number = len(self.store.versions(project_id)) + 1
        rest = self._downstream(client, brief, feas, stats, issues, f"Version {number}")
        payload = self._payload(brief, feas, rest, stats, any(t.placeholder for t in transcripts))
        return self.store.add_version(project_id, payload, [t.meeting_id for t in transcripts], based_on=None)

    # ------------------------------------------------------------------ follow-up meeting
    def run_followup(self, project_id: str, client: str, transcript: tx.Transcript) -> int:
        baseline_v = self.store.approved_baseline(project_id)
        if baseline_v is None:
            raise ValueError("No approved baseline: approve the first scope before comparing a follow-up meeting.")
        stats = RunStats()
        self._register(project_id, client, [transcript], "follow_up")
        baseline = ProjectBrief.model_validate(baseline_v["payload"]["brief"])

        start = time.perf_counter()
        followup_brief, issues, c = analyst.analyse(self.llm, [transcript], self.models.analyst,
                                                    max_chars=self.max_chunk_chars)
        stats.cost_usd += c
        mapping, c = map_followup(self.llm, baseline, followup_brief, transcript, self.models.analyst)
        stats.cost_usd += c
        requirements, report = apply_mapping(baseline, followup_brief, mapping)
        stats.timed("analyst_and_diff", start)

        revised = baseline.model_copy(update={
            "requirements": requirements,
            "deadline": mapping.deadline_change or baseline.deadline,
            "budget": mapping.budget_change or baseline.budget,
            "decisions": baseline.decisions + followup_brief.decisions,
            "constraints": baseline.constraints + followup_brief.constraints,
            "open_questions": followup_brief.open_questions,
            "client_commitments": baseline.client_commitments + followup_brief.client_commitments,
            "exclusions": baseline.exclusions + followup_brief.exclusions,
        })
        # Out-of-scope additions stay visible but are marked optional until separately quoted and approved.
        oos_ids = {o["id"] for o in report.out_of_scope}
        for r in revised.requirements:
            if r.id in oos_ids:
                r.priority = "optional"
                r.description += " [Change request: outside the approved scope - quote separately]"

        start = time.perf_counter()
        old_feas = {f["req_id"]: FeasibilityResult.model_validate(f) for f in baseline_v["payload"]["feasibility"]}
        changed = {m["id"] for m in report.modified} | oos_ids
        to_check = [r for r in revised.requirements if r.id in changed or r.id not in old_feas]
        new_feas, c = feasibility.check_all(self.llm, self.kb, to_check, self.models.feasibility)
        stats.cost_usd += c
        feas_by_id = {**{k: v for k, v in old_feas.items() if k in {r.id for r in revised.requirements}},
                      **{f.req_id: f for f in new_feas}}
        feas = [feas_by_id[r.id] for r in revised.requirements if r.id in feas_by_id]
        stats.timed("feasibility", start)

        number = len(self.store.versions(project_id)) + 1
        rest = self._downstream(client, revised, feas, stats, issues,
                                f"Version {number} (revision of approved v{baseline_v['number']})")
        report.hours_delta = hours_delta(baseline_v["payload"]["estimate"], rest["estimate"])
        payload = self._payload(revised, feas, rest, stats, transcript.placeholder, report.to_dict())
        return self.store.add_version(project_id, payload, [transcript.meeting_id], based_on=baseline_v["id"])


def build_pipeline(store: ScopeStore | None = None, llm: LLM | None = None, kb: KnowledgeClient | None = None,
                   **kwargs) -> ScopePipeline:
    return ScopePipeline(store or ScopeStore(), llm or LLM(), kb or KnowledgeClient(requester="scope-builder"),
                         **kwargs)

