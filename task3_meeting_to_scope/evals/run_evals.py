"""Meeting-to-Scope evaluation. Needs the Knowledge Brain API running (python run.py) and a model key
(GROQ_API_KEY, or ANTHROPIC_API_KEY for Claude); rows record the model that actually ran.

  python -m task3_meeting_to_scope.evals.run_evals --models haiku,sonnet

For each model configuration the full five-agent pipeline runs on meetings A and B, the A scope is approved,
and meeting C is compared with it. Cases: requirement recall per gold item, budget/deadline/exclusion
capture, change detection for C, grounded feasibility, and the approval gate.
"""

from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

import yaml

from common.evals import EvalRow, print_summary, save_summary, summarize, write_rows
from common.kb_client import KnowledgeClient
from common.llm import HAIKU, LLM, SONNET, model_label
from task3_meeting_to_scope import transcript as tx
from task3_meeting_to_scope.orchestrator import Models, ScopePipeline
from task3_meeting_to_scope.store import NotApproved, ScopeStore

GOLD = yaml.safe_load((Path(__file__).parent / "gold.yaml").read_text(encoding="utf-8"))
MODELS = {"haiku": HAIKU, "sonnet": SONNET}


def req_text(r: dict) -> str:
    return f"{r['title']} {r['description']}".lower()


def matches(text: str, groups: list[list[str]], also: list[str] | None = None) -> bool:
    ok = all(any(k.lower() in text for k in g) for g in groups)
    return ok and (not also or any(a in text for a in also))


def requirement_rows(model: str, meeting: str, payload: dict, cost: float, latency: int) -> list[EvalRow]:
    gold = GOLD[meeting]
    reqs = payload["brief"]["requirements"]
    n = len(gold["requirements"]) + 1
    rows = []
    for g in gold["requirements"]:
        hit = next((r for r in reqs if matches(req_text(r), g["groups"], g.get("also"))), None)
        rows.append(EvalRow("scope", f"{meeting}:{g['id']}", model, g["what"],
                            f"{hit['id']} {hit['title']}" if hit else "not found", 1.0 if hit else 0.0,
                            latency // n, cost / n, hit is not None, "" if hit else "requirement missed",
                            "requirement_recall"))
    brief = payload["brief"]
    deadline = (brief.get("deadline") or "").lower()
    ok = any(k in deadline for k in gold["deadline_contains"])
    rows.append(EvalRow("scope", f"{meeting}:deadline", model, str(gold["deadline_contains"]), deadline or "none",
                        float(ok), 0, 0.0, ok, "" if ok else "deadline not captured", "constraints"))
    excl = " ".join(payload["scope"]["exclusions"] + [e["text"] for e in brief["exclusions"]]).lower()
    ok = all(k in excl for k in gold["must_exclude"])
    rows.append(EvalRow("scope", f"{meeting}:exclusion", model, str(gold["must_exclude"]), excl[:150],
                        float(ok), 0, 0.0, ok, "" if ok else "exclusion missing", "constraints"))
    grounded = [f for f in payload["feasibility"]
                if f["classification"] in ("existing_configuration", "existing_feature_with_limitation")]
    ok = all(f["sources"] for f in grounded)
    rows.append(EvalRow("scope", f"{meeting}:feasibility_grounded", model,
                        "every 'existing' verdict cites a help-center source",
                        f"{len(grounded)} existing verdicts, {sum(bool(f['sources']) for f in grounded)} with sources",
                        float(ok), 0, 0.0, ok, "" if ok else "existing verdict without evidence", "feasibility"))
    return rows


def change_rows(model: str, payload: dict, cost: float, latency: int) -> list[EvalRow]:
    g = GOLD["meeting_c_changes"]
    cr = payload["change_report"]
    removed = " ".join(f"{r['title']} {r.get('reason', '')}" for r in cr["removed"]).lower()
    modified = " ".join(f"{m['title']} {m['change']} {m['after']}" for m in cr["modified"]).lower()
    added = " ".join(f"{a['title']} {a['description']}" for a in cr["added"]).lower()
    oos = " ".join(f"{o['title']} {o['description']}" for o in cr["out_of_scope"]).lower()
    exclusions = " ".join(payload["scope"]["exclusions"]).lower()
    checks = {
        "removed_instagram": any(k in removed for k in g["removed_contains"]),
        "languages_filter": any(k in modified + added for k in g["modified_or_added_contains"]),
        "gender_dropped": "gender" in removed or "gender" in modified,
        "booking_out_of_scope": any(k in oos for k in g["added_out_of_scope_contains"]),
        "blog_flagged": "blog" in oos or "blog" in exclusions or "content" in oos,
        "deadline_change": any(k in (cr["deadline_change"] or "").lower() for k in g["deadline_change_contains"]),
        "kept_unchanged_items": not any(k in removed for k in g["must_not_remove"]),
    }
    n = len(checks)
    return [EvalRow("scope", f"meeting_c:{name}", model, name, f"removed=[{removed[:80]}] oos=[{oos[:80]}]",
                    float(ok), latency // n, cost / n, ok, "" if ok else f"{name} not detected", "change_detection")
            for name, ok in checks.items()]


def approval_rows(model: str, store: ScopeStore, draft_id: int, project: str) -> list[EvalRow]:
    rows = []
    try:
        store.deliver(draft_id, "eval")
        ok = False
    except NotApproved:
        ok = True
    rows.append(EvalRow("scope", f"{project}:deliver_unapproved_blocked", model, "blocked", "blocked" if ok else "sent",
                        float(ok), 0, 0.0, ok, "" if ok else "unapproved proposal was sent", "approval"))
    return rows


def run_model(model_key: str, kb: KnowledgeClient) -> list[EvalRow]:
    model = MODELS[model_key]
    store = ScopeStore(Path(tempfile.mkdtemp()) / "scope_eval.db")
    p = ScopePipeline(store, LLM(), kb, models=Models(model, model, model, model, model))
    rows: list[EvalRow] = []

    start = time.perf_counter()
    va = p.run_initial("fitfinder", "FitFinder", [tx.load("meeting_a")])
    a = store.version(va)["payload"]
    rows += requirement_rows(model_key, "meeting_a", a, a["stats"]["cost_usd"], int((time.perf_counter() - start) * 1000))
    rows += approval_rows(model_key, store, va, "fitfinder")

    start = time.perf_counter()
    vb = p.run_initial("homefix", "HomeFix Pros", [tx.load("meeting_b")])
    b = store.version(vb)["payload"]
    rows += requirement_rows(model_key, "meeting_b", b, b["stats"]["cost_usd"], int((time.perf_counter() - start) * 1000))

    try:
        p.run_followup("homefix", "HomeFix Pros", tx.load("meeting_c"))
        ok = False
    except ValueError:
        ok = True
    rows.append(EvalRow("scope", "homefix:followup_needs_baseline", model_key, "refused", "refused" if ok else "ran",
                        float(ok), 0, 0.0, ok, "" if ok else "compared against an unapproved draft", "approval"))

    store.approve(va, "eval-senior", note="evaluation run")
    start = time.perf_counter()
    vc = p.run_followup("fitfinder", "FitFinder", tx.load("meeting_c"))
    c = store.version(vc)["payload"]
    rows += change_rows(model_key, c, c["stats"]["cost_usd"], int((time.perf_counter() - start) * 1000))
    print(f"{model_key}: A ${a['stats']['cost_usd']:.4f} {a['stats']['seconds']}, "
          f"B ${b['stats']['cost_usd']:.4f}, C ${c['stats']['cost_usd']:.4f}")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="haiku,sonnet")
    args = ap.parse_args()
    kb = KnowledgeClient(requester="scope-eval")
    rows: list[EvalRow] = []
    for m in args.models.split(","):
        rows += run_model(m, kb)
    for r in rows:  # label rows with the model that actually ran (Claude, or the Groq fallback)
        r.model = model_label(MODELS[r.model]) if r.model in MODELS else r.model
    path = write_rows("scope", rows)
    summary = summarize(rows)
    save_summary("scope", summary)
    print_summary("Meeting-to-Scope", summary)
    print(f"\nRows written to {path}")
    for r in rows:
        if not r.passed:
            print(f"  FAIL {r.model} {r.case_id}: {r.failure_reason} ({r.actual[:80]})")


if __name__ == "__main__":
    main()
