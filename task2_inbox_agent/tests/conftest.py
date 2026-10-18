from __future__ import annotations

import json
from typing import Callable

import httpx
import pytest

import common.llm
from common.kb_client import KnowledgeClient
from common.llm import LLM
from common.testing import FakeAnthropic
from task2_inbox_agent.agent import InboxAgent
from task2_inbox_agent.schemas import Classification, DraftOutput, Email
from task2_inbox_agent.store import InboxStore

KB_ANSWER = {
    "request_id": "r1", "status": "answered",
    "answer": "Go to Design Settings > Logo and upload a PNG.",
    "sources": [{"title": "Change your logo", "url": "https://support.brilliantdirectories.com/a/1",
                 "section": "", "source_type": "article", "relevance": 0.8, "image_urls": []}],
    "confidence": "high", "needs_human": False, "escalation_id": None, "model": "x",
    "latency_ms": 10, "cost_usd": 0.0001,
}


@pytest.fixture(autouse=True)
def _ledger_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(common.llm, "LEDGER_PATH", tmp_path / "ledger.jsonl")


def kb_transport(answer: dict | None = None, fail: bool = False) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(503, json={"detail": "down"})
        if request.url.path == "/query":
            return httpx.Response(200, json=answer or KB_ANSWER)
        if request.url.path == "/escalations":
            return httpx.Response(201, json={"id": 7})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def responder(cls: Classification, reply: str = "Hi Priya,\n\n1. Go to Design Settings > Logo.\n\nSupport Team",
              requires_human: bool = False) -> Callable[[dict], object]:
    def respond(kwargs):
        if kwargs["output_format"] is Classification:
            return cls
        return DraftOutput(reply=reply, requires_human=requires_human, requires_human_reason="",
                           next_action="", follow_up_in_days=None, cited_source_numbers=[1])
    return respond


def how_to(**overrides) -> Classification:
    data = dict(category="how_to", urgency="normal", risk="low", summary="Wants to change logo",
                kb_question="How do I change the logo?", flags=[])
    data.update(overrides)
    return Classification(**data)


@pytest.fixture
def make_agent(tmp_path):
    def build(resp, kb_fail: bool = False, kb_answer: dict | None = None) -> InboxAgent:
        kb = KnowledgeClient(base_url="http://kb.test", api_key="", retries=0,
                             transport=kb_transport(kb_answer, kb_fail), requester="test")
        return InboxAgent(InboxStore(tmp_path / "inbox.db"), kb, LLM(client=FakeAnthropic(resp)))
    return build


def email(i: int = 1, subject: str = "Logo", body: str = "How do I change my logo?",
          sender: str = "priya@example.com") -> Email:
    return Email(id=f"E{i}", from_name="Priya Raman", from_email=sender, subject=subject, body=body,
                 received_at=f"2026-10-18T10:{i % 60:02d}:00+00:00")


__all__ = ["json", "email", "how_to", "responder", "make_agent"]
