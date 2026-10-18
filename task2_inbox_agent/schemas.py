"""Data shapes for the Inbox Agent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["how_to", "troubleshooting", "project_request", "refund_cancellation",
                   "complaint", "quote_request", "follow_up", "other"]
Level = Literal["low", "medium", "high"]
Urgency = Literal["low", "normal", "high"]
Flag = Literal["legal_threat", "security_sensitive", "churn_risk", "prompt_injection",
               "financial_request", "personal_data"]
CATEGORIES: tuple[str, ...] = Category.__args__  # type: ignore[attr-defined]


class Email(BaseModel):
    id: str
    from_name: str
    from_email: str
    subject: str
    body: str
    received_at: str | None = None

    @property
    def thread_key(self) -> str:
        subject = self.subject.strip()
        while subject.lower().startswith(("re:", "fw:", "fwd:")):
            subject = subject.split(":", 1)[1].strip()
        return f"{self.from_email.lower()}|{subject.lower()}"

    @property
    def first_name(self) -> str:
        name = self.from_name.strip().split(" ")[0] if self.from_name.strip() else ""
        return name if name and name.lower() not in ("unknown", "n/a") else "there"


class Classification(BaseModel):
    category: Category
    urgency: Urgency
    risk: Level = Field(description="high for money, legal, security, data, or angry customers")
    summary: str = Field(description="One sentence: what the customer wants")
    kb_question: str | None = Field(
        description="A self-contained product question to look up in the help center, or null if none")
    flags: list[Flag] = Field(default_factory=list)


class DraftOutput(BaseModel):
    reply: str = Field(description="The full email reply, ready to send")
    requires_human: bool = Field(description="True if a person must check or act before sending")
    requires_human_reason: str = Field(description="Why, or empty")
    next_action: str = Field(description="What the support team should do next, if anything")
    follow_up_in_days: int | None = Field(description="Days until we should check back, or null")
    cited_source_numbers: list[int] = Field(description="Numbers of the help-center sources used")
