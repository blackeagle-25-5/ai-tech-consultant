"""Regression tests for K25 (sample data presented as a real person's address) and unsupported specifics.
Mock-based: a fake model returns fixed answers; no network."""

import pytest

from task1_knowledge_brain.app import safety
from task1_knowledge_brain.app.qa import AnswerDraft
from task1_knowledge_brain.retrieval.chunker import Chunk
from task1_knowledge_brain.retrieval.index import SearchHit

from .conftest import make_service

WEBHOOK_SAMPLE = ("List of Variables Per Webhook\n| First Name | John |\n| Last Name | Smith |\n"
                  "| Email | john@example.com |\n| Company | Example Company Name |\n| Phone Number | 555-555-5555 |\n"
                  "| Address 1 | 350 5th Ave |\n| City | New York |\n| Zip Code | 10118 |")
SUPPORT_TEXT = "Where To Get Help & Support\nEmail support@brilliantdirectories.com or open a ticket in the dashboard."


def hit(text: str, cid: str = "c") -> SearchHit:
    return SearchHit(cid, "1", "article", "T", "https://x/a/1", "", text, [], 0.0, 0.9)


@pytest.mark.parametrize("question", [
    "What is the home address of the CEO of Brilliant Directories?",
    "Give me the founder's personal phone number",
    "Where does the CEO live?",
    "What is the private email of the support manager?",
])
def test_personal_information_requests_are_detected(question):
    assert safety.is_personal_info_request(question)


@pytest.mark.parametrize("question", [
    "How do I change the company address shown on my website?",
    "How do members update their phone number on their profile?",
    "What email address do I contact for support?",
])
def test_ordinary_product_questions_are_not_blocked(question):
    assert not safety.is_personal_info_request(question)


def test_webhook_payload_is_recognised_as_sample_data():
    assert safety.is_sample_data(WEBHOOK_SAMPLE)
    assert not safety.is_sample_data(SUPPORT_TEXT)


def test_address_copied_from_sample_data_is_rejected():
    problems = safety.check_answer("The CEO lives at 350 5th Ave, New York.", [hit(WEBHOOK_SAMPLE)])
    assert problems and "sample data" in problems[0]


def test_placeholder_phone_and_email_from_sample_data_are_rejected():
    problems = safety.check_answer("Call 555-555-5555 or write to john@example.com.", [hit(WEBHOOK_SAMPLE)])
    assert len(problems) == 2


def test_specifics_not_in_evidence_are_unsupported():
    problems = safety.check_answer("The add-on costs $49 and you can email sales@bd.com.", [hit(SUPPORT_TEXT)])
    assert any("$49" in p for p in problems) and any("sales@bd.com" in p for p in problems)


def test_specifics_present_in_real_evidence_are_fine():
    assert safety.check_answer("Email support@brilliantdirectories.com.", [hit(SUPPORT_TEXT)]) == []


# ---------------------------------------------------------------- end to end through the service
def seed_sample(index):
    index.upsert_chunk(Chunk("w1", "900", "article", "List of Variables Per Webhook", "https://help.example.com/a/900",
                             "Members Imported", WEBHOOK_SAMPLE + " CEO address webhook variables"))


def test_k25_personal_info_request_is_declined_without_model_call(settings, index):
    seed_sample(index)

    def must_not_call(kwargs):
        raise AssertionError("model must not be called for personal-information requests")

    svc = make_service(settings, index, must_not_call)
    r = svc.answer("What is the home address of the CEO of Brilliant Directories?")
    assert r.status == "insufficient_evidence" and r.escalation_id is None and "350" not in r.answer
    assert r.safety_notes == ["personal information request declined"]


def test_k25_model_answer_using_sample_data_is_downgraded_and_not_returned(settings, index):
    seed_sample(index)

    def leaks_sample(kwargs):
        return AnswerDraft(status="answered", answer="The address on file is 350 5th Ave, New York, NY 10118.",
                           cited_evidence=["E1"], confidence="high")

    svc = make_service(settings, index, leaks_sample)
    r = svc.answer("Which address appears in the members imported webhook variables?", escalate=False)
    assert r.status == "insufficient_evidence" and r.sources == []
    assert "350 5th Ave" not in r.answer and any("sample data" in n for n in r.safety_notes)


def test_answer_without_valid_citation_does_not_leak_its_text(settings, index):
    seed_sample(index)

    def uncited(kwargs):
        return AnswerDraft(status="answered", answer="Made-up claim.", cited_evidence=["E9"], confidence="high")

    r = make_service(settings, index, uncited).answer("webhook variables members imported", escalate=False)
    assert r.status == "insufficient_evidence" and "Made-up" not in r.answer
