"""Regression tests for K09 (answer listed 2 of 4 mandatory import fields). Mock-based, no network."""

from task1_knowledge_brain.app import completeness
from task1_knowledge_brain.app.qa import AnswerDraft
from task1_knowledge_brain.retrieval.chunker import Chunk
from task1_knowledge_brain.retrieval.index import SearchHit

from .conftest import make_service

# Real passage structure from article 5000568062 (four mandatory fields over three passages).
P1 = ("Mandatory Fields For Member Imports\n## Name or Company (name, company)\nField Variable: name, company\n"
      "Include either the name or company fields.")
P2 = ("Mandatory Fields For Member Imports\n## Listing Type (listing_type)\nField Variable: listing_type\n"
      "Company or Individual.\n## Country Code (country_code)\nField Variable: country_code\nTwo-letter country codes.")
P3 = ("Mandatory Fields For Member Imports\n## Profession Name (profession_name)\nField Variable: profession_name\n"
      "Top-level categories.\n## Subscription & Billing Fields\nThese fields are optional for standard imports.\n"
      "Field Variable: subscription_start_date")
Q = "Which fields are mandatory when importing members from a CSV?"
ALL_FOUR = "Required: name or company, listing_type, country_code and profession_name."


def hits() -> list[SearchHit]:
    return [SearchHit(f"p{i}", "5000568062", "article", "Mandatory Fields", "https://x/a/5000568062", "", t, [], 0, .9)
            for i, t in enumerate((P1, P2, P3), 1)]


def test_items_listed_in_evidence_exclude_optional_sections():
    items = completeness.listed_items(hits(), {"5000568062"})
    assert items == ["name", "company", "listing_type", "country_code", "profession_name"]


def test_omitting_any_mandatory_field_is_detected():
    two_of_four = "You need the name or company field and the profession_name field."
    assert completeness.missing_items(Q, two_of_four, hits(), hits()) == ["listing_type", "country_code"]
    for field in ("listing_type", "country_code", "profession_name"):
        assert field in completeness.missing_items(Q, ALL_FOUR.replace(field, "x"), hits(), hits())


def test_complete_answer_passes_and_alternatives_count_once():
    assert completeness.missing_items(Q, ALL_FOUR, hits(), hits()) == []
    assert completeness.missing_items(Q, ALL_FOUR.replace("name or company", "company"), hits(), hits()) == []


def test_non_list_questions_are_not_checked():
    assert completeness.missing_items("How do I import members?", "Use the CSV importer.", hits(), hits()) == []


def test_service_repairs_an_incomplete_answer(settings, index):
    for i, t in enumerate((P1, P2, P3), 1):
        index.upsert_chunk(Chunk(f"p{i}", "5000568062", "article", "Mandatory Fields For Member Imports",
                                 "https://help.example.com/a/5000568062", "", t))
    calls = []

    def respond(kwargs):
        calls.append(kwargs["messages"][0]["content"][0]["text"])
        text = ALL_FOUR if "<review_note>" in calls[-1] else "You need name or company and profession_name."
        return AnswerDraft(status="answered", answer=text, cited_evidence=["E1", "E2", "E3"], confidence="high")

    r = make_service(settings, index, respond).answer(Q + " mandatory fields member imports", escalate=False)
    assert len(calls) == 2 and "listing_type, country_code" in calls[1]
    assert r.status == "answered" and all(f in r.answer for f in ("listing_type", "country_code", "profession_name"))
    assert r.safety_notes == ["completeness repair for listing_type, country_code; fixed"]
