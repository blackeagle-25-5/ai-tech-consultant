"""LIVE integration tests: real local Knowledge Brain API + the configured model provider (Groq free tier).

  pytest integration_tests -m live -v

Not part of the default (mocked, offline) suite. Each run starts its own API server on a copy of the
knowledge database, so nothing here modifies the real data. Skipped when no model key is configured.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from common.kb_client import KnowledgeClient
from common.llm import llm_configured, provider

ROOT = Path(__file__).resolve().parent.parent
REAL_DB = ROOT / "data" / "task1" / "knowledge.db"

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(not llm_configured(), reason="no model key configured"),
              pytest.mark.skipif(not REAL_DB.exists(), reason="knowledge base not ingested yet")]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("kb")
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(REAL_DB) + suffix)
        if src.exists():
            shutil.copy(src, data_dir / ("knowledge.db" + suffix))
    port = free_port()
    env = {**os.environ, "KB_DATA_DIR": str(data_dir), "KB_DAILY_SYNC": "0", "PYTHONPATH": str(ROOT),
           "KB_ESCALATED_EVAL_PATH": str(data_dir / "escalated.jsonl"),
           "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "task1_knowledge_brain.app.api:app", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(90):
        try:
            if httpx.get(f"{url}/health", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(1)
    else:
        proc.terminate()
        pytest.fail("API did not start")
    yield url
    proc.terminate()
    proc.wait(timeout=20)


def client(url: str, who: str) -> KnowledgeClient:
    return KnowledgeClient(base_url=url, api_key="", requester=who, timeout_s=180)


def test_health_reports_provider(api):
    h = client(api, "test").health()
    assert h["articles_active"] >= 1500 and h["llm_configured"] and h["llm_provider"] == provider()


def test_task1_answers_with_real_citation(api):
    ans = client(api, "test").query("Should I use Stripe or PayPal for member payments?", escalate=False)
    assert ans.status == "answered"
    assert any("stripe" in s.title.lower() for s in ans.sources)


def test_task1_declines_personal_information_without_model(api):
    ans = client(api, "test").query("What is the home address of the CEO of Brilliant Directories?", escalate=False)
    assert ans.status == "insufficient_evidence" and "350" not in ans.answer and ans.model is None


def test_expert_answer_reaches_both_downstream_clients(api):
    inbox, scope = client(api, "inbox-agent"), client(api, "scope-builder")
    question = "Can members pay for a featured listing with Bitcoin?"
    esc_id = inbox.escalate(question, "integration test")
    inbox.resolve_escalation(esc_id, "Not natively; crypto payments need a custom gateway integration.", "qa-expert")
    for c in (inbox, scope):
        hits = c.search("pay featured listing with Bitcoin crypto", k=3)
        assert hits[0].source_type == "expert", f"{c.requester} did not see the expert answer"


def test_task2_drafts_a_grounded_reply_through_the_api(api, tmp_path):
    from task2_inbox_agent.agent import build_agent
    from task2_inbox_agent.dataset import load

    item = next(e for e in load("dev") if e.email.id == "D03")  # mass email question
    agent = build_agent(db_path=tmp_path / "inbox.db", kb=client(api, "inbox-agent"))
    r = agent.process(item.email)
    row = agent.store.interaction(r.interaction_id)
    assert r.classification.category == "how_to" and row["kb_status"] in ("answered", "insufficient_evidence")
    assert r.draft and r.decision == "queued"


def test_task3_feasibility_uses_the_api(api):
    from common.llm import HAIKU, LLM
    from task3_meeting_to_scope.agents.feasibility import check
    from task3_meeting_to_scope.schemas import Requirement, SourceRef

    req = Requirement(id="REQ-001", title="Coupon codes at checkout", description="Let members enter a coupon code "
                      "for a discount when paying for a membership plan.", origin="explicit", status="confirmed",
                      priority="must_have", sources=[SourceRef(meeting_id="m", line=1, quote="coupon code")])
    result, _ = check(LLM(), client(api, "scope-builder"), req, HAIKU)
    assert result.kb_status in ("answered", "insufficient_evidence") and result.classification
    if result.classification in ("existing_configuration", "existing_feature_with_limitation"):
        assert result.sources, "an 'existing' verdict must cite help-center evidence"
