import shutil
from pathlib import Path

import task2_inbox_agent.learning as learning
from common.kb_client import KnowledgeClient
from common.llm import LLM
from common.prompts import PromptRegistry
from common.testing import FakeAnthropic
from task2_inbox_agent.agent import InboxAgent
from task2_inbox_agent.dataset import LabelledEmail, Expected
from task2_inbox_agent.schemas import Classification, DraftOutput
from task2_inbox_agent.simulated_reviewer import SIGN_OFF, review
from task2_inbox_agent.store import InboxStore

from .conftest import email, how_to, kb_transport

URL = "https://support.brilliantdirectories.com/a/1"


def test_reviewer_fixes_house_style():
    r = review("Hello Priya!\nI hope this helps. Go to Design Settings.\n\nThanks,\nSupport", "Priya", [URL])
    assert set(r.violations) == {"R1_greeting", "R2_signoff", "R4_guide_link", "R5_filler"}
    assert r.final_text.startswith("Hi Priya,") and r.final_text.endswith(SIGN_OFF)
    assert f"Guide: {URL}" in r.final_text and "hope this helps" not in r.final_text.lower()
    # A compliant reply is left alone.
    assert review(r.final_text, "Priya", [URL]).unchanged


def test_learning_creates_version_and_activates_only_if_better(tmp_path, monkeypatch):
    prompts_dir = tmp_path / "prompts"
    shutil.copytree(Path(learning.__file__).parent / "prompts", prompts_dir)
    prompts = PromptRegistry(prompts_dir)
    monkeypatch.setattr(learning, "LOG_PATH", tmp_path / "runs.jsonl")
    good = f"Hi Priya,\n\n1. Go to Design Settings.\n\nGuide: {URL}\n\n{SIGN_OFF}"

    def respond(kwargs):
        schema = kwargs["output_format"]
        if schema is Classification:
            return how_to()
        if schema is learning.LearnedGuidelines:
            return learning.LearnedGuidelines(guidelines=["Start with 'Hi <first name>,'",
                                                          "End with 'Best regards,\nNorthstar Support Team'"],
                                              evidence="All edits changed greeting and sign-off.")
        learned = "Learned from reviewer edits" in kwargs["system"]
        return DraftOutput(reply=good if learned else "Hello!\nGo to settings.\nThanks",
                           requires_human=False, requires_human_reason="", next_action="",
                           follow_up_in_days=None, cited_source_numbers=[1])

    kb = KnowledgeClient(base_url="http://kb.test", api_key="", retries=0, transport=kb_transport())
    agent = InboxAgent(InboxStore(tmp_path / "inbox.db"), kb, LLM(client=FakeAnthropic(respond)), prompts=prompts)
    for i in range(3):
        r = agent.process(email(i))
        fixed = review(r.draft, "Priya", [URL])
        agent.send_reviewed(r.interaction_id, fixed.final_text, "asha", ", ".join(fixed.violations))

    version, _ = learning.propose_version(agent.store, agent.llm, prompts)
    assert version == "v2" and prompts.active_version("drafter") == "v1"
    items = [LabelledEmail(email=email(10 + i), expected=Expected(category="how_to", risk="low",
                                                                   urgency="normal", escalate=False))
             for i in range(3)]
    record = learning.compare_and_maybe_activate(agent, items, version, review)
    assert record["activated"] and record["current"]["edit_rate"] == 1.0 and record["candidate"]["edit_rate"] == 0.0
    assert prompts.active_version("drafter") == "v2"

    learning.rollback(prompts, "v1", agent.store, "lead")
    assert prompts.active_version("drafter") == "v1"
