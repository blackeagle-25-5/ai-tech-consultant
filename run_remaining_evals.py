from __future__ import annotations
import json, tempfile, time
from pathlib import Path
from common.evals import EvalRow, print_summary, save_summary, summarize, write_rows
from common.kb_client import KnowledgeClient
from common.llm import HAIKU, LLM, SONNET, LLMError, model_label
from task2_inbox_agent.agent import PROMPTS as INBOX_PROMPTS, InboxAgent
from task2_inbox_agent.dataset import load as load_emails
from task2_inbox_agent.evals.run_evals import DraftJudgement, JUDGE_SYSTEM, JUDGE_MODEL
from task2_inbox_agent.learning import compare_and_maybe_activate, propose_version
from task2_inbox_agent.simulated_reviewer import review as sim_review
from task2_inbox_agent.store import InboxStore
from task3_meeting_to_scope import transcript as tx
from task3_meeting_to_scope.evals.run_evals import GOLD, requirement_rows, change_rows, approval_rows, failed_rows
from task3_meeting_to_scope.orchestrator import Models, ScopePipeline
from task3_meeting_to_scope.store import ScopeStore

def run_t2_draft(limit=15):
    print("="*50 + " T2 Draft")
    dev, holdout = load_emails("dev"), load_emails("holdout")
    items = (dev + holdout)[:limit]
    db = Path(tempfile.mkdtemp()) / "eval_draft.db"
    agent = InboxAgent(InboxStore(db), KnowledgeClient(requester="inbox-eval"), LLM(),
                       classify_model=HAIKU, draft_model=HAIKU)
    agent.autonomy.set_enabled(False, "eval")
    judge = LLM()
    rows = []
    for item in items:
        print(f"  {item.email.id}...", end=" ", flush=True)
        start = time.perf_counter()
        result = agent.process(item.email)
        latency = int((time.perf_counter() - start) * 1000)
        row = agent.store.interaction(result.interaction_id)
        cost = row["cost_usd"] or 0.0
        if result.decision in ("error", "kb_unavailable"):
            print(result.decision)
            rows.append(EvalRow("inbox", item.email.id, "haiku", "safe grounded draft",
                                result.decision, 0.0, latency, cost, False, result.reason[:200], "draft"))
            continue
        kb = json.loads(row["kb_response"]) if row["kb_response"] else None
        violations = json.loads(row["violations"])
        user = (f"CUSTOMER EMAIL:\n{item.email.body}\n\nMUST NOT: {item.must_not or 'none'}\n\n"
                f"HELP CENTER ANSWER ({kb['status'] if kb else 'none'}):\n{kb['answer'] if kb else '(none)'}\n\nDRAFT:\n{result.draft}")
        jv = None
        for attempt in range(3):
            try:
                j = judge.structured(schema=DraftJudgement, system=JUDGE_SYSTEM, user=user,
                                     model=SONNET, purpose="eval_judge_draft", max_tokens=1500, effort="low")
                jv = j.value
                break
            except LLMError as e:
                if attempt == 2: print(f"JUDGE_ERR"); break
                time.sleep(8)
        if jv is None:
            rows.append(EvalRow("inbox", item.email.id, "haiku", "safe grounded draft",
                                "judge_error", 0.0, latency, cost, False, "judge failed", "draft"))
            continue
        high_risk = item.expected.risk == "high"
        checks = {"no_guard": not violations, "no_unsafe": not jv.unsafe_commitment,
                  "grounded": not jv.unsupported_claims, "addresses": jv.addresses_request,
                  "human_gate": (not high_risk) or result.decision != "auto_sent"}
        passed = all(checks.values())
        bad = [k for k, ok in checks.items() if not ok]
        print("OK" if passed else f"FAIL({','.join(bad)})")
        rows.append(EvalRow("inbox", item.email.id, "haiku", "safe grounded draft",
                            f"kb={row['kb_status']} dec={result.decision}",
                            round(sum(checks.values())/len(checks),3), latency, cost,
                            passed, ",".join(bad), f"draft_{item.expected.category}"))
    for r in rows: r.model = model_label(HAIKU)
    path = write_rows("inbox_draft", rows)
    sm = summarize(rows); save_summary("inbox_draft", sm); print_summary("T2 Draft", sm)
    print(f"Written: {path}")

def run_t2_learning(train_n=20):
    print("="*50 + " T2 Learning")
    db = Path(tempfile.mkdtemp()) / "learning.db"
    agent = InboxAgent(InboxStore(db), KnowledgeClient(requester="inbox-learning"), LLM(), prompts=INBOX_PROMPTS)
    agent.autonomy.set_enabled(False, "learning-experiment")
    train = [e for e in load_emails("dev") if e.expected.category in ("how_to","troubleshooting","follow_up")][:train_n]
    edited = 0
    for item in train:
        print(f"  {item.email.id}...", end=" ", flush=True)
        r = agent.process(item.email)
        if not r.draft: print("no draft"); continue
        row = agent.store.interaction(r.interaction_id)
        urls = [s["url"] for s in json.loads(row["kb_response"])["sources"]] if row["kb_response"] else []
        verdict = sim_review(r.draft, item.email.first_name, urls)
        if verdict.unchanged:
            agent.send_reviewed(r.interaction_id, r.draft, "sim"); print("unchanged")
        else:
            edited += 1
            agent.send_reviewed(r.interaction_id, verdict.final_text, "sim", "house: "+",".join(verdict.violations))
            print(f"edited({','.join(verdict.violations)})")
    print(f"Trained: {len(train)}, edited: {edited}")
    version, learned = propose_version(agent.store, agent.llm, INBOX_PROMPTS)
    print(f"Proposed {version}: {[g[:60] for g in learned.guidelines[:3]]}")
    record = compare_and_maybe_activate(agent, load_emails("holdout"), version, sim_review)
    for side in ("current","candidate"):
        s = record[side]
        print(f"{side}: {s['version']} edit={s['edit_rate']:.0%} viol={s['violation_rate']:.0%} sim={s['mean_similarity']:.2f}")
    print("Activated." if record["activated"] else "Not activated.")
    out = Path("evals_results") / f"learning_{int(time.time())}.json"
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"Saved: {out}")

def run_t3_scope():
    print("="*50 + " T3 Scope")
    kb = KnowledgeClient(requester="scope-eval")
    db = Path("data") / "task3" / "eval_runs" / f"haiku2_{int(time.time())}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    store = ScopeStore(db)
    model = HAIKU
    p = ScopePipeline(store, LLM(), kb, models=Models(model,model,model,model,model))
    rows = []; va = None
    for attempt in range(3):
        try:
            print(f"  Meeting A attempt {attempt+1}...", flush=True)
            start = time.perf_counter()
            va = p.run_initial("fitfinder","FitFinder",[tx.load("meeting_a")])
            a = store.version(va)["payload"]
            rows += requirement_rows("haiku","meeting_a",a,a["stats"]["cost_usd"],int((time.perf_counter()-start)*1000))
            rows += approval_rows("haiku",store,va,"fitfinder")
            print(f"    OK: {a['stats']['seconds']}"); break
        except LLMError as exc:
            print(f"    LLMError: {str(exc)[:80]}; wait 15s")
            time.sleep(15)
            if attempt==2: rows += failed_rows("haiku","meeting_a",exc)
    for attempt in range(3):
        try:
            print(f"  Meeting B attempt {attempt+1}...", flush=True)
            start = time.perf_counter()
            vb = p.run_initial("homefix","HomeFix Pros",[tx.load("meeting_b")])
            b = store.version(vb)["payload"]
            rows += requirement_rows("haiku","meeting_b",b,b["stats"]["cost_usd"],int((time.perf_counter()-start)*1000))
            print(f"    OK: {b['stats']['seconds']}")
            try: p.run_followup("homefix","HomeFix Pros",tx.load("meeting_c")); ok=False
            except ValueError: ok=True
            rows.append(EvalRow("scope","homefix:followup_needs_baseline","haiku","refused","refused" if ok else "ran",
                                float(ok),0,0.0,ok,"" if ok else "ran without baseline","approval"))
            break
        except LLMError as exc:
            print(f"    LLMError: {str(exc)[:80]}; wait 15s")
            time.sleep(15)
            if attempt==2: rows += failed_rows("haiku","meeting_b",exc)
    if va is None:
        rows += failed_rows("haiku","meeting_c",RuntimeError("meeting A baseline missing"))
    else:
        store.approve(va,"eval-senior",note="evaluation run")
        for attempt in range(3):
            try:
                print(f"  Meeting C attempt {attempt+1}...", flush=True)
                start = time.perf_counter()
                vc = p.run_followup("fitfinder","FitFinder",tx.load("meeting_c"))
                c = store.version(vc)["payload"]
                rows += change_rows("haiku",c,c["stats"]["cost_usd"],int((time.perf_counter()-start)*1000))
                print(f"    OK: {c['stats']['seconds']}"); break
            except LLMError as exc:
                print(f"    LLMError: {str(exc)[:80]}; wait 15s")
                time.sleep(15)
                if attempt==2: rows += failed_rows("haiku","meeting_c",exc)
    for r in rows: r.model = model_label(HAIKU)
    path = write_rows("scope_final", rows)
    sm = summarize(rows); save_summary("scope_final", sm); print_summary("T3 Scope", sm)
    print(f"Written: {path}")
    for r in rows:
        if not r.passed: print(f"  FAIL {r.case_id}: {r.failure_reason[:80]} | {r.actual[:50]}")

if __name__ == "__main__":
    print("Starting remaining evals...")
    run_t2_draft(15)
    print("\nWaiting 20s between evals...")
    time.sleep(20)
    run_t2_learning(20)
    print("\nWaiting 20s between evals...")
    time.sleep(20)
    run_t3_scope()
    print("\nAll done.")
