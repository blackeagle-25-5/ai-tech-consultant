"""K09 fix: check that list-type answers keep every item the cited evidence lists.

For questions asking for required/mandatory fields, columns or steps, collect the field variables and
section items the cited articles list, and report the ones the answer does not mention. The service then
asks the model once more, naming the omitted items, and records anything still missing.
"""

from __future__ import annotations

import re

from task1_knowledge_brain.retrieval.index import SearchHit

LIST_QUESTION = re.compile(r"\b(required|mandatory|must (include|have|contain)|which (fields|columns|steps)|"
                           r"what (fields|columns|steps)|list (of|all)|columns? (are|must))\b", re.I)
FIELD_VARIABLE = re.compile(r"Field Variables?:\s*([a-z0-9_]+(?:\s*,\s*[a-z0-9_]+)*)", re.I)
OPTIONAL_HINT = re.compile(r"\b(optional|not required)\b", re.I)


def is_list_question(question: str) -> bool:
    return bool(LIST_QUESTION.search(question))


def listed_items(hits: list[SearchHit], article_ids: set[str]) -> list[str]:
    """Field variables listed in the cited articles' passages, skipping sections marked optional."""
    items: list[str] = []
    for h in hits:
        if h.article_id not in article_ids:
            continue
        for section in re.split(r"\n(?=#{2,4} )", h.text):
            if OPTIONAL_HINT.search(section):
                continue
            for group in FIELD_VARIABLE.findall(section):
                for var in re.split(r"\s*,\s*", group):
                    if var and var not in items:
                        items.append(var)
    return items


def mentioned(item: str, answer: str) -> bool:
    a = answer.lower()
    return item.lower() in a or item.lower().replace("_", " ") in a


def missing_items(question: str, answer: str, hits: list[SearchHit], cited: list[SearchHit]) -> list[str]:
    if not is_list_question(question):
        return []
    articles = {h.article_id for h in cited}
    items = listed_items(hits, articles)
    # "name, company" style alternatives count as one requirement: satisfied if any of them is mentioned.
    missing = [i for i in items if not mentioned(i, answer)]
    for group in (g for h in hits if h.article_id in articles for g in FIELD_VARIABLE.findall(h.text)):
        alts = [v for v in re.split(r"\s*,\s*", group) if v]
        if len(alts) > 1 and any(mentioned(v, answer) for v in alts):
            missing = [m for m in missing if m not in alts]
    return missing
