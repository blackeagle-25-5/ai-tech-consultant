from __future__ import annotations

import re

import httpx
import pytest

import common.llm
from common.kb_client import KnowledgeClient
from common.llm import LLM
from common.testing import FakeAnthropic
from task3_meeting_to_scope.agents.analyst import MergedBrief
from task3_meeting_to_scope.agents.estimator import Pricing
from task3_meeting_to_scope.orchestrator import ScopePipeline
from task3_meeting_to_scope.schemas import (
    ChunkExtraction, Deliverable, EstimateDraft, EstimateTask, ExtractedRequirement, FeasibilityJudgement,
    FollowupMapping, ProposalNarrative, RemovedRequirement, RequirementMapping, ReviewReport, ScopeOfWork, SourceRef,
)
from task3_meeting_to_scope.store import ScopeStore
from task3_meeting_to_scope.transcript import parse

MEETING_A = """[00:00:01] Consultant: What do you need?
[00:00:05] Client: We want filters for specialty and price on the search page.
[00:00:12] Client: Trainers want their Instagram feed on their profile.
[00:00:20] Client: Maybe add some AI to the search, it's a nice to have.
[00:00:30] Client: Launch by December fifteenth. Budget around five thousand dollars.
"""
MEETING_C = """[00:00:01] Consultant: What changed?
[00:00:04] Client: Forget the Instagram feed, remove it.
[00:00:09] Client: For filters, add languages spoken as well.
[00:00:15] Client: We also want a book a free intro session button.
[00:00:22] Client: The launch is now January thirty first.
"""


def req(title: str, line: int, quote: str, priority: str = "must_have", meeting: str = "meeting_a"):
    return ExtractedRequirement(title=title, description=title, origin="explicit", status="confirmed",
                                priority=priority, sources=[SourceRef(meeting_id=meeting, line=line, quote=quote)])


BASE_REQS = [req("Search filters", 2, "filters for specialty and price"),
             req("Instagram feed", 3, "Instagram feed on their profile", "should_have"),
             req("AI search", 4, "some AI to the search", "optional")]
FOLLOWUP_REQS = [req("Search filters incl. languages", 3, "add languages spoken", meeting="meeting_c"),
                 req("Book intro session button", 4, "book a free intro session button", meeting="meeting_c")]


class Responder:
    """Answers each agent according to the schema it asks for."""

    def __init__(self, base_reqs: list[ExtractedRequirement] | None = None) -> None:
        self.base_reqs = base_reqs or BASE_REQS
        self.followup = False
        self.narrative_text = "We will improve search so visitors find trainers faster."

    def __call__(self, kwargs):
        schema = kwargs["output_format"]
        user = kwargs["messages"][0]["content"][0]["text"]
        reqs = FOLLOWUP_REQS if self.followup else self.base_reqs
        if schema is ChunkExtraction:
            return ChunkExtraction(requirements=reqs, decisions=[], constraints=[], open_questions=[],
                                   client_commitments=[], exclusions=[])
        if schema is MergedBrief:
            return MergedBrief(summary="Trainer directory wants better search.", requirements=reqs, decisions=[],
                               constraints=[], open_questions=[], client_commitments=[], exclusions=[],
                               budget="around five thousand dollars",
                               deadline=None if self.followup else "December fifteenth")
        if schema is FeasibilityJudgement:
            if "Instagram" in user or "AI search" in user:
                return FeasibilityJudgement(classification="custom_development", explanation="Not documented.",
                                            inherently_custom=False)
            return FeasibilityJudgement(classification="existing_configuration",
                                        explanation="Search filters are configurable.", inherently_custom=False)
        if schema is ScopeOfWork:
            ids = list(dict.fromkeys(re.findall(r'"id":\s*"(REQ-\d+)"', user)))
            return ScopeOfWork(objective="Better search", current_problem="Hard to find trainers",
                               deliverables=[Deliverable(id=f"D{i}", title=f"Deliverable for {rid}", req_ids=[rid],
                                                         solution="Configure", acceptance_criteria=["It works"],
                                                         optional=False) for i, rid in enumerate(ids, 1)],
                               exclusions=["Mobile app"], assumptions=[], dependencies=[],
                               client_responsibilities=["Provide admin access"], open_questions=[], risks=[])
        if schema is EstimateDraft:
            n = len(re.findall(r'"title":\s*"Deliverable for', user))
            return EstimateDraft(tasks=[EstimateTask(deliverable_id=f"D{i}", name="Build", low_hours=2,
                                                     likely_hours=4, high_hours=6, assumptions="", confidence="medium")
                                        for i in range(1, n + 1)], notes="")
        if schema is ProposalNarrative:
            return ProposalNarrative(executive_summary=self.narrative_text, problem_and_solution="Filters.",
                                     timeline_and_milestones="Setup, build, test, launch.",
                                     risks_and_open_questions="Price ranges.", next_steps="Approve.")
        if schema is ReviewReport:
            return ReviewReport(findings=[])
        if schema is FollowupMapping:
            return FollowupMapping(
                mappings=[RequirementMapping(followup_title="Search filters incl. languages", relation="modified",
                                             baseline_id="REQ-001", change="adds languages spoken"),
                          RequirementMapping(followup_title="Book intro session button", relation="new",
                                             baseline_id=None, change="")],
                removed=[RemovedRequirement(baseline_id="REQ-002", reason="trainers don't update Instagram",
                                            sources=[SourceRef(meeting_id="meeting_c", line=2,
                                                               quote="Forget the Instagram feed")])],
                deadline_change="January thirty first", budget_change=None)
        raise AssertionError(f"unexpected schema {schema}")


def kb_transport(status: str = "answered", fail: bool = False) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(503)
        return httpx.Response(200, json={
            "request_id": "r", "status": status, "answer": "Use Search Settings to add filters.",
            "sources": [{"title": "Search settings", "url": "https://support.brilliantdirectories.com/a/9",
                         "section": "", "source_type": "article", "relevance": 0.8, "image_urls": []}]
            if status == "answered" else [],
            "confidence": "high", "needs_human": status != "answered", "escalation_id": None, "model": "x",
            "latency_ms": 5, "cost_usd": 0.0})
    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _ledger_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(common.llm, "LEDGER_PATH", tmp_path / "ledger.jsonl")


@pytest.fixture
def pipeline_factory(tmp_path):
    def build(responder=None, kb_status="answered", kb_fail=False) -> ScopePipeline:
        kb = KnowledgeClient(base_url="http://kb.test", api_key="", retries=0,
                             transport=kb_transport(kb_status, kb_fail))
        return ScopePipeline(ScopeStore(tmp_path / "scope.db"), LLM(client=FakeAnthropic(responder or Responder())),
                             kb, pricing=Pricing(hourly_rate=50, currency="USD", tax_rate=0.0, contingency_pct=10))
    return build


def meeting(mid: str, text: str):
    return parse(mid, text, date="2026-10-19")
