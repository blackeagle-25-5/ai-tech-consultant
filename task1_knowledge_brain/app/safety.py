"""Deterministic answer-safety checks that run regardless of what the model says.

- Personal-information requests (someone's home address, private phone, ...) are declined without a model call.
- Passages that look like documentation sample data (example.com emails, 555 numbers, "John Smith") are
  treated as illustrations: specific values copied from them are not facts about real people.
- Specific factual values in an answer (emails, phone numbers, street addresses, prices) must appear in the
  retrieved evidence; otherwise the answer is treated as unsupported.
"""

from __future__ import annotations

import re

from task1_knowledge_brain.retrieval.index import SearchHit

PERSONAL_INFO_REQUEST = re.compile(
    r"\b(home|house|residential|private|personal|cell|mobile)\s+(address|phone|number|email|e-mail|location)"
    r"|\bwhere\s+(does|do|did)\s+[\w .'-]{1,40}\s+live\b"
    r"|\b(date of birth|social security|passport number|salary of|net worth of)\b"
    r"|\b(ceo|founder|owner|president|employee|staff member|manager)('s|s')?\s+(address|phone|email|home)", re.I)

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\s().-]?){0,3}\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\w)")
STREET = re.compile(r"\b\d{1,6}\s+(?:[A-Z0-9][\w.]*\s){1,4}(?:st|street|ave|avenue|rd|road|blvd|boulevard|lane|ln|"
                    r"drive|dr|way|suite|ste)\b\.?", re.I)
PRICE = re.compile(r"[$€£₹]\s?\d[\d,]*(?:\.\d+)?")
SAMPLE_MARKERS = [re.compile(p, re.I) for p in (
    r"@example\.(com|org|net)", r"\b555[-. ]?\d{3}[-. ]?\d{4}\b", r"\b(john|jane)\s+(smith|doe)\b",
    r"\bexample company\b", r"\blorem ipsum\b", r"\bacme\b", r"\bsample (data|payload|response)\b")]


def is_personal_info_request(question: str) -> bool:
    return bool(PERSONAL_INFO_REQUEST.search(question))


def is_sample_data(text: str) -> bool:
    """A passage with two or more placeholder markers is documentation sample data."""
    return sum(bool(p.search(text)) for p in SAMPLE_MARKERS) >= 2


def specifics(text: str) -> list[str]:
    found = EMAIL.findall(text) + PHONE.findall(text) + STREET.findall(text) + PRICE.findall(text)
    return [s.strip() for s in found if s.strip()]


def _norm(s: str) -> str:
    return re.sub(r"[\s().,-]", "", s.lower())


def check_answer(answer: str, evidence: list[SearchHit]) -> list[str]:
    """Return problems found in an 'answered' response (empty list = OK)."""
    problems = []
    real = " ".join(_norm(h.text) for h in evidence if not is_sample_data(h.text))
    sample = " ".join(_norm(h.text) for h in evidence if is_sample_data(h.text))
    for value in specifics(answer):
        v = _norm(value)
        if v in real:
            continue
        if v in sample:
            problems.append(f"'{value}' comes from documentation sample data, not a real fact")
        else:
            problems.append(f"'{value}' does not appear in the evidence")
    return problems
