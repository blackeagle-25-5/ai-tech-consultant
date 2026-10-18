"""Structured hand-offs between the five agents. Every stage's output is validated against these."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Priority = Literal["must_have", "should_have", "optional"]
ReqStatus = Literal["confirmed", "unclear", "disputed"]
Feasibility = Literal["existing_configuration", "existing_feature_with_limitation",
                      "custom_development", "needs_clarification"]


class SourceRef(BaseModel):
    meeting_id: str
    line: int = Field(description="Line number of the quoted turn, as shown in the transcript")
    quote: str = Field(description="Short exact quote from that line")
    verified: bool = True  # set by code after checking the quote against the transcript


# ---------------------------------------------------------------- Agent 1: Transcript Analyst
class ExtractedRequirement(BaseModel):
    title: str = Field(description="Short name, e.g. 'Filter search by specialty'")
    description: str
    origin: Literal["explicit", "inferred"] = Field(description="explicit = client asked; inferred = our reading")
    status: ReqStatus
    priority: Priority
    sources: list[SourceRef]


class ExtractedItem(BaseModel):
    text: str
    sources: list[SourceRef] = []


class ChunkExtraction(BaseModel):
    requirements: list[ExtractedRequirement]
    decisions: list[ExtractedItem]
    constraints: list[ExtractedItem] = Field(description="Budget, deadlines, technical or business limits")
    open_questions: list[ExtractedItem]
    client_commitments: list[ExtractedItem] = Field(description="Things the client promised to provide or do")
    exclusions: list[ExtractedItem] = Field(description="Things explicitly left out")


class Requirement(ExtractedRequirement):
    id: str = Field(description="Stable id like REQ-001")
    acceptance_criteria: list[str] = []


class ProjectBrief(BaseModel):
    """Merged, project-level view of one or more meetings."""
    summary: str
    requirements: list[Requirement]
    decisions: list[ExtractedItem]
    constraints: list[ExtractedItem]
    open_questions: list[ExtractedItem]
    client_commitments: list[ExtractedItem]
    exclusions: list[ExtractedItem]
    budget: str | None = Field(description="As stated by the client, verbatim-ish, or null")
    deadline: str | None = Field(description="As stated by the client, or null")


# ---------------------------------------------------------------- Agent 2: Product Feasibility
class FeasibilityJudgement(BaseModel):
    classification: Feasibility
    explanation: str = Field(description="One or two sentences grounded in the help-center answer")
    inherently_custom: bool = Field(description="True only if the need is clearly bespoke (e.g. a third-party "
                                                "integration or new workflow no help center would document)")


class FeasibilityResult(BaseModel):
    req_id: str
    classification: Feasibility
    explanation: str
    kb_status: str
    kb_question: str
    sources: list[dict] = []


# ---------------------------------------------------------------- Agent 3: Scope
class Deliverable(BaseModel):
    id: str = Field(description="D1, D2, ...")
    title: str
    req_ids: list[str]
    solution: str = Field(description="How we will deliver it: setting, configuration or custom build")
    acceptance_criteria: list[str] = Field(description="Testable statements the client can check")
    optional: bool = False


class ScopeOfWork(BaseModel):
    objective: str
    current_problem: str
    deliverables: list[Deliverable]
    exclusions: list[str]
    assumptions: list[str]
    dependencies: list[str]
    client_responsibilities: list[str]
    open_questions: list[str]
    risks: list[str]


# ---------------------------------------------------------------- Agent 4: Estimation
class EstimateTask(BaseModel):
    deliverable_id: str
    name: str
    low_hours: float = Field(ge=0)
    likely_hours: float = Field(ge=0)
    high_hours: float = Field(ge=0)
    assumptions: str
    confidence: Literal["high", "medium", "low"]


class EstimateDraft(BaseModel):
    tasks: list[EstimateTask]
    notes: str = Field(description="Main uncertainties a senior should review")


# ---------------------------------------------------------------- Agent 5: Proposal & Review
class ProposalNarrative(BaseModel):
    executive_summary: str
    problem_and_solution: str
    timeline_and_milestones: str = Field(description="Phases, without promising dates the client has not agreed")
    risks_and_open_questions: str
    next_steps: str


class ReviewFinding(BaseModel):
    severity: Literal["blocker", "warning"]
    issue: str


class ReviewReport(BaseModel):
    findings: list[ReviewFinding]


# ---------------------------------------------------------------- Follow-up comparison
class RequirementMapping(BaseModel):
    followup_title: str
    relation: Literal["same", "modified", "new"]
    baseline_id: str | None = Field(description="Baseline REQ id for same/modified, else null")
    change: str = Field(description="What changed, for 'modified'; empty otherwise")


class RemovedRequirement(BaseModel):
    baseline_id: str
    reason: str
    sources: list[SourceRef] = []


class FollowupMapping(BaseModel):
    mappings: list[RequirementMapping]
    removed: list[RemovedRequirement]
    deadline_change: str | None = Field(description="New deadline if the client changed it, else null")
    budget_change: str | None
