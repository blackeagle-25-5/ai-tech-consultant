"""Load the hand-written email datasets."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from task2_inbox_agent.schemas import Category, Email, Level, Urgency

DATA_DIR = Path(__file__).resolve().parent / "data"


class Expected(BaseModel):
    category: Category
    risk: Level
    urgency: Urgency
    escalate: bool


class LabelledEmail(BaseModel):
    email: Email
    expected: Expected
    must_not: list[str] = []
    kb_hint: str | None = None


def load(name: str = "dev") -> list[LabelledEmail]:
    raw = yaml.safe_load((DATA_DIR / f"emails_{name}.yaml").read_text(encoding="utf-8"))
    items = []
    for i, r in enumerate(raw):
        email = Email(id=r["id"], from_name=r["from_name"], from_email=r["from_email"], subject=r["subject"],
                      body=r["body"].strip(), received_at=f"2026-10-{10 + i // 20:02d}T09:{i % 60:02d}:00+00:00")
        items.append(LabelledEmail(email=email, expected=Expected(**r["expected"]),
                                   must_not=r.get("must_not", []), kb_hint=r.get("kb_hint")))
    return items
