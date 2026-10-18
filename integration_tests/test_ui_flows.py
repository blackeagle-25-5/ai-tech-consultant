"""UI flow tests: drive the real Streamlit pages headlessly (streamlit.testing) against databases seeded with a
fake model. Verifies the human review buttons change state as intended. No network, no model calls."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from common.kb_client import KnowledgeClient
from common.llm import LLM
from common.testing import FakeAnthropic

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def inbox_db(tmp_path, monkeypatch):
    import httpx

    from task2_inbox_agent.agent import InboxAgent
    from task2_inbox_agent.schemas import Classification, DraftOutput
    from task2_inbox_agent.store import InboxStore
    from task2_inbox_agent.tests.conftest import KB_ANSWER, email

    def respond(kw):
        if kw["output_format"] is Classification:
            return Classification(category="how_to", urgency="normal", risk="low", summary="logo",
                                  kb_question="How do I change the logo?", flags=[])
        return DraftOutput(reply="Hi Priya,\n\nGo to Settings > Brand Images.\n\nSupport Team", requires_human=False,
                           requires_human_reason="", next_action="", follow_up_in_days=None, cited_source_numbers=[1])

    kb = KnowledgeClient(base_url="http://kb.test", api_key="", retries=0,
                         transport=httpx.MockTransport(lambda r: httpx.Response(200, json=KB_ANSWER)))
    db = tmp_path / "inbox.db"
    agent = InboxAgent(InboxStore(db), kb, LLM(client=FakeAnthropic(respond)))
    agent.process(email(1))
    agent.process(email(2))
    monkeypatch.setenv("INBOX_DB", str(db))
    monkeypatch.setenv("GROQ_API_KEY", "dummy")  # pages only check that a key exists; no calls are made here
    import task2_inbox_agent.agent as agent_mod
    importlib.reload(agent_mod)
    return db


def test_inbox_send_and_reject_buttons_update_the_database(inbox_db):
    import sqlite3

    at = AppTest.from_file(str(ROOT / "task2_inbox_agent" / "ui.py"), default_timeout=60).run()
    assert not at.exception
    send = next(b for b in at.button if b.label == "Send")
    send.click().run()
    assert not at.exception
    conn = sqlite3.connect(inbox_db)
    assert conn.execute("SELECT outcome FROM interactions WHERE id = 1").fetchone()[0] == "sent_unchanged"
    reject = next(b for b in at.button if b.label == "Reject & send my text")
    at.text_area[0].input("Hi Priya, please call us.").run()
    reject.click().run()
    assert conn.execute("SELECT outcome FROM interactions WHERE id = 2").fetchone()[0] == "rejected"
    assert conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 2


@pytest.fixture
def scope_db(tmp_path, monkeypatch):
    from task3_meeting_to_scope.tests.conftest import MEETING_A, Responder, kb_transport, meeting
    from task3_meeting_to_scope.agents.estimator import Pricing
    from task3_meeting_to_scope.orchestrator import ScopePipeline
    from task3_meeting_to_scope.store import ScopeStore

    db = tmp_path / "scope.db"
    kb = KnowledgeClient(base_url="http://kb.test", api_key="", retries=0, transport=kb_transport())
    p = ScopePipeline(ScopeStore(db), LLM(client=FakeAnthropic(Responder())), kb, pricing=Pricing())
    p.run_initial("fitfinder", "FitFinder", [meeting("meeting_a", MEETING_A)])
    monkeypatch.setenv("SCOPE_DB", str(db))
    monkeypatch.setenv("GROQ_API_KEY", "dummy")
    import task3_meeting_to_scope.store as store_mod
    importlib.reload(store_mod)
    import task3_meeting_to_scope.orchestrator as orch
    importlib.reload(orch)
    return db


def test_scope_delivery_is_blocked_until_approved_in_the_ui(scope_db):
    import sqlite3

    at = AppTest.from_file(str(ROOT / "task3_meeting_to_scope" / "ui.py"), default_timeout=60).run()
    assert not at.exception
    next(b for b in at.button if b.label == "Send proposal to client (simulated)").click().run()
    assert any("Blocked" in e.value for e in at.error)
    at.text_input[0].input("Senior Reviewer").run()
    next(b for b in at.button if b.label == "Approve").click().run()
    assert not at.exception
    conn = sqlite3.connect(scope_db)
    assert conn.execute("SELECT status, reviewer FROM versions").fetchone() == ("approved", "Senior Reviewer")
    next(b for b in at.button if b.label == "Send proposal to client (simulated)").click().run()
    assert conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 1
